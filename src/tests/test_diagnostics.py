"""Why a run did not start, said out loud.

"Some team members cannot access the API" is one sentence covering at least four
different faults: a key that was never found, a key the service refused, a quota
that ran out, and a browser that never reached the server at all.  Only the
second and third of those are visible from inside the page, and neither was
visible anywhere before: a refused run logged a 409 and a console that said
"stream failed", and a Gemini call that failed mid run turned into a cross in the
feed with the reason nowhere.

So two things are tested here.  :mod:`api_errors` keeps a bounded log of the model
calls - model, time, and the status the API itself gave - and
:func:`web.app.api_diagnostics` hands that log to the page along with which API
is switched on and where a key was looked for, without ever handing over a key.
The browser side of it, which prints these into the console, is exercised for
real in ``tests/js/client.mjs``.
"""

from __future__ import annotations

import json
import re
from unittest.mock import MagicMock

import pytest
from google.genai import errors as genai_errors

import api_errors
import backend.swarm as swarm
import web.app as app
from src.tests.conftest import frames_of

#: A key that is never expected to appear in anything the page is given.
SECRET = "not-a-real-key-9f2c"


@pytest.fixture(autouse=True)
def clean_log(monkeypatch):
    """No test inherits another's evidence."""

    api_errors.reset()
    monkeypatch.setattr(swarm, "_CLIENT", None)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    yield
    api_errors.reset()


@pytest.fixture
def gemini(monkeypatch) -> MagicMock:
    """The Gemini client, made of mocks, as in ``test_backend.py``."""

    double = MagicMock()
    monkeypatch.setattr(swarm, "get_client", lambda: double)
    return double


def refused(status: int, said: str) -> Exception:
    """What Gemini itself raises: a status, and the service's own words."""

    return genai_errors.ClientError(status, {"error": {"message": said}})


# ---------------------------------------------------------------------------
# The log of calls
# ---------------------------------------------------------------------------


def test_a_call_is_logged_with_its_model_and_how_long_it_took():
    entry = api_errors.record("gemini-3.1-pro-preview", True, 0.5)

    assert entry.model == "gemini-3.1-pro-preview"
    assert entry.ok is True
    assert entry.seconds == 0.5
    assert entry.status is None
    assert entry.when  # a time, not just a counter
    assert api_errors.calls() == (entry,)
    assert api_errors.failures() == ()


def test_a_refused_call_keeps_the_status_the_service_gave():
    entry = api_errors.record(
        "gemini-2.5-flash", False, 0.2, refused(429, "Quota exceeded for quota metric")
    )

    # The status is kept as a number of its own, because 403 (the key was
    # refused), 429 (the quota is spent) and 404 (the model name is wrong) are
    # three different fixes and one message would flatten them into one.
    assert entry.ok is False
    assert entry.status == 429
    assert entry.problem == "Quota exceeded for quota metric"
    assert api_errors.failures() == (entry,)


def test_the_service_words_are_preferred_over_the_exception_text():
    problem = refused(403, "API key not valid")
    assert str(problem)  # the client quotes the whole response body in this

    assert api_errors.message_of(problem) == "API key not valid"


def test_a_failure_with_no_status_of_its_own_says_so_rather_than_guessing():
    class Problem(Exception):
        status = "not a number"

    entry = api_errors.record("gemini-3.1-pro-preview", False, 0.1, Problem("timed out"))

    assert entry.status is None
    assert "timed out" in entry.problem


def test_a_very_long_failure_is_shortened():
    entry = api_errors.record("model", False, 0.1, RuntimeError("x" * 5000))

    assert len(entry.problem) <= api_errors.PROBLEM_LIMIT
    assert entry.problem.endswith("…")


def test_the_log_keeps_the_newest_calls_and_no_more():
    for at in range(api_errors.LOG_LIMIT + 5):
        api_errors.record(f"model-{at}", True, 0.1)

    logged = api_errors.calls()
    assert len(logged) == api_errors.LOG_LIMIT
    # The oldest went first: a failure is only interesting next to what the run
    # did after it.
    assert logged[0].model == "model-5"
    assert logged[-1].model == f"model-{api_errors.LOG_LIMIT + 4}"


# ---------------------------------------------------------------------------
# The swarm, as seen through the log
# ---------------------------------------------------------------------------


def test_a_completed_call_is_logged(gemini):
    gemini.models.generate_content.return_value = MagicMock(text="all right")

    assert swarm.generate("gemini-2.5-flash", "go") == "all right"

    assert [(c.model, c.ok) for c in api_errors.calls()] == [("gemini-2.5-flash", True)]


def test_a_call_that_will_not_answer_is_logged_with_the_status(gemini):
    gemini.models.generate_content.side_effect = refused(403, "API key not valid")

    with pytest.raises(api_errors.BackendUnavailable, match="did not answer"):
        swarm.generate("gemini-3.1-pro-preview", "go")

    (entry,) = api_errors.calls()
    assert (entry.model, entry.ok, entry.status) == ("gemini-3.1-pro-preview", False, 403)
    assert entry.problem == "API key not valid"


def test_a_reply_that_never_arrives_is_logged(gemini):
    chat = MagicMock()
    chat.send_message.side_effect = refused(503, "The model is overloaded")
    gemini.chats.create.return_value = chat

    with pytest.raises(api_errors.BackendUnavailable, match="lost service"):
        swarm.reply(chat, "your move")

    assert api_errors.calls()[-1].status == 503


def test_a_client_that_cannot_be_built_is_logged_too(monkeypatch):
    """A missing key is the first thing anyone asks about, and the log is where
    it shows up: the page cannot tell a key that was never found from one the
    service refused."""

    monkeypatch.setattr(
        swarm.genai, "Client", MagicMock(side_effect=RuntimeError("no key"))
    )

    with pytest.raises(api_errors.BackendUnavailable):
        swarm.get_client()

    (entry,) = api_errors.calls()
    assert (entry.model, entry.ok) == ("client", False)
    assert "no key" in entry.problem


# ---------------------------------------------------------------------------
# What the page is given
# ---------------------------------------------------------------------------


@pytest.fixture
def browser(monkeypatch):
    """A client with the dummy switch off, so the real swarm is what answers."""

    monkeypatch.setattr(app, "USE_DUMMY_API", False)
    app.reset_sessions()
    with app.app.test_client() as test_client:
        response = test_client.get("/")
        assert response.status_code == 200
        yield test_client


def diagnostics_of(browser) -> dict:
    response = browser.get("/api/diagnostics")
    assert response.status_code == 200
    return response.get_json()


# ---------------------------------------------------------------------------
# The whole path, from a teammate's browser to the reason
# ---------------------------------------------------------------------------


def test_the_diagnostics_say_which_api_is_switched_on(browser, monkeypatch):
    assert diagnostics_of(browser)["api"] == "real"

    monkeypatch.setattr(app, "USE_DUMMY_API", True)
    assert diagnostics_of(browser)["api"] == "dummy"


def test_a_key_in_the_environment_is_reported_without_being_revealed(
    browser, monkeypatch
):
    monkeypatch.setattr(app, "find_dotenv", lambda: "")
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)

    report = diagnostics_of(browser)

    assert report["key_found"] is True
    assert report["key_source"] == "environment"
    assert SECRET not in json.dumps(report)


def test_a_key_in_a_dotenv_is_reported_without_being_revealed(browser, monkeypatch):
    """The ``.env`` is read as well as the environment, and read first, because
    ``load_dotenv`` copies it into the environment once the real backend is
    imported - and because a teammate whose key is in a file the server never
    found would otherwise be told they have no key at all."""

    monkeypatch.setattr(app, "find_dotenv", lambda: "/srv/hacks/.env")
    monkeypatch.setattr(app, "dotenv_values", lambda path: {"GEMINI_API_KEY": SECRET})
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)  # what load_dotenv would do

    report = diagnostics_of(browser)

    assert report["key_found"] is True
    assert report["key_source"] == "dotenv"
    assert report["dotenv"] == "/srv/hacks/.env"
    assert SECRET not in json.dumps(report)


def test_no_key_anywhere_says_where_it_looked(browser, monkeypatch):
    monkeypatch.setattr(app, "find_dotenv", lambda: "/srv/hacks/.env")
    monkeypatch.setattr(app, "dotenv_values", lambda path: {})

    report = diagnostics_of(browser)

    assert report["key_found"] is False
    assert report["key_source"] == "missing"
    assert "GEMINI_API_KEY" in report["hint"]
    assert "/srv/hacks/.env" in report["hint"]


def test_a_missing_dotenv_is_not_a_key(browser, monkeypatch):
    """No ``.env`` at all is the common case on a machine where the key is in the
    shell, and it must not read as a key that was found and then lost."""

    monkeypatch.setattr(app, "find_dotenv", lambda: "")
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)

    assert diagnostics_of(browser)["key_source"] == "environment"


def test_the_models_in_use_are_named_and_the_client_is_watched_for(browser, monkeypatch):
    """A 404 from the service is a model name the account cannot reach, so which
    names were asked for is part of the answer.  Whether the client has been
    built says the key was found: if it has not, nothing has been asked yet, and
    "no calls" is not the same fault as "the calls all failed"."""

    report = diagnostics_of(browser)
    assert report["client_built"] is False
    assert report["models"]["rules"] == swarm.RULES_MODEL
    assert report["models"]["player"] == swarm.PLAYER_MODEL

    monkeypatch.setattr(swarm.genai, "Client", MagicMock(return_value=MagicMock()))
    swarm.get_client()

    assert diagnostics_of(browser)["client_built"] is True


def test_a_run_the_service_refuses_is_readable_in_the_browser(browser, gemini):
    """The case this was written for.  The turn is answered with a stream that
    says a step failed and does not say why - the model refused the key, or the
    quota ran out, or the name was wrong, and the page shows a cross either way.
    So the reason has to be somewhere the reader can get at, and until the log
    existed it was not."""

    gemini.chats.create.side_effect = refused(403, "API key not valid")
    answered = browser.post("/api/turn", json={"text": "A game about deep space"})

    # The stream itself says only that something failed: it cannot tell a refused
    # key from a spent quota, and a 200 is the honest answer to a run that did
    # start.
    assert answered.status_code == 200
    frames = frames_of(answered)
    assert any('data-result="failure"' in frame.get("html", "") for frame in frames)

    report = diagnostics_of(browser)
    (entry,) = report["calls"]
    assert (entry["model"], entry["ok"], entry["status"]) == (swarm.RULES_MODEL, False, 403)
    assert entry["problem"] == "API key not valid"
    assert report["failures"] == 1


def test_a_run_that_will_not_start_says_so_in_the_answer(browser, monkeypatch):
    """The other half: a fault before the first step comes back as a 409 with the
    server's own reason in the body, which is what the page reads out to print."""

    def down(state, text, files, api):
        raise api_errors.BackendUnavailable("the swarm is down")
        yield  # pragma: no cover - a generator, so the route sees a failure

    monkeypatch.setattr(app.pipeline, "turn", down)

    answered = browser.post("/api/turn", json={"text": "A game"})

    assert answered.status_code == 409
    assert json.loads(answered.get_data(as_text=True))["error"] == "the swarm is down"


def test_a_service_outage_is_logged_as_one(browser, gemini):
    gemini.chats.create.side_effect = genai_errors.ServerError(503, {"error": "overloaded"})

    answered = browser.post("/api/turn", json={"text": "A game"})
    assert answered.status_code == 200
    frames_of(answered)  # the run only reaches a model as the stream is read

    report = diagnostics_of(browser)
    # Worth the status being kept as a number: a 5xx is the service's problem
    # and a 4xx is not, and retrying a refused key will never work.
    assert report["calls"][-1]["status"] == 503


def test_the_diagnostics_route_needs_no_session(browser):
    """It is read by hand, from a page whose session has long since expired, and
    the state of a session is not what is being asked about."""

    assert browser.get("/api/diagnostics").status_code == 200
