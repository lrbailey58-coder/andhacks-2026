"""The four things "pass 2 notes.md" asked for.

Each test names the note it answers.  There is no browser in this suite, so the
behaviour that lives in ``static/js/app.js`` is guarded at the source, the same
way :mod:`test_spec_conformance` guards the typing playback, and everything that
lives in the model is driven through the real HTTP surface so the streamed
markup is what gets asserted.
"""

from __future__ import annotations

import os

import pytest

import web.render as render
import web.ui as ui
from src.tests.conftest import COMPLETE_PROMPT, StreamedFeed, frames_of

WEB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"
)
SCRIPT = os.path.join(WEB_DIR, "static", "js", "app.js")


@pytest.fixture(scope="module")
def script() -> str:
    with open(SCRIPT, encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture
def click(client, session):
    """One checkbox, clicked - the request app.js makes for a single box."""

    def go(which: str, value: bool, context: str) -> StreamedFeed:
        response = client.post(
            "/api/toggle", json={"which": which, "value": value, "context": context}
        )
        assert response.status_code == 200, response.get_data(as_text=True)
        return session.feed.apply_all(frames_of(response))

    return go


def boxes(state: ui.UIState) -> list[ui.Entry]:
    """Every <ShowCode> / <ShowSample> in the feed, nested ones included."""

    return [
        node
        for entry in state.entries
        for node in entry.walk()
        if node.component in (ui.COMPONENT_SHOW_CODE, ui.COMPONENT_SHOW_SAMPLE)
    ]


def answer_boxes(state: ui.UIState, run: int | None = None) -> list[ui.Entry]:
    """The two checkboxes at the bottom of one answer."""

    return [
        node
        for node in boxes(state)
        if node.props["scope"] == ui.TOGGLE_FINAL
        and (run is None or node.props["run"] == run)
    ]


# ---------------------------------------------------------------------------
# Note 1: a checkbox that is no longer about the newest thing stops being one
# ---------------------------------------------------------------------------


def test_a_fresh_page_has_nothing_to_retire(page):
    assert "disabled" not in page
    assert "checkbox--off" not in page


def test_the_boxes_beside_the_steps_retire_when_the_answer_arrives(submit, session):
    submit(COMPLETE_PROMPT)
    state = session.state

    editor = state.entries[1]
    # The request beside the editor has been handed to the run, so the box that
    # asked for it is greyed out like all the others.
    assert editor.props["code_disabled"] is True
    assert "disabled" in render.entry_html(editor)
    assert "checkbox--off" in render.entry_html(editor)

    playing = state.status(ui.STATUS_PLAYING).first(ui.COMPONENT_SHOW_SAMPLE)
    # The answer is the most recent object in the feed now, so the box beside the
    # swarm is history.
    assert playing.props["scope"] == ui.TOGGLE_RUN
    assert playing.props["disabled"] is True
    assert "disabled" in render.entry_html(state.status(ui.STATUS_PLAYING))

    # The answer's own two boxes are the live ones.
    answer = answer_boxes(state)
    assert len(answer) == 2
    assert all(node.props["disabled"] is False for node in answer)
    assert all(node.props["run"] == state.run for node in answer)


def test_a_retired_checkbox_is_drawn_greyed_and_labelled_as_such(submit, session):
    submit(COMPLETE_PROMPT)
    state = session.state
    playing = state.status(ui.STATUS_PLAYING)

    html = render.entry_html(playing)
    assert "checkbox--off" in html
    assert 'data-toggle="sample" data-context="run" disabled' in html


def test_the_next_run_takes_the_previous_answers_boxes_away(submit, click, session):
    submit(COMPLETE_PROMPT)
    click("code", True, ui.TOGGLE_FINAL)
    click("sample", True, ui.TOGGLE_FINAL)
    assert [child.id for child in session.state.find("final-1").children] == [
        "final-code-1",
        "final-sample-1",
    ]

    submit("Now make the grid 4x4.")
    state = session.state
    assert state.run == 2

    retired = [node for node in boxes(state) if node.props["run"] == 1]
    assert retired, "the first run's boxes are still in the feed"
    assert all(node.props["disabled"] is True for node in retired)

    # The new answer gets a fresh pair, ticked where its run left off.
    fresh = answer_boxes(state, 2)
    assert [node.props["checked"] for node in fresh] == [False, False]
    assert all(node.props["disabled"] is False for node in fresh)
    # ...and the boxes the first run left behind are still where they were, greyed.
    first_answer = render.entry_html(
        state.find_all(ui.COMPONENT_LONG_BLOCK_RESPONSE)[0]
    )
    assert first_answer.count("disabled") == 2


def test_a_runs_own_boxes_are_live_while_the_run_is_under_way(state):
    ui.submit(state, "a game of dice")
    ui.show_status(state, ui.STATUS_PLAYING)

    playing = state.status(ui.STATUS_PLAYING)
    assert playing.first(ui.COMPONENT_SHOW_SAMPLE).props["disabled"] is False
    assert "disabled" not in render.entry_html(playing)
    # The run is still the newest thing in the feed, so nothing is stale yet and
    # there is nothing to tell the browser.
    assert ui.refresh_toggles(state) == []


def test_the_browser_ignores_a_checkbox_the_page_has_retired(script):
    """A disabled box must not send anything, and must not be drawn again."""

    assert "if (toggle.disabled) return;" in script


# ---------------------------------------------------------------------------
# Note 2: one click, one window, one checkbox
# ---------------------------------------------------------------------------


def test_the_review_checkbox_opens_exactly_one_code_block(submit, click, session):
    """The reviewer's own example: tick Show Code under the design review.

    One code window, in the columns beside the answer, and nowhere else - not a
    second one back at the deploying step.
    """

    feed = submit(COMPLETE_PROMPT)
    assert feed.count("LongBlockCode") == 0

    feed = click("code", True, ui.TOGGLE_FINAL)

    assert feed.count("LongBlockCode") == 1
    columns = feed.all("Columns")[-1]["html"]
    assert 'data-component="LongBlockCode"' in columns
    assert 'data-columns="1"' in columns
    # It is the answer's column and nothing else, so it is read inside the group
    # rather than back beside the step that fetched it.
    assert feed.index("LongBlockCode") == feed.index("Columns") + 1
    assert feed.index("LongBlockCode") < feed.index("Followup")
    assert "LongBlockCode" not in feed.all("StatusDeploying")[0]["html"]


def test_the_review_checkbox_leaves_the_boxes_beside_the_steps_alone(
    submit, click, session
):
    feed = submit(COMPLETE_PROMPT)
    feed = click("code", True, ui.TOGGLE_FINAL)
    state = session.state

    # The run was never asked for the code, so its own boxes are untouched.
    assert state.show_code is False
    assert state.review_code is True
    assert state.entries[1].props["code_checked"] is False
    playing = state.status(ui.STATUS_PLAYING).first(ui.COMPONENT_SHOW_SAMPLE)
    assert playing.props["checked"] is False

    # Not one of them reads as ticked on the page: the editor's own copy, the one
    # beside the swarm, and the answer's other box.
    assert " checked" not in feed.all("PromptEditor")[0]["html"]
    assert " checked" not in feed.all("StatusPlaying")[0]["html"]
    # Exactly one of the answer's two boxes is the ticked one, in the model as
    # well as on the page: the browser drew the click, the model settles it.
    code, sample = answer_boxes(state)
    assert "checked" in render.entry_html(code)
    assert "checked" not in render.entry_html(sample)
    assert [node.props["checked"] for node in answer_boxes(state)] == [True, False]


def test_a_run_request_is_not_also_an_answer_request(client, session):
    """Ticking the box at the editor asks for the run, and only for the run.

    The code is shown under the step that wrote it.  The answer's own boxes are
    a different question about a different object, and they arrive unticked: a
    request made before the run must not open the answer's columns as well, or
    the same block is on the page twice.
    """

    # Ticked beside the editor before submitting, the way the page does it.
    response = client.post(
        "/api/turn", json={"text": COMPLETE_PROMPT, "show_code": True}
    )
    assert response.status_code == 200
    feed = session.feed.apply_all(frames_of(response))

    # "If <ShowCode> was checked earlier ... the code can now be retrieved from
    # the back end and displayed": it is under the deploying step.
    assert feed.index("LongBlockCode") > feed.index("StatusDeploying")
    assert feed.index("LongBlockCode") < feed.index("StatusPlaying")
    # Once, not twice.
    assert feed.count("LongBlockCode") == 1

    state = session.state
    assert state.show_code is True  # what the editor asked for
    assert state.review_code is False  # what the answer has not been asked
    assert [node.props["checked"] for node in answer_boxes(state)] == [False, False]
    # And the answer's row is an empty, hidden row rather than a second copy.
    assert state.find("final-1").children == []
    assert state.find("final-1").props["columns"] == 1


def test_unchecking_at_the_review_leaves_the_run_alone(submit, click, session):
    submit(COMPLETE_PROMPT)
    click("code", True, ui.TOGGLE_FINAL)
    feed = click("code", False, ui.TOGGLE_FINAL)

    assert feed.count("LongBlockCode") == 0
    assert session.state.review_code is False
    assert session.state.show_code is False


def test_the_browser_does_not_tick_every_checkbox_of_a_kind(script):
    """One setting, one box: nothing sweeps the document for its siblings."""

    # The old sweep asked the document for every input of the clicked kind, by
    # building a selector out of the kind, and then settled them all.  The editor
    # still reads its own boxes - inside its own form - and that is all.
    assert 'input[type="checkbox"][data-toggle="' not in script
    assert ".checked = toggle.checked" not in script
    # What goes over the wire is the one box, and where it came from.
    assert 'which: toggle.getAttribute("data-toggle")' in script
    assert 'context: toggle.getAttribute("data-context")' in script


def test_a_toggle_from_an_unknown_place_is_refused(client, session):
    # `session` is what loaded the page, so the client is carrying its cookie.
    assert session.state is not None
    refused = client.post(
        "/api/toggle", json={"which": "code", "value": True, "context": "somewhere"}
    )
    assert refused.status_code == 400
    assert refused.get_json()["error"] == "unknown toggle context"


# ---------------------------------------------------------------------------
# Note 3: showing a block does not break the vertical feed
# ---------------------------------------------------------------------------
#
# The two stylesheet tests that used to be here are gone.  They pinned the way
# the free space that centres the feed is expressed, and the way a hidden column
# group is hidden, and neither of those is a thing the rest of the suite needs
# to be true: the CSS is settled, so they could only fail when somebody restyled
# the page on purpose, and the noise they made hid the failures worth reading.
# The behaviour they were standing in for is still here - the feed does not grow
# a spare page's worth of blank when a long block arrives, because
# ``test_a_long_block_does_not_drag_the_page_with_it`` and the ``atBottom()``
# check underneath it are what make that true.


def test_the_browser_measures_the_document_not_the_page(script):
    """Following the feed along has to ask the thing that scrolls.

    The page element is as tall as its content, so measuring it would always say
    the reader is at the bottom, and every long block would yank the document
    down as it typed.
    """

    assert "function atBottom() {" in script
    assert "body.scrollHeight - body.scrollTop - body.clientHeight < 120" in script
    assert "page.scrollHeight" not in script


def test_a_re_rendered_entry_is_armed_again(script):
    """An update replaces markup, so what is inside it has to be set up again.

    Without this the code block that appears beside the answer was inserted with
    its text marked as not yet typed, and stayed invisible.
    """

    assert "afterInsert(fresh, false);" in script
    # ...and a re-render is not new content, so it does not pull the page down.
    assert "if (mayScroll !== false && atBottom()) scrollTo(node);" in script


def test_a_long_block_does_not_drag_the_page_with_it(script):
    """Typing scrolls the block, not the feed.

    The block is a window onto itself, so it follows its own text; the page only
    follows when the reader is already at the bottom of the feed.
    """

    assert "function scrollTyped(node)" in script
    assert "scrollTyped(code);" in script
    assert "if (atBottom()) scrollTo(node);" in script


# ---------------------------------------------------------------------------
# Note 4: more words
# ---------------------------------------------------------------------------


def test_the_multiword_has_a_lot_more_to_say():
    words = ui.MULTIWORD_WORDS

    # Enough of them that the title is worth looking at.
    assert len(words) >= 14
    assert len(set(words)) == len(words)
    # The page still opens on the one the rest of the suite expects.
    assert words[0] == "an Experience"
    # Every one reads as the tail of "Let's design ...".
    assert all(word.startswith(("an ", "a ")) for word in words)
    # And most of them are about games, which is what the tool is for.
    gaming = [
        word
        for word in words
        if any(
            topic in word.lower()
            for topic in (
                "game", "duel", "campaign", "skirmish", "wargame", "dungeon",
                "arcade", "tournament", "board", "card", "dice", "heist",
                "sandbox", "roguelike", "strategy",
            )
        )
    ]
    assert len(gaming) >= len(words) - 6
    # Short enough that the title does not wrap on itself: the word sits in an
    # inline-grid one line tall next to "Let's design".
    assert max(len(word) for word in words) <= 16
