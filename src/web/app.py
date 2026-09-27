"""Flask entry point.

There is no REST API and no database: the routes here exist to serve the page
and to stream the UI patches that :mod:`pipeline` produces, and the work itself
is done by calling the functions in whichever API the switch below selects
directly.  Sessions are an in-memory dict, which is enough for the minimum
viable product.

The switch is :data:`USE_DUMMY_API`.  It is off for a normal run, so the
agents in ``backend/`` answer, and the test suite turns it on so the front end
can be exercised without a Gemini key or the cost of a playtest.  Nothing on
the page knows it exists.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from itertools import chain
from types import ModuleType
from typing import Any, Iterator

from flask import Flask, Response, request

import api_errors
import web.pipeline as pipeline
import web.render as render
import web.ui as ui

HERE = os.path.dirname(os.path.abspath(__file__))

#: The dummy API stands in for the real swarm while the front end is being built
#: and tested.  Off is a normal run: the real swarm answers.
USE_DUMMY_API = False

app = Flask(__name__, template_folder=os.path.join(HERE, "templates"))
app.config["JSON_SORT_KEYS"] = False

_lock = threading.Lock()
_sessions: dict[str, ui.UIState] = {}


def active_api() -> ModuleType:
    """The API this app is talking to, as the switch has it.

    The real backend is imported here rather than at the top of the module, so a
    run against the dummy never loads the Gemini client at all.
    """

    if USE_DUMMY_API:
        import web.dummy_api as dummy_api

        return dummy_api
    import backend.api as backend_api

    return backend_api


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


def new_session() -> str:
    session_id = secrets.token_hex(8)
    with _lock:
        _sessions[session_id] = ui.initial_state(session_id)
    return session_id


def get_session(session_id: str | None) -> ui.UIState | None:
    if not session_id:
        return None
    with _lock:
        return _sessions.get(session_id)


def reset_sessions() -> None:
    with _lock:
        _sessions.clear()
    with pipeline._playing_lock:
        pipeline._playing.clear()


def _session() -> ui.UIState | None:
    state = get_session(request.headers.get("X-Session") or request.cookies.get("session_id"))
    if state is None:
        state = get_session(request.args.get("session"))
    return state


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------


def wire_patch(patch: dict[str, Any]) -> dict[str, Any]:
    """A patch as it goes over the wire, with its markup already rendered."""

    op = patch.get("op")
    if op in (ui.OP_INSERT, ui.OP_UPDATE):
        return {
            "op": op,
            "index": patch.get("index"),
            "id": patch["entry"]["id"],
            "component": patch["entry"]["component"],
            "html": render.entry_html(_entry(patch["entry"])),
        }
    if op == ui.OP_REMOVE:
        return {"op": op, "id": patch["id"]}
    return {"op": op, "id": patch.get("id")}


def _entry(payload: dict[str, Any]) -> ui.Entry:
    return ui.Entry(
        id=payload["id"],
        component=payload["component"],
        props=payload["props"],
        children=[_entry(child) for child in payload["children"]],
    )


def frame(patch: dict[str, Any]) -> str:
    return "data: " + json.dumps(wire_patch(patch)) + "\n\n"


def stream(patches: Iterator[list[dict[str, Any]]]) -> Response:
    def generate() -> Iterator[str]:
        for batch in patches:
            for patch in batch:
                yield frame(patch)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/")
def index() -> str:
    session_id = new_session()
    state = get_session(session_id)
    html = render.page_html(state, session_id)
    response = Response(html, mimetype="text/html")
    response.set_cookie("session_id", session_id, httponly=True, samesite="Lax")
    return response


@app.post("/api/turn")
def api_turn() -> Response:
    """A submitted prompt: run the agents and stream the feed as it fills."""

    state = _session()
    if state is None:
        return Response('{"error":"no session"}', status=400, mimetype="application/json")
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    files = tuple(tuple(item) for item in payload.get("files", ()))
    if not text.strip() and not files:
        return Response('{"error":"empty prompt"}', status=400, mimetype="application/json")
    # <ShowCode> / <ShowSample> may have been ticked while the prompt was being
    # written.  Only what the editor actually carries is applied, so a run
    # without a checkbox keeps the choices made earlier.  A box beside the editor
    # is a request for the run, which is the default scope.
    for setter, key in (
        (ui.set_show_code, "show_code"),
        (ui.set_show_sample, "show_sample"),
    ):
        if key in payload:
            setter(state, bool(payload[key]))
    with _lock:
        state.turn += 1
    try:
        batches = pipeline.turn(state, text, files, active_api())
        first = next(batches)
    except (ui.UIError, api_errors.BackendUnavailable) as problem:
        # Nothing was waiting for a prompt, or the swarm is down before the
        # first step.  Say so rather than streaming a traceback.
        return Response(
            json.dumps({"error": str(problem)}),
            status=409,
            mimetype="application/json",
        )
    return stream(chain([first], batches))


@app.post("/api/toggle")
def api_toggle() -> Response:
    """<ShowCode> / <ShowSample> were checked or unchecked.

    ``context`` says which of the two the click came from, because they do
    different things: the boxes beside the first editor and beside
    <StatusPlaying> are a request for the run under way, and the two at the
    bottom of the <LongBlockResponse> open the columns beside the answer.  A
    request with no context is a request for the run.
    """

    state = _session()
    if state is None:
        return Response('{"error":"no session"}', status=400, mimetype="application/json")
    payload = request.get_json(silent=True) or {}
    which = payload.get("which")
    value = bool(payload.get("value"))
    context = payload.get("context") or ui.TOGGLE_RUN
    if context not in ui.TOGGLE_SCOPES:
        return Response('{"error":"unknown toggle context"}', status=400, mimetype="application/json")
    if which == "code":
        patches = ui.set_show_code(state, value, context)
    elif which == "sample":
        patches = ui.set_show_sample(state, value, context)
    else:
        return Response('{"error":"unknown toggle"}', status=400, mimetype="application/json")
    return stream(iter([patches]))


@app.post("/api/override")
def api_override() -> Response:
    """<SimOverride>: end the player agent simulation before it finishes.

    The answer is streamed like everything else, because the status has to end
    with a ``<Result>`` and the game has to say it was cut short - the turn in
    progress is still coming back from the swarm, and pretending otherwise
    would be a lie the feed then repeats.
    """

    state = _session()
    if state is None:
        return Response('{"error":"no session"}', status=400, mimetype="application/json")
    if not pipeline.begin_override(state):
        return Response(
            '{"error":"no simulation is running"}',
            status=409,
            mimetype="application/json",
        )
    return stream(iter([[]]))


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok", "scenarios": "complete, vague, unstable"}


def main() -> None:
    debug = os.environ.get("HACKS_UI_DEBUG") == "1"
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=debug)


if __name__ == "__main__":
    main()
