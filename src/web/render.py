"""HTML rendering for the feed.

The model in :mod:`ui` produces entries and patches; this module turns them into
the markup in ``templates/feed.html``.  Flask streams the same fragments to the
browser, so the first paint and every later update come from one renderer.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

import flask
from jinja2 import Environment, FileSystemLoader, select_autoescape

import web.ui as ui

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")

_env: Environment | None = None
_feed = None
_index = None


def url_for(endpoint: str, **values: Any) -> str:
    """Flask's url_for, with a plain path fallback outside a request."""

    try:
        return flask.url_for(endpoint, **values)
    except Exception:
        if endpoint == "static":
            return "/static/" + values.get("filename", "")
        return "/" + endpoint


def environment() -> Environment:
    global _env, _feed, _index
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(TEMPLATE_DIR),
            autoescape=select_autoescape(["html"]),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )
        _env.globals["url_for"] = url_for
        # The two checkbox scopes are wire values as much as they are model
        # values, so the template reads them from one place.
        _env.globals["scope_run"] = ui.TOGGLE_RUN
        _env.globals["scope_final"] = ui.TOGGLE_FINAL
        _feed = _env.get_template("feed.html").module
        _index = _env.get_template("index.html")
    return _env


def entry_html(entry: ui.Entry) -> str:
    """Markup for a single entry, addressed by ``data-entry-id``."""

    environment()
    return _feed.entry(entry).strip()


def entries_html(entries: Iterable[ui.Entry]) -> str:
    return "\n".join(entry_html(entry) for entry in entries)


def patch_html(patch: dict[str, Any]) -> str:
    """Markup for an update, or ``""`` for a removal (the client just drops it)."""

    if patch["op"] == ui.OP_REMOVE:
        return ""
    return entry_html(patch["entry"])


def page_html(state: ui.UIState, session_id: str) -> str:
    environment()
    return _index.render(
        entries=state.entries,
        session_id=session_id,
        feed=_feed,
    )
