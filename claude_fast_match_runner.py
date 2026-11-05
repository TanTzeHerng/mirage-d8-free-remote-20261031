"""Eight concurrent, real-ROM Mirage D8 vs official Maia3 79M chess games.
The full Variety.bin leaf opening is entered with ENT, preserving game history.
Only python-chess-confirmed chess results score; technical errors replay and retry.
"""
import os,re,json,sys,time,threading,queue,subprocess,traceback,shutil,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import chess,chess.pgn
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'match_fast_data'
OUT.mkdir(exist_ok=True)
NATIVE_BINARY=os.getenv('MIRAGE_NATIVE_BINARY',str(ROOT/'claude_fast_native'/'mm_game_driver'))
NATIVE_ROM=os.getenv('MIRAGE_NATIVE_ROM',str(ROOT/'claude_fast_native'/'g79'))
PRINT_LOCK=threading.Lock()
def log(*a):
    with PRINT_LOCK: print(time.strftime('%Y-%m-%dT%H:%M:%S'),*a,flush=True)
def atomic_write(path,data):
    p=Path(path);tmp=p.with_name(p.name+'.tmp')
    tmp.write_text(data,encoding='utf-8');tmp.replace(p)
# Eight independently replenished worker slots; every book leaf is paired once.
MAX_CONCURRENT_GAMES = 8
SCHEDULER_PATH = OUT / "scheduler.json"
def load_all_book_seeds():
    with (ROOT / "opening_lines.jsonl").open(encoding="utf8") as f:
        seeds=[json.loads(line) for line in f]
    if len(seeds)!=81523:
        raise ValueError("Variety book enumeration does not have 81523 leaf lines")
    for i,seed in enumerate(seeds,1):
        if int(seed["id"])!=i:
            raise ValueError("Book opening IDs are not contiguous at "+str(i))
    return seeds

class GameScheduler:
    """Slot ownership is checkpointed before starting each new game."""
    def __init__(self, seeds):
        self.lock=threading.Lock()
        self.max_gid=2*len(seeds)
        existing={}
        for path in OUT.glob("game_*/status.json"):
            try:
                state=json.loads(path.read_text(encoding="utf8"))
                existing[int(state["game_id"])]=state
            except (ValueError,KeyError):
                raise RuntimeError("Invalid persistent game status "+str(path))
        self.next_game_id=max(9,max(existing,default=0)+1)
        if SCHEDULER_PATH.is_file():
            saved=json.loads(SCHEDULER_PATH.read_text(encoding="utf8"))
            self.next_game_id=max(self.next_game_id,int(saved["next_game_id"]))
            self.slots={str(int(k)):int(v) for k,v in saved["slots"].items()
                        if 1<=int(k)<=MAX_CONCURRENT_GAMES and 1<=int(v)<=self.max_gid}
        else:
            self.slots={str(i):i for i in range(1,MAX_CONCURRENT_GAMES+1)}
        self.existing=existing
        self._save()
    def _save(self):
        assigned=list(self.slots.values())
        if len(assigned)!=len(set(assigned)):
            raise RuntimeError("A game was assigned to more than one slot")
        state={"version":1,"next_game_id":self.next_game_id,
               "slots":self.slots,"max_game_id":self.max_gid,
               "updated_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
        atomic_write(SCHEDULER_PATH,json.dumps(state,indent=2)+"\n")
    def _is_finished(self,gid):
        status=OUT/("game_%06d"%gid)/"status.json"
        if not status.exists():return False
        state=json.loads(status.read_text(encoding="utf8"))
        return state.get("status")=="finished" and state.get("chess_result") not in (None,"*")
    def claim(self,slot):
        key=str(slot)
        with self.lock:
            gid=self.slots.get(key)
            if gid is not None and not self._is_finished(gid):
                return gid
            self.slots.pop(key,None)
            owned=set(self.slots.values())
            # Recover unfinished games even if a preceding run ended between
            # game assignment and a scheduler-state checkpoint.
            pending=sorted(g for g in self.existing if g not in owned
                           and not self._is_finished(g))
            if pending:
                gid=pending[0]
                self.existing.pop(gid,None)
            elif self.next_game_id<=self.max_gid:
                gid=self.next_game_id
                self.next_game_id+=1
            else:
                self._save()
                return None
            self.slots[key]=gid
            self._save()
            return gid

class Maia:
    def __init__(self):
        self.lock=threading.Lock();self.q=queue.Queue()
        cmd=[sys.executable,'-m','maia3.uci','--model','maia3-79m',
             '--device','cpu','--use-uci-history','--temperature','0']
        self.err=open(OUT/'maia_stderr.log','a')
        self.p=subprocess.Popen(cmd,cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
             stderr=self.err,text=True,bufsize=1)
        threading.Thread(target=self._read,daemon=True).start()
        self._send('uci');self._expect(r'^uciok$',90)
        for k in ('Elo','SelfElo','OppoElo'):
            self._send('setoption name '+k+' value 2600')
        self._send('setoption name Temperature value 0')
        self._send('isready');self._expect(r'^readyok$',700)
        log('MAIA_READY official Maia3-79M nodes=1 Elo=SelfElo=OppoElo=2600 temperature=0')
    def _read(self):
        try:
            for s in self.p.stdout:
                line=s.strip()
                if line:self.q.put(line)
        except Exception as e:self.q.put('IO_ERROR '+str(e))
    def _send(self,s):
        self.p.stdin.write(s+'\n');self.p.stdin.flush()
    def _expect(self,pattern,seconds):
        expiry=time.monotonic()+seconds if seconds is not None else None
        while expiry is None or time.monotonic()<expiry:
            try:s=self.q.get(timeout=2 if expiry is None else min(2,max(.02,expiry-time.monotonic())))
            except queue.Empty:
                if self.p.poll() is not None:raise RuntimeError('Maia exited '+str(self.p.returncode))
                continue
            if s.startswith('IO_ERROR'):raise RuntimeError(s)
            if re.search(pattern,s):return s
        raise TimeoutError('Maia no response '+pattern)
    def move(self,board):
        with self.lock:
            # Full move history, not a static FEN: Maia 3's UCI history mode is enabled.
            moves=' '.join(m.uci() for m in board.move_stack)
            self._send('position startpos moves '+moves)
            self._send('go nodes 1')
            msg=self._expect(r'^bestmove [a-h][1-8][a-h][1-8][qrbn]?(?: |$)',None)
            u=msg.split()[1]
            m=chess.Move.from_uci(u)
            if m not in board.legal_moves:raise ValueError('Maia illegal move '+u+' '+board.fen())
            return m
    def close(self):
        try:self._send('quit')
        except Exception:pass
        try:self.p.wait(timeout=15)
        except subprocess.TimeoutExpired:self.p.kill()
        self.err.close()
class MirageSession:
    def __init__(self,wd,moves,mirage_color):
        self.wd=wd;self.proc=None;self.cmdseq=0;self.cursor=0;self.leftover=''
        self.out=None;self.err=None
        wd.mkdir(parents=True,exist_ok=True)
        for f in wd.glob('cmd_*.txt'):f.unlink()
        for f in ('worker.out','mame_stdout.log','mame_stderr.log',
                  'native_search_state.bin','native_search_state.json',
                  'native_search_state.bin.tmp','native_search_state.json.tmp'):
            (wd/f).unlink(missing_ok=True)
        atomic_write(wd/'job.txt','')
        atomic_write(wd/'book_moves.txt','\n'.join(moves)+'\n')
        b=chess.Board()
        for m in moves:b.push_uci(m)
        mode='MIRAGE' if b.turn==mirage_color else 'MAIA'
        atomic_write(wd/'start_mode.txt',mode+'\n')
        lua=(ROOT/'mirage_maia_game_driver.lua').read_text()
        assert '@@BASE@@' in lua
        atomic_write(wd/'game_driver.lua',lua.replace('@@BASE@@',str(wd.resolve())+'/'))
        cmd=[NATIVE_BINARY,str(wd.resolve()),NATIVE_ROM]
        self.out=open(wd/'mame_stdout.log','ab')
        self.err=open(wd/'mame_stderr.log','ab')
        self.proc=subprocess.Popen(cmd,cwd=wd,stdout=self.out,stderr=self.err)
    def commands(self,msg):
        self.cmdseq+=1
        atomic_write(self.wd/('cmd_'+str(self.cmdseq)+'.txt'),msg+'\n')
    def events(self):
        p=self.wd/'worker.out'
        if not p.exists():return []
        with p.open('r',encoding='utf8',errors='replace') as f:
            f.seek(self.cursor);s=f.read();self.cursor=f.tell()
        s=self.leftover+s
        rows=s.split('\n')
        self.leftover=rows.pop()
        return rows
    def await_mirage(self):
        while True:
            for line in self.events():
                if line.startswith('LUA_ERROR'):raise RuntimeError(line)
                if line.startswith('MOVE '):
                    x=re.match(r'^MOVE ([a-h][1-8][a-h][1-8]) EMU ([0-9.]+)',line)
                    if not x:raise ValueError('Malformed Mirage move '+line)
                    return x.group(1),float(x.group(2))
            if self.proc.poll() is not None:
                raise RuntimeError('Mirage exited during search rc='+str(self.proc.returncode))
            time.sleep(.8)
    def close(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.commands('QUIT')
                try:self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.terminate()
                    try:self.proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:self.proc.kill()
        finally:
            if self.out:self.out.close()
            if self.err:self.err.close()
def result_state(board):
    o=board.outcome(claim_draw=True)
    return o.result() if o else '*'
def write_state(gid,seed,board,color,error_count,status,error=None,mirage_thinks=None):
    wd=OUT/('game_%06d'%gid)
    wd.mkdir(parents=True,exist_ok=True)
    state={'game_id':gid,'opening_id':seed['id'],
       'mirage_color':'white' if color else 'black',
       'maia_color':'black' if color else 'white',
       'book_plies':len(seed['uci']),
       'played_plies':len(board.move_stack)-len(seed['uci']),
       'move_history_uci':[m.uci() for m in board.move_stack],
       'fen':board.fen(),'status':status,
       'chess_result':result_state(board),
       'technical_error_count':error_count,'last_error':error,
       'last_mirage_emulated_seconds':mirage_thinks,
       'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())}
    atomic_write(wd/'status.json',json.dumps(state,indent=2)+'\n')
    pg=chess.pgn.Game.from_board(board)
    pg.headers['Event']='Claude native Mephisto Mirage D8 99:99 vs Maia3 79M 1-node'
    pg.headers['White']='Mephisto Mirage D8' if color else 'Maia3 79M'
    pg.headers['Black']='Maia3 79M' if color else 'Mephisto Mirage D8'
    pg.headers['OpeningId']=str(seed['id'])
    pg.headers['MirageTime']='99:99'
    pg.headers['MaiaElo']='2600'
    pg.headers['Result']=result_state(board)
    atomic_write(wd/'current.pgn',str(pg)+'\n')
def hard_recovery_archive(gid,board,errors,reason):
    """Save one game's full legal state, search-memory snapshot and diagnostics.
    Firmware-internal snapshot is preserved for analysis, not falsely reloaded.
    Hard reset replays the complete legal move history and searches afresh.
    """
    wd=OUT/("game_%06d"%gid)
    dest=wd/"recovery"/("attempt_%06d"%errors)
    dest.mkdir(parents=True,exist_ok=True)
    protected=("status.json","current.pgn","worker.out","mame_stdout.log",
               "mame_stderr.log","native_search_state.bin",
               "native_search_state.json","book_moves.txt","start_mode.txt")
    saved=[]
    for filename in protected:
        src=wd/filename
        if src.is_file():
            try:
                shutil.copy2(src,dest/filename)
                saved.append(filename)
            except OSError as exc:
                log("RECOVERY_COPY_ERROR",gid,filename,str(exc))
    info={"game_id":gid,"attempt":errors,"reason":str(reason),
          "utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),
          "fen":board.fen(),"uci_history":[m.uci() for m in board.move_stack],
          "legal_position_saved":True,
          "native_search_snapshot_saved":"native_search_state.bin" in saved,
          "native_snapshot_reloaded":False,
          "restart":"hard_reset_worker_and_replay_legal_game_then_fresh_search",
          "saved_files":saved}
    atomic_write(dest/"recovery_manifest.json",json.dumps(info,indent=2)+"\n")
    atomic_write(wd/"recovery_status.json",json.dumps(info,indent=2)+"\n")
    log("GAME_HARD_RECOVERY",gid,"attempt",errors,"snapshots",saved,
        "search_restart_from_fen",board.fen())

def play_game(gid,seed,color,maia):
    wd=OUT/('game_%06d'%gid)
    state_file=wd/'status.json'
    previous=json.loads(state_file.read_text()) if state_file.exists() else None
    if previous and previous.get('status')=='finished' and previous.get('chess_result')!='*':
        log('CHESS_RESULT_ALREADY_CHECKPOINTED',gid,previous['chess_result'])
        return
    moves=list(previous.get('move_history_uci',seed['uci'])) if previous else list(seed['uci'])
    if moves[:len(seed['uci'])]!=list(seed['uci']):
        raise ValueError('Saved game does not match its opening-book line '+str(gid))
    board=chess.Board()
    for u in moves:
        m=chess.Move.from_uci(u)
        if m not in board.legal_moves:
            raise ValueError('Saved move is illegal '+u+' for game '+str(gid))
        board.push(m)
    errors=int(previous.get('technical_error_count',0)) if previous else 0
    last_think=previous.get('last_mirage_emulated_seconds') if previous else None
    log('GAME_RESUME' if previous else 'GAME_START',gid,'opening',seed['id'],
        'mirage_color',('white' if color else 'black'),
        'book_plies',len(seed['uci']),'fen',board.fen())
    write_state(gid,seed,board,color,errors,'starting')
    while True:
        if board.outcome(claim_draw=True):
            log('CHESS_RESULT',gid,result_state(board),'plies',len(board.move_stack))
            write_state(gid,seed,board,color,errors,'finished',mirage_thinks=last_think)
            return
        session=None
        try:
            session=MirageSession(wd,[m.uci() for m in board.move_stack],color)
            write_state(gid,seed,board,color,errors,'active',mirage_thinks=last_think)
            while True:
                if board.outcome(claim_draw=True):
                    write_state(gid,seed,board,color,errors,'finished',mirage_thinks=last_think)
                    log('CHESS_RESULT',gid,result_state(board))
                    return
                if board.turn==color:
                    move,emusec=session.await_mirage()
                    m=chess.Move.from_uci(move)
                    if m not in board.legal_moves:
                        # Underpromotion is entered on hardware through piece replacement;
                        # default queen promotion must be verified by arbiter.
                        p=chess.Move.from_uci(move+'q')
                        if p in board.legal_moves:m=p
                        else:raise ValueError('Mirage illegal move '+move+' '+board.fen())
                    board.push(m);last_think=emusec
                    log('MIRAGE_MOVE',gid,m.uci(),'emu_seconds',round(emusec,2))
                else:
                    m=maia.move(board)
                    board.push(m)
                    log('MAIA_MOVE',gid,m.uci())
                    if not board.outcome(claim_draw=True):
                        if m.promotion not in (None,chess.QUEEN):
                            raise RuntimeError('Need physical underpromotion input in Mirage '+m.uci())
                        session.commands('MOVE '+m.uci()[:4])
                write_state(gid,seed,board,color,errors,'active',mirage_thinks=last_think)
            break
        except Exception as e:
            errors+=1
            recovery_failure=e
            log("TECHNICAL_RESET",gid,"attempt",errors,repr(e))
            write_state(gid,seed,board,color,errors,"recovering",str(e),last_think)
            with (wd/"technical_errors.log").open("a",encoding="utf8") as file:
                file.write(time.strftime("%FT%TZ",time.gmtime())+" "+repr(e)+"\n")
                traceback.print_exc(file=file)
        finally:
            if session:
                try:
                    session.close()
                except Exception as close_error:
                    log("GAME_WORKER_CLOSE_ERROR",gid,str(close_error))
        # Game-specific reset only: all other Mirage processes and Maia keep running.
        # Preserve the firmware's latest CPU/RAM/search diagnostic BEFORE fresh replay.
        try:
            hard_recovery_archive(gid,board,errors,recovery_failure)
        except Exception as archive_error:
            log("GAME_RECOVERY_ARCHIVE_FAILED",gid,repr(archive_error))
        # No chess result is assigned for an emulator or controller problem.
        # After resetting only this worker, replay the complete UCI history.
        time.sleep(min(15,errors*2))

def main():
    seeds=load_all_book_seeds()
    scheduler=GameScheduler(seeds)
    maia=Maia()
    def run_slot(slot):
        while True:
            gid=scheduler.claim(slot)
            if gid is None:
                log("BOOK_SLOT_COMPLETE",slot)
                return
            seed=seeds[(gid-1)//2]
            color=(gid%2==1)
            log("SLOT_ASSIGN",slot,"game",gid,"opening",seed["id"],
                "mirage",("white" if color else "black"))
            play_game(gid,seed,color,maia)
            state=json.loads((OUT/("game_%06d"%gid)/"status.json").read_text())
            if state["status"]!="finished" or state["chess_result"]=="*":
                raise RuntimeError("Slot returned without a chess result "+str(gid))
            log("SLOT_CHESS_RESULT",slot,gid,state["chess_result"],
                "reserving_next_game_immediately")
    try:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_GAMES) as pool:
            futures=[pool.submit(run_slot,slot) for slot in range(1,MAX_CONCURRENT_GAMES+1)]
            for future in futures:future.result()
    finally:
        maia.close()
    summary=[]
    for p in sorted(OUT.glob("game_*/status.json")):
        summary.append(json.loads(p.read_text()))
    scored=[x for x in summary if x["status"]=="finished" and x["chess_result"]!="*"]
    results={"planned_full_book_paired_games":2*len(seeds),
             "assigned_games":len(summary),
             "completed_real_chess_games":len(scored),
             "results_by_game":[{"id":x["game_id"],"result":x["chess_result"]} for x in scored]}
    atomic_write(OUT/"match_status.json",json.dumps(results,indent=2)+"\n")
    log("MATCH_COMPLETE",len(scored),"real chess results")
if __name__=="__main__":main()
