"""The two flows the UI has to survive, and the failure case.

"A test where the user specifies a game and asks for everything." and "A test
where the user specifies a game poorly and must answer a question to continue."
Both run through the HTTP surface, so the streamed patches, the rendering and
the component rules are all exercised.
"""

from __future__ import annotations

from src.tests.conftest import COMPLETE_PROMPT, UNSTABLE_PROMPT, VAGUE_PROMPT
import web.ui as ui

STATUSES = [
    "StatusInterpreting",
    "StatusCoding",
    "StatusDeploying",
    "StatusPlaying",
    "StatusCollecting",
]


def status_order(feed) -> list[str]:
    return [name for name in feed.components if name in STATUSES]


# ---------------------------------------------------------------------------
# Test one: a game, and everything asked for
# ---------------------------------------------------------------------------


def test_specified_game_with_everything_asked_for(page, submit, toggle):
    # The page opens with a <Title> and a <PromptEditor>, and nothing else.
    assert "Let's design" in page
    assert "an Experience" in page
    assert "drag and drop a file" in page
    assert "StatusInterpreting" not in page

    # The user ticks "show me the code" and "show sample game" before sending.
    toggle("code", True)
    toggle("sample", True)

    feed = submit(COMPLETE_PROMPT)

    # The title collapses to the left once the prompt is submitted, and settles
    # on the name the Rules agent settled on.
    title = feed.all("Title")[0]["html"]
    assert 'data-title-state="collapsed"' in title
    assert "Tic-Tac-Toe" in title

    # The submitted editor slides to a right alignment and gains copy and
    # download buttons.
    editor = feed.all("PromptEditor")[0]["html"]
    assert 'data-state="submitted"' in editor
    assert "editor--right" in editor
    assert 'data-action="copy"' in editor
    assert 'data-action="download"' in editor

    # The linear half of the flow, in order, each with a success <Result>.
    assert status_order(feed) == STATUSES
    assert feed.count("Result") == len(STATUSES)
    assert feed.count("Ellipses") == 0
    assert "✓" in feed.text
    assert "✕" not in feed.text
    assert 'data-result="success"' in feed.html

    # The code is shown next to the deploying step, the sample next to playing.
    assert feed.index("LongBlockCode") > feed.index("StatusDeploying")
    assert feed.index("LongBlockCode") < feed.index("StatusPlaying")
    assert feed.index("LongBlock") > feed.index("StatusPlaying")
    assert feed.index("LongBlock") < feed.index("StatusCollecting")

    # Both blocks cover the feed and type themselves out, and carry the buttons.
    code_html = feed.html_of("LongBlockCode")
    assert "def initial_game_state" in code_html
    assert "data-typing=" in code_html
    assert 'data-action="copy"' in code_html
    assert 'data-action="download"' in code_html
    assert "Turn 1" in feed.html_of("LongBlock")

    # The <ShowSample> sat beside <StatusPlaying> from the start.
    assert "Show sample game" in feed.all("StatusPlaying")[0]["html"]

    # The Design agent's answer, then "What's next?" and another editor.
    response = feed.html_of("LongBlockResponse")
    assert "Here is what the playtests say" in response
    assert response.index("Show me the code") > response.index("playtests say")
    assert "Show sample game" in response

    assert "What's next?" in feed.text
    assert feed.components[-3:] == ["Followup", "PromptEditor", "Ping"]

    # Every completed task pinged.
    assert feed.count("Ping") >= 4
    assert "hidden" in feed.html_of("Ping")


def test_both_toggles_at_the_response_give_adjacent_columns(submit, toggle):
    # Nothing checked yet: the code and the sample stay hidden, the run finishes.
    feed = submit(COMPLETE_PROMPT)
    assert feed.count("LongBlockCode") == 0
    assert feed.count("LongBlock") == 0
    # An empty column group is hidden, so it leaves no gap in the feed.
    assert "hidden" in feed.all("Columns")[0]["html"]

    toggle("code", True)
    feed = toggle("sample", True)

    columns = feed.all("Columns")[0]["html"]
    assert 'data-columns="2"' in columns
    assert "columns--2" in columns
    # One block of code beside one sample game, like a debugger's two panes.
    assert columns.count('data-component="LongBlockCode"') == 1
    assert columns.count('data-component="LongBlock"') == 1

    # The same two blocks also appear in the middle of the feed.
    assert feed.count("LongBlockCode") == 2
    assert feed.count("LongBlock") == 2


def test_show_code_can_be_asked_for_after_the_run(submit, toggle):
    feed = submit(COMPLETE_PROMPT)
    assert feed.count("LongBlockCode") == 0
    assert "initial_game_state" not in feed.html

    feed = toggle("code", True)
    assert feed.count("LongBlockCode") == 2
    assert "initial_game_state" in feed.html
    # It is anchored to the deploying step, which is where the code is fetched.
    assert feed.index("LongBlockCode") > feed.index("StatusDeploying")
    assert feed.index("LongBlockCode") < feed.index("StatusPlaying")


# ---------------------------------------------------------------------------
# Test two: a bad description, one question, then straight through
# ---------------------------------------------------------------------------


def test_poorly_specified_game_asks_a_question_and_waits(submit, toggle, session):
    toggle("code", True)

    feed = submit(VAGUE_PROMPT)

    # Interpreting failed, so its <Result> is a cross, and the Rules agent is
    # asking how the rules work.
    assert status_order(feed) == ["StatusInterpreting"]
    assert feed.count("Result") == 1
    assert 'data-result="failure"' in feed.html
    assert "✕" in feed.text
    assert "✓" not in feed.text

    question = feed.all("StatusQuestion")[0]["html"]
    assert "how many" in question.lower()
    # A question bends the feed, and carries its own <PromptEditor>.
    assert 'class="curve"' in question
    assert 'data-role="question"' in question
    assert 'data-state="editing"' in question

    # The flow is waiting for that answer and nothing else has appeared.
    state = session.state
    assert state.entry(state.awaiting).props["role"] == "question"
    assert state.results(ui.COMPONENT_STATUS_INTERPRETING) == ["failure"]
    assert not state.code
    assert feed.count("StatusCoding") == 0
    assert feed.count("LongBlockResponse") == 0

    # The answer loops back into interpreting, and the question is now resolved.
    feed = submit("Two players. On your turn, take one counter off the table.")

    # Interpreting appears twice: the pass that asked the question, then this one.
    assert status_order(feed) == [
        "StatusInterpreting",
        "StatusInterpreting",
        "StatusCoding",
        "StatusDeploying",
        "StatusPlaying",
        "StatusCollecting",
    ]
    # The question's own <Ellipses> became a success <Result>, and so did the
    # second pass at interpreting.
    question_html = feed.all("StatusQuestion")[0]["html"]
    assert 'data-result="success"' in question_html
    assert 'data-state="done"' in question_html
    assert state.results(ui.COMPONENT_STATUS_INTERPRETING) == ["failure", "success"]

    # And the run carries on to the finish.  <ShowCode> was checked before the
    # run and again at the response, so the code shows in both places.
    assert feed.count("LongBlockCode") == 2
    assert feed.count("LongBlockResponse") == 1
    assert "What's next?" in feed.text
    assert state.answers == ["Two players. On your turn, take one counter off the table."]


def test_the_question_loop_runs_the_whole_way_through(submit, session):
    feed = submit(VAGUE_PROMPT)
    assert feed.count("StatusQuestion") == 1
    assert feed.count("StatusCoding") == 0

    feed = submit("Two players, and a turn is taking one counter.")
    assert feed.count("StatusInterpreting") == 2
    assert feed.count("StatusQuestion") == 1
    assert feed.count("LongBlockResponse") == 1
    assert feed.count("Followup") == 1


# ---------------------------------------------------------------------------
# The failure case the spec says must be handled
# ---------------------------------------------------------------------------


def test_loss_of_service_leaves_a_failure_result_and_offers_a_retry(submit, session):
    feed = submit(UNSTABLE_PROMPT)

    assert status_order(feed) == ["StatusInterpreting", "StatusCoding", "StatusDeploying"]
    assert feed.count("Result") == 3
    # The step that lost service is a cross; the ones before it are checkmarks.
    assert 'data-result="failure"' in feed.html_of("StatusDeploying")
    assert "✕" in feed.html_of("StatusDeploying")
    assert "✓" in feed.html_of("StatusCoding")

    # Nothing downstream of the failure happened.
    assert feed.count("StatusPlaying") == 0
    assert feed.count("StatusCollecting") == 0
    assert feed.count("LongBlockResponse") == 0
    assert session.state.failures

    # The user is not stuck: a failed task pings, and they can try again.
    assert "What's next?" in feed.text
    state = session.state
    assert state.entry(state.awaiting).props["role"] == "next"

    # The retry runs from the top with the definition appended to.
    feed = submit("Two players, four in a row wins, and no time limit.")
    assert status_order(feed)[-5:] == STATUSES
    assert feed.count("LongBlockResponse") == 1
    assert "tic tac toe" not in session.state.prompt.lower()
    assert "no time limit" in session.state.prompt.lower()


def test_an_empty_prompt_is_refused(client, session):
    response = client.post("/api/turn", json={"text": "   "})
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Feedback loop
# ---------------------------------------------------------------------------


def test_whats_next_runs_again_and_keeps_the_first_response(submit, session):
    first = submit(COMPLETE_PROMPT)
    assert first.count("LongBlockResponse") == 1

    second = submit("Now add a third player who blocks squares instead of marking them.")

    # A second pass of the whole flow appears below the first answer.
    assert status_order(second)[-5:] == STATUSES
    assert second.count("LongBlockResponse") == 2
    assert second.count("Followup") == 2
    assert session.state.run == 2
    assert "Now add a third player" in session.state.prompt
    assert COMPLETE_PROMPT in session.state.prompt
