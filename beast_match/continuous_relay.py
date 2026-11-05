#!/usr/bin/env python3
"""Keep the GitHub-hosted Mirage D8 vs Beast match advancing across its 6h job cap.

Runs only in the inexpensive scheduled/completion relay workflow. The actual
match worker has NO chess clock or artificial per-move deadline. Only the
chess match's published, python-chess verified result count can mark complete.
"""
import base64
import datetime as dt
import json
import os
import subprocess
import sys

REPOSITORY=os.environ.get("GITHUB_REPOSITORY","TanTzeHerng/mirage-d8-free-remote-20261031")
WORKFLOW="mirage-beast-claude-fast-fresh.yml"
TOTAL_GAMES=2*81523
# Ahead of GitHub's 6h hard cap, request the next job once. Concurrency queues
# it until the old job finishes; the relay itself does not stop the old game.
PREQUEUE_AFTER=dt.timedelta(hours=5,minutes=25)
STATUS_ACTIVE={"in_progress","queued","waiting","pending","requested"}

def api(path,post=False):
    cmd=["gh","api"]
    if post:cmd.extend(["-X","POST"])
    cmd.append("repos/"+REPOSITORY+"/"+path)
    if post:cmd.extend(["-f","ref=main"])
    p=subprocess.run(cmd,text=True,capture_output=True,timeout=60)
    if p.returncode:
        raise RuntimeError("GitHub API failed "+path+": "+p.stderr[-700:])
    return None if post else json.loads(p.stdout)

def read_score():
    path="contents/live/slot_score.json?ref=match-claude-beast"
    try:
        blob=api(path)
        if blob.get("encoding")!="base64":
            raise ValueError("Unexpected slot_score encoding")
        data=json.loads(base64.b64decode(blob["content"]).decode("utf-8"))
        return data.get("score",{})
    except Exception as e:
        # Missing or temporary unavailable telemetry MUST NOT mark complete.
        print("SCORE_NOT_YET_AVAILABLE",repr(e),flush=True)
        return {}

def age(run):
    stamp=run.get("run_started_at") or run.get("created_at")
    if not stamp:return dt.timedelta()
    started=dt.datetime.fromisoformat(stamp.replace("Z","+00:00"))
    return dt.datetime.now(dt.timezone.utc)-started

def main():
    if os.environ.get("BEAST_MATCH_PAUSED","").strip().lower() in {"1","true","yes","on"}:
        print("RELAY_PAUSED_BY_REPOSITORY_VARIABLE",flush=True)
        return
    score=read_score()
    scored=int(score.get("completed_chess_games",0))
    print("VERIFIED_GAME_RESULTS",scored,"/",TOTAL_GAMES,
          "mirage",score.get("mirage_wins"),
          "beast",score.get("beast_wins"),
          "draws",score.get("draws"),flush=True)
    if scored>=TOTAL_GAMES:
        print("ALL_VARIETY_OPENINGS_BOTH_COLORS_COMPLETE_NO_RESTART",flush=True)
        return
    result=api("actions/workflows/"+WORKFLOW+"/runs?per_page=50&branch=main")
    runs=result.get("workflow_runs",[])
    active=[r for r in runs if r.get("status") in STATUS_ACTIVE]
    queued=[r for r in active if r.get("status")!="in_progress"]
    executing=[r for r in active if r.get("status")=="in_progress"]
    print("CURRENT_EXECUTING",[(r["id"],str(age(r))) for r in executing],
          "CURRENT_QUEUED",[(r["id"],r["status"]) for r in queued],flush=True)
    if queued:
        print("CONTINUATION_ALREADY_QUEUED_DO_NOT_DUPLICATE",flush=True)
        return
    if executing and max((age(r) for r in executing),default=dt.timedelta())<PREQUEUE_AFTER:
        print("CHESS_RUNNER_ACTIVE_NOT_NEAR_HOST_LIMIT",flush=True)
        return
    # No running match OR a run approaching GitHub's six-hour cancellation.
    why="prequeue_before_github_host_limit" if executing else "recover_from_completed_or_cancelled_runner"
    if os.environ.get("RELAY_DRY_RUN","").lower() in ("1","true"):
        print("DRY_RUN_WOULD_DISPATCH",why,flush=True)
        return
    api("actions/workflows/"+WORKFLOW+"/dispatches",post=True)
    print("CONTINUATION_DISPATCHED",why,flush=True)

if __name__=="__main__":
    main()
