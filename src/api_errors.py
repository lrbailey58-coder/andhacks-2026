"""The error contract between the front end and whichever API is switched on.

``web/dummy_api.py`` and ``backend/api.py`` are two implementations of one
interface, so they have to agree on what "the swarm is not answering" looks
like.  The UI catches that one exception and turns it into a ``<Result>`` of
"failure" plus an offer to try again, so it lives in a leaf module both sides
can import without either depending on the other.

The second half of the module is a short log of the model calls themselves.
"Gemini would not answer" is not a diagnosis: the same sentence covers a key
that was never found, a quota that ran out, a model name that does not exist
and a network that drops.  Each call is written down here as it finishes - model
name, how long it took, and on a failure the status the API gave - and
:func:`web.app.api_diagnostics` hands the log to the page, so a browser console
can say which of those it was.  Nothing here holds a key, a prompt or a reply:
the log is meant to be safe to paste into a chat window.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

#: How many calls are remembered.  A run makes a dozen or so, so this holds a
#: few runs' worth, and the log cannot grow without bound on a server that is
#: left switched on.
LOG_LIMIT = 50

#: How much of a failure is kept.  The exceptions from a model client carry the
#: whole response body, which is mostly noise for the one question being asked,
#: and the console is not the place for a page of it.
PROBLEM_LIMIT = 300


class BackendUnavailable(RuntimeError):
    """The agent swarm lost service, or a model call came back unusable.

    Raised for a client that cannot be built (no API key), a model call that
    fails or times out, and a reply that cannot be parsed into what the caller
    asked for.  The UI survives all three the same way: a cross on the step that
    failed, and the run offered back.
    """


@dataclass(frozen=True)
class Call:
    """One model call, and what became of it.

    ``status`` is the HTTP status the API itself reported, when it reported one:
    ``403`` says the key was refused, ``429`` says the quota is spent, ``404``
    says the model name is wrong, and ``503`` says Gemini is overloaded.  Those
    are four different fixes for a teammate, which is why the number is kept
    rather than flattened into the message.
    """

    when: str
    model: str
    ok: bool
    seconds: float
    status: int | None = None
    problem: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_calls: list[Call] = []
_calls_lock = threading.Lock()


def status_of(problem: BaseException) -> int | None:
    """The HTTP status out of whatever the model client raised, if it had one.

    The Gemini client puts the status on ``.code`` and its own words on
    ``.message``; a timeout from a socket has neither.  Anything that is not a
    plain integer is ignored rather than guessed at, because a number in the
    console has to mean one thing.
    """

    for name in ("code", "status"):
        value = getattr(problem, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def message_of(problem: BaseException) -> str | None:
    """The API's own explanation, when it gave one, over its exception text.

    The client quotes the service's message ("API key not valid") in
    ``.message`` and leaves a repr of the response in ``str()``, so the useful
    half is the attribute.  Absent, the exception text stands in.
    """

    said = getattr(problem, "message", None)
    if isinstance(said, str) and said.strip():
        return _clamp(said)
    return _clamp(str(problem))


def _clamp(text: str) -> str:
    """Shortened to what a console can be asked to read."""

    text = " ".join(text.split())
    return text if len(text) <= PROBLEM_LIMIT else text[: PROBLEM_LIMIT - 1] + "…"


def record(
    model: str,
    ok: bool,
    seconds: float,
    problem: BaseException | None = None,
) -> Call:
    """Write one finished call down, and hand the entry back."""

    entry = Call(
        when=datetime.now(timezone.utc).strftime("%H:%M:%S"),
        model=model,
        ok=ok,
        seconds=round(seconds, 3),
        status=None if problem is None else status_of(problem),
        problem=None if problem is None else message_of(problem),
    )
    with _calls_lock:
        _calls.append(entry)
        # Only the newest calls earn their keep: a failure is only interesting
        # next to what the run did after it.
        del _calls[: max(0, len(_calls) - LOG_LIMIT)]
    return entry


def calls() -> tuple[Call, ...]:
    """The log, oldest first."""

    with _calls_lock:
        return tuple(_calls)


def failures() -> tuple[Call, ...]:
    """Just the calls that did not answer, oldest first."""

    return tuple(call for call in calls() if not call.ok)


def reset() -> None:
    """Empty the log, so one test's failures are not the next one's evidence."""

    with _calls_lock:
        _calls.clear()
