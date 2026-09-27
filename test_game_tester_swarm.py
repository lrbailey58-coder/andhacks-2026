import os
import sys
import json
import unittest
from unittest.mock import patch, MagicMock, mock_open

# Set a dummy API key before importing swarm so top-level initialization doesn't fail
os.environ.setdefault("GEMINI_API_KEY", "dummy_test_api_key_12345")

import game_tester_swarm as swarm


class TestHelperFunctions(unittest.TestCase):
    """Tests for standalone utility and formatting helpers."""

    # ---------------------------------------------------------------------------
    # Tests for _is_range_spec
    # ---------------------------------------------------------------------------
    def test_is_range_spec_intended(self):
        """Intended path: valid integer and float range specs [min, max, step]."""
        self.assertTrue(swarm._is_range_spec([40, 200, 1]))
        self.assertTrue(swarm._is_range_spec([0.0, 1.0, 0.1]))
        self.assertTrue(swarm._is_range_spec([-10, 10, 2]))

    def test_is_range_spec_unintended(self):
        """Unintended path: invalid types, wrong length, zero step, boolean values."""
        # Booleans in Python are instances of int, but _is_range_spec explicitly rejects them
        self.assertFalse(swarm._is_range_spec([True, 10, 1]))
        self.assertFalse(swarm._is_range_spec([0, 10, 0]))  # Step is zero
        self.assertFalse(swarm._is_range_spec([1, 10]))  # Length != 3
        self.assertFalse(swarm._is_range_spec([1, 10, 2, 5]))  # Length != 3
        self.assertFalse(swarm._is_range_spec("not a list"))
        self.assertFalse(swarm._is_range_spec([1, "10", 2]))  # Non-numeric element

    # ---------------------------------------------------------------------------
    # Tests for _number_in_range
    # ---------------------------------------------------------------------------
    def test_number_in_range_intended(self):
        """Intended path: numbers lying on the [min, max, step] lattice."""
        spec = [40, 200, 1]
        self.assertTrue(swarm._number_in_range(40, spec))  # Min inclusive
        self.assertTrue(swarm._number_in_range(100, spec))
        self.assertTrue(swarm._number_in_range(199, spec))

        float_spec = [0.0, 1.0, 0.2]
        self.assertTrue(swarm._number_in_range(0.4, float_spec))

    def test_number_in_range_unintended(self):
        """Unintended path: out of bounds, off-lattice values, wrong types, booleans."""
        spec = [40, 200, 2]
        self.assertFalse(swarm._number_in_range(200, spec))  # Max exclusive
        self.assertFalse(swarm._number_in_range(39, spec))  # Below min
        self.assertFalse(swarm._number_in_range(41, spec))  # Off lattice (step is 2)
        self.assertFalse(swarm._number_in_range(True, spec))  # Booleans rejected
        self.assertFalse(swarm._number_in_range("40", spec))  # String input

    # ---------------------------------------------------------------------------
    # Tests for move_is_listed
    # ---------------------------------------------------------------------------
    def test_move_is_listed_intended(self):
        """Intended path: matching exact move or concrete move inside ranged move."""
        # Exact string/primitive match
        self.assertTrue(swarm.move_is_listed("fold", ["fold", "check"]))

        # Exact dictionary match
        valid_moves = [{"action": "fold"}, {"action": "call"}]
        self.assertTrue(swarm.move_is_listed({"action": "fold"}, valid_moves))

        # Range expansion match
        ranged_valid_moves = [{"action": "raise", "amount": [40, 200, 1]}]
        chosen_move = {"action": "raise", "amount": 50}
        self.assertTrue(swarm.move_is_listed(chosen_move, ranged_valid_moves))

    def test_move_is_listed_unintended(self):
        """Unintended path: invalid inputs, key mismatches, out-of-range values."""
        # Non-list valid_moves
        self.assertFalse(swarm.move_is_listed({"action": "fold"}, None))

        # Out-of-range value
        ranged_valid_moves = [{"action": "raise", "amount": [40, 200, 1]}]
        self.assertFalse(swarm.move_is_listed({"action": "raise", "amount": 250}, ranged_valid_moves))

        # Missing or extra dictionary key
        self.assertFalse(swarm.move_is_listed({"action": "raise"}, ranged_valid_moves))
        self.assertFalse(swarm.move_is_listed({"action": "raise", "amount": 50, "extra": True}, ranged_valid_moves))

    # ---------------------------------------------------------------------------
    # Tests for format_valid_moves and get_config
    # ---------------------------------------------------------------------------
    def test_format_valid_moves(self):
        """Intended path: returns readable string representation with guidance."""
        valid_moves = [{"action": "raise", "amount": [40, 200, 1]}]
        formatted = swarm.format_valid_moves(valid_moves)
        self.assertIn("Valid moves", formatted)
        self.assertIn("[40, 200, 1]", formatted)

    def test_get_config(self):
        """Intended path: verifies configuration object construction."""
        cfg = swarm.get_config(system_instruction="Test Instruction", json_mode=True, temperature=0.5)
        self.assertEqual(cfg.system_instruction, "Test Instruction")
        self.assertEqual(cfg.response_mime_type, "application/json")
        self.assertEqual(cfg.temperature, 0.5)
        self.assertTrue(cfg.automatic_function_calling.disable)


class TestFileAndEngineFunctions(unittest.TestCase):
    """Tests for file loading and code generation functions."""

    # ---------------------------------------------------------------------------
    # Tests for load_input_files
    # ---------------------------------------------------------------------------
    @patch("glob.glob")
    @patch("builtins.open", new_callable=mock_open, read_data="Rulebook content here.")
    def test_load_input_files_intended(self, mock_file, mock_glob):
        """Intended path: successfully reads text/markdown files in input directory."""
        mock_glob.return_value = ["input_files/rules.txt"]
        content = swarm.load_input_files()
        self.assertIn("--- FILE: rules.txt ---", content)
        self.assertIn("Rulebook content here.", content)

    @patch("glob.glob", return_value=[])
    def test_load_input_files_no_files_unintended(self, mock_glob):
        """Unintended path: exits gracefully when input_files directory is empty."""
        with patch("sys.exit") as mock_exit:
            swarm.load_input_files()
            mock_exit.assert_called_once_with(1)

    @patch("glob.glob", return_value=["input_files/corrupt.txt"])
    @patch("builtins.open", side_effect=IOError("Permission denied"))
    def test_load_input_files_read_error_unintended(self, mock_file, mock_glob):
        """Unintended path: catches file read errors without crashing."""
        content = swarm.load_input_files()
        self.assertEqual(content, "")

    # ---------------------------------------------------------------------------
    # Tests for generate_game_engine
    # ---------------------------------------------------------------------------
    @patch("game_tester_swarm.client.models.generate_content")
    @patch("builtins.open", new_callable=mock_open)
    @patch("importlib.util.spec_from_file_location")
    @patch("importlib.util.module_from_spec")
    def test_generate_game_engine_intended(self, mock_mod_from_spec, mock_spec_from_loc, mock_file, mock_generate):
        """Intended path: extracts Python code from response, saves file, and dynamic loads module."""
        fake_python_code = "def initial_game_state(p): return {}\n"
        mock_response = MagicMock()
        mock_response.text = f"```python\n{fake_python_code}```"
        mock_generate.return_value = mock_response

        mock_spec = MagicMock()
        mock_spec_from_loc.return_value = mock_spec
        mock_module = MagicMock()
        mock_mod_from_spec.return_value = mock_module

        engine = swarm.generate_game_engine("Rule restatement text")

        # Verify API called with 0.0 temperature for deterministic code output
        self.assertEqual(engine, mock_module)
        mock_file().write.assert_called_with(fake_python_code)
        mock_spec.loader.exec_module.assert_called_once_with(mock_module)

    @patch("game_tester_swarm.client.models.generate_content")
    @patch("builtins.open", new_callable=mock_open)
    @patch("importlib.util.spec_from_file_location")
    @patch("importlib.util.module_from_spec")
    def test_generate_game_engine_unintended_code_block_format(
        self, mock_mod_from_spec, mock_spec_from_loc, mock_file, mock_generate
    ):
        """Unintended path: handles generic markdown code blocks missing 'python' tag."""
        fake_python_code = "def initial_game_state(p): pass"
        mock_response = MagicMock()
        mock_response.text = f"```\n{fake_python_code}\n```"
        mock_generate.return_value = mock_response

        mock_spec = MagicMock()
        mock_spec_from_loc.return_value = mock_spec
        mock_module = MagicMock()
        mock_mod_from_spec.return_value = mock_module

        swarm.generate_game_engine("Rule restatement text")
        mock_file().write.assert_called_with(fake_python_code)


class TestAgentWorkflows(unittest.TestCase):
    """Tests for Rules Agent, Play Game loop, and Design Agent."""

    # ---------------------------------------------------------------------------
    # Tests for synthesize_rules
    # ---------------------------------------------------------------------------
    @patch("builtins.input", side_effect=["yes"])
    @patch("game_tester_swarm.client.chats.create")
    def test_synthesize_rules_intended(self, mock_chat_create, mock_input):
        """Intended path: user immediately approves restated rules and parses player count."""
        mock_chat = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Restated rules summary...\n[PLAYER_COUNT: 4]"
        mock_chat.send_message.return_value = mock_response
        mock_chat_create.return_value = mock_chat

        rules, count = swarm.synthesize_rules("Raw rules text")

        self.assertEqual(count, 4)
        self.assertIn("Restated rules summary...", rules)

    @patch("builtins.input", side_effect=["No, fix player count", "yes"])
    @patch("game_tester_swarm.client.chats.create")
    def test_synthesize_rules_unintended_missing_count_and_retry(self, mock_chat_create, mock_input):
        """Unintended path: user gives corrections before approval; defaults player count if unparseable."""
        mock_chat = MagicMock()

        # First turn misses tag; second turn gives invalid tag syntax
        res1 = MagicMock(text="Initial rules restatement without tag")
        res2 = MagicMock(text="Updated rules without valid tag")
        mock_chat.send_message.side_effect = [res1, res2]
        mock_chat_create.return_value = mock_chat

        rules, count = swarm.synthesize_rules("Raw rules text")

        self.assertEqual(count, 2)  # Defaults to 2 player fallback
        self.assertEqual(mock_chat.send_message.call_count, 2)

    # ---------------------------------------------------------------------------
    # Tests for play_game
    # ---------------------------------------------------------------------------
    @patch("game_tester_swarm.client.models.generate_content")
    def test_play_game_intended_win(self, mock_generate):
        """Intended path: players make valid moves and player 0 wins."""
        mock_engine = MagicMock()
        mock_engine.initial_game_state.return_value = {"board": "start"}
        mock_engine.list_valid_moves.return_value = [{"action": "move_a"}]
        mock_engine.execution_function.return_value = {"board": "step_1"}
        mock_engine.translation_function.side_effect = lambda g, p, p_id: g
        # Player 0 wins after their move
        mock_engine.eval_function.side_effect = [0]

        mock_response = MagicMock()
        mock_response.text = json.dumps({"move": {"action": "move_a"}, "explanation": "Good strategy"})
        mock_generate.return_value = mock_response

        artifacts, winner = swarm.play_game(mock_engine, "Rules text", player_count=2)

        self.assertEqual(winner, 0)
        self.assertEqual(len(artifacts), 1)
        self.assertEqual(artifacts[0]["type"], "turn")
        self.assertEqual(artifacts[0]["move"], {"action": "move_a"})

    @patch("game_tester_swarm.client.models.generate_content")
    def test_play_game_unintended_illegal_move_retry_and_forced_move(self, mock_generate):
        """Unintended path: AI makes illegal move, retries max times, and gets forced random move."""
        mock_engine = MagicMock()
        mock_engine.initial_game_state.return_value = {"board": "start"}
        mock_engine.list_valid_moves.return_value = [{"action": "legal_move"}]
        mock_engine.execution_function.return_value = {"board": "step_1"}
        mock_engine.translation_function.side_effect = lambda g, p, p_id: g
        mock_engine.eval_function.side_effect = [1]  # Player 1 wins
        mock_engine.get_random_valid_move.return_value = {"action": "legal_move"}

        # Return illegal move repeatedly
        illegal_response = MagicMock()
        illegal_response.text = json.dumps({"move": {"action": "illegal_move"}, "explanation": "Flawed thinking"})
        mock_generate.return_value = illegal_response

        artifacts, winner = swarm.play_game(mock_engine, "Rules text", player_count=1)

        # Should record MAX_RETRIES (3) misfire artifacts + 1 forced turn artifact
        misfires = [a for a in artifacts if a["type"] == "misfire"]
        turns = [a for a in artifacts if a["type"] == "turn"]

        self.assertEqual(len(misfires), swarm.MAX_RETRIES)
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["explanation"], "Forced random move due to invalid attempts.")

    @patch("game_tester_swarm.client.models.generate_content")
    def test_play_game_unintended_max_turns_draw(self, mock_generate):
        """Unintended path: game hits max turns limit without any winner."""
        mock_engine = MagicMock()
        mock_engine.initial_game_state.return_value = {"board": "start"}
        mock_engine.list_valid_moves.return_value = ["pass"]
        mock_engine.execution_function.return_value = {"board": "start"}
        mock_engine.translation_function.side_effect = lambda g, p, p_id: g
        mock_engine.eval_function.return_value = None  # No winner ever

        mock_response = MagicMock()
        mock_response.text = json.dumps({"move": "pass", "explanation": "Passing"})
        mock_generate.return_value = mock_response

        with patch("game_tester_swarm.MAX_TURNS", 2):  # Limit max turns to 2 for quick testing
            artifacts, winner = swarm.play_game(mock_engine, "Rules text", player_count=2)

        self.assertIsNone(winner)
        # 2 turns * 2 players = 4 turns executed
        self.assertEqual(len(artifacts), 4)

    # ---------------------------------------------------------------------------
    # Tests for critique_design and main
    # ---------------------------------------------------------------------------
    @patch("game_tester_swarm.client.models.generate_content")
    def test_critique_design_intended(self, mock_generate):
        """Intended path: sends artifacts to design model and returns critique text."""
        mock_response = MagicMock()
        mock_response.text = "Design Critique: Player 1 has a huge advantage."
        mock_generate.return_value = mock_response

        # Verify critique executes cleanly without error
        swarm.critique_design("Rules", [{"turn": 1}], "Files content")
        mock_generate.assert_called_once()

    @patch("builtins.input", side_effect=["2"])  # Option 2: Quit
    @patch("game_tester_swarm.load_input_files", return_value="Raw rule text")
    @patch("game_tester_swarm.synthesize_rules", return_value=("Rules restated", 2))
    @patch("game_tester_swarm.generate_game_engine")
    @patch("game_tester_swarm.play_game", return_value=([], 0))
    @patch("game_tester_swarm.critique_design")
    def test_main_loop_intended_quit(
        self, mock_critique, mock_play, mock_engine, mock_synth, mock_load, mock_input
    ):
        """Intended path: runs full swarm pipeline once and exits upon user selection."""
        swarm.main()

        mock_load.assert_called_once()
        mock_synth.assert_called_once_with("Raw rule text")
        mock_engine.assert_called_once_with("Rules restated")
        mock_play.assert_called_once()
        mock_critique.assert_called_once()


if __name__ == "__main__":
    unittest.main()