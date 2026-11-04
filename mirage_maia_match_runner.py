"""Eight concurrent, real-ROM Mirage D8 vs official Maia3 79M chess games.
The full Variety.bin leaf opening is entered with ENT, preserving game history.
Only python-chess-confirmed chess results score; technical errors replay and retry.
"""
import os,re,json,sys,time,threading,queue,subprocess,traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import chess,chess.pgn
ROOT=Path(__file__).resolve().parent
OUT=ROOT/'match_data'
OUT.mkdir(exist_ok=True)
DEADLINE=time.monotonic()+int(os.getenv('MATCH_BUDGET_SECONDS','3200'))
EMULATOR=os.getenv('MIRAGE_EMULATOR','/usr/games/mame')
PRINT_LOCK=threading.Lock()
def log(*a):
    with PRINT_LOCK: print(time.strftime('%Y-%m-%dT%H:%M:%S'),*a,flush=True)
def atomic_write(path,data):
    p=Path(path);tmp=p.with_name(p.name+'.tmp')
    tmp.write_text(data,encoding='utf-8');tmp.replace(p)
def current_batch_start():
    p=OUT/'batch_state.json'
    start=1
    if p.exists():start=int(json.loads(p.read_text())['next_opening_id'])
    # Advance only after all eight games in the current batch have actual chess results.
    while start<=81523:
        ids=[(x-1)*2+j for x in range(start,min(start+4,81524)) for j in (1,2)]
        if not ids:break
        all_finished=True
        for gid in ids:
            p=OUT/('game_%06d'%gid)/'status.json'
            if not p.exists():
                all_finished=False;break
            state=json.loads(p.read_text())
            if state['status']!='finished' or state['chess_result']=='*':
                all_finished=False;break
        if not all_finished:break
        start+=4
    atomic_write(OUT/'batch_state.json',json.dumps({'next_opening_id':start})+'\n')
    return start
def load_book_seeds(k=4):
    start=current_batch_start()
    if start>81523:return []
    out=[]
    with (ROOT/'opening_lines.jsonl').open(encoding='utf8') as f:
        for line in f:
            data=json.loads(line)
            if int(data['id'])<start:continue
            out.append(data)
            if len(out)==k:break
    assert len(out)==min(k,81524-start)
    for x in out:
        b=chess.Board()
        for u in x['uci']:
            m=chess.Move.from_uci(u)
            if m not in b.legal_moves:raise ValueError('Invalid opening '+str(x))
            b.push(m)
    return out
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
        expiry=time.monotonic()+seconds
        while time.monotonic()<expiry:
            try:s=self.q.get(timeout=min(2,max(.02,expiry-time.monotonic())))
            except queue.Empty:
                if self.p.poll() is not None:raise RuntimeError('Maia exited '+str(self.p.returncode))
                continue
            if s.startswith('IO_ERROR'):raise RuntimeError(s)
            if re.search(pattern,s):return s
        raise TimeoutError('Maia no response '+pattern)
    def move(self,board):
        with self.lock:
            if time.monotonic()>=DEADLINE:raise TimeoutError('Runner budget')
            # Full move history, not a static FEN: Maia 3's UCI history mode is enabled.
            moves=' '.join(m.uci() for m in board.move_stack)
            self._send('position startpos moves '+moves)
            self._send('go nodes 1')
            msg=self._expect(r'^bestmove [a-h][1-8][a-h][1-8][qrbn]?(?: |$)',360)
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
        for f in ('worker.out','mame_stdout.log','mame_stderr.log'):
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
        cmd=[EMULATOR,'mmirage','-rompath',str(ROOT/'mame'/'roms'),
             '-noreadconfig','-nowriteconfig','-nothrottle','-nosleep','-video','none',
             '-sound','none','-skip_gameinfo','-autoboot_delay','1',
             '-autoboot_script',str(wd/'game_driver.lua')]
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
        while time.monotonic()<DEADLINE:
            for line in self.events():
                if line.startswith('LUA_ERROR'):raise RuntimeError(line)
                if line.startswith('MOVE '):
                    x=re.match(r'^MOVE ([a-h][1-8][a-h][1-8]) EMU ([0-9.]+)',line)
                    if not x:raise ValueError('Malformed Mirage move '+line)
                    return x.group(1),float(x.group(2))
            if self.proc.poll() is not None:
                raise RuntimeError('Mirage exited during search rc='+str(self.proc.returncode))
            time.sleep(.8)
        raise TimeoutError('Runner budget during Mirage search')
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
    pg.headers['Event']='Mephisto Mirage D8 99:99 vs Maia3 79M 1-node'
    pg.headers['White']='Mephisto Mirage D8' if color else 'Maia3 79M'
    pg.headers['Black']='Maia3 79M' if color else 'Mephisto Mirage D8'
    pg.headers['OpeningId']=str(seed['id'])
    pg.headers['MirageTime']='99:99'
    pg.headers['MaiaElo']='2600'
    pg.headers['Result']=result_state(board)
    atomic_write(wd/'current.pgn',str(pg)+'\n')
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
    while time.monotonic()<DEADLINE:
        if board.outcome(claim_draw=True):
            log('CHESS_RESULT',gid,result_state(board),'plies',len(board.move_stack))
            write_state(gid,seed,board,color,errors,'finished',mirage_thinks=last_think)
            return
        session=None
        try:
            session=MirageSession(wd,[m.uci() for m in board.move_stack],color)
            write_state(gid,seed,board,color,errors,'active',mirage_thinks=last_think)
            while time.monotonic()<DEADLINE:
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
        except TimeoutError as e:
            if time.monotonic()>=DEADLINE:
                write_state(gid,seed,board,color,errors,'paused',str(e),last_think)
                return
            errors+=1
            log('TECHNICAL_RETRY',gid,str(e))
            write_state(gid,seed,board,color,errors,'recovering',str(e),last_think)
        except Exception as e:
            errors+=1
            log('TECHNICAL_RETRY',gid,str(e))
            with (wd/'technical_errors.log').open('a') as f:
                f.write(time.strftime('%FT%TZ',time.gmtime())+' '+repr(e)+'\n')
                traceback.print_exc(file=f)
            write_state(gid,seed,board,color,errors,'recovering',str(e),last_think)
            time.sleep(min(15,errors*2))
        finally:
            if session:session.close()
    write_state(gid,seed,board,color,errors,'paused','Time budget exhausted',last_think)
    log('GAME_PAUSED',gid,'plies',len(board.move_stack),'no game scored')
def main():
    seeds=load_book_seeds(4)
    if not seeds:
        log('ALL_BOOK_OPENINGS_COMPLETED');return
    maia=Maia()
    try:
        with ThreadPoolExecutor(max_workers=8) as pool:
            f=[]
            for i,seed in enumerate(seeds):
                for j,color in enumerate((True,False)):
                    f.append(pool.submit(play_game,(seed["id"]-1)*2+j+1,seed,color,maia))
            for x in f:x.result()
    finally:maia.close()
    summary=[]
    for p in sorted(OUT.glob('game_*/status.json')):
        summary.append(json.loads(p.read_text()))
    scored=[x for x in summary if x['status']=='finished' and x['chess_result']!='*']
    results={'planned_full_book_paired_games':163046,
             'next_opening_id':current_batch_start(),
             'launched_games':len(summary),'completed_real_chess_games':len(scored),
             'results_by_game':[{'id':x['game_id'],'result':x['chess_result']} for x in scored],
             'games':summary}
    atomic_write(OUT/'match_status.json',json.dumps(results,indent=2)+'\n')
    log('MATCH_PILOT_CHECKPOINT',len(summary),'games',len(scored),'real chess results')
if __name__=='__main__':main()
