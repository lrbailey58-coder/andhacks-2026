"""The real back end: the Gemini swarm behind the web front end.

:mod:`backend.api` is what the front end calls, and :mod:`backend.swarm` is the
swarm it calls into.  Nothing is imported here on purpose: the app-level switch
imports :mod:`backend.api` when it is off, so a run against the dummy never
loads the Gemini client at all.
"""
