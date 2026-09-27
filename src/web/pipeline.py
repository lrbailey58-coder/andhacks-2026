"""The driver flask calls.

One turn of the conversation: whatever the user submitted is applied to the UI
first, then the agents are called one at a time and every event they produce is
turned into UI patches as it happens.  The generator is what the HTTP layer
streams, and what the tests walk through.

Which swarm the agents are is decided by the app-level switch in
:mod:`web.app`: with it off the real backend in ``backend/`` answers, with it on
the deterministic dummy does.  ``turn`` is called with no ``api`` by both the
routes and the tests, so the resolver below is what both go through.

Two things in here are not "call an agent and wait": the playtest, which is
played on a worker thread so ``<ShowSample>`` can show the game while the swarm
is still playing it, and the flag that lets the user end that playtest early.
"""

from __future__ import annotations

import queue
import threading
from types import ModuleType
from typing import Any, Callable, Iterator, Sequence

import api_errors
import web.ui as ui

Patch = dict[str, Any]


def focus_patch(state: ui.UIState) -> Patch:
    """Tell the browser to put the caret in whichever editor is now live."""

    return {"op": "focus", "id": state.awaiting}


def resolve_api() -> ModuleType:
    """The API the switch selects: ``backend.api`` normally, the dummy in tests.

    The import is inside the function because the switch lives in the module
    that imports this one.
    """

    import web.app as app

    return app.active_api()


def conversation_key(state: ui.UIState) -> str:
    """Which rules conversation a run is talking in.

    The backend keeps one open chat per run so the Rules agent can be asked a
    question and answered it in the same conversation.  The key has to change
    when a new run starts - the "What's next?" run is a new conversation with
    the whole definition in it - and to be different per session besides.
    """

    return f"{state.session}:{state.run}"


# The playtests in flight, by run.  A playtest is started from a request and
# finished from another one, so the button on <StatusPlaying> needs somewhere to
# find the swarm to stop; the app keeps the UI states the same way.
_playing: dict[str, tuple[ui.UIState, threading.Event]] = {}
_playing_lock = threading.Lock()


def begin_override(state: ui.UIState) -> bool:
    """The manual override: ask the player agent swarm in this run to stop.

    The playtest is not killed - a turn is already in flight and killing it
    mid-call would be exactly the kind of sharp edge a backend should not have -
    so a flag is set and the swarm finishes the turn it is on, then reports
    where it got to.  That is why <SimOverride> can say the swarm is stopping
    and the status settles afterwards: the result is honest about a game that
    ended early, which is not the same as one that finished.
    """

    with _playing_lock:
        entry = _playing.get(conversation_key(state))
    if entry is None or entry[0] is not state:
        return False
    entry[1].set()
    return True


def end_override(state: ui.UIState) -> None:
    with _playing_lock:
        _playing.pop(conversation_key(state), None)


def _playtest(
    state: ui.UIState,
    rules: str,
    deployment: Any,
    api: ModuleType,
) -> Iterator[list[Patch]]:
    """The playtest, played on a worker thread so the sample game can be shown.

    The backend plays the whole game in one call, so the turns are handed to it
    as a callback and come back one at a time; each one that arrives is put in
    the feed immediately, which is the point of the ``<ShowSample>`` box being
    there at all while ``<StatusPlaying>`` is still going.
    """

    records: list[dict[str, Any]] = []
    arrived: queue.Queue[Any] = queue.Queue()
    stop = threading.Event()

    def report(record: dict[str, Any]) -> None:
        arrived.put(record)

    def play() -> None:
        try:
            api.play_sample_game(rules, deployment, report=report, stop=stop)
        except BaseException as error:  # noqa: BLE001 - handed to the caller
            arrived.put(error)
        finally:
            arrived.put(None)

    end_override(state)
    with _playing_lock:
        _playing[conversation_key(state)] = (state, stop)
    worker = threading.Thread(target=play, name="playtest", daemon=True)
    worker.start()
    try:
        while True:
            record = arrived.get()
            if record is None:
                break
            if isinstance(record, BaseException):
                raise record
            records.append(record)
            ui.store_sample(state, records)
            yield ui.update_sample(state)
        if state.sample is None:
            # The swarm played nothing at all - a ticked <ShowSample> then has to
            # show an empty game rather than nothing at all, or the box looks
            # like it is lying.
            ui.store_sample(state, records)
            yield ui.update_sample(state)
    finally:
        end_override(state)
        worker.join(timeout=0.1)


def turn(
    state: ui.UIState,
    text: str,
    files: Sequence[tuple[str, str]] = (),
    api: ModuleType | None = None,
) -> Iterator[list[Patch]]:
    """Run one submitted prompt, yielding patch batches as the swarm works."""

    if api is None:
        api = resolve_api()

    if state.awaiting is None:
        raise ui.UIError("nothing is waiting for a prompt")

    role = state.entry(state.awaiting).props["role"]
    if role == "question":
        # The answer loops back into interpreting.
        yield [ui.answer_question(state, text)]
    else:
        # "What's next?" starts another run with the definition appended to.
        # What is appended is what the editor carried, dropped files and all,
        # because that is what ``submit`` already put together.
        previous = state.prompt
        added = ui.combine_prompt(text, files)
        yield ui.start_run(state, text, files)
        state.prompt = f"{previous}\n\n{added}".strip() if previous else added

    # -- interpreting, looping on <StatusQuestion> until the rules hold up ----
    while True:
        yield ui.show_status(state, ui.STATUS_INTERPRETING)
        try:
            reading = api.interpret_rules(
                state.prompt, tuple(state.answers), conversation_key(state)
            )
        except api_errors.BackendUnavailable as unavailable:
            yield from _failed(state, str(unavailable))
            return
        if reading.needs_clarification:
            # The interpreting step "failed" because it needs clarification.
            yield [ui.show_result(state, ui.STATUS_INTERPRETING, ui.RESULT_FAILURE)]
            yield ui.show_question(state, reading.question)
            yield [focus_patch(state)]
            return
        yield _named(state, reading.game_name)
        break

    rules = reading.rules

    # -- coding ---------------------------------------------------------------
    yield ui.show_status(state, ui.STATUS_CODING)
    try:
        code = api.generate_code(rules)
    except api_errors.BackendUnavailable as unavailable:
        yield from _failed(state, str(unavailable))
        return
    # The API's confirmation of the coding step carries the code contents.
    ui.store_code(state, code)
    yield [ui.ping(state, "coding_complete")]

    # -- deploying ------------------------------------------------------------
    yield ui.show_status(state, ui.STATUS_DEPLOYING)
    if state.show_code:
        yield ui.show_code(state)
    try:
        deployment = api.deploy_instances(rules, code)
    except api_errors.BackendUnavailable as unavailable:
        yield from _failed(state, str(unavailable))
        return
    yield [ui.ping(state, "deploy_complete")]

    # -- playing --------------------------------------------------------------
    yield ui.show_status(state, ui.STATUS_PLAYING)
    try:
        yield from _playtest(state, rules, deployment, api)
    except api_errors.BackendUnavailable as unavailable:
        yield from _failed(state, str(unavailable))
        return
    yield [ui.ping(state, "play_complete")]

    # -- collecting -----------------------------------------------------------
    yield ui.show_status(state, ui.STATUS_COLLECTING)
    try:
        analyses = api.collect_analyses(rules, state.sample or [])
    except api_errors.BackendUnavailable as unavailable:
        yield from _failed(state, str(unavailable))
        return
    yield [ui.ping(state, "analysis_complete")]

    # -- the design agent's answer -------------------------------------------
    try:
        feedback = api.design_feedback(state.prompt, rules, analyses)
    except api_errors.BackendUnavailable as unavailable:
        yield from _failed(state, str(unavailable))
        return
    yield ui.show_response(state, feedback)
    yield [ui.ping(state, "response_ready"), focus_patch(state)]


def _named(state: ui.UIState, name: str) -> list[Patch]:
    """The collapsed title settles on the name the Rules agent settled on."""

    if not name or state.title is None:
        return []
    word = state.title.first(ui.COMPONENT_MULTIWORD)
    if word is None or word.props.get("pinned") == name:
        return []
    word.props["pinned"] = name
    return [ui.update_patch(state.title)]


def _failed(state: ui.UIState, message: str) -> Iterator[list[Patch]]:
    yield ui.show_failure(state, message)
    yield [focus_patch(state)]


def run_events(
    state: ui.UIState,
    text: str,
    files: Sequence[tuple[str, str]] = (),
    api: ModuleType | None = None,
) -> list[dict[str, Any]]:
    """Flattened patch list for one turn, for callers that do not stream."""

    batches: list[dict[str, Any]] = []
    for batch in turn(state, text, files, api):
        batches.extend(batch)
    return batches
