"""The error contract between the front end and whichever API is switched on.

``web/dummy_api.py`` and ``backend/api.py`` are two implementations of one
interface, so they have to agree on what "the swarm is not answering" looks
like.  The UI catches that one exception and turns it into a ``<Result>`` of
"failure" plus an offer to try again, so it lives in a leaf module both sides
can import without either depending on the other.
"""


class BackendUnavailable(RuntimeError):
    """The agent swarm lost service, or a model call came back unusable.

    Raised for a client that cannot be built (no API key), a model call that
    fails or times out, and a reply that cannot be parsed into what the caller
    asked for.  The UI survives all three the same way: a cross on the step that
    failed, and the run offered back.
    """
