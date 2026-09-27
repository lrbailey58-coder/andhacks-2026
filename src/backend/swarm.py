"""The game tester swarm, upgraded from ``game_tester_swarm.py`` for the web app.

The old script drove the whole run from the terminal: it read a directory of
design files, then sat in ``input()`` loops asking a person to approve the rules
and pick a menu option.  The web front end is the conversation surface now, so
what was terminal scaffolding is gone and what was the actual swarm is here:

* the Rules agent keeps a **live chat**.  ``RulesConversation`` is a real
  ``client.chats`` session that stays open for as long as the run does, so
  answering a question continues the conversation instead of restarting it.
  What the agent says is what the user is asked, and it is kept as the
  conversation's current text;
* the engine is built **in memory**.  ``load_engine`` execs the generated source
  in a module of its own, so nothing is written to the file tree and the old
  ``generated_engine.py`` dance - write, invalidate the caches, import by path -
  is not needed;
* the player agents are **objects**.  ``PlayerAgent`` is one player's share of
  the swarm: it is handed the authoritative state, it proposes a move, and it
  records the attempts that were not legal.  Deploying is what instances them;
* every model call goes through ``generate`` / ``reply``, which turn a missing
  API key, a failed request or an unreadable reply into one
  :class:`~api_errors.BackendUnavailable`, which is the failure the UI knows how
  to draw.

The client is built on first use rather than at import, so the app can boot
without an API key and only a run that actually reaches a model needs one.
That is also what makes the whole module testable with ``MagicMock`` clients and
no prompts sent.

``backend/api.py`` is the seam the front end talks to; this module is what it
talks to.
"""

from __future__ import annotations

import json
import re
import threading
import time
import types
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

from dotenv import load_dotenv
from google import genai
from google.genai import types as genai_types

import api_errors
from api_errors import BackendUnavailable

# ---------------------------------------------------------------------------
# Setup & Configuration
# ---------------------------------------------------------------------------

RULES_MODEL = "gemini-3.1-pro-preview"
DESIGN_MODEL = "gemini-3.1-pro-preview"
PLAYER_MODEL = "gemini-2.5-flash"

MAX_TURNS = 150
MAX_RETRIES = 3

# Wall clock for one playtest, in seconds.  A playtest is a swarm of agents each
# asking a model for a move, so it is bounded in time and not only in turns:
# 150 turns of a slow model is half an hour of a browser spinner.  Checked between
# player actions by :func:`play_game`, which reports a game cut short this way as
# such - a timeout is a fact about the playtest, not a win for anybody.
MAX_PLAY_SECONDS = 300.0

#: The generated engine is a module, not a file, so the name is only ever the
#: label a traceback carries.
ENGINE_MODULE_NAME = "generated_engine"

SEVERITIES = ("low", "medium", "high")

load_dotenv()

_CLIENT: Any | None = None


def get_client() -> Any:
    """The Gemini client, built the first time a model is actually needed."""

    global _CLIENT
    if _CLIENT is None:
        started = time.monotonic()
        try:
            _CLIENT = genai.Client()
        except Exception as problem:
            api_errors.record("client", False, time.monotonic() - started, problem)
            raise BackendUnavailable(
                f"the Gemini client could not be started ({problem}); "
                "GEMINI_API_KEY may be missing"
            ) from problem
        api_errors.record("client", True, time.monotonic() - started)
    return _CLIENT


def reset_client() -> None:
    """Drop the client so the next call builds another one."""

    global _CLIENT
    _CLIENT = None


def get_config(
    system_instruction: str | None = None,
    json_mode: bool = False,
    temperature: float = 0.2,
) -> genai_types.GenerateContentConfig:
    """Standardized config, with the automatic function calling warning silenced."""

    config_args: dict[str, Any] = {
        "automatic_function_calling": genai_types.AutomaticFunctionCallingConfig(
            disable=True
        ),
        "temperature": temperature,
    }
    if system_instruction:
        config_args["system_instruction"] = system_instruction
    if json_mode:
        config_args["response_mime_type"] = "application/json"

    return genai_types.GenerateContentConfig(**config_args)


# ---------------------------------------------------------------------------
# Model calls
# ---------------------------------------------------------------------------


def generate(
    model: str,
    contents: str,
    config: genai_types.GenerateContentConfig | None = None,
) -> str:
    """One completion, as text.  Anything that goes wrong is a lost service."""

    started = time.monotonic()
    try:
        text = get_client().models.generate_content(
            model=model, contents=contents, config=config
        ).text
    except BackendUnavailable:
        raise
    except Exception as problem:
        api_errors.record(model, False, time.monotonic() - started, problem)
        raise BackendUnavailable(f"{model} did not answer ({problem})") from problem
    api_errors.record(model, True, time.monotonic() - started)
    return text


def open_chat(model: str, system_instruction: str, temperature: float) -> Any:
    """Start a chat session with its own system instruction."""

    started = time.monotonic()
    try:
        chat = get_client().chats.create(
            model=model,
            config=get_config(
                system_instruction=system_instruction, temperature=temperature
            ),
        )
    except BackendUnavailable:
        raise
    except Exception as problem:
        api_errors.record(model, False, time.monotonic() - started, problem)
        raise BackendUnavailable(
            f"{model} could not be reached ({problem})"
        ) from problem
    api_errors.record(model, True, time.monotonic() - started)
    return chat


def reply(chat: Any, message: str) -> str:
    """The next thing the agent says, as text."""

    started = time.monotonic()
    try:
        text = chat.send_message(message).text
    except BackendUnavailable:
        raise
    except Exception as problem:
        api_errors.record("chat", False, time.monotonic() - started, problem)
        raise BackendUnavailable(f"the agent lost service ({problem})") from problem
    # The chat has already been logged as a model when it was opened, so this is
    # recorded as a turn of the same conversation rather than a second model.
    api_errors.record("chat", True, time.monotonic() - started)
    return text


# ---------------------------------------------------------------------------
# Phase 1: the Rules agent, and the conversation it holds open
# ---------------------------------------------------------------------------

#: The rules are settled when the agent states the player count.  A reply
#: without it is a question, and that is the whole of the "is this the rules or
#: is this a question" decision the UI needs.
PLAYER_COUNT_TAG = re.compile(r"\[PLAYER_COUNT:\s*([1-8])\]", re.IGNORECASE)
GAME_NAME_TAG = re.compile(r"\[GAME_NAME:\s*([^\]\n]+)\]", re.IGNORECASE)

RULES_SYSTEM_INSTRUCTION = (
    "You are the Rules Agent. Your job is to read board game documents and "
    "synthesize them into a crystal clear, comprehensive natural language "
    "rulebook. If there are any edge cases or ambiguities, ask the user "
    "clarifying questions first, one question at a time, and say nothing else "
    "while you are still asking. Also, you MUST detect the requested player "
    "count from the design and, in your final approved rules, state the game's "
    "name and the player count exactly in this format at the very end: "
    "[GAME_NAME: <name>] then [PLAYER_COUNT: X] (capped at 8). Those two tags are "
    "how the rest of the system knows your answer is final."
)

RULES_TEMPERATURE = 0.4

DESIGN_PROMPT = (
    "Here is the game design. Please ask a question if you need one, or output "
    "the restated rules:\n{design}"
)


def parse_player_count(text: str) -> int | None:
    """The player count the agent settled on, or None if it is still asking."""

    match = PLAYER_COUNT_TAG.search(text or "")
    return None if match is None else int(match.group(1))


def parse_game_name(text: str) -> str | None:
    """The name the agent settled on, or None if it did not give one."""

    match = GAME_NAME_TAG.search(text or "")
    return None if match is None else match.group(1).strip()


@dataclass
class RulesConversation:
    """The Rules agent's own chat, held open for as long as the run lasts.

    ``text`` is whatever the agent has said last, which is either the restated
    rules or the question being put to the user.  ``answered`` counts the user's
    own messages, so a caller that has the full answer list can send only the
    ones this conversation has not seen.
    """

    chat: Any
    text: str = ""
    answered: int = 0

    @classmethod
    def open(cls, design: str) -> "RulesConversation":
        chat = open_chat(RULES_MODEL, RULES_SYSTEM_INSTRUCTION, RULES_TEMPERATURE)
        conversation = cls(chat=chat)
        conversation.text = reply(chat, DESIGN_PROMPT.format(design=design))
        return conversation

    def answer(self, message: str) -> str:
        """The user answered, and the agent responds in the same conversation."""

        self.text = reply(self.chat, message)
        self.answered += 1
        return self.text

    @property
    def settled(self) -> bool:
        """The rules hold up, as opposed to this being a question."""

        return parse_player_count(self.text) is not None

    @property
    def game_name(self) -> str | None:
        return parse_game_name(self.text)


# ---------------------------------------------------------------------------
# Phase 2: code generation & loading
# ---------------------------------------------------------------------------

ENGINE_PROMPT = """
Based on the following approved rules, generate a complete Python module to run this game.
The code MUST contain the following functions exactly:

1. `initial_game_state(player_count)`: Returns the starting state dictionary (include hidden info).
    Player IDs are 0-based: 0 through player_count - 1. Use those same IDs everywhere
    (state dict keys, current player, winner, and every player_id argument).
2. `list_valid_moves(game_state, player_id)`: Returns a list of every legal move that
    player can make right now. Do NOT take a candidate move argument.
    Discrete moves are concrete values (for example "action": "fold").
    If a move needs a number, do NOT enumerate every integer. Put a [min, max, step]
    list in that field, where min is inclusive, max is exclusive, and step is the
    spacing between legal values. Example: "action": "raise", "amount": [40, 200, 1].
    A move may contain more than one ranged field.
    Return an empty list if that player cannot act (not their turn, folded, eliminated,
    or the game is already over).
3. `execution_function(game_state, move, player_id)`: Returns the NEW updated game_state.
    `move` is always concrete: every ranged field has already been replaced by one chosen number.
4. `eval_function(game_state)`: Returns the player_id of the winner (0-based), or None if no winner yet.
5. `translation_function(x1, x2, p)`: Takes current game state (x1), another player's previous state (x2), and that player's ID (p, 0-based). It returns a new state for player 'p' that removes hidden info they shouldn't see, but keeps their own hidden info.
6. `get_random_valid_move(game_state, player_id)`: Returns one random legal move.
    Build it from `list_valid_moves` — do not duplicate legality rules.
    If a listed move contains a [min, max, step] field, pick one in-range number
    (min <= n < max and (n - min) % step == 0) instead of returning the range itself.

Output ONLY valid Python code inside a ```python block. No markdown outside the block.

RULES:
{rules}
"""


def generate_engine(rules: str) -> str:
    """The engine the Rules agent wrote, as source text."""

    text = generate(
        RULES_MODEL, ENGINE_PROMPT.format(rules=rules), get_config(temperature=0.0)
    )
    return extract_code(text)


def extract_code(text: str) -> str:
    """The python block out of a reply, which is a failure if there is not one."""

    if "```python" in text:
        code = text.split("```python")[1].split("```")[0]
    elif "```" in text:
        code = text.split("```")[1].split("```")[0]
    else:
        raise BackendUnavailable("the Rules agent returned no code block")
    return code.strip() + "\n"


def load_engine(code: str) -> types.ModuleType:
    """Run the generated source in a module of its own, in memory.

    No file is written and nothing is put on ``sys.path``: the engine exists
    only for the run that generated it, which is the same lifetime the player
    agents have.
    """

    module = types.ModuleType(ENGINE_MODULE_NAME)
    module.__dict__["__file__"] = ENGINE_MODULE_NAME
    try:
        exec(compile(code, ENGINE_MODULE_NAME, "exec"), module.__dict__)
    except Exception as problem:
        raise BackendUnavailable(
            f"the generated engine would not load ({problem})"
        ) from problem
    return module


# ---------------------------------------------------------------------------
# Moves: what an agent is allowed to choose from
# ---------------------------------------------------------------------------


def _is_range_spec(value: Any) -> bool:
    """A compact numeric choice: [min, max, step], min inclusive, max exclusive."""

    return (
        isinstance(value, list)
        and len(value) == 3
        and all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in value)
        and value[2] != 0
    )


def _number_in_range(chosen: Any, spec: Sequence[float]) -> bool:
    """True if chosen is a number on the [min, max, step] lattice."""

    if isinstance(chosen, bool) or not isinstance(chosen, (int, float)):
        return False
    minimum, maximum, step = spec
    if not (minimum <= chosen < maximum):
        return False
    # Integer lattice uses modulo so 40 matches [40, 200, 1] exactly.
    if all(isinstance(n, int) for n in (chosen, minimum, step)):
        return (chosen - minimum) % step == 0
    units = (chosen - minimum) / step
    return abs(units - round(units)) < 1e-9


def move_is_listed(chosen_move: Any, valid_moves: Any) -> bool:
    """True if chosen_move equals a listed move, expanding any [min, max, step] fields."""

    if not isinstance(valid_moves, list):
        return False
    for listed in valid_moves:
        if not isinstance(listed, dict) or not isinstance(chosen_move, dict):
            if listed == chosen_move:
                return True
            continue
        if set(listed.keys()) != set(chosen_move.keys()):
            continue
        if all(
            (
                _is_range_spec(listed_value)
                and _number_in_range(chosen_move[key], listed_value)
            )
            or (not _is_range_spec(listed_value) and listed_value == chosen_move[key])
            for key, listed_value in listed.items()
        ):
            return True
    return False


def format_valid_moves(valid_moves: Any) -> str:
    """Text the player agent must choose from. Ranges stay compact, not expanded."""

    rendered = json.dumps(valid_moves, indent=2, default=str)
    # Compress 3-element numeric lists back to a single line
    rendered = re.sub(
        r"\[\s+([-\d.]+),\s+([-\d.]+),\s+([-\d.]+)\s+\]", r"[\1, \2, \3]", rendered
    )
    return (
        "Valid moves (you must choose one of these exactly; "
        "if a field is [min, max, step], pick one number in that range, "
        "min inclusive and max exclusive, and return that number — not the range):\n"
        f"{rendered}"
    )


# ---------------------------------------------------------------------------
# Phase 3: the player agents, and the game loop they play
# ---------------------------------------------------------------------------

PLAYER_SYSTEM_INSTRUCTION = (
    "You are an AI Player Agent playing a board game. "
    "Try to win the game without breaking the rules. "
    "You will be given the current game state and the valid moves you can make. "
    "You must respond in STRICT JSON format with two keys: "
    "'move' (one of the valid moves; replace any [min, max, step] field with one chosen number), "
    "and 'explanation' (your strategic reasoning)."
)

NO_MOVE = "(no move chosen)"


@dataclass
class Turn:
    """One player's turn: the state they see, and the state behind it.

    The two are not the same.  ``view`` is the player's own translated copy of
    the state, so it is what the prompt shows them; ``state`` is the
    authoritative one, so re-listing the legal moves off a translated view
    cannot hide a move that is in fact legal.
    """

    view: str
    state: Any
    valid_moves: Any


@dataclass
class Misfire:
    """An attempt that was not a legal move, kept because the design agent looks for them."""

    move: Any
    explanation: str


@dataclass
class Decision:
    """The move a player agent settled on, and the attempts behind it."""

    move: Any
    explanation: str
    misfires: list[Misfire] = field(default_factory=list)


@dataclass
class PlayerAgent:
    """One player's share of the swarm.

    Deploying the engine is what instances these: one per player, each holding
    only its own id, and each taking its turn against the generated code.
    """

    player_id: int
    model: str = PLAYER_MODEL

    def act(self, engine: types.ModuleType, rules: str, turn: Turn) -> Decision:
        """Choose a legal move, retrying while the proposals are not one."""

        prompt = (
            f"Rules:\n{rules}\n\n"
            f"Current State:\n{turn.view}\n\n"
            f"{format_valid_moves(turn.valid_moves)}\n\n"
            "What is your move?"
        )
        misfires: list[Misfire] = []

        for _attempt in range(MAX_RETRIES):
            # A model we cannot reach is a lost service and goes straight up to
            # the UI; a reply we cannot read is this agent's own problem and is
            # retried below.
            reply_text = generate(
                self.model,
                prompt,
                get_config(
                    system_instruction=PLAYER_SYSTEM_INSTRUCTION,
                    json_mode=True,
                    temperature=0.7,
                ),
            )
            try:
                data = json.loads(reply_text)
                if not isinstance(data, dict):
                    raise ValueError("expected a json object")
                chosen = data.get("move")
                explanation = data.get("explanation")
            except Exception as problem:
                misfires.append(Misfire(NO_MOVE, f"Unreadable reply: {problem}"))
                prompt += (
                    f"\n\nSystem Error or invalid JSON returned: {problem}. "
                    "Please return valid JSON.\n"
                    f"{format_valid_moves(self._relist(engine, turn))}"
                )
                continue

            if move_is_listed(chosen, turn.valid_moves):
                return Decision(chosen, explanation, misfires)

            misfires.append(Misfire(chosen, explanation))
            prompt += (
                f"\n\nPrevious attempt '{chosen}' failed. You have "
                f"{MAX_RETRIES - len(misfires)} attempts left.\n"
                f"{format_valid_moves(self._relist(engine, turn))}"
            )

        forced = engine.get_random_valid_move(turn.state, self.player_id)
        return Decision(forced, "Forced random move due to invalid attempts.", misfires)

    def _relist(self, engine: types.ModuleType, turn: Turn) -> Any:
        """The legal moves as the engine itself still sees them."""

        return engine.list_valid_moves(turn.state, self.player_id)


def play_game(
    engine: types.ModuleType,
    rules: str,
    players: Sequence[PlayerAgent],
    report: Callable[[dict[str, Any]], None] | None = None,
    stop: threading.Event | None = None,
) -> tuple[list[dict[str, Any]], int | None]:
    """The player agents playing the generated game, and everything they tried.

    Player IDs are 0-based throughout, exactly as the generated engine counts
    them.

    Two things end the game besides somebody winning it: :data:`MAX_PLAY_SECONDS`
    of wall clock, and ``stop`` being set, which is how the manual override on
    ``<StatusPlaying>`` asks for the swarm to stop.  Both are checked between
    player actions and both are counted - a turn in flight is always finished and
    recorded, because dropping it would leave the game state and the log
    disagreeing, which is the one thing a playtest log must never do.  A game cut
    short reports why, so ``None`` for a winner never means "the players
    cooperated to stalemate" by accident.
    """

    player_count = len(players)
    global_state = engine.initial_game_state(player_count)
    player_states = {player.player_id: global_state for player in players}
    artifacts: list[dict[str, Any]] = []
    winner: int | None = None
    deadline = time.monotonic() + MAX_PLAY_SECONDS

    turn_count = 0
    while winner is None and turn_count < MAX_TURNS:
        turn_count += 1
        stopped = False

        for player in players:
            valid_moves = engine.list_valid_moves(global_state, player.player_id)
            if not valid_moves:
                continue

            turn = Turn(
                view=str(player_states[player.player_id]),
                state=global_state,
                valid_moves=valid_moves,
            )
            decision = player.act(engine, rules, turn)

            for misfire in decision.misfires:
                record = {
                    "player": player.player_id,
                    "type": "misfire",
                    "move": misfire.move,
                    "explanation": misfire.explanation,
                    "state": turn.view,
                }
                artifacts.append(record)
                if report is not None:
                    report(record)

            global_state = engine.execution_function(
                global_state, decision.move, player.player_id
            )
            record = {
                "player": player.player_id,
                "type": "turn",
                "turn": turn_count,
                "move": decision.move,
                "explanation": decision.explanation,
                "state": turn.view,
            }
            artifacts.append(record)
            if report is not None:
                report(record)

            for player_id in player_states:
                player_states[player_id] = engine.translation_function(
                    global_state, player_states[player_id], player_id
                )

            winner = engine.eval_function(global_state)
            if winner is not None:
                break

            # Between one player's action and the next, not between rounds: an
            # override pressed part way through a round is answered after the
            # player whose turn it is has been recorded, and the wall clock keeps
            # the same promise.
            if stop is not None and stop.is_set():
                artifacts.append({"player": None, "type": "stop", "reason": "override"})
                stopped = True
                break
            if time.monotonic() >= deadline:
                artifacts.append({"player": None, "type": "stop", "reason": "timeout"})
                stopped = True
                break

        if winner is not None or stopped:
            break

    return artifacts, winner


# ---------------------------------------------------------------------------
# Phase 4: what the playtests say
# ---------------------------------------------------------------------------

ANALYSIS_PROMPT = """
You are the Design Agent, reviewing the playtests of a game you did not design.
The artifacts are the full playtest history: one record per player action, plus a
record of type "misfire" for every attempt that was not a legal move.

Identify:
1. Overpowered or dominant strategies.
2. Any bottlenecks or dead turns where AI agents got stuck (look at "misfire" records).
3. Anything the playtests show about how the game actually feels to play.

Rules the game was written from:
{rules}

Game logs (artifacts):
{artifacts}

Return STRICT JSON: a list of findings, each an object with "title" (short),
"detail" (a sentence or two of evidence from the logs) and "severity", which is
one of "low", "medium" or "high". Return no prose outside the list.
"""

REVIEW_PROMPT = """
You are the Design Agent, giving the designer feedback on their own game.

Review the original design goals, the restated rules, and the findings the
playtests produced. Say what the swarm found in the designer's own rules - the
game was played as written - and give specific, actionable suggestions for new
mechanics, rule tweaks or card modifications to solve the problems found. Finish
by offering to rebuild the engine and run the same batch of games again if they
describe one of the changes.

Original design goals:
{prompt}

Rules:
{rules}

Findings from the playtests:
{analyses}
"""


def analyse_artifacts(rules: str, artifacts: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """The playtest log turned into findings the design agent can quote."""

    reply_text = generate(
        DESIGN_MODEL,
        ANALYSIS_PROMPT.format(
            rules=rules, artifacts=json.dumps(list(artifacts), indent=2, default=str)
        ),
        get_config(json_mode=True, temperature=0.5),
    )
    return normalise_findings(read_json(reply_text))


def design_review(prompt: str, rules: str, analyses: Sequence[dict[str, Any]]) -> str:
    """The long answer the user finally reads."""

    return generate(
        DESIGN_MODEL,
        REVIEW_PROMPT.format(
            prompt=prompt, rules=rules, analyses=json.dumps(list(analyses), indent=2)
        ),
        get_config(temperature=0.5),
    )


def read_json(text: str) -> Any:
    """A json reply, minus the fences a model sometimes leaves on anyway."""

    cleaned = (text or "").strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1]
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
        cleaned = cleaned.rsplit("```", 1)[0]
    try:
        return json.loads(cleaned)
    except Exception as problem:
        raise BackendUnavailable(f"the reply was not json ({problem})") from problem


def normalise_findings(payload: Any) -> list[dict[str, str]]:
    """Findings as the UI reads them: a title, the detail behind it, a severity."""

    if isinstance(payload, dict):
        payload = payload.get("findings", payload.get("analyses", []))
    if not isinstance(payload, list):
        raise BackendUnavailable("the design agent did not return a list of findings")

    findings: list[dict[str, str]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        severity = str(item.get("severity", "medium")).lower()
        findings.append(
            {
                "title": str(item.get("title", "Untitled finding")),
                "detail": str(item.get("detail", "")),
                "severity": severity if severity in SEVERITIES else "medium",
            }
        )
    return findings
