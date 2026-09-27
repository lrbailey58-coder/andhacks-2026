# Goal
Connect front end code in web/ with upgraded backend code, moved from ../game_tester_swarm.py
into backend/

# Maintaining front end tests
The front end has an array of unit tests in tests/ that relies on web/dummy_api.py,
which has until now been standing in for the actual api. The front end needs a
"switch" at the app level, not visible to the user, that can be toggled on when testing 
to continue using the dummy api for testing purposes, and off for normal execution.

When the switch is off, the app will instead use a new api in backend/

# Backend upgrades
The current code in ../game_tester_swarm.py cannot be blindly copied over,
though that's a good place to start. That current file doesn't expose critical
conversation contexts- like the conversation with the rules agent that allows
for asking and answering questions.

For any clarification on how the app should behave from the perspective of the
user, refer to "web/ui instructions.md".

# Testing
Unlike with the dummy api, unit tests that interact with the gemini api would
be too expensive for our project. Instead only test the backend using MagicMock
objects that don't actual cause prompt generation. ../test_gemini.py is a
working test file of ../game_tester_swarm.py, but this change will require new
tests in the tests/ folder.