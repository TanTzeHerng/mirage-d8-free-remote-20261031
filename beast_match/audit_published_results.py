"""Independent, read-only chess result audit of published Mirage D8 vs Beast games.

No match runner functions are imported. Every PGN is replayed from the initial
position with python-chess; no unsupported technical result is accepted.
"""
import collections
import io
import json
from pathlib import Path
import chess
import chess.pgn

DATA=Path("live")
index=json.loads((DATA/"index.json").read_text(encoding="utf-8"))
fail=[]
term=collections.Counter()
score=collections.Counter()
retries=collections.Counter()
lengths=[]
ids=set()
examples=[]
for entry in index["games"]:
    gid=int(entry["game_id"])
    if gid in ids:fail.append(f"Duplicate game ID {gid}")
    ids.add(gid)
    name=f"game_{gid:06d}"
    try:
        state=json.loads((DATA/(name+".json")).read_text(encoding="utf-8"))
        raw=(DATA/(name+".pgn")).read_text(encoding="utf-8")
        game=chess.pgn.read_game(io.StringIO(raw))
        if game is None:
            raise ValueError("PGN parse produced no game")
        if game.errors:
            raise ValueError("PGN parser errors: "+str(game.errors[:2]))
        if chess.pgn.read_game(io.StringIO(raw)) is None:
            raise ValueError("Empty PGN")
        board=chess.Board()
        moves=[]
        for move in game.mainline_moves():
            if move not in board.legal_moves:
                raise ValueError(f"Illegal move {move.uci()} at {board.fen()}")
            moves.append(move.uci())
            board.push(move)
        if moves!=state["move_history_uci"]:
            raise ValueError("PGN differs from independently recorded UCI history")
        if len(moves)!=int(entry["total_plies"]):
            raise ValueError("PGN length differs from index")
        if board.fen()!=state["fen"]:
            raise ValueError("Final FEN mismatch")
        outcome=board.outcome(claim_draw=True)
        expected=outcome.result() if outcome else "*"
        result=state["chess_result"]
        if result!=expected:
            raise ValueError(f"Result {result} differs from actual board outcome {expected}")
        if result!=entry["result"] or result!=game.headers.get("Result","*"):
            raise ValueError("Result differs between index, status and PGN header")
        if state["status"]!=entry["status"]:
            raise ValueError("Status differs between index and game state")
        if state["status"]=="finished":
            if outcome is None or result=="*":
                raise ValueError("Finished result lacks terminal or claimable chess outcome")
            if result not in ("1-0","0-1","1/2-1/2"):
                raise ValueError("Non-chess result recorded")
            term[outcome.termination.name]+=1
            if result=="1/2-1/2":
                score["draws"]+=1
            elif (result=="1-0")== (state["mirage_color"]=="white"):
                score["mirage_wins"]+=1
            else:
                score["beast_wins"]+=1
            rc=int(state.get("technical_error_count",0))
            retries["finished_with_retries" if rc else "finished_without_retries"]+=1
            retries["total_technical_retries_in_finished_games"]+=rc
            lengths.append(len(moves))
            if len(examples)<10: examples.append({"game_id":gid,"chess_result":result,
                "termination":outcome.termination.name,"plies":len(moves),
                "technical_retries":rc})
        elif result!="*":
            raise ValueError("Unfinished game has a recorded result")
    except Exception as exc:
        fail.append(f"Game {gid}: {exc}")
missing=set(range(1,max(ids,default=0)+1))-ids
if missing: fail.append("Missing assigned game IDs "+str(sorted(missing)[:30]))
actual={"mirage_wins":score["mirage_wins"],"beast_wins":score["beast_wins"],
        "draws":score["draws"],"completed_chess_games":sum(term.values()),
        "mirage_points":score["mirage_wins"]+score["draws"]/2,
        "beast_points":score["beast_wins"]+score["draws"]/2}
published=index.get("score",{})
for k,v in actual.items():
    if published.get(k)!=v:
        fail.append(f"Score {k}: published {published.get(k)} independently {v}")
if index.get("completed_chess_games")!=actual["completed_chess_games"]:
    fail.append("Index completed total mismatch")
report={"snapshot_run":index.get("source_run_id"),
        "snapshot_published_utc":index.get("published_utc"),
        "audited_game_ids":len(ids),"finished_game_results":actual["completed_chess_games"],
        "unfinished_with_no_result":sum(e["status"]!="finished" for e in index["games"]),
        "independent_match_score":actual,
        "independent_chess_terminations":dict(term),
        "technical_retry_diagnostics":dict(retries),
        "min_finished_plies":min(lengths,default=None),
        "max_finished_plies":max(lengths,default=None),
        "example_verified_games":examples,
        "failures":fail,
        "all_recorded_results_are_verified_chess_results":not fail}
print("INDEPENDENT_CHESS_RESULT_AUDIT")
print(json.dumps(report,indent=2,sort_keys=True))
Path("beast_result_audit.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
if fail:raise SystemExit("INDEPENDENT_AUDIT_FAILED")
