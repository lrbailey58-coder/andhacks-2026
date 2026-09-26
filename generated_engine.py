import copy
import random
import itertools
from collections import Counter

def generate_deck():
    suits = ['♠', '♥', '♦', '♣']
    ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    return [{'rank': r, 'suit': s} for s in suits for r in ranks]

def get_next_non_eliminated(state, current):
    nxt = (current + 1) % state["player_count"]
    while state["players"][nxt]["eliminated"]:
        nxt = (nxt + 1) % state["player_count"]
    return nxt

def score_5_cards(cards, rank_values):
    ranks = sorted([rank_values[c['rank']] for c in cards], reverse=True)
    suits = [c['suit'] for c in cards]
    
    is_flush = len(set(suits)) == 1
    
    is_straight = False
    if ranks == [14, 5, 4, 3, 2]:
        is_straight = True
        ranks = [5, 4, 3, 2, 1]
    elif ranks[0] - ranks[4] == 4 and len(set(ranks)) == 5:
        is_straight = True
        
    counts = Counter(ranks)
    freqs = sorted([(count, rank) for rank, count in counts.items()], reverse=True)
    
    ranks = tuple(ranks)
    
    if is_straight and is_flush:
        return (8, ranks[0])
    if freqs[0][0] == 4:
        return (7, freqs[0][1], freqs[1][1])
    if freqs[0][0] == 3 and freqs[1][0] == 2:
        return (6, freqs[0][1], freqs[1][1])
    if is_flush:
        return (5, ranks)
    if is_straight:
        return (4, ranks[0])
    if freqs[0][0] == 3:
        return (3, freqs[0][1], freqs[1][1], freqs[2][1])
    if freqs[0][0] == 2 and freqs[1][0] == 2:
        return (2, freqs[0][1], freqs[1][1], freqs[2][1])
    if freqs[0][0] == 2:
        return (1, freqs[0][1], freqs[1][1], freqs[2][1], freqs[3][1])
        
    return (0, ranks)

def evaluate_hand(hole_cards, board):
    cards = hole_cards + board
    if not cards:
        return (0,)
        
    rank_values = {'2':2, '3':3, '4':4, '5':5, '6':6, '7':7, '8':8, '9':9, '10':10, 'J':11, 'Q':12, 'K':13, 'A':14}
    best_score = (-1,)
    
    for combo in itertools.combinations(cards, 5):
        score = score_5_cards(combo, rank_values)
        if score > best_score:
            best_score = score
            
    return best_score

def resolve_hand(state):
    for i in range(state["player_count"]):
        state["players"][i]["invested_in_hand"] += state["players"][i]["invested_in_round"]
        state["players"][i]["invested_in_round"] = 0

    investments = {i: state["players"][i]["invested_in_hand"] for i in range(state["player_count"])}
    
    max_invest = max(investments.values())
    players_with_max = [i for i, inv in investments.items() if inv == max_invest]
    if len(players_with_max) == 1:
        p_idx = players_with_max[0]
        second_max = max([inv for i, inv in investments.items() if i != p_idx] + [0])
        refund = max_invest - second_max
        state["players"][p_idx]["chips"] += refund
        investments[p_idx] -= refund

    active_players = [i for i, p in state["players"].items() if not p["folded"] and not p["eliminated"]]
    
    state["last_hand_results"] = {
        "board": state["board"].copy(),
        "hands": {i: state["players"][i]["hole_cards"].copy() for i in active_players}
    }
    
    if len(active_players) == 1:
        winner = active_players[0]
        total_pot = sum(investments.values())
        state["players"][winner]["chips"] += total_pot
    else:
        scores = {i: evaluate_hand(state["players"][i]["hole_cards"], state["board"]) for i in active_players}
        
        while sum(investments.values()) > 0 and len(active_players) > 0:
            min_invest = min(investments[i] for i in active_players if investments[i] > 0)
            if min_invest == 0:
                break
                
            current_pot = 0
            for i in range(state["player_count"]):
                contrib = min(investments[i], min_invest)
                current_pot += contrib
                investments[i] -= contrib
                
            best_score = max(scores[i] for i in active_players)
            winners = [i for i in active_players if scores[i] == best_score]
            
            split_amount = current_pot // len(winners)
            rem = current_pot % len(winners)
            for w in winners:
                state["players"][w]["chips"] += split_amount
            state["players"][winners[0]]["chips"] += rem
            
            active_players = [i for i in active_players if investments[i] > 0]

    start_new_hand(state)

def advance_phase(state):
    for i in range(state["player_count"]):
        state["pot"] += state["players"][i]["invested_in_round"]
        state["players"][i]["invested_in_hand"] += state["players"][i]["invested_in_round"]
        state["players"][i]["invested_in_round"] = 0
        
    state["highest_bet_in_round"] = 0
    state["min_raise"] = state["big_blind"]
    
    for i in range(state["player_count"]):
        state["players"][i]["has_acted"] = False
        
    active_not_all_in = [i for i, p in state["players"].items() if not p["folded"] and not p["eliminated"] and not p["all_in"]]
    
    if state["phase"] == "pre-flop":
        state["phase"] = "flop"
        state["board"].extend([state["deck"].pop() for _ in range(3)])
    elif state["phase"] == "flop":
        state["phase"] = "turn"
        state["board"].append(state["deck"].pop())
    elif state["phase"] == "turn":
        state["phase"] = "river"
        state["board"].append(state["deck"].pop())
    elif state["phase"] == "river":
        state["phase"] = "showdown"
        resolve_hand(state)
        return
        
    if len(active_not_all_in) <= 1:
        while state["phase"] != "showdown":
            if state["phase"] == "pre-flop":
                state["phase"] = "flop"
                state["board"].extend([state["deck"].pop() for _ in range(3)])
            elif state["phase"] == "flop":
                state["phase"] = "turn"
                state["board"].append(state["deck"].pop())
            elif state["phase"] == "turn":
                state["phase"] = "river"
                state["board"].append(state["deck"].pop())
            elif state["phase"] == "river":
                state["phase"] = "showdown"
                resolve_hand(state)
                return
            
    curr = state["dealer"]
    while True:
        curr = (curr + 1) % state["player_count"]
        if curr in active_not_all_in:
            state["current_player"] = curr
            break

def start_new_hand(state):
    active_count = 0
    last_active = None
    for i in range(state["player_count"]):
        if state["players"][i]["chips"] == 0:
            state["players"][i]["eliminated"] = True
        if not state["players"][i]["eliminated"]:
            active_count += 1
            last_active = i
            
    if active_count == 1:
        state["winner"] = last_active
        return
        
    state["dealer"] = (state["dealer"] + 1) % state["player_count"]
    while state["players"][state["dealer"]]["eliminated"]:
        state["dealer"] = (state["dealer"] + 1) % state["player_count"]
        
    state["deck"] = generate_deck()
    random.shuffle(state["deck"])
    state["board"] = []
    state["pot"] = 0
    state["phase"] = "pre-flop"
    state["highest_bet_in_round"] = state["big_blind"]
    state["min_raise"] = state["big_blind"]
    
    for i in range(state["player_count"]):
        p = state["players"][i]
        p["folded"] = False
        p["all_in"] = False
        p["has_acted"] = False
        p["invested_in_round"] = 0
        p["invested_in_hand"] = 0
        if not p["eliminated"]:
            p["hole_cards"] = [state["deck"].pop(), state["deck"].pop()]
        else:
            p["hole_cards"] = []
            
    sb_player = get_next_non_eliminated(state, state["dealer"])
    bb_player = get_next_non_eliminated(state, sb_player)
    
    sb_amount = min(state["small_blind"], state["players"][sb_player]["chips"])
    state["players"][sb_player]["chips"] -= sb_amount
    state["players"][sb_player]["invested_in_round"] = sb_amount
    if state["players"][sb_player]["chips"] == 0:
        state["players"][sb_player]["all_in"] = True
        
    bb_amount = min(state["big_blind"], state["players"][bb_player]["chips"])
    state["players"][bb_player]["chips"] -= bb_amount
    state["players"][bb_player]["invested_in_round"] = bb_amount
    if state["players"][bb_player]["chips"] == 0:
        state["players"][bb_player]["all_in"] = True
        
    can_act = [i for i in range(state["player_count"]) if not state["players"][i]["eliminated"] and not state["players"][i]["folded"] and not state["players"][i]["all_in"]]
    
    if len(can_act) == 0:
        state["current_player"] = bb_player
        advance_phase(state)
    else:
        curr = bb_player
        while True:
            curr = (curr + 1) % state["player_count"]
            if curr in can_act:
                state["current_player"] = curr
                break

def initial_game_state(player_count):
    state = {
        "player_count": player_count,
        "players": {
            i: {
                "chips": 1000,
                "hole_cards": [],
                "folded": False,
                "all_in": False,
                "has_acted": False,
                "eliminated": False,
                "invested_in_round": 0,
                "invested_in_hand": 0
            } for i in range(player_count)
        },
        "pot": 0,
        "board": [],
        "deck": [],
        "dealer": -1,
        "current_player": 0,
        "phase": "pre-flop",
        "highest_bet_in_round": 20,
        "min_raise": 20,
        "small_blind": 10,
        "big_blind": 20,
        "winner": None,
        "last_hand_results": None
    }
    
    start_new_hand(state)
    return state

def rule_function(state, move, player_id):
    if state.get("winner") is not None:
        return False
    if player_id != state["current_player"]:
        return False
    
    p = state["players"][player_id]
    action = move.get("action")
    
    if action == "fold":
        return True
    elif action == "call":
        return True
    elif action == "raise":
        amount = move.get("amount")
        if not isinstance(amount, int):
            return False
        
        additional_needed = amount - p["invested_in_round"]
        if additional_needed <= 0 or additional_needed > p["chips"]:
            return False
            
        min_raise_total = state["highest_bet_in_round"] + state["min_raise"]
        max_total = p["invested_in_round"] + p["chips"]
        
        if amount < min_raise_total and amount != max_total:
            return False
            
        if amount <= state["highest_bet_in_round"]:
            return False
            
        return True
        
    return False

def execution_function(state, move, player_id):
    state = copy.deepcopy(state)
    p = state["players"][player_id]
    action = move["action"]
    
    if action == "fold":
        p["folded"] = True
    elif action == "call":
        to_call = state["highest_bet_in_round"] - p["invested_in_round"]
        actual_call = min(to_call, p["chips"])
        p["chips"] -= actual_call
        p["invested_in_round"] += actual_call
        if p["chips"] == 0:
            p["all_in"] = True
    elif action == "raise":
        amount = move["amount"]
        additional = amount - p["invested_in_round"]
        p["chips"] -= additional
        p["invested_in_round"] += additional
        if p["chips"] == 0:
            p["all_in"] = True
            
        raise_amount = amount - state["highest_bet_in_round"]
        if raise_amount >= state["min_raise"]:
            state["min_raise"] = raise_amount
        state["highest_bet_in_round"] = amount
        
        for i in range(state["player_count"]):
            state["players"][i]["has_acted"] = False
            
    p["has_acted"] = True
    
    active_players = [i for i, player in state["players"].items() if not player["folded"] and not player["eliminated"]]
    if len(active_players) == 1:
        resolve_hand(state)
        return state
        
    active_not_all_in = [i for i in active_players if not state["players"][i]["all_in"]]
    
    all_matched_or_all_in = all(
        (state["players"][i]["invested_in_round"] == state["highest_bet_in_round"]) or state["players"][i]["all_in"]
        for i in active_players
    )
    all_acted = all(
        state["players"][i]["has_acted"] or state["players"][i]["all_in"]
        for i in active_players
    )
    
    if all_matched_or_all_in and all_acted:
        advance_phase(state)
    else:
        if len(active_not_all_in) > 0:
            curr = state["current_player"]
            while True:
                curr = (curr + 1) % state["player_count"]
                if curr in active_not_all_in and not (state["players"][curr]["has_acted"] and state["players"][curr]["invested_in_round"] == state["highest_bet_in_round"]):
                    state["current_player"] = curr
                    break
        else:
            advance_phase(state)
            
    return state

def eval_function(state):
    return state.get("winner")

def translation_function(x1, x2, p):
    state_copy = copy.deepcopy(x1)
    state_copy["deck"] = []
    for i in range(state_copy["player_count"]):
        if i != p:
            state_copy["players"][i]["hole_cards"] = []
    return state_copy

def get_random_valid_move(state, player_id):
    valid_moves = [{"action": "fold"}, {"action": "call"}]
    p = state["players"][player_id]
    
    min_raise_total = state["highest_bet_in_round"] + state["min_raise"]
    max_total = p["invested_in_round"] + p["chips"]
    
    if max_total >= min_raise_total:
        valid_moves.append({"action": "raise", "amount": min_raise_total})
        if max_total > min_raise_total:
            valid_moves.append({"action": "raise", "amount": max_total})
    elif max_total > state["highest_bet_in_round"]:
        valid_moves.append({"action": "raise", "amount": max_total})
        
    return random.choice(valid_moves)