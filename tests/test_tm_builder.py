from pathlib import Path
import tempfile, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from localization_tm import seed_db, init_db, lookup, validate_tokens, add_tm
from pak_builder import nrv2b_compress
from pak_core import nrv2b, decompress_frame_spr

def main():
    with tempfile.TemporaryDirectory() as td:
        dbp=Path(td)/'localization.db'; seed_db(dbp); db=init_db(dbp)
        assert lookup(db,'$Mỉm cười')[0]=='微笑'
        assert validate_tokens('A <c=g>0/1<c> %d','甲 <c=g>0/1<c> %d')[0]
        assert not validate_tokens('A <c=g>0/1<c> %d','甲 <c=g>1/1<c>')[0]
        add_tm(db,'Nhận <c=g>0/1<c>','领取 <c=g>0/1<c>')
        db.close()
    for raw in (b'',b'hello',bytes(range(256))*2):
        packed=nrv2b_compress(raw); assert nrv2b(packed,len(raw))==raw
    # Method 17: header/palette + frame length table + per-frame streams.
    header=bytearray(32)
    header[:4]=b'SPR\0'
    header[12:16]=(2).to_bytes(2,'little')+(0).to_bytes(2,'little')
    frames=[b'A'*128,bytes(range(64))]
    packed_frames=[nrv2b_compress(frames[0]),frames[1]]
    table=(len(packed_frames[0]).to_bytes(4,'little')+len(frames[0]).to_bytes(4,'little',signed=True)+
           len(packed_frames[1]).to_bytes(4,'little')+(-len(frames[1])).to_bytes(4,'little',signed=True))
    stored=bytes(header)+table+b''.join(packed_frames)
    expected=(bytes(header)+b'\0\0\0\0'+len(frames[0]).to_bytes(4,'little')+
              len(frames[0]).to_bytes(4,'little')+len(frames[1]).to_bytes(4,'little')+b''.join(frames))
    assert decompress_frame_spr(stored,len(expected))==expected
    print('TM/token/NRV2B tests: PASS')
if __name__=='__main__': main()
