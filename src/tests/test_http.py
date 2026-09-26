"""End to end against a real Flask client: real HTML, real stream, no doubles.

The requests here are the ones ``static/js/app.js`` makes: JSON bodies, the
session carried in the cookie the page hands out, and the streamed body consumed
the way ``fetch`` consumes it - the work happens as the frames are read.
"""

from __future__ import annotations

import json

import pytest

import web.app as server
import web.dummy_api as dummy_api
import web.ui as ui
from src.tests.conftest import COMPLETE_PROMPT, VAGUE_PROMPT


@pytest.fixture()
def client():
    server.reset_sessions()
    server.app.config.update(TESTING=True)
    with server.app.test_client() as test_client:
        yield test_client


@pytest.fixture()
def page(client):
    """A client that has loaded the page, so it holds a session."""

    client.get("/")
    return client


def frames_of(response) -> list[dict]:
    """Parse an SSE body into frames, the way the browser does."""

    response.get_data()  # the stream only runs as the body is read
    out = []
    for line in response.get_data(as_text=True).splitlines():
        if line.startswith("data: "):
            out.append(json.loads(line[len("data: ") :]))
    return out


def run(page, text: str, **extra) -> list[dict]:
    """Submit a prompt and read the whole stream, as ``fetch`` does."""

    response = page.post("/api/turn", json={"text": text, **extra})
    assert response.status_code == 200, response.get_data(as_text=True)
    return frames_of(response)


def inserted(response) -> str:
    return "\n".join(f["html"] for f in frames_of(response) if f["op"] == "insert")


def updated(response) -> str:
    return "\n".join(f["html"] for f in frames_of(response) if f["op"] == "update")


def test_the_page_comes_up_centred_with_a_title_and_an_editor(client):
    body = client.get("/").get_data(as_text=True)

    assert "Let's design" in body
    assert 'data-component="Multiword"' in body
    assert ui.PROMPT_PLACEHOLDER in body
    assert 'data-role="primary"' in body
    assert "Show me the code" in body
    # Centred to begin with, both ways.
    assert "title title--centered" in body
    assert "editor editor--primary" in body
    # Nothing is shown that has not been asked for.
    assert 'data-component="LongBlockCode"' not in body


def test_the_health_check_is_happy(client):
    assert client.get("/healthz").get_json()["status"] == "ok"


def test_a_full_run_streams_the_documented_sequence(page):
    response = page.post("/api/turn", json={"text": COMPLETE_PROMPT})

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"

    html = inserted(response)
    for label in ui.STATUS_LABELS.values():
        assert label in html
    for component in (
        ui.COMPONENT_LONG_BLOCK_RESPONSE,
        ui.COMPONENT_PROMPT_EDITOR,
        ui.COMPONENT_PING,
        ui.COMPONENT_FOLLOWUP,
    ):
        assert f'data-component="{component}"' in html
    # Neither was asked for, so neither is there.
    assert "longblock--code" not in html
    assert "longblock--sample" not in html
    # The final response is still a row, with room for two.
    assert 'data-component="Columns"' in html
    # It ends with a heading and an editor to answer.
    assert "What's next?" in html


def test_every_status_ends_in_a_checkmark(page):
    marks = [
        f["html"]
        for f in run(page, COMPLETE_PROMPT)
        if f["op"] == "update" and 'data-result="success"' in f["html"]
    ]
    assert len(marks) == len(ui.STATUS_LABELS)
    assert all(mark.count("✓") == 1 for mark in marks)


def test_the_title_settles_on_the_name_the_rules_agent_chose(page):
    titles = [f["html"] for f in run(page, COMPLETE_PROMPT) if f["id"] == "title-1"]
    assert titles, "the title is updated when the game is named"
    # It stops cycling and holds the name.
    assert "multiword--pinned" in titles[-1]
    assert 'data-multiword="0"' in titles[-1]
    assert ">Tic-Tac-Toe</span>" in titles[-1]


def test_asking_for_the_code_up_front_puts_it_under_deploying(page):
    frames = run(page, COMPLETE_PROMPT, show_code=True)
    by_id = {f["id"]: f for f in frames}

    code = next(f for f in frames if f.get("component") == ui.COMPONENT_LONG_BLOCK_CODE)
    deploying = next(
        f for f in frames if f["id"].startswith("status")
        and 'data-status="deploying"' in f["html"]
    )
    playing = next(
        f for f in frames if f["id"].startswith("status")
        and 'data-status="playing"' in f["html"]
    )
    # It arrived once deploying was under way, before the swarm started playing.
    assert deploying["index"] < code["index"] < playing["index"]
    assert "def initial_game_state" in code["html"]
    # And the final response got a second column to hold it.
    assert 'data-columns="2"' in by_id["final-1"]["html"]


def test_the_editor_collapses_and_the_title_moves_left(page):
    changes = "\n".join(
        f["html"] for f in run(page, COMPLETE_PROMPT) if f["op"] == "update"
    )
    assert "title--collapsed" in changes
    assert "is-submitted" in changes


def test_a_vague_prompt_asks_a_question_instead_of_guessing(page):
    frames = run(page, VAGUE_PROMPT)

    assert any(
        f["op"] == "insert" and f.get("component") == ui.COMPONENT_STATUS_QUESTION
        for f in frames
    )
    # The interpreting step is marked as a failure, not a success.
    assert any(
        f["op"] == "update" and 'data-result="failure"' in f["html"] for f in frames
    )
    # Nothing was written, because nothing had been decided yet.
    assert not any(f.get("component") == ui.COMPONENT_LONG_BLOCK_CODE for f in frames)


def test_the_answer_lets_the_run_carry_on(page):
    run(page, VAGUE_PROMPT)
    frames = run(page, "Two players, five rounds, first to three wins.")

    # The run carried on to the answer, reusing the editor already on the page.
    assert any(f.get("component") == ui.COMPONENT_LONG_BLOCK_RESPONSE for f in frames)
    assert not any(f.get("component") == ui.COMPONENT_STATUS_QUESTION for f in frames)


def test_asking_to_see_the_code_later_puts_it_under_deploying(page):
    run(page, COMPLETE_PROMPT)
    frames = frames_of(
        page.post("/api/toggle", json={"which": "code", "value": True})
    )

    assert [f["op"] for f in frames] == ["update", "insert"]
    assert frames[-1].get("component") == ui.COMPONENT_LONG_BLOCK_CODE
    # It was anchored after the deploying status, not at the end of the feed.
    deploying = next(
        f for f in frames
        if f["id"].startswith("status") and 'data-status="deploying"' in f["html"]
    )
    assert frames[-1]["index"] > deploying["index"]


def test_unchecking_removes_the_code_again(page):
    run(page, COMPLETE_PROMPT, show_code=True)
    frames = frames_of(
        page.post("/api/toggle", json={"which": "code", "value": False})
    )

    removals = [f["id"] for f in frames if f["op"] == "remove"]
    # The mid feed block, and the column beside the answer.
    assert removals == ["code-1", "final-code-1"]


def test_the_sample_toggle_works_the_same_way(page):
    run(page, COMPLETE_PROMPT)
    frames = frames_of(
        page.post("/api/toggle", json={"which": "sample", "value": True})
    )
    assert any('data-variant="sample"' in f.get("html", "") for f in frames)


def test_an_unknown_toggle_is_refused(page):
    response = page.post("/api/toggle", json={"which": "nope", "value": True})
    assert response.status_code == 400


def test_an_empty_prompt_is_refused_with_a_reason(page):
    response = page.post("/api/turn", json={"text": "   "})
    assert response.status_code == 400
    assert response.get_json()["error"] == "empty prompt"


def test_a_request_without_a_session_is_refused():
    server.reset_sessions()
    with server.app.test_client() as stranger:
        response = stranger.post("/api/turn", json={"text": COMPLETE_PROMPT})
    assert response.status_code == 400
    assert response.get_json()["error"] == "no session"


def test_a_dragged_file_counts_as_a_prompt(page):
    frames = run(page, "", files=[["design.md", COMPLETE_PROMPT]])
    # The file's contents reached the Rules agent like any other prompt.
    assert any("Tic-Tac-Toe" in f.get("html", "") for f in frames)


def test_a_follow_up_run_keeps_the_definition(page):
    run(page, COMPLETE_PROMPT)
    frames = run(page, "Now make the grid 4x4.")
    assert any(f.get("component") == ui.COMPONENT_LONG_BLOCK_RESPONSE for f in frames)


def test_a_second_prompt_while_one_is_running_is_refused(page):
    response = page.post("/api/turn", json={"text": COMPLETE_PROMPT})
    # Deliberately not read: the run is still in flight.
    assert response.status_code == 200
    assert page.post("/api/turn", json={"text": "and again"}).status_code == 409
