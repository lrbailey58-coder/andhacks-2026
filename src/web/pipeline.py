"""The driver flask calls.

One turn of the conversation: whatever the user submitted is applied to the UI
first, then the agents are called one at a time and every event they produce is
turned into UI patches as it happens.  The generator is what the HTTP layer
streams, and what the tests walk through.

Swapping the dummy for the real swarm means changing the ``api`` argument; the
event order below is the one the Logic section describes.
"""

from __future__ import annotations

from typing import Any, Iterator, Sequence

import dummy_api
import ui

Patch = dict[str, Any]


def focus_patch(state: ui.UIState) -> Patch:
    """Tell the browser to put the caret in whichever editor is now live."""

    return {"op": "focus", "id": state.awaiting}


def turn(
    state: ui.UIState,
    text: str,
    files: Sequence[tuple[str, str]] = (),
    api=dummy_api,
) -> Iterator[list[Patch]]:
    """Run one submitted prompt, yielding patch batches as the swarm works."""

    if state.awaiting is None:
        raise ui.UIError("nothing is waiting for a prompt")

    role = state.entry(state.awaiting).props["role"]
    if role == "question":
        # The answer loops back into interpreting.
        yield [ui.answer_question(state, text)]
    else:
        # "What's next?" starts another run with the definition appended to.
        previous = state.prompt
        yield [ui.start_run(state, text, files)]
        state.prompt = f"{previous}\n\n{text}".strip() if previous else text

    # -- interpreting, looping on <StatusQuestion> until the rules hold up ----
    while True:
        yield ui.show_status(state, ui.STATUS_INTERPRETING)
        try:
            reading = api.interpret_rules(state.prompt, tuple(state.answers))
        except dummy_api.BackendUnavailable as unavailable:
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
    except dummy_api.BackendUnavailable as unavailable:
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
    except dummy_api.BackendUnavailable as unavailable:
        yield from _failed(state, str(unavailable))
        return
    yield [ui.ping(state, "deploy_complete")]

    # -- playing --------------------------------------------------------------
    yield ui.show_status(state, ui.STATUS_PLAYING)
    try:
        sample = api.play_sample_game(rules, deployment)
    except dummy_api.BackendUnavailable as unavailable:
        yield from _failed(state, str(unavailable))
        return
    ui.store_sample(state, sample)
    if state.show_sample:
        yield ui.show_sample(state)
    yield [ui.ping(state, "play_complete")]

    # -- collecting -----------------------------------------------------------
    yield ui.show_status(state, ui.STATUS_COLLECTING)
    try:
        analyses = api.collect_analyses(rules, state.sample or [])
    except dummy_api.BackendUnavailable as unavailable:
        yield from _failed(state, str(unavailable))
        return
    yield [ui.ping(state, "analysis_complete")]

    # -- the design agent's answer -------------------------------------------
    try:
        feedback = api.design_feedback(state.prompt, rules, analyses)
    except dummy_api.BackendUnavailable as unavailable:
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
    state: ui.UIState, text: str, files: Sequence[tuple[str, str]] = (), api=dummy_api
) -> list[dict[str, Any]]:
    """Flattened patch list for one turn, for callers that do not stream."""

    batches: list[dict[str, Any]] = []
    for batch in turn(state, text, files, api):
        batches.extend(batch)
    return batches
