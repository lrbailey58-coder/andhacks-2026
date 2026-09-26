"""Conformance with "ui instructions.md".

The brief is the source of truth, so these tests read it: every component it
names has to be implemented, and the style rules it lists have to hold in the
stylesheet.  If the document changes and the code does not, this fails.
"""

from __future__ import annotations

import os
import re

import pytest

import render
import ui

WEB_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTRUCTIONS = os.path.join(WEB_DIR, "ui instructions.md")
STYLESHEET = os.path.join(WEB_DIR, "static", "css", "style.css")
SCRIPT = os.path.join(WEB_DIR, "static", "js", "app.js")
TEMPLATE = os.path.join(WEB_DIR, "templates", "feed.html")

# The component names the brief uses in angle brackets.
COMPONENTS = [
    "Title",
    "PromptEditor",
    "ShowCode",
    "Multiword",
    "Ellipses",
    "Result",
    "Status",
    "StatusInterpreting",
    "StatusCoding",
    "StatusDeploying",
    "StatusPlaying",
    "ShowSample",
    "StatusCollecting",
    "StatusQuestion",
    "LongBlock",
    "LongBlockResponse",
    "LongBlockCode",
    "Ping",
]


@pytest.fixture(scope="module")
def instructions() -> str:
    with open(INSTRUCTIONS, encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def stylesheet() -> str:
    with open(STYLESHEET, encoding="utf-8") as handle:
        return handle.read()


@pytest.fixture(scope="module")
def template() -> str:
    with open(TEMPLATE, encoding="utf-8") as handle:
        return handle.read()


def test_the_brief_still_lists_these_components(instructions):
    listed = set(re.findall(r"<([A-Z][A-Za-z]+)>", instructions))
    missing = [name for name in COMPONENTS if name not in listed]
    assert not missing, f"the brief no longer describes: {missing}"


def constant_name(component: str) -> str:
    return "COMPONENT_" + re.sub(r"(?<!^)(?=[A-Z])", "_", component).upper()


def strip_comments(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def test_every_component_in_the_brief_is_implemented():
    """Each name in the brief's component list exists in the model."""

    for name in COMPONENTS:
        assert hasattr(ui, constant_name(name)), name


def test_every_component_is_rendered(template):
    for name in COMPONENTS:
        assert f"'{name}'" in template or f'"{name}"' in template, name


def test_the_status_labels_are_the_ones_the_brief_gives():
    assert ui.STATUS_LABELS == {
        "interpreting": "Interpreting rules",
        "coding": "Writing game code",
        "deploying": "Deploying game instances",
        "playing": "Herding player agent swarm",
        "collecting": "Collecting gameplay data and analyses",
    }


def test_the_editor_placeholder_is_the_documented_one():
    assert ui.PROMPT_PLACEHOLDER == (
        "Describe a board game, scenario or simulation, alternatively, "
        "drag and drop a file"
    )
    assert ui.PROMPT_BUTTON_LABEL == "Show me the code"
    assert ui.FOLLOWUP_HEADING == "What's next?"


def test_the_multiword_uses_multiword_phrases():
    for word in ui.MULTIWORD_WORDS:
        assert word.startswith(("an ", "a ")), word


# ---------------------------------------------------------------------------
# Style: the DOs and the DONTs
# ---------------------------------------------------------------------------


def test_no_gradients_anywhere(stylesheet):
    """DONT: "Gradients and fullscreen animations"."""

    offenders = [
        line.strip()
        for line in strip_comments(stylesheet).splitlines()
        if "gradient" in line
    ]
    assert not offenders, offenders


def test_no_fullscreen_animation(stylesheet):
    """DONT: "Gradients and fullscreen animations"."""

    css = strip_comments(stylesheet)
    assert "100vw" not in css
    assert "100vh" not in css
    assert "vw;" not in css
    # Nothing is pinned over the whole viewport either.
    assert "position: fixed" not in css


def test_the_page_carries_a_grid_pattern(stylesheet):
    """DO: "Grid patterns and small assorted arrows"."""

    css = strip_comments(stylesheet)
    # Drawn as an inline SVG data uri, not faked with a gradient.
    assert "data:image/svg+xml" in css
    assert "%3Csvg" in css and "%3Cpath" in css
    assert "background-image: url(" in css


def test_arrows_are_used_as_signposts(template, state):
    """DO: "Grid patterns and small assorted arrows"."""

    title = state.entries[0]
    assert title.props["arrow"] == "↓"
    assert "title__arrow" in render.entry_html(title)
    assert "status__arrow" in template
    for kind in ui.STATUS_LABELS:
        entry = ui.status_entry(state, kind, running=True)
        assert entry.props["arrow"] in ui.STATUS_ARROWS.values()


def test_changes_of_direction_are_curved(template):
    """DO: "Curved lines indicate changes in direction"."""

    assert "macro curve()" in template
    assert "e.props.curve" in template
    assert "svg" in template


def test_type_is_readable(stylesheet):
    """DO: "readability accepts no compromises", DONT: "thin or ostentatious fonts"."""

    # A system stack, at a body size nobody has to squint at.
    assert "--font:" in stylesheet
    assert "font-size: 1rem" in stylesheet
    assert "line-height: 1.6" in stylesheet
    # No font thinner than a normal weight, and no display faces.
    weights = [int(w) for w in re.findall(r"font-weight:\s*(\d{3})", stylesheet)]
    assert weights and min(weights) >= 500


def test_text_stays_selectable(stylesheet):
    """DO: "All text needs to be highlightable"."""

    assert "user-select: none" not in stylesheet.replace("user-select: none;\n", "")


def test_the_feed_scrolls_and_stays_most_of_the_width(stylesheet):
    """Logic: "The feed takes up most of the screen horizontally, but not all"."""

    assert "overflow-y: auto" in stylesheet
    assert "--feed-width" in stylesheet
    assert "width: min(100% - 3rem, var(--feed-width))" in stylesheet
    # It starts centred, vertically as well as horizontally.
    assert "margin-block: auto" in stylesheet
    assert "margin-inline: auto" in stylesheet


def test_long_blocks_cover_the_feed(stylesheet):
    """Logic: "<LongBlock> components cover the feed horizontally"."""

    assert ".longblock," in stylesheet
    assert "overflow: auto" in stylesheet  # wide code scrolls rather than stretching


def test_the_same_component_looks_different_per_context(template, stylesheet):
    """DO: "Components used in different contexts should appear subtly differently"."""

    # The context is a property of the entry, so the markup is derived from it
    # and the stylesheet holds one variant per context.
    assert "editor--{{ e.props.role }}" in template
    assert "checkbox--{{ e.props.context }}" in template
    assert "longblock longblock--{{ e.props.variant | default('text') }}" in template
    for context in ("editor--primary", "editor--question", "editor--next"):
        assert context in stylesheet, context
    for context in ("checkbox--editor", "checkbox--playing", "checkbox--final"):
        assert context in stylesheet, context
    for context in ("longblock--code", "longblock--sample", "longblock--response"):
        assert context in stylesheet, context


def test_motion_is_smooth_and_simple(stylesheet):
    """DO: "Smooth, simple motion"."""

    assert "--ease:" in stylesheet
    assert "transition:" in stylesheet
    # Nothing decorative moving across the whole screen.
    animations = re.findall(r"@keyframes\s+([\w-]+)", stylesheet)
    assert set(animations) <= {"word-cycle", "dot-bounce", "caret"}
    assert "prefers-reduced-motion" in stylesheet


def test_two_columns_for_the_code_and_the_sample(stylesheet):
    """Logic: adjacent columns, "similar to an online code debugger"."""

    assert ".columns--2" in stylesheet
    assert "repeat(2, minmax(0, 1fr))" in stylesheet


def test_the_browser_plays_back_each_typing_delay():
    """Logic: <LongBlock> types itself out, faster and faster.

    There is no browser in the test suite, so the playback is guarded at the
    source: the timer must use the step's own delay, not the first one.
    """

    with open(SCRIPT, encoding="utf-8") as handle:
        script = handle.read()
    assert "window.setTimeout(step, chunk[1])" in script
    # The plan itself travels with the markup, so the browser never guesses.
    assert "JSON.parse(code.getAttribute(\"data-typing\")" in script
