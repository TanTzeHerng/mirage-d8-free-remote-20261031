"""Publish verified live Mephisto Mirage vs Beast PGNs to the match-live branch."""
import argparse, io, json, os, shutil, subprocess, time
from datetime import datetime, timezone
from pathlib import Path
import chess, chess.pgn

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "match_beast_data"
WORKTREE = Path(os.environ.get("RUNNER_TEMP", "/tmp")) / "mirage_beast_native_worktree"

def cmd(*args, cwd=ROOT, check=True):
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if check and result.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr[-2000:]}")
    return result

def initialize():
    cmd("fetch", "origin", "+refs/heads/match-claude-beast:refs/remotes/origin/match-claude-beast")
    if not (WORKTREE / ".git").exists():
        cmd("worktree", "add", "--detach", str(WORKTREE), "origin/match-claude-beast")
    cmd("config", "user.name", "Mirage Beast PGN checkpoint bot", cwd=WORKTREE)
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
    live_search = {}
    for status_path in sorted(DATA.glob("game_*/status.json")):
        verified = verified_game(status_path)
        if not verified:
            print(f"LIVE_PGN_SKIP_UNVERIFIED {status_path}", flush=True)
            continue
        status, pgn_text = verified
        gid = int(status["game_id"])
        name = f"game_{gid:06d}"
        # A completed-move time is NOT the emulated time of the current search.
        # The native emulator independently checkpoints its firmware timer
        # every 30 emulated seconds. Only publish a snapshot newer than the
        # latest arbiter status, and only when it is Mirage's turn to think.
        search_file = status_path.parent / "native_search_state.json"
        if status["status"] == "active" and search_file.is_file():
            try:
                side_to_move = status["fen"].split()[1]
                mirage_turn = side_to_move == ("w" if status["mirage_color"] == "white" else "b")
                snapshot_mtime = search_file.stat().st_mtime_ns
                if mirage_turn and snapshot_mtime > status_path.stat().st_mtime_ns:
                    info = json.loads(search_file.read_text(encoding="utf8"))
                    seconds = float(info["emulated_search_seconds"])
                    if info.get("format") == "MRGCPU01" and 0 <= seconds < 10**9:
                        live_search[str(gid)] = {
                            "emulated_seconds": round(seconds, 3),
                            "observed_utc": datetime.fromtimestamp(
                                snapshot_mtime / 1e9, tz=timezone.utc).isoformat(),
                            "source": "native_mirage_cpu_snapshot",
                        }
            except (ValueError, KeyError, OSError, TypeError) as exc:
                print("LIVE_SEARCH_SNAPSHOT_SKIP", gid, repr(exc), flush=True)
        changed |= update_file(WORKTREE / "live" / (name + ".pgn"), pgn_text)
        changed |= update_file(WORKTREE / "live" / (name + ".json"), json.dumps(status, indent=2) + "\n")
        games.append({"game_id": gid, "opening_id": status["opening_id"],
                      "status": status["status"], "result": status["chess_result"],
                      "total_plies": len(status["move_history_uci"]),
                      "last_move_utc": status["time_utc"],
                      "pgn": "live/" + name + ".pgn"})
        pgn_parts.append(pgn_text.rstrip())
    # Game identity is not the dashboard slot. A finished game immediately
    # releases its slot for the next unplayed opening/color assignment.
    schedule_path=DATA/"scheduler.json"
    if schedule_path.exists():
        schedule=json.loads(schedule_path.read_text(encoding="utf8"))
        slots={str(k):int(v) for k,v in schedule.get("slots",{}).items()}
    else:
        slots={str(i):i for i in range(1,9)}
    all_states=[x[0] for x in [
        verified_game(p) or ({},None)
        for p in sorted(DATA.glob("game_*/status.json"))
    ] if x]
    completed=[g for g in all_states if g.get("status")=="finished"
               and g.get("chess_result") in ("1-0","0-1","1/2-1/2")]
    mirage_wins=0
    beast_wins=0
    draws=0
    for g in completed:
        result=g["chess_result"]
        if result=="1/2-1/2":
            draws+=1
        elif ((result=="1-0")==(g["mirage_color"]=="white")):
            mirage_wins+=1
        else:
            beast_wins+=1
    score={"mirage_wins":mirage_wins,"beast_wins":beast_wins,
           "draws":draws,"completed_chess_games":len(completed),
           "mirage_points":mirage_wins+draws/2,
           "beast_points":beast_wins+draws/2,
           "duplicate_results_included":True}
    recent=sorted(completed,key=lambda g:g.get("time_utc",""),reverse=True)[:16]
    recent_results=[{"game_id":g["game_id"],"result":g["chess_result"],
                    "mirage_color":g["mirage_color"],
                    "opening_id":g["opening_id"],"time_utc":g["time_utc"]}
                    for g in recent]
    stats={"slots":slots,"score":score,"recent_results":recent_results,
           "assigned_game_count":len(all_states)}
    # Save the score and display-slot mapping even if no new move occurred.
    stats_path=WORKTREE/"live"/"slot_score.json"
    changed |= update_file(stats_path,json.dumps(stats,indent=2)+"\n")
    changed |= update_file(WORKTREE/"live"/"current_search.json",
                           json.dumps(live_search,indent=2,sort_keys=True)+"\n")
    # Publish the durable next-game counter too, so interruptions cannot omit
    # openings allocated shortly before a runner was terminated.
    if schedule_path.exists():
        changed |= update_file(WORKTREE/"live"/"scheduler.json",
                               schedule_path.read_text(encoding="utf8"))
    if not changed:
        return False
    update_file(WORKTREE / "live" / "all_games.pgn", "\n\n".join(pgn_parts) + "\n")
    index = {"published_utc": datetime.now(timezone.utc).isoformat(),
             "source_run_id": os.getenv("GITHUB_RUN_ID", "seeded"),
             "games": games,"slots":slots,"score":score,"live_search":live_search,
             "recent_results":recent_results,"assigned_game_count":len(all_states),
             "completed_chess_games":len(completed)}
    update_file(WORKTREE / "live" / "index.json", json.dumps(index, indent=2) + "\n")
    cmd("add", "live", cwd=WORKTREE)
    if cmd("diff", "--cached", "--quiet", cwd=WORKTREE, check=False).returncode == 0:
        return False
    cmd("commit", "-m", "Publish verified live chess PGNs and restart checkpoints", cwd=WORKTREE)
    for attempt in range(3):
        pushed = cmd("push", "origin", "HEAD:refs/heads/match-claude-beast", cwd=WORKTREE, check=False)
        if pushed.returncode == 0:
            print("LIVE_PGNS_PUBLISHED", len(games), "games", index["published_utc"], flush=True)
            return True
        print("LIVE_PGN_PUSH_RETRY", attempt + 1, pushed.stderr[-400:], flush=True)
        cmd("fetch", "origin", "+refs/heads/match-claude-beast:refs/remotes/origin/match-claude-beast")
        cmd("rebase", "origin/match-claude-beast", cwd=WORKTREE)
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
