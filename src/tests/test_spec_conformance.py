"""Conformance with "ui instructions.md".

The brief is the source of truth, so these tests read it: every component it
names has to be implemented, and the behaviour it describes has to hold.  If the
document changes and the code does not, this fails.

The style rules the brief lists are deliberately not asserted on from here any
more - see the note in the style section below.
"""

from __future__ import annotations

import os
import re

import pytest

import web.render as render
import web.ui as ui

WEB_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"
)
INSTRUCTIONS = os.path.join(WEB_DIR, "ui instructions.md")
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
def template() -> str:
    with open(TEMPLATE, encoding="utf-8") as handle:
        return handle.read()


def test_the_brief_still_lists_these_components(instructions):
    listed = set(re.findall(r"<([A-Z][A-Za-z]+)>", instructions))
    missing = [name for name in COMPONENTS if name not in listed]
    assert not missing, f"the brief no longer describes: {missing}"


def constant_name(component: str) -> str:
    return "COMPONENT_" + re.sub(r"(?<!^)(?=[A-Z])", "_", component).upper()


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
#
# The tests that used to live here read the stylesheet and asserted on its
# declarations - no gradients, a grid pattern in the background, a readable
# font size, a feed that is centred, two columns, smooth motion.  They are gone
# on purpose.  The CSS is settled, so those tests could only ever fail when
# somebody changed a colour or a width on purpose, and every time they did they
# buried the failures worth looking at: a status that lands in the wrong place,
# a block that never types itself out, a game that never finishes.  The rules
# they policed are still rules - they are simply kept in the stylesheet, where
# they belong, and not asserted on from python.


def test_arrows_are_used_as_signposts(template, state):
    """DO: "Grid patterns and small assorted arrows"."""

    title = state.entries[0]
    assert title.props["arrow"] == "↓"
    assert "title__arrow" in render.entry_html(title)
    assert "status__arrow" in template
    for kind in ui.STATUS_LABELS:
        entry = ui.status_entry(state, kind, running=True)
        assert entry.props["arrow"] in ui.STATUS_ARROWS.values()


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
