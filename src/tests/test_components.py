"""The component rules from the Components and Logic sections.

These drive the model in :mod:`ui` directly - no HTTP, no browser - because the
interesting behaviour is the state machine, not the transport.
"""

from __future__ import annotations

import pytest

import web.pipeline as pipeline
import web.render as render
import web.ui as ui


def drive(state: ui.UIState, text: str) -> ui.UIState:
    for _batch in pipeline.turn(state, text):
        pass
    return state


# ---------------------------------------------------------------------------
# <Title> and <Multiword>
# ---------------------------------------------------------------------------


def test_the_title_is_a_multiword_and_scrolls_between_words(state):
    title = state.entries[0]
    assert title.component == ui.COMPONENT_TITLE
    assert title.props["text"] == "Let's design"

    word = title.first(ui.COMPONENT_MULTIWORD)
    assert word is not None
    assert word.props["words"] == list(ui.MULTIWORD_WORDS)
    assert word.props["words"][word.props["index"]] == "an Experience"
    assert word.props["period_ms"] > 0

    html = render.entry_html(title)
    assert "data-multiword" in html
    assert "word-cycle" not in html  # the animation lives in the stylesheet
    assert "an Experience" in html


def test_the_title_collapses_to_the_left_once_a_prompt_is_submitted(state):
    assert state.title.props["state"] == "centered"
    ui.submit(state, "a game of dice")
    assert state.title.props["state"] == "collapsed"
    html = render.entry_html(state.title)
    assert 'data-title-state="collapsed"' in html
    assert "title--collapsed" in html


# ---------------------------------------------------------------------------
# <PromptEditor>
# ---------------------------------------------------------------------------


def test_enter_submits_but_a_modifier_turns_it_into_a_newline():
    assert ui.enter_keypress() == ui.SUBMIT
    for modifier in ("ctrlKey", "altKey", "shiftKey", "metaKey"):
        assert ui.enter_keypress({modifier: True}) == ui.NEWLINE
        assert ui.enter_keypress({modifier: False}) == ui.SUBMIT
    # Any modifier at all, not just the ones someone thought of.
    assert ui.enter_keypress({"ctrlKey": True, "shiftKey": True}) == ui.NEWLINE


def test_the_editor_carries_the_documented_default_text(state):
    editor = state.entries[1]
    assert editor.component == ui.COMPONENT_PROMPT_EDITOR
    assert editor.props["placeholder"] == (
        "Describe a board game, scenario or simulation, alternatively, "
        "drag and drop a file"
    )
    assert editor.props["show_code"] is True
    assert editor.props["drop_target"] is True
    html = render.entry_html(editor)
    assert 'data-toggle="code"' in html
    assert "Show me the code" in html


def test_a_submitted_editor_aligns_right_and_gains_copy_and_download(state):
    editor = state.entries[1]
    assert editor.props["align"] == "wide"
    assert "buttons" not in editor.props

    ui.submit(state, "a game of dice")
    assert editor.props["submitted"] is True
    assert editor.props["align"] == "right"
    assert editor.props["buttons"] == ["copy", "download"]

    html = render.entry_html(editor)
    assert "editor--right" in html
    assert "is-submitted" in html
    assert 'data-action="copy"' in html
    assert 'data-action="download"' in html
    # The words the user sent stay visible and selectable.
    assert "a game of dice" in html


def test_a_dropped_file_is_joined_to_the_prompt(state):
    combined = ui.combine_prompt(
        "my notes", [("sketch.txt", "players take turns")]
    )
    assert "my notes" in combined
    assert "--- FILE: sketch.txt ---" in combined
    assert "players take turns" in combined


def test_an_empty_prompt_is_not_submittable(state):
    with pytest.raises(ui.UIError):
        ui.submit(state, "   ")


# ---------------------------------------------------------------------------
# <Status>, <Ellipses>, <Result>
# ---------------------------------------------------------------------------


def test_a_running_status_ends_in_ellipses(state):
    ui.submit(state, "a game of dice")
    ui.show_status(state, ui.STATUS_CODING)
    status = state.status(ui.STATUS_CODING)
    assert status.props["label"] == "Writing game code"
    assert status.first(ui.COMPONENT_ELLIPSES) is not None
    assert status.first(ui.COMPONENT_RESULT) is None
    html = render.entry_html(status)
    assert 'data-ellipses="1"' in html
    # Three dots, each bouncing on its own.
    assert html.count('class="ellipses__dot"') == 3


def test_showing_a_status_replaces_earlier_ellipses_with_a_success(state):
    ui.submit(state, "a game of dice")
    ui.show_status(state, ui.STATUS_INTERPRETING)
    ui.show_status(state, ui.STATUS_CODING)
    assert state.status_result(ui.STATUS_INTERPRETING) == ui.RESULT_SUCCESS
    assert state.status(ui.STATUS_INTERPRETING).first(ui.COMPONENT_ELLIPSES) is None
    assert state.status(ui.STATUS_CODING).first(ui.COMPONENT_ELLIPSES) is not None

    html = render.entry_html(state.status(ui.STATUS_INTERPRETING))
    assert "✓" in html
    assert 'data-result="success"' in html
    assert "ellipses" not in html


def test_a_status_carries_its_arrow_and_its_glyph(state):
    """A status says where in the flow it is: an arrow, and a checkerboard for
    the one step that is a swarm playing rather than an agent working.

    The alignment of the row is not asserted on any more - that is CSS, and the
    CSS is settled.
    """

    ui.submit(state, "a game of dice")
    ui.show_status(state, ui.STATUS_PLAYING)
    status = state.status(ui.STATUS_PLAYING)
    assert status.props["arrow"]
    html = render.entry_html(status)
    assert "status__arrow" in html
    # A checkerboard, in the thin grey a status is written in.
    assert 'class="glyph glyph--checkerboard"' in html


def test_playing_brings_an_unchecked_show_sample_with_it(state):
    ui.submit(state, "a game of dice")
    ui.show_status(state, ui.STATUS_PLAYING)
    status = state.status(ui.STATUS_PLAYING)
    checkbox = status.first(ui.COMPONENT_SHOW_SAMPLE)
    assert checkbox is not None
    assert checkbox.props["checked"] is False
    assert checkbox.props["label"] == "Show sample game"
    assert "checked" not in render.entry_html(status).split("data-toggle=\"sample\"")[1][:20]


def test_a_question_marks_the_previous_status_as_a_failure(state):
    ui.submit(state, "a game of dice")
    ui.show_status(state, ui.STATUS_INTERPRETING)
    ui.show_result(state, ui.STATUS_INTERPRETING, ui.RESULT_FAILURE)
    ui.show_question(state, "How many players?")

    assert state.status_result(ui.STATUS_INTERPRETING) == ui.RESULT_FAILURE
    assert "✕" in render.entry_html(state.status(ui.STATUS_INTERPRETING))

    question = state.entries[-1]
    assert question.component == ui.COMPONENT_STATUS_QUESTION
    assert question.props["text"] == "How many players?"
    assert question.props["curve"] is True
    assert 'class="curve"' in render.entry_html(question)
    # Waiting on the user, so it still ends in bouncing dots.
    assert 'data-ellipses="1"' in render.entry_html(question)
    assert question.first(ui.COMPONENT_PROMPT_EDITOR) is not None


# ---------------------------------------------------------------------------
# <LongBlock>
# ---------------------------------------------------------------------------


def test_typing_accelerates_so_long_blocks_do_not_hang():
    plan = ui.typing_schedule(4000)
    assert sum(step[0] for step in plan) == 4000
    delays = [step[1] for step in plan]
    # Each step waits no longer than the one before it, up to the ceiling.  The
    # last step is the remainder, so it can be shorter.
    assert delays[:-1] == sorted(delays[:-1])
    assert delays[0] == 14
    assert max(delays) == 90
    # The first characters are deliberate, later ones come thick and fast.
    assert plan[0] == [1, 14]
    assert plan[-1][0] > plan[0][0]
    # A long block still finishes in seconds rather than minutes.
    seconds = sum(step[1] for step in plan) / 1000
    assert 1 < seconds < 12
    # Each character takes far less wall clock time than the one before it.
    per_character_start = plan[0][1] / plan[0][0]
    bulk = plan[-2]  # a full sized step, rather than the remainder
    assert bulk[1] / bulk[0] < per_character_start / 20


def test_a_short_block_is_not_rushed():
    plan = ui.typing_schedule(12)
    assert sum(step[0] for step in plan) == 12
    assert plan[0] == [1, 14]
    # A short block stays fine grained, and takes long enough to be read.
    assert max(step[0] for step in plan) <= 5
    assert 0.1 < sum(step[1] for step in plan) / 1000 < 1.0


def test_a_long_block_types_itself_and_offers_copy_and_download(state):
    ui.submit(state, "a game of dice")
    ui.store_code(state, "def initial_game_state():\n    return {}\n")
    ui.show_status(state, ui.STATUS_DEPLOYING)
    ui.set_show_code(state, True)

    block = state.find("code-0")
    assert block.component == ui.COMPONENT_LONG_BLOCK_CODE
    assert block.props["buttons"] == ["copy", "download"]
    assert block.props["download_name"] == ui.CODE_DOWNLOAD_NAME
    assert block.props["typing"]["total"] == len(block.props["text"])

    html = render.entry_html(block)
    assert "data-typing=" in html
    assert 'data-action="copy"' in html
    assert 'data-action="download"' in html
    # Readable and selectable: real text in a pre, not an image or a canvas.
    assert "<pre" in html
    assert "def initial_game_state" in html


def test_the_sample_game_transcript_shows_the_swarm_reasoning(state):
    ui.submit(state, "a game of dice")
    ui.store_sample(
        state,
        [
            {"turn": 1, "player": 1, "move": "centre", "explanation": "opens four lines"},
            {
                "turn": 2,
                "player": 2,
                "move": "corner",
                "explanation": "waste",
                "illegal": True,
            },
            {"turn": 2, "player": 2, "move": "corner", "explanation": "forced",
             "outcome": "Player 1 wins."},
        ],
    )
    ui.show_status(state, ui.STATUS_PLAYING)
    ui.set_show_sample(state, True)

    block = state.find("sample-0")
    text = block.props["text"]
    assert "Turn 1 · Player 1 — plays centre" in text
    assert "rejected" in text  # the misfire is part of the record
    assert "Player 1 wins." in text
    assert block.props["download_name"] == ui.SAMPLE_DOWNLOAD_NAME


def test_the_code_is_anchored_to_deploying_and_the_sample_to_playing(state):
    ui.submit(state, "a game of dice")
    ui.store_code(state, "code")
    ui.store_sample(state, [{"turn": 1, "player": 1, "move": "a", "explanation": "b"}])
    for kind in (ui.STATUS_INTERPRETING, ui.STATUS_CODING, ui.STATUS_DEPLOYING,
                 ui.STATUS_PLAYING, ui.STATUS_COLLECTING):
        ui.show_status(state, kind)
    ui.set_show_code(state, True)
    ui.set_show_sample(state, True)

    order = [entry.component for entry in state.entries]
    assert order.index(ui.COMPONENT_LONG_BLOCK_CODE) == (
        order.index(ui.COMPONENT_STATUS_DEPLOYING) + 1
    )
    assert order.index(ui.COMPONENT_LONG_BLOCK) == (
        order.index(ui.COMPONENT_STATUS_PLAYING) + 1
    )


# ---------------------------------------------------------------------------
# <ShowCode>, <ShowSample>, the columns
# ---------------------------------------------------------------------------


def test_the_code_only_shows_up_when_it_was_asked_for(state):
    ui.submit(state, "a game of dice")
    ui.store_code(state, "the code")
    ui.show_status(state, ui.STATUS_DEPLOYING)
    assert ui.show_code(state) == []  # nothing checked

    ui.set_show_code(state, True)
    assert state.find("code-0") is not None
    ui.set_show_code(state, False)
    assert state.find("code-0") is None


def test_both_toggles_at_the_response_give_two_columns(state):
    ui.submit(state, "a game of dice")
    ui.store_code(state, "the code")
    ui.store_sample(state, [{"turn": 1, "player": 1, "move": "a", "explanation": "b"}])
    for kind in (ui.STATUS_INTERPRETING, ui.STATUS_CODING, ui.STATUS_DEPLOYING,
                 ui.STATUS_PLAYING, ui.STATUS_COLLECTING):
        ui.show_status(state, kind)
    ui.show_response(state, "Here is what the playtests say.")

    group = state.find("final-0")
    assert group.component == ui.COMPONENT_COLUMNS
    assert group.children == []

    ui.set_show_code(state, True, ui.TOGGLE_FINAL)
    assert group.props["columns"] == 1
    ui.set_show_sample(state, True, ui.TOGGLE_FINAL)
    assert group.props["columns"] == 2
    assert [child.component for child in group.children] == [
        ui.COMPONENT_LONG_BLOCK_CODE,
        ui.COMPONENT_LONG_BLOCK,
    ]

    html = render.entry_html(group)
    assert 'data-columns="2"' in html
    assert "columns--2" in html


def test_a_checkbox_only_settles_the_scope_it_belongs_to(state):
    """One box ticked, one box ticked - the other kinds of question untouched."""

    ui.submit(state, "a game of dice")
    ui.show_status(state, ui.STATUS_PLAYING)
    ui.set_show_sample(state, True)

    playing = [
        node
        for entry in state.entries
        for node in entry.walk()
        if node.component == ui.COMPONENT_SHOW_SAMPLE
    ]
    assert len(playing) == 1
    assert playing[0].props["scope"] == ui.TOGGLE_RUN
    assert playing[0].props["checked"] is True
    assert "checked" in render.entry_html(playing[0])

    # The run's own boxes carry the run's request, and nothing moved them.
    editor = state.entries[1]
    assert editor.props["code_checked"] is False
    assert state.show_code is False


# ---------------------------------------------------------------------------
# The end of the flow
# ---------------------------------------------------------------------------


def test_the_run_finishes_with_the_answer_a_question_and_another_editor(state):
    drive(state, "a two player game of dice, highest total wins")
    response = state.find_all(ui.COMPONENT_LONG_BLOCK_RESPONSE)[0]
    assert response.props["buttons"] == ["copy", "download"]
    assert response.first(ui.COMPONENT_SHOW_CODE) is not None
    assert response.first(ui.COMPONENT_SHOW_SAMPLE) is not None
    # A change of direction is marked with a curve.
    assert 'class="curve"' in render.entry_html(response)

    followup = state.find_all(ui.COMPONENT_FOLLOWUP)[0]
    assert followup.props["heading"] == "What's next?"
    assert followup.props["align"] == "left"
    assert followup.first(ui.COMPONENT_PROMPT_EDITOR) is not None
    assert state.awaiting == followup.first(ui.COMPONENT_PROMPT_EDITOR).id


def test_a_failed_task_leaves_a_cross_and_lets_the_user_carry_on(state):
    drive(state, "a game of dice, but the agent swarm is flaky")
    assert state.status_result(ui.STATUS_DEPLOYING) == ui.RESULT_FAILURE
    assert state.failures
    assert state.finished
    # Recovery: the same "What's next?" editor, so a retry is one submit away.
    assert state.entry(state.awaiting).props["role"] == "next"
    assert any(entry.component == ui.COMPONENT_PING for entry in state.entries)


def test_nothing_is_awaiting_input_twice(state):
    ui.submit(state, "a game of dice")
    assert state.awaiting is None
    with pytest.raises(ui.UIError):
        ui.submit(state, "another one")
