"""Dummy API.

The production back end is "an assortment of Python scripts connecting to the
Gemini API ... a bunch of exposed python functions being called by flask", so
this module is shaped like that: a handful of module level functions, plain
dataclasses for the answers, and a :class:`BackendUnavailable` exception for
the loss-of-service case the UI has to survive.  Nothing here is a route, a
JSON schema, or a database.

The functions are deterministic and scenario driven, so the flows in
"ui instructions.md" can be exercised end to end:

``complete``
    A well specified game.  Interpreting, coding, deploying, playing and
    collecting all succeed with no question.
``vague``
    A poorly specified game.  The Rules agent cannot finish interpreting, so it
    asks a question and the interpreting ``<Result>`` is a failure.
``unstable``
    The swarm loses service part way through, so a ``<Result>`` is a failure
    that was not a question.

Scenarios are chosen from keywords in the prompt, with ``complete`` as the
default, and ``BASE_LATENCY``/``GAME`` can be overridden in tests.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

# Set by tests to make the run instant and deterministic.
BASE_LATENCY = float(os.environ.get("HACKS_UI_LATENCY", "0"))

SCENARIO_COMPLETE = "complete"
SCENARIO_VAGUE = "vague"
SCENARIO_UNSTABLE = "unstable"

_VAGUE_MARKERS = (
    "vague",
    "something like",
    "something fun",
    "you decide",
    "you pick",
    "i don't know",
    "i dont know",
    "idk",
    "no idea",
    "unsure",
    "whatever",
    "surprise me",
    "just a game",
    "a fun game",
    "make it up",
    "not sure",
)

_UNSTABLE_MARKERS = (
    "flaky",
    "unstable",
    "lose service",
    "loss of service",
    "crash",
    "outage",
    "chaos",
    "agent down",
    "timeout",
)

_KNOWN_GAMES = (
    ("tic tac toe", "Tic-Tac-Toe", "code_tictactoe"),
    ("noughts", "Noughts and Crosses", "code_tictactoe"),
    ("nim", "Nim", "code_nim"),
    ("connect four", "Connect Four", "code_tictactoe"),
    ("chess", "Chess", "code_tictactoe"),
)


class BackendUnavailable(RuntimeError):
    """The agent swarm lost service, or a model call came back unusable."""


# The simulated outage fires once, so a retry from the "What's next?" editor
# gets through.  Cleared by reset().
_OUTAGE_REPORTED = False


def reset() -> None:
    """Put the dummy back to how it started."""

    global _OUTAGE_REPORTED
    _OUTAGE_REPORTED = False
    _pause.__defaults__ = (BASE_LATENCY,)


@dataclass
class Interpretation:
    """What the Rules agent made of the user's words."""

    rules: str = ""
    game_name: str = ""
    player_count: int = 2
    needs_clarification: bool = False
    question: str = ""
    scenario: str = SCENARIO_COMPLETE


@dataclass
class Deployment:
    instances: int = 0
    urls: list[str] = field(default_factory=list)
    engine: str = ""


# ---------------------------------------------------------------------------
# Scenario selection
# ---------------------------------------------------------------------------


def scenario_for(prompt: str) -> str:
    text = prompt.lower()
    if any(marker in text for marker in _UNSTABLE_MARKERS):
        return SCENARIO_UNSTABLE
    if any(marker in text for marker in _VAGUE_MARKERS):
        return SCENARIO_VAGUE
    return SCENARIO_COMPLETE


def game_for(prompt: str) -> tuple[str, str]:
    text = prompt.lower()
    for marker, name, code_key in _KNOWN_GAMES:
        if marker in text:
            return name, code_key
    return "Skirmish", "code_nim"


def _pause(seconds: float) -> None:
    if BASE_LATENCY and seconds:
        time.sleep(BASE_LATENCY * seconds)


# ---------------------------------------------------------------------------
# The functions flask calls
# ---------------------------------------------------------------------------


def interpret_rules(prompt: str, answers: Sequence[str] = ()) -> Interpretation:
    """The Rules agent reads the design and either restates it or asks."""

    _pause(1.2)
    scenario = scenario_for(prompt)
    name, _code_key = game_for(prompt)

    if answers:
        # The user answered the question, so interpretation can finish.
        return Interpretation(
            rules=_restate(prompt, name, answers[-1]),
            game_name=name,
            player_count=4 if "four" in answers[-1].lower() else 2,
            scenario=scenario,
        )

    if scenario == SCENARIO_VAGUE:
        return Interpretation(
            needs_clarification=True,
            question=(
                "Before I can write rules, two things are missing: how many "
                "players are there, and what is a legal turn for a player? I "
                "can read the theme, but not the moves."
            ),
            game_name=name,
        )

    return Interpretation(
        rules=_restate(prompt, name, ""),
        game_name=name,
        player_count=2,
        scenario=scenario,
    )


def generate_code(rules: str) -> str:
    """The engine the Rules agent wrote, as source text."""

    _pause(2.5)
    _, code_key = game_for(rules)
    return CODE[code_key]


def deploy_instances(rules: str, code: str) -> Deployment:
    """Spin up one sandbox per player, then confirm the engine imports."""

    global _OUTAGE_REPORTED
    _pause(1.4)
    if scenario_for(rules) == SCENARIO_UNSTABLE and not _OUTAGE_REPORTED:
        # The outage is transient: the retry gets through.
        _OUTAGE_REPORTED = True
        raise BackendUnavailable("two of the sandbox instances never came up")
    instances = 4 if "four players" in rules.lower() else 2
    return Deployment(
        instances=instances,
        urls=[f"https://sandbox.local/game/{index}" for index in range(1, instances + 1)],
        engine=code,
    )


def play_sample_game(rules: str, deployment: Deployment) -> list[dict[str, Any]]:
    """The player agent swarm playing itself, with the reasoning it used."""

    _pause(3.0)
    return list(SAMPLE_GAMES["tictactoe" if deployment.instances <= 2 else "nim"])


def collect_analyses(
    rules: str, sample: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Gameplay data turned into findings by the analysis pass."""

    _pause(1.8)
    misfires = sum(1 for record in sample if record.get("illegal"))
    return [
        {
            "title": "Opening move is solved",
            "detail": (
                "The centre was taken on turn 1 in every game, so the two corner "
                "openings were never explored."
            ),
            "severity": "medium",
        },
        {
            "title": "Dead turns",
            "detail": f"{misfires} attempt(s) were rejected as illegal before a "
            "fallback move was forced.",
            "severity": "low" if misfires < 2 else "high",
        },
        {
            "title": "Length",
            "detail": "Games ended on turn 5 or 6, short of the 12 turn target "
            "in the design.",
            "severity": "high",
        },
    ]


def design_feedback(
    prompt: str, rules: str, analyses: Sequence[dict[str, Any]]
) -> str:
    """The Design agent's answer: feedback on the player's design."""

    _pause(2.2)
    name, _ = game_for(prompt)
    lines = [
        f"Here is what the playtests say about {name}.",
        "",
        "The rules you gave were followed exactly, so what you are looking at "
        "is your design rather than a misread of it. Three things stand out.",
        "",
    ]
    for index, analysis in enumerate(analyses, start=1):
        lines.append(f"{index}. {analysis['title']} ({analysis['severity']}). "
                     f"{analysis['detail']}")
    lines += [
        "",
        "The swarm also leaned on one strategy almost every game: take the "
        "largest legal move first and trade spaces for tempo. That is fine as "
        "a baseline, but it means the interesting decisions are all on the "
        "second move of a turn, and the first move is not costing anyone "
        "anything.",
        "",
        "Worth trying next:",
        "- Charge a cost for the greedy move, or make the biggest move reveal "
        "less information, so the opening is not free.",
        "- Add one card or tile that punishes repeating the same shape, to "
        "break the mirror games that show up late.",
        "- Cut the target length to about 8 turns, or add a second scoring "
        "phase, so the pacing matches what the playtests produced.",
        "",
        "If you want, describe one of those changes and the swarm will rebuild "
        "the engine and run the same batch of games again so we can compare.",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Canned content
# ---------------------------------------------------------------------------


def _restate(prompt: str, name: str, answer: str) -> str:
    detail = f" The player clarified: {answer.strip()}" if answer.strip() else ""
    return (
        f"{name}, restated from the designer's description. "
        f"Two players alternate turns; a turn is one legal action; the first "
        f"player who satisfies the win condition takes the game."
        f"{detail} Original intent: {prompt.strip()[:280]}"
    )


CODE: dict[str, str] = {}


CODE["code_nim"] = '''"""Nim - generated by the rules agent from the designer's description.

The game tester swarm expects exactly these six functions.
"""

from __future__ import annotations

import random
from typing import Any

GameState = dict[str, Any]

START_HEAPS = (3, 4, 5)


def initial_game_state(player_count: int) -> GameState:
    """Three heaps of stones. Whoever clears the last stone wins."""
    return {
        "heaps": list(START_HEAPS),
        "turn": 1,
        "players": player_count,
        "moves": [],
        "notes": {},
    }


def rule_function(game_state: GameState, move: Any, player_id: int) -> bool:
    """A move is (heap, stones): take between one and all of a heap."""
    if game_state["turn"] != player_id:
        return False
    if not isinstance(move, (list, tuple)) or len(move) != 2:
        return False
    heap, stones = move
    if not isinstance(heap, int) or not isinstance(stones, int):
        return False
    if heap < 0 or heap >= len(game_state["heaps"]):
        return False
    return 1 <= stones <= game_state["heaps"][heap]


def execution_function(game_state: GameState, move: Any, player_id: int) -> GameState:
    if not rule_function(game_state, move, player_id):
        return game_state
    heap, stones = move
    heaps = list(game_state["heaps"])
    heaps[heap] -= stones
    new_state: GameState = {
        "heaps": heaps,
        "turn": player_id + 1 if player_id < game_state["players"] else 1,
        "players": game_state["players"],
        "moves": game_state["moves"] + [{"player": player_id, "move": [heap, stones]}],
        "notes": dict(game_state["notes"]),
    }
    new_state["notes"][player_id] = f"took {stones} from heap {heap}"
    return new_state


def eval_function(game_state: GameState) -> int | None:
    """The player who emptied the board, or None while stones remain."""
    if any(game_state["heaps"]):
        return None
    last = game_state["moves"][-1]["player"] if game_state["moves"] else 1
    return last


def translation_function(
    x1: GameState, x2: GameState, p: int
) -> GameState:
    """Nim has no hidden information, so a player keeps their own notes only."""
    view = {
        "heaps": list(x1["heaps"]),
        "turn": x1["turn"],
        "players": x1["players"],
        "moves": [record for record in x1["moves"] if record["player"] != p or True],
        "notes": {p: x2["notes"].get(p, "")},
    }
    return view


def get_random_valid_move(game_state: GameState, player_id: int) -> Any:
    """Fallback when the player agent keeps proposing illegal moves."""
    options = [
        (heap, stones)
        for heap, size in enumerate(game_state["heaps"])
        for stones in range(1, size + 1)
    ]
    return random.choice(list(options))
'''


CODE["code_tictactoe"] = '''"""Tic-tac-toe - generated by the rules agent from the designer's description.

The game tester swarm expects exactly these six functions.
"""

from __future__ import annotations

import random
from typing import Any

GameState = dict[str, Any]

BOARD = 3
LINES = (
    ((0, 0), (0, 1), (0, 2)),
    ((1, 0), (1, 1), (1, 2)),
    ((2, 0), (2, 1), (2, 2)),
    ((0, 0), (1, 0), (2, 0)),
    ((0, 1), (1, 1), (2, 1)),
    ((0, 2), (1, 2), (2, 2)),
    ((0, 0), (1, 1), (2, 2)),
    ((0, 2), (1, 1), (2, 0)),
)


def initial_game_state(player_count: int) -> GameState:
    """An empty board. X always moves first."""
    return {
        "board": [[None] * BOARD for _ in range(BOARD)],
        "marks": {1: "X", 2: "O"},
        "turn": 1,
        "players": player_count,
        "moves": [],
        "plans": {},
    }


def rule_function(game_state: GameState, move: Any, player_id: int) -> bool:
    """A move is a (row, column) pair on an empty square, on your turn."""
    if game_state["turn"] != player_id:
        return False
    if not isinstance(move, (list, tuple)) or len(move) != 2:
        return False
    row, column = move
    if not isinstance(row, int) or not isinstance(column, int):
        return False
    if not (0 <= row < BOARD and 0 <= column < BOARD):
        return False
    return game_state["board"][row][column] is None


def execution_function(game_state: GameState, move: Any, player_id: int) -> GameState:
    if not rule_function(game_state, move, player_id):
        return game_state
    row, column = move
    board = [list(line) for line in game_state["board"]]
    board[row][column] = game_state["marks"][player_id]
    return {
        "board": board,
        "marks": dict(game_state["marks"]),
        "turn": 2 if player_id == 1 else 1,
        "players": game_state["players"],
        "moves": game_state["moves"] + [{"player": player_id, "move": [row, column]}],
        "plans": dict(game_state["plans"]),
    }


def eval_function(game_state: GameState) -> int | None:
    """The first player with three in a row, or None."""
    board = game_state["board"]
    for line in LINES:
        values = [board[row][column] for row, column in line]
        if values[0] is not None and values[0] == values[1] == values[2]:
            for player, mark in game_state["marks"].items():
                if mark == values[0]:
                    return player
    return None


def translation_function(x1: GameState, x2: GameState, p: int) -> GameState:
    """Both marks are public, so a player only keeps their own private plan."""
    return {
        "board": [list(line) for line in x1["board"]],
        "marks": dict(x1["marks"]),
        "turn": x1["turn"],
        "players": x1["players"],
        "moves": list(x1["moves"]),
        "plans": {p: x2["plans"].get(p, "")},
    }


def get_random_valid_move(game_state: GameState, player_id: int) -> Any:
    """Fallback when the player agent keeps proposing illegal moves."""
    squares = [
        (row, column)
        for row in range(BOARD)
        for column in range(BOARD)
        if game_state["board"][row][column] is None
    ]
    return random.choice(squares)
'''


SAMPLE_GAMES: dict[str, list[dict[str, Any]]] = {
    "tictactoe": [
        {
            "turn": 1,
            "player": 1,
            "move": "(1, 1)",
            "explanation": "Centre first: it is the only square that opens four "
            "lines instead of three, and it cannot be wasted on a corner block.",
        },
        {
            "turn": 2,
            "player": 2,
            "move": "(0, 0)",
            "explanation": "A corner keeps two of my own lines alive. Taking an "
            "edge would hand the centre's owner a cheap fork next turn.",
        },
        {
            "turn": 3,
            "player": 1,
            "move": "(0, 0)",
            "illegal": True,
            "explanation": "I tried to block the corner I had just given away; "
            "the rules agent rejected it because the square was mine.",
        },
        {
            "turn": 3,
            "player": 1,
            "move": "(2, 0)",
            "explanation": "Forced fallback. I block the remaining corner so the "
            "opposite diagonal is closed, and I keep a row open on the right.",
        },
        {
            "turn": 4,
            "player": 2,
            "move": "(2, 2)",
            "explanation": "Counter play in the far corner. This threatens the "
            "anti-diagonal, which the centre already guards, so it is safe and "
            "it denies me nothing.",
        },
        {
            "turn": 5,
            "player": 1,
            "move": "(0, 2)",
            "explanation": "Top right completes the top row. The game is over "
            "in five moves, well short of the length the design aimed for.",
            "outcome": "Player 1 wins on turn 5.",
        },
    ],
    "nim": [
        {
            "turn": 1,
            "player": 1,
            "move": "(heap 2, 5 stones)",
            "explanation": "Empty the largest heap outright. It is the greediest "
            "move available and it also sets up the mirror strategy I want.",
        },
        {
            "turn": 2,
            "player": 2,
            "move": "(heap 1, 4 stones)",
            "explanation": "Match the sweep. Clearing a whole heap leaves the "
            "opponent a single obvious reply, which is what I want them to take.",
        },
        {
            "turn": 3,
            "player": 1,
            "move": "(heap 0, 3 stones)",
            "explanation": "Same pattern on the last heap. Both of us are "
            "playing the same line, which is exactly the mirror pattern the "
            "design was supposed to discourage.",
            "outcome": "Player 2 takes the last stone and wins on turn 4.",
        },
    ],
}
