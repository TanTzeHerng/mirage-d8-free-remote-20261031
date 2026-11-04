"""Publish verified live Mephisto Mirage vs Maia PGNs to the match-live branch."""
import argparse, io, json, os, shutil, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
import chess, chess.pgn

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "match_data"
WORKTREE = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "mirage_maia_live_worktree"

def cmd(*args, cwd=ROOT, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr[-2000:]}")
    return result

def initialize():
    cmd("fetch", "origin", "+refs/heads/match-live:refs/remotes/origin/match-live")
    if not (WORKTREE / ".git").exists():
        cmd("worktree", "add", "--detach", str(WORKTREE), "origin/match-live")
    cmd("config", "user.name", "Mirage Maia PGN checkpoint bot", cwd=WORKTREE)
    cmd("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com", cwd=WORKTREE)
    (WORKTREE / "live").mkdir(exist_ok=True)

def verified_game(state_path):
    state = json.loads(state_path.read_text(encoding="utf8"))
    pgn_path = state_path.with_name("current.pgn")
    if not pgn_path.is_file():
        return None
    pgn_text = pgn_path.read_text(encoding="utf8")
    game = chess.pgn.read_game(io.StringIO(pgn_text))
    if game is None or game.errors:
        return None
    moves = [m.uci() for m in game.mainline_moves()]
    if moves != state["move_history_uci"]:
        return None
    board = chess.Board()
    for u in moves:
        board.push_uci(u)
    result = board.outcome(claim_draw=True)
    if board.fen() != state["fen"] or (result.result() if result else "*") != state["chess_result"]:
        return None
    if game.headers.get("Result", "*") != state["chess_result"]:
        return None
    return state, pgn_text

def update_file(path, contents):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf8") == contents:
        return False
    path.write_text(contents, encoding="utf8")
    return True

def snapshot():
    changed = False
    games = []
    pgn_parts = []
    for status_path in sorted(DATA.glob("game_*/status.json")):
        verified = verified_game(status_path)
        if not verified:
            print(f"LIVE_PGN_SKIP_UNVERIFIED {status_path}", flush=True)
            continue
        status, pgn_text = verified
        gid = int(status["game_id"])
        name = f"game_{gid:06d}"
        changed |= update_file(WORKTREE / "live" / (name + ".pgn"), pgn_text)
        changed |= update_file(WORKTREE / "live" / (name + ".json"), json.dumps(status, indent=2) + "\n")
        games.append({"game_id": gid, "opening_id": status["opening_id"],
                      "status": status["status"], "result": status["chess_result"],
                      "total_plies": len(status["move_history_uci"]),
                      "last_move_utc": status["time_utc"],
                      "pgn": "live/" + name + ".pgn"})
        pgn_parts.append(pgn_text.rstrip())
    if not changed:
        return False
    update_file(WORKTREE / "live" / "all_games.pgn", "\n\n".join(pgn_parts) + "\n")
    index = {"published_utc": datetime.now(timezone.utc).isoformat(),
             "source_run_id": os.getenv("GITHUB_RUN_ID", "seeded"),
             "games": games, "completed_chess_games":
             sum(g["status"] == "finished" and g["result"] != "*" for g in games)}
    update_file(WORKTREE / "live" / "index.json", json.dumps(index, indent=2) + "\n")
    cmd("add", "live", cwd=WORKTREE)
    if cmd("diff", "--cached", "--quiet", cwd=WORKTREE, check=False).returncode == 0:
        return False
    cmd("commit", "-m", "Publish verified live chess PGNs and restart checkpoints", cwd=WORKTREE)
    for attempt in range(3):
        pushed = cmd("push", "origin", "HEAD:refs/heads/match-live", cwd=WORKTREE, check=False)
        if pushed.returncode == 0:
            print("LIVE_PGNS_PUBLISHED", len(games), "games", index["published_utc"], flush=True)
            return True
        print("LIVE_PGN_PUSH_RETRY", attempt + 1, pushed.stderr[-400:], flush=True)
        cmd("fetch", "origin", "+refs/heads/match-live:refs/remotes/origin/match-live")
        cmd("rebase", "origin/match-live", cwd=WORKTREE)
    raise RuntimeError("Could not publish live PGNs after three attempts")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--interval", type=int, default=15)
    args = ap.parse_args()
    initialize()
    while True:
        try:
            snapshot()
        except Exception as exc:
            print("LIVE_PGN_PUBLISH_ERROR", repr(exc), flush=True)
            if not args.watch:
                raise
        if not args.watch:
            return
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
