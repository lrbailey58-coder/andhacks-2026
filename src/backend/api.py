"""The production back end: the swarm behind the same shape as the dummy.

``web/dummy_api.py`` is a deterministic stand-in for this module, and
:mod:`web.pipeline` calls whichever one the app-level switch selects.  The two
therefore have to be interchangeable, so the six functions here carry the same
names, the same arguments and the same answer types as the dummy's:

===============================  ==========================================
``interpret_rules(prompt, ...)``  What the Rules agent made of the design, or
                                  the question it is asking about it.
``generate_code(rules)``          The engine, as source text.
``deploy_instances(rules, code)`` One player agent per player, against the
                                  engine.
``play_sample_game(rules, dep)``  The agents playing, with their reasoning.
``collect_analyses(rules, ...)``  The playtest log turned into findings.
``design_feedback(prompt, ...)``  The Design agent's long answer.
===============================  ==========================================

The one argument the dummy does not have is ``run``, the identity of the run
doing the asking.  The Rules agent's conversation is a real chat session that
has to stay open between the question and the answer to it, so the backend
holds one per run and ``run`` says which.  Anything that goes wrong on the way
to a model arrives as :class:`~api_errors.BackendUnavailable`, which is what
the UI draws as a failure.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

import backend.swarm as swarm

# The one exception the UI knows how to draw, imported rather than defined, the
# same way the dummy does it, so flipping the switch is invisible above.
from api_errors import BackendUnavailable  # noqa: F401

#: How many rules conversations are kept open.  One per run under way, and the
#: oldest goes when there are more than this, so a long session cannot pile them
#: up.  Tests reset it with :func:`reset`.
MAX_LIVE_CONVERSATIONS = 32

#: What a game says when the manual override ended it.  A game that was cut
#: short is not a draw, and the Design agent is going to read this line.
OVERRIDE_OUTCOME = "Stopped by the user before the game finished."

_CONVERSATIONS: dict[Any, swarm.RulesConversation] = {}
_CONVERSATION_LOCK = threading.Lock()


def reset() -> None:
    """Put the backend back to how it started."""

    with _CONVERSATION_LOCK:
        _CONVERSATIONS.clear()
    swarm.reset_client()


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------


@dataclass
class Interpretation:
    """What the Rules agent made of the user's words."""

    rules: str = ""
    game_name: str = ""
    player_count: int = 2
    needs_clarification: bool = False
    question: str = ""


@dataclass
class Deployment:
    """The player agents, and the engine they are all talking to.

    ``engine`` is the loaded module rather than its source, because deployment
    is the step that makes the generated code something the agents can play.
    """

    instances: int = 0
    urls: list[str] = field(default_factory=list)
    engine: Any = None
    players: list[swarm.PlayerAgent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The functions flask calls
# ---------------------------------------------------------------------------


def interpret_rules(
    prompt: str, answers: Sequence[str] = (), run: Any = None
) -> Interpretation:
    """The Rules agent reads the design and either restates it or asks.

    ``answers`` is everything the user has said back so far, and ``run`` is
    which run is asking.  A run the backend has not seen starts a conversation
    and sends the design; a run it has seen continues that same conversation with
    only the answers it has not had, so the agent keeps everything it was told
    before.  The rules hold up once the agent states the player count; until
    then what it said is the question.
    """

    conversation = _conversation_for(run)
    if conversation is None:
        conversation = swarm.RulesConversation.open(prompt)
        if run is not None:
            _remember(run, conversation)

    for answer in list(answers)[conversation.answered:]:
        conversation.answer(answer)

    text = conversation.text
    if not conversation.settled:
        return Interpretation(needs_clarification=True, question=text)

    player_count = swarm.parse_player_count(text) or 2
    return Interpretation(
        rules=text,
        game_name=conversation.game_name or "",
        player_count=player_count,
    )


def generate_code(rules: str) -> str:
    """The engine the Rules agent wrote, as source text."""

    return swarm.generate_engine(rules)


def deploy_instances(rules: str, code: str) -> Deployment:
    """Load the generated code, then instance one player agent per player.

    That is all deploying is here: the agents interact with the engine as it
    was generated, so there is nothing to copy and nothing to write down.
    """

    player_count = swarm.parse_player_count(rules) or 2
    engine = swarm.load_engine(code)
    players = [swarm.PlayerAgent(player_id) for player_id in range(player_count)]
    return Deployment(
        instances=player_count,
        urls=[
        f"https://sandbox.local/game/{index}" for index in range(1, player_count + 1)
    ],
        engine=engine,
        players=players,
    )


def play_sample_game(
    rules: str,
    deployment: Deployment,
    report: Callable[[dict[str, Any]], None] | None = None,
    stop: threading.Event | None = None,
) -> list[dict[str, Any]]:
    """The player agent swarm playing itself, with the reasoning it used.

    The playtest history the swarm records - one entry per action, plus one per
    attempt that was not legal - is the transcript the UI shows, with the engine
    its 0-based player ids renumbered the way a reader counts players.

    ``report`` is handed each record as it is played and ``stop`` ends the game
    between player actions, which is how the ``<ShowSample>`` block can show the
    game while ``<StatusPlaying>`` is still running, and how the manual override
    ends it early.  The full list is still returned, so a caller that does not
    stream gets exactly what it always did.
    """

    def publish(artifact: dict[str, Any]) -> None:
        if report is None:
            return
        for row in _rows([artifact], deployment.instances)[0]:
            report(row)

    artifacts, winner = swarm.play_game(
        deployment.engine,
        rules,
        deployment.players,
        report=publish,
        stop=stop,
    )
    return transcript(artifacts, winner, deployment.instances)


def collect_analyses(
    rules: str, sample: Sequence[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Gameplay data turned into findings by the analysis pass."""

    return swarm.analyse_artifacts(rules, _as_artifacts(sample))


def design_feedback(
    prompt: str, rules: str, analyses: Sequence[dict[str, Any]]
) -> str:
    """The Design agent's answer: feedback on the player's design."""

    return swarm.design_review(prompt, rules, analyses)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _conversation_for(run: Any) -> swarm.RulesConversation | None:
    """The conversation already open for a run, if there is one.

    The lock is only ever held around the map, never around a model call: the
    app serves requests on threads, and the reply that may take a minute is
    allowed to take it.
    """

    if run is None:
        return None
    with _CONVERSATION_LOCK:
        return _CONVERSATIONS.get(run)


def _remember(run: Any, conversation: swarm.RulesConversation) -> None:
    """Keep a run's conversation, dropping the oldest when there are too many."""

    with _CONVERSATION_LOCK:
        if run in _CONVERSATIONS:
            del _CONVERSATIONS[run]
        elif len(_CONVERSATIONS) >= MAX_LIVE_CONVERSATIONS:
            oldest = next(iter(_CONVERSATIONS))
            del _CONVERSATIONS[oldest]
        _CONVERSATIONS[run] = conversation


def _as_artifacts(sample: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """The transcript, back in the shape the swarm records playtests in.

    A misfire is an attempt that was not a legal move, and the analysis pass
    looks for exactly those, so the flag has to survive the trip.
    """

    artifacts: list[dict[str, Any]] = []
    for record in sample:
        artifacts.append(
            {
                "player": record["player"],
                "type": "misfire" if record.get("illegal") else "turn",
                "move": record.get("move"),
                "explanation": record.get("explanation", ""),
                "state": record.get("state", ""),
            }
        )
    return artifacts


def _rows(
    artifacts: Sequence[dict[str, Any]], player_count: int
) -> tuple[list[dict[str, Any]], str]:
    """One reader-facing row per player action, and why the game stopped.

    Player ids arrive 0-based from the engine and leave 1-based, the way the
    rest of the sample games read.  A stop marker is not an action and does not
    become a row: it is the reason the game ended early, and it goes on the last
    real row instead.
    """

    records: list[dict[str, Any]] = []
    stop_reason = ""
    for position, artifact in enumerate(artifacts, start=1):
        if artifact.get("type") == "stop":
            stop_reason = str(artifact.get("reason") or "")
            continue
        record = {
            "turn": position,
            "player": artifact["player"] + 1,
            "move": artifact["move"],
            "explanation": artifact["explanation"] or "",
        }
        if artifact.get("type") == "misfire":
            record["illegal"] = True
        records.append(record)
    return records, stop_reason


def _ending(
    records: list[dict[str, Any]],
    stop_reason: str,
    winner: int | None,
    player_count: int,
) -> None:
    """The last row of a game says how the game ended.

    A game that was cut short says so.  It did not finish, nobody won it, and
    writing it up as a draw would put a fact in the Design agent's lap that the
    playtest never showed.
    """

    if not records:
        return
    if stop_reason == "override":
        records[-1]["outcome"] = OVERRIDE_OUTCOME
    elif stop_reason == "timeout":
        records[-1]["outcome"] = (
            f"Playtest cut short after {swarm.MAX_PLAY_SECONDS:.0f}s and "
            f"{len(records)} turns, so the game never finished."
        )
    elif winner is None:
        records[-1]["outcome"] = (
            f"No winner after {len(records)} turns, so the game is a draw."
        )
    else:
        records[-1]["outcome"] = (
            f"Player {winner + 1} wins on turn {len(records)} of {player_count}."
        )


def transcript(
    artifacts: Sequence[dict[str, Any]], winner: int | None, player_count: int
) -> list[dict[str, Any]]:
    """The playtest history as the rows of a sample game.

    The outcome is the last row, because the transcript is the story of how the
    game ended.
    """

    records, stop_reason = _rows(artifacts, player_count)
    _ending(records, stop_reason, winner, player_count)
    return records
