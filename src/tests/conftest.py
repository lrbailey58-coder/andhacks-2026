"""Shared fixtures.

The UI is driven here the way the browser drives it: submit through the HTTP
surface, read the streamed patches back, and apply them to a feed.  No browser
is involved, so the assertions are about the interface itself - what is in the
feed, in what order, with what values, and with what markup.
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
import sys
import typing
from types import SimpleNamespace

import pytest

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WEB_DIR not in sys.path:
    sys.path.insert(0, WEB_DIR)

import web.app as app  # noqa: E402
import web.dummy_api as dummy_api  # noqa: E402
import web.render as render  # noqa: E402
import web.ui as ui  # noqa: E402

COMPLETE_PROMPT = (
    "A two player game of tic tac toe on a 3x3 grid. X moves first, players "
    "alternate placing their mark on an empty square, and the first player to "
    "get three in a row wins. A full board with no line is a draw."
)
VAGUE_PROMPT = "I want a fun game about deep space but you decide everything else."
UNSTABLE_PROMPT = "A two player connect four game. Heads up, the swarm is flaky today."


class StreamedFeed:
    """The feed as the browser ends up holding it.

    Applies the streamed patches the same way ``static/js/app.js`` does, and
    reads the entries - nested ones included - back out of the markup, so a
    test can ask what the user is looking at, in reading order.
    """

    # Any opening tag carrying both markers, in either order.
    ENTRY_TAG = re.compile(
        r'<(?P<tag>\w+)(?=[^>]*\bdata-entry-id="(?P<id>[^"]+)")'
        r'(?=[^>]*\bdata-component="(?P<component>[^"]+)")[^>]*>'
    )

    def __init__(self) -> None:
        self.nodes: list[dict] = []  # top level, in feed order
        self.known: dict[str, dict] = {}  # every entry, nested ones included

    # -- the client side of the wire ---------------------------------------
    def apply(self, frame: dict) -> None:
        op = frame["op"]
        if op == "insert":
            self.nodes.insert(frame["index"], self._node(frame))
        elif op == "update":
            self._replace(frame["id"], frame)
        elif op == "remove":
            for index, node in enumerate(self.nodes):
                if node["id"] == frame["id"]:
                    del self.nodes[index]
                    break

    def _node(self, frame: dict) -> dict:
        node = {
            "id": frame["id"],
            "component": frame.get("component", ""),
            "html": frame.get("html", ""),
            "child_ids": [],
        }
        self._index(node)
        return node

    def _index(self, node: dict) -> None:
        """Record the entry and everything rendered inside it.

        One pass over the markup finds every nested entry at any depth, so
        there is nothing to recurse into; a nested entry borrows the markup it
        was rendered inside, which is what the user is looking at anyway.
        """

        self.known[node["id"]] = node
        for match in self.ENTRY_TAG.finditer(node["html"]):
            child_id = match.group("id")
            if child_id == node["id"] or child_id in node["child_ids"]:
                continue
            node["child_ids"].append(child_id)
            self.known[child_id] = {
                "id": child_id,
                "component": match.group("component"),
                "html": node["html"],
                "child_ids": [],
            }

    def _replace(self, entry_id: str, frame: dict) -> None:
        for index, node in enumerate(self.nodes):
            if node["id"] == entry_id:
                self.nodes[index] = self._node(frame)
                return
        self.nodes.append(self._node(frame))

    def apply_all(self, frames: typing.Iterable[dict]) -> "StreamedFeed":
        for frame in frames:
            self.apply(frame)
        return self

    # -- what the user can see ---------------------------------------------
    def flat(self) -> list[dict]:
        """Every entry, nested ones included, in reading order."""

        found: list[dict] = []
        seen: set[str] = set()

        def walk(entry_id: str) -> None:
            if entry_id in seen:
                return
            seen.add(entry_id)
            node = self.known.get(entry_id)
            if node is None:
                return
            found.append(node)
            for child_id in node["child_ids"]:
                walk(child_id)

        for node in self.nodes:
            walk(node["id"])
        return found

    @property
    def components(self) -> list[str]:
        return [node["component"] for node in self.flat()]

    @property
    def html(self) -> str:
        return "\n".join(node["html"] for node in self.nodes)

    @property
    def text(self) -> str:
        """The same markup with entities resolved: what the user reads."""

        return html_module.unescape(self.html)

    def all(self, component: str) -> list[dict]:
        return [node for node in self.flat() if node["component"] == component]

    def html_of(self, component: str) -> str:
        return "\n".join(node["html"] for node in self.all(component))

    def index(self, component: str) -> int:
        for index, node in enumerate(self.flat()):
            if node["component"] == component:
                return index
        raise AssertionError(f"{component} is not in the feed: {self.components}")

    def count(self, component: str) -> int:
        return len(self.all(component))


def frames_of(response) -> list[dict]:
    """Parse an SSE body into frames, the way the browser does."""

    frames: list[dict] = []
    for block in response.get_data(as_text=True).split("\n\n"):
        for line in block.split("\n"):
            if line.startswith("data:"):
                frames.append(json.loads(line[len("data:"):].strip()))
    return frames


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def instant_api(monkeypatch):
    """No simulated latency, and a clean dummy, so runs are quick and repeatable."""

    monkeypatch.setattr(dummy_api, "BASE_LATENCY", 0.0)
    dummy_api.reset()


@pytest.fixture
def state() -> ui.UIState:
    """A fresh feed, driven without HTTP."""

    return ui.initial_state("test")


@pytest.fixture
def client():
    app.reset_sessions()
    with app.app.test_client() as test_client:
        yield test_client


@pytest.fixture
def session(client) -> SimpleNamespace:
    """Loading the page is what creates the session."""

    response = client.get("/")
    assert response.status_code == 200
    match = re.search(r"session_id=([0-9a-f]+)", response.headers.get("Set-Cookie", ""))
    assert match, "the page should set a session cookie"
    state = app.get_session(match.group(1))
    feed = StreamedFeed().apply_all(_frames_of_state(state))
    return SimpleNamespace(
        id=match.group(1),
        html=response.get_data(as_text=True),
        state=state,
        feed=feed,
    )


def _frames_of_state(state: ui.UIState) -> list[dict]:
    """The first paint, as the same patches the stream would have sent."""

    return [
        {
            "op": "insert",
            "index": index,
            "id": entry.id,
            "component": entry.component,
            "html": render.entry_html(entry),
        }
        for index, entry in enumerate(state.entries)
    ]


@pytest.fixture
def page(session) -> str:
    return session.html


@pytest.fixture
def submit(client, session):
    """Submit a prompt through the HTTP surface, exactly as the page does.

    The patches land on the page's own feed, so each call returns the feed as
    it stands after the prompt, not just that prompt's changes.
    """

    def send(text: str, files: tuple = ()) -> StreamedFeed:
        response = client.post("/api/turn", json={"text": text, "files": list(files)})
        assert response.status_code == 200
        return session.feed.apply_all(frames_of(response))

    return send


@pytest.fixture
def toggle(client, session):
    """Check or uncheck <ShowCode> / <ShowSample>."""

    def check(which: str, value: bool) -> StreamedFeed:
        response = client.post("/api/toggle", json={"which": which, "value": value})
        assert response.status_code == 200
        return session.feed.apply_all(frames_of(response))

    return check
