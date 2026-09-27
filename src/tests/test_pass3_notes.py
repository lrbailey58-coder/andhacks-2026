"""What "pass 3 notes.md" asked for.

Each test names the note it answers.

The client is exercised for real, not grepped.  The notes are about logic - a
status that turned up twice at the bottom of the feed, a block that was never
written out, a game that only appeared once it was over - and none of that is
visible in the script's source, so a source assertion can only ever say that a
line still looks right.  ``tests/js/client.mjs`` runs the actual ``app.js``
against a small DOM, fed the patches that :mod:`web.ui` and :mod:`web.pipeline`
really produce for these flows, and stops the stream half way so the page can be
looked at mid run.  Without node on the machine those two checks are skipped;
everything below runs either way.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
import types

import pytest

import backend.api as backend_api
import backend.swarm as swarm
import web.app as app
import web.dummy_api as dummy_api
import web.pipeline as pipeline
import web.render as render
import web.ui as ui
from src.tests.conftest import COMPLETE_PROMPT, VAGUE_PROMPT, frames_of

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
JS_DIR = os.path.join(TESTS_DIR, "js")
CLIENT = os.path.join(JS_DIR, "client.mjs")
WEB_DIR = os.path.join(os.path.dirname(TESTS_DIR), "web")
SCRIPT = os.path.join(WEB_DIR, "static", "js", "app.js")
TEMPLATE = os.path.join(WEB_DIR, "templates", "feed.html")

NODE = shutil.which("node")

ANSWER = "Two players, on a 3x3 grid, X first, three in a row wins."


def script() -> str:
    with open(SCRIPT, encoding="utf-8") as handle:
        return handle.read()


def template() -> str:
    with open(TEMPLATE, encoding="utf-8") as handle:
        return handle.read()


# ---------------------------------------------------------------------------
# Note 1: the failed status came back, and stayed at the bottom of the feed
# ---------------------------------------------------------------------------


def test_a_status_is_not_reused_when_the_run_tries_again(client, session):
    """The model half: two attempts, two statuses, one failure each.

    The failure is where the question is asked - the interpreting step "failed"
    because it needs clarification - and the second attempt gets a status of its
    own with a result of its own.  Nothing is reused, and nothing is left
    running once the question has been answered.
    """

    first = client.post("/api/turn", json={"text": VAGUE_PROMPT})
    assert first.status_code == 200
    session.feed.apply_all(frames_of(first))
    second = client.post("/api/turn", json={"text": ANSWER})
    assert second.status_code == 200
    session.feed.apply_all(frames_of(second))

    feed = session.feed
    assert feed.count("StatusInterpreting") == 2, feed.components
    first, second = feed.all("StatusInterpreting")
    # The first attempt "failed" because it needed clarification, and that failure
    # is the one the question is asked on.  The answer gets a status of its own,
    # which succeeds.
    assert first["html"].count('data-result="failure"') == 1
    assert second["html"].count('data-result="success"') == 1
    for status in (first, second):
        assert 'data-state="running"' not in status["html"]
    # The question sits between the two attempts, and only the first one failed.
    order = feed.components
    attempts = [i for i, component in enumerate(order) if component == "StatusInterpreting"]
    assert len(attempts) == 2
    question_at = order.index("StatusQuestion")
    assert attempts[0] < question_at < attempts[1], order


def test_a_returned_status_lands_in_the_feed_not_at_the_bottom(client, session):
    """The client half, executed against the real app.js.

    The question nests its own editor inside its status, so every patch after it
    updates an entry that is not a child of the feed.  While the client kept its
    own list of entries beside the document, that list went out of step with the
    document: a status that had never arrived was re-created at the bottom of the
    feed, and the failure beside it stayed there for the rest of the run.
    """

    if NODE is None:
        pytest.skip("node is not on the path")

    first = client.post("/api/turn", json={"text": VAGUE_PROMPT})
    assert first.status_code == 200
    vague = frames_of(first)
    assert session.state.awaiting, "the vague prompt should have asked a question"
    second = client.post("/api/turn", json={"text": ANSWER})
    assert second.status_code == 200
    answer = frames_of(second)

    run_client(
        page_html=session.html,
        scenarios={
            "prompts": {
                "vague": VAGUE_PROMPT,
                "answer": ANSWER,
                "complete": COMPLETE_PROMPT,
            },
            "vague": vague,
            "answer": answer,
            "sample": sample_scenario(),
        },
    )


def wire(batch) -> list[dict]:
    """The patches as they go over the wire, markup and all."""

    return [app.wire_patch(patch) for patch in batch]


def sample_scenario() -> dict:
    """A run where the reader asks for the sample game while the swarm is playing.

    Split into the two streams the browser really gets: the run's own, and the
    toggle posted into it.  Both are needed, because the block appearing during
    the playtest is the claim being tested.
    """

    state = ui.initial_state("test")
    entered = threading.Event()
    released = threading.Event()

    def play_sample_game(rules, deployment, report=None, stop=None):
        entered.set()
        released.wait(10)
        return dummy_api.play_sample_game(rules, deployment, report=report, stop=stop)

    run_frames: list[dict] = []
    toggle_frames: list[dict] = []

    def run() -> None:
        for batch in pipeline.turn(state, COMPLETE_PROMPT, (), fake_api(play_sample_game)):
            run_frames.extend(wire(batch))

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(10), "the playtest never started"
    # The click on the box beside <StatusPlaying>, mid run.  Its context is the
    # run's, not the answer's, which is what the box's own scope says.
    toggle_frames.extend(wire(ui.set_show_sample(state, True, ui.TOGGLE_RUN)))
    released.set()
    worker.join(20)
    assert not worker.is_alive()
    return {"turn": run_frames, "toggle": toggle_frames}


# ---------------------------------------------------------------------------
# Note 2: the ellipses belong to the right of the checkbox
# ---------------------------------------------------------------------------


def test_the_playing_row_is_label_box_override_ellipses(state):
    ui.submit(state, COMPLETE_PROMPT)
    ui.show_status(state, ui.STATUS_PLAYING)
    status = state.status(ui.STATUS_PLAYING)
    assert [child.component for child in status.children] == [
        ui.COMPONENT_SHOW_SAMPLE,
        ui.COMPONENT_SIM_OVERRIDE,
        ui.COMPONENT_ELLIPSES,
    ]


def test_the_ellipses_come_after_the_box_in_the_markup(state):
    ui.submit(state, COMPLETE_PROMPT)
    ui.show_status(state, ui.STATUS_PLAYING)
    html = render.entry_html(state.status(ui.STATUS_PLAYING))
    box = html.index('data-toggle="sample"')
    override = html.index('data-override="stop"')
    dots = html.index('data-ellipses="1"')
    assert box < override < dots, (
        "the row is label, Show sample game, the override, then the ellipses"
    )


# ---------------------------------------------------------------------------
# Note 3: the simulation is bounded in time, not only in turns
# ---------------------------------------------------------------------------


def test_the_backend_has_a_wall_clock_limit_for_the_simulation():
    assert isinstance(swarm.MAX_PLAY_SECONDS, float)
    assert 0 < swarm.MAX_PLAY_SECONDS <= 3600, swarm.MAX_PLAY_SECONDS


class _EndlessEngine:
    """An engine nobody can win against, so only the clock can end the game."""

    @staticmethod
    def initial_game_state(player_count):
        return {"moves": []}

    @staticmethod
    def list_valid_moves(state, player_id):
        return [1, 2, 3]

    @staticmethod
    def execution_function(state, move, player_id):
        return {"moves": state["moves"] + [move]}

    @staticmethod
    def translation_function(state, private, player_id):
        return state

    @staticmethod
    def eval_function(state):
        return None


class _SlowPlayer:
    player_id = 0

    def __init__(self, clock, seconds_per_action):
        self.clock = clock
        self.seconds = seconds_per_action

    def act(self, engine, rules, turn):
        # Every action costs real time, so the limit lands mid run rather than on
        # the last turn by accident.
        self.clock[0] += self.seconds
        return swarm.Decision(move=1, explanation="thinking")


def _player(player_id, clock=None, seconds=0.0, stop=None):
    class _Player:
        def act(self, engine, rules, turn):
            if clock is not None:
                clock[0] += seconds
            if stop is not None:
                # The override is pressed by the reader while the first player of
                # the round is thinking.
                stop.set()
            return swarm.Decision(move=1, explanation="thinking")

    _Player.player_id = player_id
    return _Player()


def test_a_simulation_that_outlasts_the_limit_stops(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(swarm.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(swarm, "MAX_PLAY_SECONDS", 25.0)

    reported: list[dict] = []
    artifacts, winner = swarm.play_game(
        _EndlessEngine(),
        "rules",
        [_SlowPlayer(clock, 10.0)],
        report=reported.append,
    )

    assert winner is None
    assert artifacts[-1] == {"player": None, "type": "stop", "reason": "timeout"}
    played = [row for row in artifacts if row["type"] == "turn"]
    assert len(played) == 3, played
    # Every action played before the clock ran out is in the log and was reported
    # as it happened: the log and the game state cannot disagree.
    assert len(reported) == 3, reported


def test_the_clock_is_kept_between_players_and_not_only_between_rounds(monkeypatch):
    """Four players, a limit that falls inside the first round.

    A limit only checked between rounds would let three more actions happen after
    the deadline, which is the overrun it exists to stop.
    """

    clock = [0.0]
    monkeypatch.setattr(swarm.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(swarm, "MAX_PLAY_SECONDS", 25.0)

    artifacts, winner = swarm.play_game(
        _EndlessEngine(),
        "rules",
        [_player(index, clock, 10.0) for index in range(4)],
    )

    assert winner is None
    played = [row for row in artifacts if row["type"] == "turn"]
    assert [row["player"] for row in played] == [0, 1, 2], played
    assert clock[0] == 30.0
    assert artifacts[-1] == {"player": None, "type": "stop", "reason": "timeout"}


def test_the_override_lands_between_players_too():
    """The reader presses it during the first player of a round.

    The turn in flight is still finished and recorded - the log and the game
    state must not be able to disagree - and the second player does not act.
    """

    stop = threading.Event()
    artifacts, winner = swarm.play_game(
        _EndlessEngine(),
        "rules",
        [_player(0, stop=stop), _player(1), _player(2)],
        stop=stop,
    )

    assert winner is None
    played = [row for row in artifacts if row["type"] == "turn"]
    assert [row["player"] for row in played] == [0], played
    assert artifacts[-1] == {"player": None, "type": "stop", "reason": "override"}


def test_a_game_cut_short_by_the_clock_is_not_called_a_draw():
    rows = backend_api.transcript(
        [
            {"player": 0, "type": "turn", "move": 1, "explanation": ""},
            {"player": None, "type": "stop", "reason": "timeout"},
        ],
        None,
        2,
    )
    outcome = rows[-1]["outcome"]
    assert "draw" not in outcome
    assert "never finished" in outcome
    assert f"{swarm.MAX_PLAY_SECONDS:.0f}s" in outcome


def test_a_game_the_user_stopped_says_so():
    rows = backend_api.transcript(
        [
            {"player": 0, "type": "turn", "move": 1, "explanation": ""},
            {"player": None, "type": "stop", "reason": "override"},
        ],
        None,
        2,
    )
    assert rows[-1]["outcome"] == backend_api.OVERRIDE_OUTCOME
    assert "draw" not in rows[-1]["outcome"]


# ---------------------------------------------------------------------------
# Note 4: a manual override, to the left of the ellipses
# ---------------------------------------------------------------------------


def test_the_override_is_a_button_beside_the_playing_status(state):
    ui.submit(state, COMPLETE_PROMPT)
    ui.show_status(state, ui.STATUS_PLAYING)
    button = state.status(ui.STATUS_PLAYING).first(ui.COMPONENT_SIM_OVERRIDE)
    assert button is not None
    assert button.props["label"] == ui.OVERRIDE_LABEL
    assert button.props["context"] == "playing"
    html = render.entry_html(button)
    assert 'data-override="stop"' in html
    assert 'data-context="playing"' in html
    assert "disabled" not in html


def test_the_override_goes_away_when_the_simulation_is_over(state):
    ui.submit(state, COMPLETE_PROMPT)
    ui.show_status(state, ui.STATUS_PLAYING)
    ui.show_status(state, ui.STATUS_COLLECTING)  # settles the playing status
    status = state.status(ui.STATUS_PLAYING)
    assert status.first(ui.COMPONENT_SIM_OVERRIDE) is None, (
        "there is nothing left to override once the swarm has stopped"
    )
    assert status.first(ui.COMPONENT_ELLIPSES) is None
    assert status.first(ui.COMPONENT_RESULT).props["value"] == ui.RESULT_SUCCESS


def test_the_override_ends_a_run_and_the_game_says_it_was_cut_short(state):
    """End to end through the pipeline, with the swarm playing a game that the
    user stops after one turn.
    """

    entered = threading.Event()
    released = threading.Event()

    def play_sample_game(rules, deployment, report=None, stop=None):
        entered.set()
        released.wait(10)
        assert stop is not None and stop.is_set(), "the override never reached the playtest"
        records = [dict(dummy_api.SAMPLE_GAMES["tictactoe"][0])]
        records[-1]["outcome"] = backend_api.OVERRIDE_OUTCOME
        if report is not None:
            for record in records:
                report(record)
        return records

    api = fake_api(play_sample_game)
    ui.set_show_sample(state, True)
    collected: list[dict] = []

    def run() -> None:
        for batch in pipeline.turn(state, COMPLETE_PROMPT, (), api):
            collected.extend(batch)

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(10), "the playtest never started"
    assert pipeline.begin_override(state), "the override could not find the playtest"
    released.set()
    worker.join(20)
    assert not worker.is_alive()

    sample = state.find("sample-1")
    assert sample is not None, "the sample game is in the feed"
    assert backend_api.OVERRIDE_OUTCOME in sample.props["text"]
    playing = state.status(ui.STATUS_PLAYING)
    assert playing.props["running"] is False
    assert playing.first(ui.COMPONENT_RESULT).props["value"] == ui.RESULT_SUCCESS
    assert playing.first(ui.COMPONENT_SIM_OVERRIDE) is None
    # The rest of the run carried on: an early end is not a failed one.
    assert state.response, "the design agent still answered"


def test_the_override_route_finds_the_run_that_is_playing(client, session):
    """The route reaches the playtest in flight, and refuses when there is none."""

    entered = threading.Event()
    released = threading.Event()

    def play_sample_game(rules, deployment, report=None, stop=None):
        entered.set()
        released.wait(10)
        return []

    api = fake_api(play_sample_game)
    state = session.state

    def run() -> None:
        for _ in pipeline.turn(state, COMPLETE_PROMPT, (), api):
            pass

    worker = threading.Thread(target=run)
    worker.start()
    assert entered.wait(10), "the playtest never started"

    accepted = client.post("/api/override", json={})
    assert accepted.status_code == 200
    assert accepted.get_data(as_text=True).strip() == ""

    released.set()
    worker.join(20)
    assert not worker.is_alive()

    # The run has finished, so there is nothing left to stop.
    refused = client.post("/api/override", json={})
    assert refused.status_code == 409
    assert "no simulation" in refused.get_json()["error"]


def test_the_dummy_stops_between_turns_and_marks_the_game(monkeypatch):
    """The dummy obeys the same contract: a turn in flight is finished, the game
    says it was cut short, and the turns that were played are handed back.
    """

    monkeypatch.setattr(dummy_api, "BASE_LATENCY", 0.0)
    records = dummy_api.SAMPLE_GAMES["tictactoe"]
    deployment = types.SimpleNamespace(instances=2)
    reported: list[dict] = []

    stop = threading.Event()
    stop.set()  # as if the reader had already pressed the override

    played = dummy_api.play_sample_game(
        "rules", deployment, report=reported.append, stop=stop
    )
    assert played == [], played
    assert reported == []

    # Let one turn through, then stop: the game that comes back says it was cut
    # short rather than claiming somebody won it.
    class _StopAfterOne:
        """Asks whether it should stop, and starts saying yes on the second ask."""

        def __init__(self) -> None:
            self.inner = threading.Event()
            self.asked = 0

        def is_set(self) -> bool:
            self.asked += 1
            if self.asked == 1:
                return False
            self.inner.set()
            return True

    stop_after_one = _StopAfterOne()
    reported = []
    played = dummy_api.play_sample_game(
        "rules", deployment, report=reported.append, stop=stop_after_one
    )
    assert len(played) == 1, played
    assert played[-1]["outcome"] == dummy_api.OVERRIDE_OUTCOME, played
    assert len(reported) == 1, "the turn that was played was reported as it was played"
    assert stop_after_one.inner.is_set()
    # The records after it were never played, so they cannot be in the log.
    assert len(played) < len(records)


def test_the_client_says_the_swarm_is_stopping_rather_than_done():
    """The click cannot claim the run is over: the turn in flight still has to
    come back, and the status is only settled then.
    """

    source = script()
    assert "override.disabled = true;" in source
    assert 'override.textContent = "Stopping the swarm…"' in source
    assert 'override.classList.add("is-stopping")' in source
    # ...and it does not touch the page's busy state, which the run still holds.
    assert 'stream("/api/override", { run: parseInt(override.getAttribute("data-run"), 10) }, true)' in source


# ---------------------------------------------------------------------------
# Note 5: <ShowSample> shows the game while it is being played
# ---------------------------------------------------------------------------


def test_the_sample_block_is_put_in_with_the_first_turn(state):
    """Not after: the block goes in on turn one and is replaced as the rest
    arrive, while ``<StatusPlaying>`` is still running.
    """

    ui.set_show_sample(state, True)
    ui.show_status(state, ui.STATUS_PLAYING)
    patches = []
    played: list[dict] = []
    for record in dummy_api.SAMPLE_GAMES["tictactoe"][:2]:
        played.append(record)
        ui.store_sample(state, played)
        patches.extend(ui.update_sample(state))

    assert patches[0]["op"] == ui.OP_INSERT, "the first turn inserts the block"
    assert all(patch["op"] == ui.OP_UPDATE for patch in patches[1:]), (
        "the turns after that replace it rather than adding another block"
    )
    block = state.find("sample-0")
    assert block is not None
    assert block.props["live"] is True, "the block knows it is still being played"
    assert state.status(ui.STATUS_PLAYING).props["running"] is True

    # And when the swarm stops, the block says so rather than looking finished.
    ui.show_status(state, ui.STATUS_COLLECTING)
    assert ui._sample_block(state, "sample-0").props["live"] is False


def test_an_unticked_box_still_collects_the_game_without_showing_it(state):
    """The Design agent wants the whole playtest, ticked box or not."""

    for record in dummy_api.SAMPLE_GAMES["tictactoe"][:2]:
        ui.store_sample(state, state.sample or [] and state.sample or [])
        ui.store_sample(state, (state.sample or []) + [record])
    assert len(state.sample) == 2
    assert ui.update_sample(state) == [], "an unticked box shows nothing"
    assert state.find("sample-0") is None


def test_a_ticked_box_with_an_empty_game_still_shows_a_block(state):
    """An empty game is an empty block, not a ticked box that lies."""

    ui.set_show_sample(state, True)
    ui.store_sample(state, [])
    patches = ui.update_sample(state)
    assert patches, "a ticked box with nothing played still puts a block in the feed"
    block = state.find("sample-0")
    assert block is not None
    assert block.props["text"] == ""
    assert block.props["rows"] == 0
    html = render.entry_html(block)
    assert "nothing yet" in html, html


def test_the_sample_reads_as_moves_rather_than_as_python(state):
    """A generated engine hands back dictionaries, and a reader wants a move,
    not ``{'square': 4, 'kind': 'place'}``.
    """

    ui.store_sample(
        state,
        [
            {
                "turn": 1,
                "player": 1,
                "move": {"square": 4, "kind": "place"},
                "explanation": "The centre wins fastest.",
            }
        ],
    )
    lines = ui.sample_transcript(state.sample)
    assert lines[0] == "Turn 1 · Player 1 — plays square 4, kind place"
    assert "{" not in lines[0] and "'" not in lines[0]
    assert lines[1] == "    The centre wins fastest."


def test_a_rejected_move_is_shown_as_one(state):
    ui.store_sample(
        state,
        [
            {
                "turn": 1,
                "player": 2,
                "move": {"square": 9},
                "illegal": True,
                "explanation": "That square is taken.",
            }
        ],
    )
    assert ui.sample_transcript(state.sample)[0] == "Turn 1 · Player 2 — rejected: square 9"


def test_a_move_with_nothing_in_it_still_reads_as_a_move():
    assert ui.sample_transcript(
        [{"turn": 1, "player": 1, "move": {}, "explanation": ""}]
    )[0] == "Turn 1 · Player 1 — plays (empty move)"


def test_a_game_in_progress_does_not_say_how_it_ended():
    """The block grows a turn at a time, and it cannot know how the game ends
    until it does: writing "Game over." after the first turn and taking it back
    on the next is a claim the page cannot keep.
    """

    played = [{"turn": 1, "player": 1, "move": 4, "explanation": "Centre first."}]
    assert "Game over" not in "\n".join(ui.sample_transcript(played, live=True))
    assert "Game over" in "\n".join(ui.sample_transcript(played))

    # Once the game is over, what it was says what it is, including a stop the
    # reader asked for.
    for outcome in ("Player 1 wins on turn 5.", dummy_api.OVERRIDE_OUTCOME):
        ended = ui.sample_transcript(played + [dict(played[0], outcome=outcome)])
        assert ended[-1] == outcome


def test_the_block_grows_by_appending_to_what_is_already_there():
    """Each turn adds to the text rather than rewriting it.

    This is what lets the browser carry on typing where it left off instead of
    starting the paragraph again, so the two have to agree about it.
    """

    first = [{"turn": 1, "player": 1, "move": 4, "explanation": "Centre first."}]
    second = first + [{"turn": 2, "player": 2, "move": 1, "explanation": "A corner."}]
    assert "\n".join(ui.sample_transcript(second, live=True)).startswith(
        "\n".join(ui.sample_transcript(first, live=True))
    )


# ---------------------------------------------------------------------------
# Note 6: the design review and the code block are not blank
# ---------------------------------------------------------------------------


def test_the_typing_guard_is_on_the_finished_state():
    """``data-typed="0"`` is the string ``"0"``, and a non-empty string is true.

    The guard used to be ``if (code.dataset.typed) return;``, so every long block
    stopped before it started.  The stylesheet hides ``[data-typed="0"]``, so a
    block whose typing never began was not late - it was blank.
    """

    source = script()
    assert 'if (!code || code.dataset.typed === "2") return;' in source
    assert "if (!code || code.dataset.typed) return;" not in source


def test_a_block_that_grows_keeps_what_it_has_already_written():
    """The sample game is re-rendered as each turn arrives, which replaces the
    element.  The writing has to pick up where it was, or every turn makes the
    reader watch the whole game again from the top.
    """

    source = script()
    assert 'code.getAttribute("data-block")' in source
    assert "written[key] = full.slice(0, done);" in source
    assert "code.textContent = full.slice(0, done);" in source
    # What is remembered is the text, not a count of it, so text that is rewritten
    # as well as grown still carries on from where it agrees with the page.
    assert "var seen = written[key]" in source
    assert "full[keep] === seen[keep]" in source
    # The plan is replayed from the start to find the step the text already
    # passed, so a longer plan lines up with a shorter one already on the page.
    assert "while (done < want && at < plan.schedule.length)" in source


def test_a_block_that_is_interrupted_carries_on_rather_than_restarting():
    """Only one block is written at a time, so a long block is interrupted when
    another one starts - and the one that was interrupted is not finished, it is
    waiting.  It has to be carried on when the pen is free, from the text that is
    actually on the page: remembering it as complete skips the part the reader
    never saw typed.
    """

    source = script()
    assert "finishTyping(typing.node, false)" in source
    assert "written[key] = code.textContent || \"\";" in source
    assert 'code.dataset.typed = "1";' in source
    assert "resumeInterrupted();" in source
    # The block that was interrupted is looked up again rather than kept, because
    # the element is replaced by the next update.
    assert "feed.querySelector('[data-block=\"'" in source


def test_the_review_is_not_marked_written_until_it_is():
    """The head of a long block said how long it was, and the length was known
    before the text was - so the Design agent's answer looked finished, ticked and
    all, while it was still arriving.
    """

    markup = template()
    assert 'data-state="typing">writing' in markup
    assert 'data-state="done" role="img" aria-label="written"' in markup
    # The tick is picked by the body's own typing state, so it cannot be on
    # screen before the last character is.
    assert '.longblock__head:has(+ .longblock__body .typed[data-typed="2"])' in open(
        os.path.join(WEB_DIR, "static", "css", "style.css"), encoding="utf-8"
    ).read()


def test_a_long_block_reports_what_it_is_doing(state):
    ui.set_show_code(state, True)
    ui.store_code(state, "print('hello')\n")
    ui.show_code(state)
    block = state.find("code-0")
    assert block is not None
    html = render.entry_html(block)
    assert "longblock__writing" in html
    assert 'data-state="done"' in html


def test_the_code_block_arrives_with_something_to_type(state):
    ui.store_code(state, "def initial_game_state(n):\n    return {}\n")
    block = ui._code_block(state, "code-0")
    assert block.props["text"].startswith("def initial_game_state")
    assert block.props["typing"]["schedule"], "the block arrives with a typing plan"
    assert block.props["rows"] == 3, "the trailing newline is not a line of code"


# ---------------------------------------------------------------------------
# The two APIs stay interchangeable
# ---------------------------------------------------------------------------


def test_the_two_apis_take_the_same_play_signature():
    """The override and the per-turn report are keywords on the seam, and the
    front end runs against either implementation.
    """

    import inspect

    real = inspect.signature(backend_api.play_sample_game)
    fake = inspect.signature(dummy_api.play_sample_game)
    assert list(real.parameters) == list(fake.parameters), (real, fake)
    assert list(real.parameters) == ["rules", "deployment", "report", "stop"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def fake_api(play_sample_game):
    """The dummy, with one function swapped out."""

    return types.SimpleNamespace(
        interpret_rules=dummy_api.interpret_rules,
        generate_code=dummy_api.generate_code,
        deploy_instances=dummy_api.deploy_instances,
        play_sample_game=play_sample_game,
        collect_analyses=dummy_api.collect_analyses,
        design_feedback=dummy_api.design_feedback,
    )


def run_client(page_html: str, scenarios: dict) -> None:
    """Run ``app.js`` against the DOM with these patches, and fail on a diff."""

    payload = {"page": page_html, "scenarios": scenarios}
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    )
    try:
        json.dump(payload, handle)
        handle.close()
        result = subprocess.run(
            [NODE, CLIENT, handle.name], capture_output=True, text=True, timeout=180
        )
    finally:
        os.unlink(handle.name)
    assert result.returncode == 0, result.stdout + result.stderr
