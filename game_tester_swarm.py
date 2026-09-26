import os
import sys
import glob
import re
import json
import importlib.util
from dotenv import load_dotenv
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Setup & Configuration
# ---------------------------------------------------------------------------
load_dotenv()

try:
    client = genai.Client()
except Exception as e:
    print(f"Error initializing client: {e}")
    print("Make sure GEMINI_API_KEY is set in your .env file.")
    sys.exit(1)

# Model definitions based on optimal roles
RULES_MODEL = "gemini-2.5-pro"
DESIGN_MODEL = "gemini-2.5-pro"
PLAYER_MODEL = "gemini-2.5-flash"

MAX_TURNS = 150
MAX_RETRIES = 3
INPUT_DIR = "input_files"
ENGINE_FILE = "generated_engine.py"

os.makedirs(INPUT_DIR, exist_ok=True)

# Helper function to generate standardized config (and silence the AFC warning)
def get_config(system_instruction=None, json_mode=False, temperature=0.2):
    config_args = {
        "automatic_function_calling": types.AutomaticFunctionCallingConfig(disable=True),
        "temperature": temperature
    }
    if system_instruction:
        config_args["system_instruction"] = system_instruction
    if json_mode:
        config_args["response_mime_type"] = "application/json"
    
    return types.GenerateContentConfig(**config_args)

# ---------------------------------------------------------------------------
# Phase 1: File Loading
# ---------------------------------------------------------------------------
def load_input_files():
    """Reads all text/markdown files in the input_files directory."""
    files_content = ""
    filepaths = glob.glob(os.path.join(INPUT_DIR, "*.*"))
    
    if not filepaths:
        print(f"No files found in '{INPUT_DIR}'. Please add your rulebook/notes.")
        sys.exit(1)
        
    for filepath in filepaths:
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                files_content += f"\n--- FILE: {os.path.basename(filepath)} ---\n{content}\n"
        except Exception as e:
            print(f"Could not read {filepath}: {e}")
            
    return files_content

# ---------------------------------------------------------------------------
# Phase 2: Rules Agent (Synthesis & Interactive Loop)
# ---------------------------------------------------------------------------
def synthesize_rules(files_content):
    """Interactive chat loop to synthesize and approve game rules."""
    print("\n[System] Starting Rules Agent synthesis...")
    
    system_instruction = (
        "You are the Rules Agent. Your job is to read board game documents and synthesize them into "
        "a crystal clear, comprehensive natural language rulebook. "
        "If there are any edge cases or ambiguities, ask the user clarifying questions first. "
        "Also, you MUST detect the requested player count from the user's files and output it exactly "
        "in this format at the very end of your final approved rules: [PLAYER_COUNT: X] (capped at 8)."
    )
    
    # We use a chat session so the user can correct the agent interactively
    chat = client.chats.create(
        model=RULES_MODEL, 
        config=get_config(system_instruction=system_instruction, temperature=0.4)
    )
    
    initial_prompt = f"Here are the game files. Please ask questions if needed, or output the restated rules:\n{files_content}"
    response = chat.send_message(initial_prompt)
    
    print("\n--- Rules Agent Restatement / Questions ---")
    print(response.text)
    
    # Interactive loop for user approval
    while True:
        user_input = input("\n[System] Do you approve this restatement? (Type 'yes' to approve, or type your corrections/answers): ")
        if user_input.strip().lower() in ['yes', 'y', 'approve']:
            break
        else:
            print("\n[System] Sending feedback to Rules Agent...")
            response = chat.send_message(user_input)
            print("\n--- Rules Agent Updated Response ---")
            print(response.text)
            
    # Extract player count from the final approved text
    final_rules = response.text
    player_count = 2 # Default fallback
    match = re.search(r'\[PLAYER_COUNT:\s*([1-8])\]', final_rules, re.IGNORECASE)
    if match:
        player_count = int(match.group(1))
    else:
        print("\n[Warning] Could not parse [PLAYER_COUNT: X]. Defaulting to 2 players.")
        
    return final_rules, player_count

# ---------------------------------------------------------------------------
# Phase 3: Code Generation & Dynamic Loading
# ---------------------------------------------------------------------------
def generate_game_engine(restated_rules):
    """Prompts the Rules Agent to generate the Python backend for the game."""
    print("\n[System] Generating Game Engine Code (this may take a minute)...")
    
    prompt = f"""
    Based on the following approved rules, generate a complete Python module to run this game.
    The code MUST contain the following functions exactly:
    
    1. `initial_game_state(player_count)`: Returns the starting state dictionary (include hidden info).
    2. `rule_function(game_state, move, player_id)`: Returns True if 'move' is legal, False otherwise.
    3. `execution_function(game_state, move, player_id)`: Returns the NEW updated game_state.
    4. `eval_function(game_state)`: Returns the player_id of the winner, or None if no winner yet.
    5. `translation_function(x1, x2, p)`: Takes current game state (x1), another player's previous state (x2), and that player's ID (p). It returns a new state for player 'p' that removes hidden info they shouldn't see, but keeps their own hidden info.
    6. `get_random_valid_move(game_state, player_id)`: Returns a random legal move (used if AI fails too many times).
    
    Output ONLY valid Python code inside a ```python block. No markdown outside the block.
    
    RULES:
    {restated_rules}
    """
    
    response = client.models.generate_content(
        model=RULES_MODEL,
        contents=prompt,
        config=get_config(temperature=0.0) # 0.0 for strict code generation
    )
    
    # Extract python code from markdown block
    code = response.text
    if "```python" in code:
        code = code.split("```python")[1].split("```")[0].strip()
    elif "```" in code:
        code = code.split("```")[1].split("```")[0].strip()
        
    # Save to local file
    with open(ENGINE_FILE, "w", encoding="utf-8") as f:
        f.write(code)
        
    print(f"\n[System] Game engine saved to {ENGINE_FILE}.")
    print("\n--- Generated Code Preview ---")
    print(code[:500] + "\n... (truncated)\n")
    
    # Dynamically import the generated code
    spec = importlib.util.spec_from_file_location("generated_engine", ENGINE_FILE)
    engine = importlib.util.module_from_spec(spec)
    sys.modules["generated_engine"] = engine
    spec.loader.exec_module(engine)
    
    return engine

# ---------------------------------------------------------------------------
# Phase 4 & 5: Instantiation and Game Loop
# ---------------------------------------------------------------------------
def play_game(engine, restated_rules, player_count):
    """Instantiates Player Agents and runs the turn-by-turn game loop."""
    print(f"\n[System] Instantiating Game with {player_count} players...")
    
    # Global state is tracked by the engine
    global_state = engine.initial_game_state(player_count)
    
    # Each player maintains their own perspective of the state
    player_states = {i: global_state for i in range(1, player_count + 1)}
    artifacts = []
    winner = None

    # The prompt system instruction for the Player Agents
    player_instruction = (
        "You are an AI Player Agent playing a board game. "
        "Try to win the game without breaking the rules. "
        "You will be given the current game state. You must respond in STRICT JSON format with two keys: "
        "'move' (your requested action), and 'explanation' (your strategic reasoning)."
    )
    
    turn_count = 0
    while winner is None and turn_count < MAX_TURNS:
        turn_count += 1
        print(f"\n--- Turn {turn_count} ---")
        
        for player_id in range(1, player_count + 1):
            print(f"Player {player_id} is thinking...")
            
            # Prepare prompt for this specific turn
            state_str = str(player_states[player_id])
            prompt = f"Rules:\n{restated_rules}\n\nCurrent State:\n{state_str}\n\nWhat is your move?"
            
            valid_move_made = False
            retries = 0
            
            # Request move from Player Agent
            while not valid_move_made and retries < MAX_RETRIES:
                try:
                    response = client.models.generate_content(
                        model=PLAYER_MODEL,
                        contents=prompt,
                        config=get_config(system_instruction=player_instruction, json_mode=True, temperature=0.7)
                    )
                    
                    data = json.loads(response.text)
                    chosen_move = data.get("move")
                    explanation = data.get("explanation")
                    
                    # Validate move using the generated engine
                    if engine.rule_function(global_state, chosen_move, player_id):
                        valid_move_made = True
                        print(f"  -> Valid Move: {chosen_move}")
                    else:
                        retries += 1
                        error_msg = f"That move was illegal. You have {MAX_RETRIES - retries} attempts left."
                        prompt += f"\n\nPrevious attempt '{chosen_move}' failed. {error_msg}"
                        artifacts.append({"player": player_id, "type": "misfire", "move": chosen_move, "explanation": explanation, "state": state_str})
                        
                except Exception as e:
                    retries += 1
                    prompt += f"\n\nSystem Error or invalid JSON returned: {e}. Please return valid JSON."

            # Force random move if AI fails too many times
            if not valid_move_made:
                print(f"  -> Max retries hit. Forcing random move.")
                chosen_move = engine.get_random_valid_move(global_state, player_id)
                explanation = "Forced random move due to invalid attempts."
                print(f"  -> Forced Move: {chosen_move}")

            # Execute the legal move
            global_state = engine.execution_function(global_state, chosen_move, player_id)
            
            # Save successful turn artifact
            artifacts.append({
                "player": player_id,
                "type": "turn",
                "move": chosen_move,
                "explanation": explanation,
                "state": state_str
            })
            
            # Update all players' perspectives using translation_function
            for p_id in range(1, player_count + 1):
                player_states[p_id] = engine.translation_function(global_state, player_states[p_id], p_id)
            
            # Check for win condition
            winner = engine.eval_function(global_state)
            if winner:
                print(f"\n[System] Player {winner} has won the game!")
                break
                
    if not winner:
        print(f"\n[System] Max turns ({MAX_TURNS}) reached. Game ended in a draw.")
        
    return artifacts, winner

# ---------------------------------------------------------------------------
# Phase 6 & 7: Design Agent Critique & Feedback Loop
# ---------------------------------------------------------------------------
def critique_design(restated_rules, artifacts, files_content):
    """Analyzes the game logs and provides design feedback."""
    print("\n[System] Sending artifacts to Design Agent for critique...")
    
    # We serialize the artifacts log to a string. If it's too massive, Gemini 2.5 Pro's huge context window handles it perfectly.
    artifacts_str = json.dumps(artifacts, indent=2)
    
    prompt = f"""
    You are the Design Agent. Review the user's original design goals, the restated rules, and the complete playtest history logs.
    
    Identify:
    1. Overpowered or dominant strategies.
    2. Any bottlenecks or dead turns where AI agents got stuck (look at 'misfire' artifacts).
    3. Areas where the game failed to meet the original design intent.
    
    Provide specific suggestions for new mechanics, rule tweaks, or card modifications to solve these issues.
    
    Original Files/Goals:
    {files_content}
    
    Rules:
    {restated_rules}
    
    Game Logs (Artifacts):
    {artifacts_str}
    """
    
    response = client.models.generate_content(
        model=DESIGN_MODEL,
        contents=prompt,
        config=get_config(temperature=0.5)
    )
    
    print("\n===========================================")
    print("         DESIGN AGENT CRITIQUE")
    print("===========================================")
    print(response.text)
    print("===========================================")

# ---------------------------------------------------------------------------
# Main Execution Flow
# ---------------------------------------------------------------------------
def main():
    while True:
        files_content = load_input_files()
        
        # 1. Rules Agent Interaction
        restated_rules, player_count = synthesize_rules(files_content)
        
        # 2. Code Generation
        engine = generate_game_engine(restated_rules)
        
        # 3. Game Execution
        artifacts, winner = play_game(engine, restated_rules, player_count)
        
        # 4. Critique
        critique_design(restated_rules, artifacts, files_content)
        
        # 5. Feedback Loop
        print("\n[System] Playtest complete.")
        print("Options: ")
        print("1. Modify rules and run again (Restart Loop)")
        print("2. Quit")
        choice = input("Enter choice (1/2): ")
        
        if choice.strip() == '2':
            print("Exiting. Have a great day!")
            break
        else:
            print("\n[System] Please update the files in the 'input_files' directory.")
            input("Press Enter when you are ready to restart...")

if __name__ == "__main__":
    main()