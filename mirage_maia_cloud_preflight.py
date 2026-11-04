"""Unscored original firmware and Maia3 79M preflight."""
import os,re,json,time,subprocess,sys,threading,queue
from pathlib import Path
import chess
R=Path(__file__).resolve().parent
rom=R/'mame/roms/mmirage.zip'
assert rom.is_file() and rom.stat().st_size>1000
lua=R/'mirage_driver.lua'
orig=lua.read_text(encoding='utf-8')
old='C:'+chr(92)+'Khan Academy'+chr(92)+'Mirage_D8_Chesscom2500_20261031'+chr(92)
assert old in orig
b=chess.Board()
rows=['TIME 9999','SIDE WH']
for color,side in ((False,'BL'),(True,'WH')):
    for sq,p in b.piece_map().items():
        if p.color==color:
            s=chess.square_name(sq)
            rows.append(f'PIECE {side} {p.piece_type+1} {s[0].upper()} {s[1]}')
(R/'job.txt').write_text('\n'.join(rows)+'\n')
lua.write_text(orig.replace(old,str(R)+'/'),encoding='utf-8')
try:
    cmd=[os.environ.get('MIRAGE_EMULATOR','/usr/games/mame'),'mmirage',
     '-rompath',str(R/'mame/roms'),'-noreadconfig','-nowriteconfig',
     '-nothrottle','-nosleep','-video','none','-sound','none',
     '-skip_gameinfo','-autoboot_delay','1','-autoboot_script',str(lua)]
    start=time.monotonic()
    with open(R/'mame_stdout.log','w') as so,open(R/'mame_stderr.log','w') as se:
        result=subprocess.run(cmd,cwd=R,stdout=so,stderr=se,timeout=1250)
    out=(R/'worker.out').read_text(errors='replace') if (R/'worker.out').exists() else ''
    found=re.search(r'^MOVE ([a-h][1-8][a-h][1-8]) EMU ([0-9.]+)',out,re.M)
    if result.returncode or not found:
        raise RuntimeError('Mirage rc='+str(result.returncode)+' worker_tail='+repr(out[-600:]))
    move=found.group(1)
    assert any(x.uci()[:4]==move for x in b.legal_moves)
    proof={'mirage':{'move':move,'emu_seconds':float(found.group(2)),
          'wall_seconds':round(time.monotonic()-start,2),'level':'D8','mmss':'9999'}}
    print('MIRAGE_VERIFIED',json.dumps(proof['mirage']),flush=True)
finally:
    lua.write_text(orig,encoding='utf-8')
cmd=[sys.executable,'-m','maia3.uci','--model','maia3-79m',
     '--device','cpu','--use-uci-history','--temperature','0']
with open(R/'maia_preflight_stderr.log','w') as stderr:
    p=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=stderr,text=True,bufsize=1)
    q=queue.Queue()
    threading.Thread(target=lambda:[q.put(x.strip()) for x in p.stdout],daemon=True).start()
    def send(s):p.stdin.write(s+'\n');p.stdin.flush()
    def until(pattern,seconds):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            try:line=q.get(timeout=min(2,max(.01,end-time.monotonic())))
            except queue.Empty:
                if p.poll() is not None:raise RuntimeError('Maia exited '+str(p.returncode))
                continue
            print('MAIA:',line,flush=True)
            if re.search(pattern,line):return line
        raise TimeoutError(pattern)
    try:
        send('uci');until(r'^uciok$',60)
        for key in ('Elo','SelfElo','OppoElo'):send('setoption name '+key+' value 2600')
        send('setoption name Temperature value 0')
        send('isready');until(r'^readyok$',700)
        send('ucinewgame');send('position startpos');send('go nodes 1')
        line=until(r'^bestmove [a-h][1-8][a-h][1-8][qrbn]?(?: |$)',240)
        move=line.split()[1]
        assert chess.Move.from_uci(move) in b.legal_moves
        proof['maia']={'move':move,'model':'Maia3 79M','nodes':1,
           'Elo':2600,'SelfElo':2600,'OppoElo':2600,'temperature':0}
        print('MAIA_VERIFIED',json.dumps(proof['maia']),flush=True)
        (R/'match_preflight.json').write_text(json.dumps(proof,indent=2)+'\n')
    finally:
        try:send('quit')
        except Exception:pass
        try:p.wait(timeout=10)
        except subprocess.TimeoutExpired:p.kill()
