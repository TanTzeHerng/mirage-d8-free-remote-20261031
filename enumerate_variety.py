"""Enumerate the complete legal leaf openings from Variety.bin. No engines run here."""
import chess,chess.polyglot,json,hashlib
from pathlib import Path
R=Path(__file__).resolve().parent
book=R/'Variety.bin'
assert book.is_file()
reader=chess.polyglot.open_reader(str(book))
board=chess.Board()
count=0
first=None
maximum=0
with (R/'opening_lines.jsonl').open('w',encoding='utf8') as f:
    def dfs(path,seen):
        global count,maximum,first
        moves=list(reader.find_all(board))
        legal=[]
        for entry in moves:
            m=entry.move
            if m not in board.legal_moves:continue
            legal.append(m)
        if not legal or len(path)>=80 or board.is_game_over(claim_draw=True):
            count+=1;maximum=max(maximum,len(path))
            out={'id':count,'book_plies':len(path),'uci':path}
            f.write(json.dumps(out,separators=(',',':'))+'\n')
            if first is None:first=out
            return
        for move in legal:
            board.push(move)
            dfs(path+[move.uci()],seen)
            board.pop()
    dfs([],set())
reader.close()
summary={'source':'Jeroen Noomen Variety.bin','sha256':hashlib.sha256(book.read_bytes()).hexdigest(),
    'book_entries':book.stat().st_size//16,'leaf_openings':count,'paired_games':count*2,
    'longest_book_plies':maximum,'first_line':first}
(R/'opening_schedule_summary.json').write_text(json.dumps(summary,indent=2)+'\n')
print('VARIETY_ENUMERATION_COMPLETE',json.dumps(summary),flush=True)
