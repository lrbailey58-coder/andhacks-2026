"""Front end model for the turn-based game design assistant.

The browser is a thin renderer.  Every component named in ``ui instructions.md``
is modelled here as an :class:`Entry`, every transition in the Logic section is
a function in this module, and ``render.py`` turns the resulting feed into
HTML.  Keeping the feed in Python means the described flows can be driven and
asserted from pytest without a browser, and it means there is exactly one
implementation of the fiddly rules (feed order, ``<Result>`` values, the
``<ShowCode>`` / ``<ShowSample>`` bookkeeping, Enter-with-a-modifier, ...).

The model hands out *patches* rather than HTML.  ``app.py`` renders those
patches to markup on the way to the browser; tests inspect the same patches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

# ---------------------------------------------------------------------------
# Components
# ---------------------------------------------------------------------------
# Names mirror the component list in the spec so tests can assert on the exact
# vocabulary the document uses.

COMPONENT_TITLE = "Title"
COMPONENT_PROMPT_EDITOR = "PromptEditor"
COMPONENT_SHOW_CODE = "ShowCode"
COMPONENT_SHOW_SAMPLE = "ShowSample"
COMPONENT_MULTIWORD = "Multiword"
COMPONENT_ELLIPSES = "Ellipses"
COMPONENT_RESULT = "Result"
COMPONENT_STATUS = "Status"
COMPONENT_STATUS_INTERPRETING = "StatusInterpreting"
COMPONENT_STATUS_CODING = "StatusCoding"
COMPONENT_STATUS_DEPLOYING = "StatusDeploying"
COMPONENT_STATUS_PLAYING = "StatusPlaying"
COMPONENT_STATUS_COLLECTING = "StatusCollecting"
COMPONENT_STATUS_QUESTION = "StatusQuestion"
COMPONENT_LONG_BLOCK = "LongBlock"
COMPONENT_LONG_BLOCK_RESPONSE = "LongBlockResponse"
COMPONENT_LONG_BLOCK_CODE = "LongBlockCode"
COMPONENT_PING = "Ping"
COMPONENT_FOLLOWUP = "Followup"

# Not in the spec's component list: a pure layout container with no appearance
# of its own, needed for "the code and sample game appear in adjacent columns".
COMPONENT_COLUMNS = "Columns"

STATUS_INTERPRETING = "interpreting"
STATUS_CODING = "coding"
STATUS_DEPLOYING = "deploying"
STATUS_PLAYING = "playing"
STATUS_COLLECTING = "collecting"

STATUS_COMPONENTS: dict[str, str] = {
    STATUS_INTERPRETING: COMPONENT_STATUS_INTERPRETING,
    STATUS_CODING: COMPONENT_STATUS_CODING,
    STATUS_DEPLOYING: COMPONENT_STATUS_DEPLOYING,
    STATUS_PLAYING: COMPONENT_STATUS_PLAYING,
    STATUS_COLLECTING: COMPONENT_STATUS_COLLECTING,
}

STATUS_LABELS: dict[str, str] = {
    STATUS_INTERPRETING: "Interpreting rules",
    STATUS_CODING: "Writing game code",
    STATUS_DEPLOYING: "Deploying game instances",
    STATUS_PLAYING: "Herding player agent swarm",
    STATUS_COLLECTING: "Collecting gameplay data and analyses",
}

# The linear half of the flow, in order.  Interpreting is included because it is
# the step that loops with <StatusQuestion>.
STATUS_FLOW = (
    STATUS_INTERPRETING,
    STATUS_CODING,
    STATUS_DEPLOYING,
    STATUS_PLAYING,
    STATUS_COLLECTING,
)

# Small assorted arrows, one per step, plus the themed glyph for playing.
STATUS_ARROWS: dict[str, str] = {
    STATUS_INTERPRETING: "↘",
    STATUS_CODING: "→",
    STATUS_DEPLOYING: "⇢",
    STATUS_PLAYING: "↻",
    STATUS_COLLECTING: "↙",
}

RESULT_SUCCESS = "success"
RESULT_FAILURE = "failure"
RESULT_MARKS = {RESULT_SUCCESS: "✓", RESULT_FAILURE: "✕"}

PROMPT_PLACEHOLDER = (
    "Describe a board game, scenario or simulation, alternatively, "
    "drag and drop a file"
)
#: The editor that answers a question says so: a different default text, and the
#: same one whichever question was asked.
QUESTION_PLACEHOLDER = "Answer the question, then Enter to carry on"
PROMPT_BUTTON_LABEL = "Show me the code"
FOLLOWUP_HEADING = "What's next?"

MULTIWORD_WORDS = (
    "an Experience",
    "a Scenario",
    "a Board Game",
    "a Simulation",
    "a Duel",
    "a Campaign",
)
MULTIWORD_PERIOD_MS = 2600

CODE_DOWNLOAD_NAME = "generated_engine.py"
SAMPLE_DOWNLOAD_NAME = "sample_game.txt"
RESPONSE_DOWNLOAD_NAME = "design_feedback.md"

LONG_BLOCK_LANGUAGE = {"code": "python"}


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    """One node of the vertical feed."""

    id: str
    component: str
    props: dict[str, Any] = field(default_factory=dict)
    children: list["Entry"] = field(default_factory=list)

    # -- composition -------------------------------------------------------
    def add(self, child: "Entry") -> "Entry":
        self.children.append(child)
        return child

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def first(self, component: str) -> "Entry | None":
        for node in self.walk():
            if node.component == component:
                return node
        return None

    # -- serialisation -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "component": self.component,
            "props": self.props,
            "children": [child.to_dict() for child in self.children],
        }


def components(entry: Entry) -> list[str]:
    """Every component name in the subtree rooted at ``entry``, in order."""

    return [node.component for node in entry.walk()]


# ---------------------------------------------------------------------------
# Patches
# ---------------------------------------------------------------------------

OP_INSERT = "insert"
OP_UPDATE = "update"
OP_REMOVE = "remove"

Patch = dict[str, Any]


def insert_patch(index: int, entry: Entry) -> Patch:
    return {"op": OP_INSERT, "index": index, "entry": entry.to_dict()}


def update_patch(entry: Entry) -> Patch:
    return {"op": OP_UPDATE, "id": entry.id, "entry": entry.to_dict()}


def remove_patch(entry_id: str) -> Patch:
    return {"op": OP_REMOVE, "id": entry_id}


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


@dataclass
class UIState:
    """Everything the front end knows, in feed order."""

    session: str = "local"
    entries: list[Entry] = field(default_factory=list)
    title: Entry | None = None
    awaiting: str | None = None
    finished: bool = False
    show_code: bool = False
    show_sample: bool = False
    code: str | None = None
    sample: list[dict[str, Any]] | None = None
    prompt: str = ""
    files: list[str] = field(default_factory=list)
    question: str | None = None
    answers: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    run: int = 0
    turn: int = 0
    _counter: int = 0

    # -- feed helpers ------------------------------------------------------
    def next_id(self, stem: str) -> str:
        self._counter += 1
        return f"{stem}-{self._counter}"

    def append(self, entry: Entry) -> Entry:
        self.entries.append(entry)
        return entry

    def index_of(self, entry_id: str) -> int:
        """Position in the feed.  Only top level entries have one."""

        for index, entry in enumerate(self.entries):
            if entry.id == entry_id:
                return index
        raise KeyError(entry_id)

    def entry(self, entry_id: str) -> Entry:
        """Any entry, nested ones included (a question's editor is nested)."""

        found = self.find(entry_id)
        if found is None:
            raise KeyError(entry_id)
        return found

    def find(self, entry_id: str) -> Entry | None:
        for entry in self.entries:
            if entry.id == entry_id:
                return entry
            for node in entry.walk():
                if node.id == entry_id:
                    return node
        return None

    def find_all(self, component: str) -> list[Entry]:
        return [entry for entry in self.entries if entry.component == component]

    def status(self, kind: str) -> Entry | None:
        component = STATUS_COMPONENTS[kind]
        for entry in reversed(self.entries):
            if entry.component == component:
                return entry
        return None

    def status_result(self, kind: str) -> str | None:
        entry = self.status(kind)
        if entry is None:
            return None
        result = entry.first(COMPONENT_RESULT)
        return None if result is None else result.props["value"]

    def results(self, component: str) -> list[str | None]:
        """Every ``<Result>`` in the feed for one component, in feed order.

        Interpreting appears once per pass of the loop, so the question flow has
        a failure followed by a success.
        """

        found: list[str | None] = []
        for entry in self.entries:
            if entry.component != component:
                continue
            result = entry.first(COMPONENT_RESULT)
            found.append(None if result is None else result.props["value"])
        return found

    def status_labels(self) -> list[str]:
        return [
            entry.props["label"]
            for entry in self.entries
            if entry.component in STATUS_COMPONENTS.values()
            or entry.component == COMPONENT_STATUS_QUESTION
        ]

    def components(self) -> list[str]:
        names: list[str] = []
        for entry in self.entries:
            names.extend(components(entry))
        return names


class UIError(Exception):
    """Raised when the UI is asked to do something the flow does not allow."""


# ---------------------------------------------------------------------------
# Long block typing
# ---------------------------------------------------------------------------


def typing_schedule(
    total: int,
    start_delay: int = 14,
    growth: float = 1.18,
    max_delay: int = 90,
) -> list[list[int]]:
    """Typing plan as ``[characters, delay_ms]`` steps.

    The first characters are deliberate, then each step carries more of them
    than the last, so a long block types itself out in seconds instead of
    hanging.  Computed here so the browser only has to play the plan back.
    """

    plan: list[list[int]] = []
    remaining = max(0, int(total))
    chars = 1
    while remaining > 0:
        step = min(chars, remaining)
        plan.append([step, min(max_delay, int(round(step * start_delay)))])
        remaining -= step
        chars = max(chars + 1, int(chars * growth))
    return plan


def typing_plan(text: str, **kwargs: Any) -> dict[str, Any]:
    return {"total": len(text), "schedule": typing_schedule(len(text), **kwargs)}


# ---------------------------------------------------------------------------
# Input handling
# ---------------------------------------------------------------------------

SUBMIT = "submit"
NEWLINE = "newline"

_MODIFIER_KEYS = ("ctrlKey", "altKey", "shiftKey", "metaKey")


def enter_keypress(modifiers: dict[str, bool] | None = None) -> str:
    """Enter submits the prompt, unless any modifier is held down.

    "if the user presses enter with a modifier, any modifier, it's treated like
    a regular enter keypress inside the editor" (Logic / General Rules).
    """

    modifiers = modifiers or {}
    for key in _MODIFIER_KEYS:
        if modifiers.get(key):
            return NEWLINE
    return SUBMIT


def combine_prompt(text: str, files: Sequence[tuple[str, str]] = ()) -> str:
    """Editor contents plus any dropped files, in the shape the API expects."""

    chunks: list[str] = []
    if text.strip():
        chunks.append(text.strip())
    for name, content in files:
        chunks.append(f"--- FILE: {name} ---\n{content.strip()}")
    return "\n\n".join(chunks)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def prompt_editor(
    state: UIState,
    role: str,
    *,
    show_code: bool = False,
    value: str = "",
) -> Entry:
    """The editor for ``role``, sized to what it is asked for.

    The first editor is the tall one: it takes a whole prompt.  A question gets
    a short answer, so it is one line that grows to fit, and says what it is for
    rather than repeating the first editor's text.
    """

    question = role == "question"
    props: dict[str, Any] = {
        "role": role,
        "placeholder": QUESTION_PLACEHOLDER if question else PROMPT_PLACEHOLDER,
        "value": value,
        "submitted": False,
        "align": "wide",
        "drop_target": role == "primary",
        "show_code": show_code,
        "rows": 1 if question else 2,
        "auto_grow": question,
    }
    if show_code:
        # <ShowCode> lives to the right of the <PromptEditor>.
        props["code_checked"] = state.show_code
    return Entry(id=state.next_id("editor"), component=COMPONENT_PROMPT_EDITOR, props=props)


def initial_state(session: str = "local") -> UIState:
    """``<Title>`` followed by a ``<PromptEditor>`` (Main Flow)."""

    state = UIState(session=session)
    title = Entry(
        id=state.next_id("title"),
        component=COMPONENT_TITLE,
        props={
            "text": "Let's design",
            "state": "centered",
            "arrow": "↓",
        },
    )
    title.add(
        Entry(
            id=state.next_id("word"),
            component=COMPONENT_MULTIWORD,
            props={
                "words": list(MULTIWORD_WORDS),
                "index": 0,
                "period_ms": MULTIWORD_PERIOD_MS,
            },
        )
    )
    state.title = title
    state.append(title)
    editor = prompt_editor(state, "primary", show_code=True)
    state.append(editor)
    state.awaiting = editor.id
    return state


def status_entry(state: UIState, kind: str, **props: Any) -> Entry:
    entry = Entry(
        id=state.next_id("status"),
        component=STATUS_COMPONENTS[kind],
        props={
            "kind": kind,
            "label": STATUS_LABELS[kind],
            "arrow": STATUS_ARROWS[kind],
            "glyph": "checkerboard" if kind == STATUS_PLAYING else "",
            "running": True,
            **props,
        },
    )
    entry.add(
        Entry(
            id=state.next_id("ellipses"),
            component=COMPONENT_ELLIPSES,
            props={"text": "...", "bounce": True, "dots": 3},
        )
    )
    if kind == STATUS_PLAYING:
        # "<StatusPlaying> appears eventually alongside an unchecked <ShowSample>"
        entry.add(
            Entry(
                id=state.next_id("showsample"),
                component=COMPONENT_SHOW_SAMPLE,
                props={
                    "label": "Show sample game",
                    "checked": state.show_sample,
                    "context": "playing",
                },
            )
        )
    return entry


def resolve_statuses(state: UIState) -> list[Patch]:
    """Replace the ``<Ellipses>`` of every running ``<Status>`` with a ``<Result>``.

    "When a ``<Status>`` is shown, all ``<Ellipses>`` components from previous
    statuses are replaced by ``<Result>`` with a default value of 'success'."
    Only the status that is being superseded by the new one is finalised, so a
    status the API explicitly reported as a failure keeps its cross.
    """

    patches: list[Patch] = []
    for entry in list(state.entries):
        if not entry.props.get("running") or entry.component not in (
            *STATUS_COMPONENTS.values(),
            COMPONENT_STATUS_QUESTION,
        ):
            continue
        ellipses = entry.first(COMPONENT_ELLIPSES)
        if ellipses is None:
            continue
        index = entry.children.index(ellipses)
        entry.props["running"] = False
        result = Entry(
            id=state.next_id("result"),
            component=COMPONENT_RESULT,
            props={"value": RESULT_SUCCESS, "mark": RESULT_MARKS[RESULT_SUCCESS]},
        )
        entry.children[index] = result
        patches.append(update_patch(entry))
    return patches


# ---------------------------------------------------------------------------
# Transitions
# ---------------------------------------------------------------------------


def submit(state: UIState, text: str, files: Sequence[tuple[str, str]] = ()) -> Patch:
    """Submit the prompt currently being written (General Rules / Main Flow).

    The title collapses to the left and the editor slides from covering the feed
    horizontally to a right alignment, gaining copy and download buttons.
    """

    if state.awaiting is None:
        raise UIError("no prompt editor is accepting input")
    editor = state.entry(state.awaiting)
    if not text.strip() and not files:
        raise UIError("prompt is empty")

    combined = combine_prompt(text, files)
    editor.props["value"] = combined
    editor.props["submitted"] = True
    editor.props["align"] = "right"
    editor.props["buttons"] = ["copy", "download"]
    editor.props["download_name"] = (
        CODE_DOWNLOAD_NAME if editor.props["show_code"] else "prompt.txt"
    )
    state.awaiting = None

    if state.title is not None and state.title.props["state"] == "centered":
        state.title.props["state"] = "collapsed"

    state.prompt = combined
    state.files = [name for name, _ in files]
    return update_patch(editor)


def show_status(state: UIState, kind: str) -> Patch:
    patches = resolve_statuses(state)
    entry = state.append(status_entry(state, kind))
    patches.append(insert_patch(len(state.entries) - 1, entry))
    return patches


def show_result(
    state: UIState, kind: str, value: str = RESULT_SUCCESS
) -> Patch:
    """Explicit ``<Result>`` from the API, e.g. a failure before a question."""

    if value not in (RESULT_SUCCESS, RESULT_FAILURE):
        raise UIError(f"unknown result value: {value!r}")
    component = STATUS_COMPONENTS[kind]
    entry = next(
        (item for item in reversed(state.entries) if item.component == component),
        None,
    )
    if entry is None:
        raise UIError(f"no {component} to report a result for")
    ellipses = entry.first(COMPONENT_ELLIPSES)
    index = entry.children.index(ellipses) if ellipses else len(entry.children)
    entry.props["running"] = False
    result = Entry(
        id=state.next_id("result"),
        component=COMPONENT_RESULT,
        props={"value": value, "mark": RESULT_MARKS[value]},
    )
    if ellipses is not None:
        entry.children[index] = result
    else:
        entry.add(result)
    return update_patch(entry)


def show_question(state: UIState, text: str) -> list[Patch]:
    """``<StatusQuestion>`` with its own ``<PromptEditor>``, awaiting an answer."""

    patches = resolve_statuses(state)
    entry = Entry(
        id=state.next_id("question"),
        component=COMPONENT_STATUS_QUESTION,
        props={
            "kind": "question",
            "label": "",
            "text": text,
            "arrow": "↩",
            "curve": True,
            "running": True,
        },
    )
    entry.add(
        Entry(
            id=state.next_id("ellipses"),
            component=COMPONENT_ELLIPSES,
            props={"text": "...", "bounce": True, "dots": 3},
        )
    )
    editor = prompt_editor(state, "question")
    editor.props["align"] = "wide"
    entry.add(editor)
    state.append(entry)
    state.awaiting = editor.id
    state.question = text
    patches.append(insert_patch(len(state.entries) - 1, entry))
    return patches


def _code_block(state: UIState, entry_id: str) -> Entry:
    if state.code is None:
        raise UIError("the code has not been generated yet")
    return Entry(
        id=entry_id,
        component=COMPONENT_LONG_BLOCK_CODE,
        props={
            "text": state.code,
            "language": LONG_BLOCK_LANGUAGE["code"],
            "caption": "Generated game engine",
            "buttons": ["copy", "download"],
            "download_name": CODE_DOWNLOAD_NAME,
            "typing": typing_plan(state.code),
            "rows": state.code.count("\n") + 1,
        },
    )


def _sample_block(state: UIState, entry_id: str) -> Entry:
    if state.sample is None:
        raise UIError("the sample game is not available yet")
    lines = sample_transcript(state.sample)
    return Entry(
        id=entry_id,
        component=COMPONENT_LONG_BLOCK,
        props={
            "variant": "sample",
            "text": "\n".join(lines),
            "lines": lines,
            "caption": "Sample game: player agent swarm",
            "buttons": ["copy", "download"],
            "download_name": SAMPLE_DOWNLOAD_NAME,
            "typing": typing_plan("\n".join(lines)),
            "rows": len(lines),
            "pending": False,
        },
    )


def sample_transcript(sample: Sequence[dict[str, Any]]) -> list[str]:
    """The player agent swarm reasoning through the game, turn by turn."""

    lines: list[str] = []
    for record in sample:
        head = f"Turn {record['turn']} · Player {record['player']}"
        if record.get("illegal"):
            lines.append(f"{head} — rejected: {record['move']}")
            lines.append(f"    {record['explanation']}")
            continue
        lines.append(f"{head} — plays {record['move']}")
        lines.append(f"    {record['explanation']}")
    if sample:
        lines.append("")
        lines.append(str(sample[-1].get("outcome", "Game over.")))
    return lines


def store_code(state: UIState, code: str) -> None:
    """The API's confirmation of the coding step carries the code contents."""

    state.code = code


def store_sample(state: UIState, sample: Sequence[dict[str, Any]]) -> None:
    state.sample = [dict(record) for record in sample]


def insert_after(state: UIState, entry: Entry, after_id: str | None) -> int:
    if after_id is None:
        state.append(entry)
        return len(state.entries) - 1
    state.entries.insert(state.index_of(after_id) + 1, entry)
    return state.index_of(entry.id)


def show_code(state: UIState) -> list[Patch]:
    """``<LongBlockCode>`` for the code the API handed over with the coding step.

    "If ``<ShowCode>`` was checked earlier and the process, since the coding is
    complete, the code can now be retrieved from the back end and displayed."
    Nothing appears unless the checkbox asked for it.
    """

    if not state.show_code or state.code is None:
        return []
    entry_id = f"code-{state.run}"
    if state.find(entry_id) is not None:
        return []
    block = _code_block(state, entry_id)
    index = insert_after(state, block, _anchor(state, STATUS_DEPLOYING))
    return [insert_patch(index, block)]


def show_sample(state: UIState) -> list[Patch]:
    if not state.show_sample or state.sample is None:
        return []
    entry_id = f"sample-{state.run}"
    if state.find(entry_id) is not None:
        return []
    block = _sample_block(state, entry_id)
    index = insert_after(state, block, _anchor(state, STATUS_PLAYING))
    return [insert_patch(index, block)]


def _anchor(state: UIState, kind: str) -> str | None:
    status = state.status(kind)
    return None if status is None else status.id


def set_show_code(state: UIState, value: bool) -> list[Patch]:
    """The user checked or unchecked a ``<ShowCode>``."""

    return _set_toggle(state, COMPONENT_SHOW_CODE, "code", state.show_code, value)


def set_show_sample(state: UIState, value: bool) -> list[Patch]:
    """The user checked or unchecked a ``<ShowSample>``."""

    return _set_toggle(
        state, COMPONENT_SHOW_SAMPLE, "sample", state.show_sample, value
    )


def _set_toggle(
    state: UIState,
    component: str,
    what: str,
    current: bool,
    value: bool,
) -> list[Patch]:
    """A ``<ShowCode>`` / ``<ShowSample>`` was checked or unchecked.

    The checkbox that was clicked is already showing the new state and the
    browser keeps the other copies of that checkbox in step with it, so what
    travels back is the feed: the row beside the answer, and the block anchored
    to the step that produced it.
    """

    value = bool(value)
    if component == COMPONENT_SHOW_CODE:
        state.show_code = value
    else:
        state.show_sample = value

    # The model's copies of the checkbox follow anyway, so the next render of
    # any of them - the one at <StatusPlaying>, the one at the answer - is right.
    for checkbox in _checkboxes(state, component):
        checkbox.props["checked"] = value
    if component == COMPONENT_SHOW_CODE:
        # <ShowCode> also lives inside the primary editor's own markup.
        for entry in state.entries:
            if entry.component == COMPONENT_PROMPT_EDITOR and entry.props.get("show_code"):
                entry.props["code_checked"] = value

    patches: list[Patch] = []
    if value:
        # The row beside the answer settles first, then the block beside the
        # step that made it.
        patches.extend(_set_final(state, what, value))
        patches.extend(_set_mid_feed(state, what, value))
    else:
        # Coming out, the block in the feed goes first, then the row closes.
        patches.extend(_set_mid_feed(state, what, value))
        patches.extend(_set_final(state, what, value))
    return patches


def _set_mid_feed(state: UIState, what: str, value: bool) -> list[Patch]:
    """The block in the middle of the feed, anchored to the step that made it."""

    entry_id = f"{what}-{state.run}"
    shown = state.find(entry_id) is not None
    if value:
        if shown:
            return []
        return show_code(state) if what == "code" else show_sample(state)
    if not shown:
        return []
    state.entries.remove(state.entry(entry_id))
    return [remove_patch(entry_id)]


def _set_final(state: UIState, what: str, value: bool) -> list[Patch]:
    group = _final_group(state)
    if group is None:
        return []
    entry_id = f"final-{what}-{state.run}"
    block = next((child for child in group.children if child.id == entry_id), None)
    patches: list[Patch] = []
    if value and block is None:
        block = (
            _code_block(state, entry_id)
            if what == "code"
            else _sample_block(state, entry_id)
        )
        block.props["column"] = True
        group.add(block)
    elif not value and block is not None:
        # The block is an element in its own right, so the browser is told to
        # drop it by id; the group is re-rendered behind it so the row matches
        # the model again and closes up.
        group.children.remove(block)
        patches.append(remove_patch(entry_id))
    else:
        return []
    group.props["columns"] = len(group.children)
    patches.append(update_patch(group))
    return patches


def _checkboxes(state: UIState, component: str) -> list[Entry]:
    return [
        node
        for entry in state.entries
        for node in entry.walk()
        if node.component == component
    ]


def _final_group(state: UIState) -> Entry | None:
    return state.find(f"final-{state.run}")


def show_response(state: UIState, text: str) -> list[Patch]:
    """``<LongBlockResponse>`` with the two checkboxes at the bottom."""

    patches = resolve_statuses(state)
    entry = Entry(
        id=state.next_id("response"),
        component=COMPONENT_LONG_BLOCK_RESPONSE,
        props={
            "text": text,
            "caption": "Design review",
            "curve": True,
            "buttons": ["copy", "download"],
            "download_name": RESPONSE_DOWNLOAD_NAME,
            "typing": typing_plan(text),
            "rows": text.count("\n") + 1,
        },
    )
    entry.add(
        Entry(
            id=state.next_id("showcode"),
            component=COMPONENT_SHOW_CODE,
            props={"label": PROMPT_BUTTON_LABEL, "checked": state.show_code, "context": "final"},
        )
    )
    entry.add(
        Entry(
            id=state.next_id("showsample"),
            component=COMPONENT_SHOW_SAMPLE,
            props={
                "label": "Show sample game",
                "checked": state.show_sample,
                "context": "final",
            },
        )
    )
    state.append(entry)
    group = Entry(
        id=f"final-{state.run}",
        component=COMPONENT_COLUMNS,
        props={"columns": 1, "curve": False},
    )
    state.append(group)
    state.response = text
    state.finished = True
    patches.append(insert_patch(len(state.entries) - 2, entry))
    patches.append(insert_patch(len(state.entries) - 1, group))
    # A checkbox the user already ticked stays ticked at the bottom.
    if state.show_code:
        patches.extend(_set_final(state, "code", True))
    if state.show_sample:
        patches.extend(_set_final(state, "sample", True))
    patches.extend(show_followup(state))
    return patches


def show_followup(state: UIState) -> list[Patch]:
    """A left aligned "What's next?" box followed by a ``<PromptEditor>``."""

    existing = state.find(f"followup-{state.run}")
    if existing is not None:
        return []
    entry = Entry(
        id=f"followup-{state.run}",
        component=COMPONENT_FOLLOWUP,
        props={"heading": FOLLOWUP_HEADING, "align": "left", "run": state.run},
    )
    editor = prompt_editor(state, "next")
    editor.props["align"] = "wide"
    entry.add(editor)
    state.append(entry)
    state.awaiting = editor.id
    return [insert_patch(len(state.entries) - 1, entry)]


def ping(state: UIState, reason: str) -> Patch:
    entry = Entry(
        id=state.next_id("ping"),
        component=COMPONENT_PING,
        props={"reason": reason, "invisible": True, "tone": "chime"},
    )
    state.append(entry)
    return insert_patch(len(state.entries) - 1, entry)


def show_failure(state: UIState, message: str) -> list[Patch]:
    """A ``<Result>`` of "failure" plus recovery: the user can try again."""

    patches: list[Patch] = []
    running = [
        entry
        for entry in state.entries
        if entry.props.get("running")
        and entry.component in (*STATUS_COMPONENTS.values(), COMPONENT_STATUS_QUESTION)
    ]
    for entry in running:
        ellipses = entry.first(COMPONENT_ELLIPSES)
        index = entry.children.index(ellipses) if ellipses else len(entry.children)
        entry.props["running"] = False
        result = Entry(
            id=state.next_id("result"),
            component=COMPONENT_RESULT,
            props={"value": RESULT_FAILURE, "mark": RESULT_MARKS[RESULT_FAILURE]},
        )
        if ellipses is not None:
            entry.children[index] = result
        else:
            entry.add(result)
        patches.append(update_patch(entry))
    state.failures.append(message)
    state.finished = True
    patches.append(ping(state, "task_failed"))
    patches.extend(show_followup(state))
    return patches


def start_run(state: UIState, text: str, files: Sequence[tuple[str, str]] = ()) -> Patch:
    """A new run from the "What's next?" editor: append to the definition."""

    state.run += 1
    state.turn = 0
    state.finished = False
    state.question = None
    state.answers = []
    state.code = None
    state.sample = None
    state.response = None
    return submit(state, text, files)


def answer_question(state: UIState, text: str) -> Patch:
    state.answers.append(text)
    return submit(state, text)
