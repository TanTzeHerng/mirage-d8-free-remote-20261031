"""Recover newer verified live positions without replacing fresher artifact checkpoints."""
import io, json, subprocess
from pathlib import Path
import chess, chess.pgn

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "match_fast_data"

def run(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout

def get_live(path):
    item = subprocess.run(["git", "show", "refs/remotes/origin/match-claude-fast:" + path],
                          cwd=ROOT, capture_output=True, text=True)
    return item.stdout if item.returncode == 0 else None

def valid(state, pgn_text):
    try:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None or game.errors:
            return False
        moves = [m.uci() for m in game.mainline_moves()]
        if moves != state["move_history_uci"]:
            return False
        board = chess.Board()
        for move in moves:
            board.push_uci(move)
        result = board.outcome(claim_draw=True)
        return (board.fen() == state["fen"] and
                (result.result() if result else "*") == state["chess_result"] and
                game.headers.get("Result", "*") == state["chess_result"])
    except (ValueError, KeyError, TypeError, AssertionError):
        return False

def main():
    run("fetch", "origin", "+refs/heads/match-claude-fast:refs/remotes/origin/match-claude-fast")
    raw = get_live("live/index.json")
    if not raw:
        print("NO_LIVE_CHECKPOINT_INDEX; artifact-only restart", flush=True)
        return
    index = json.loads(raw)
    restored = 0
    for entry in index.get("games", []):
        gid = int(entry["game_id"])
        base = "game_%06d" % gid
        state_raw = get_live("live/" + base + ".json")
        pgn_raw = get_live("live/" + base + ".pgn")
        if not state_raw or not pgn_raw:
            continue
        state = json.loads(state_raw)
        if int(state["game_id"]) != gid or not valid(state, pgn_raw):
            print("SKIP_INVALID_LIVE_GAME", gid, flush=True)
            continue
        wd = DATA / base
        old_path = wd / "status.json"
        if old_path.exists():
            old = json.loads(old_path.read_text(encoding="utf8"))
            if old["opening_id"] != state["opening_id"] or old["mirage_color"] != state["mirage_color"]:
                raise RuntimeError("Checkpoint identity mismatch for game " + base)
            old_moves = old["move_history_uci"]
            new_moves = state["move_history_uci"]
            if new_moves[:len(old_moves)] != old_moves and old_moves[:len(new_moves)] != new_moves:
                raise RuntimeError("Divergent checkpoint for game " + base)
            if len(new_moves) < len(old_moves):
                continue
            if len(new_moves) == len(old_moves):
                if old["status"] == "finished" or state["status"] != "finished":
                    continue
        wd.mkdir(parents=True, exist_ok=True)
        (wd / "status.json").write_text(json.dumps(state, indent=2) + "\n", encoding="utf8")
        (wd / "current.pgn").write_text(pgn_raw, encoding="utf8")
        restored += 1
        print("RECOVERED_LIVE_GAME", gid, len(state["move_history_uci"]), flush=True)
    print("LIVE_CHECKPOINT_RESTORED", restored, "games", flush=True)

if __name__ == "__main__":
    main()
