"""Experimental CAT diagnostic, not a gameplay interpreter or production parser.

Independent format-fact reader archived for reproducibility. Requires the local
Steam installation, extracted Act bank, native analysis copy, and bully_agr.py.
The historical local/debug diagnostic remains preserved. Never overwrites output.
"""
from pathlib import Path
import argparse, re, struct, hashlib, json, sys
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
from bully_agr import read_hxd

def hashstring(s):
    n=0
    for c in s.upper().encode('ascii',errors='ignore'): n=(n*131+c)&0x7fffffff
    return n

names={}
def add(s):
    if len(s)>1 and s.isascii():
        names.setdefault(hashstring(s),set()).add(s)

inputs=[ROOT/'local/native/Bully.steam-original.exe.unpacked.exe']
inputs+=list((ROOT/'local/extracted/Act/Act').glob('*.cat'))
for p in inputs:
    for m in re.finditer(rb'[ -~]{2,}',p.read_bytes()):
        s=m.group().decode('ascii');add(s)
        for piece in re.split(r'[/\\\s]+',s): add(piece)
for s in ['JumpActions','Jump','IdleJump','Falling','Fall','FallingOpps','FallingOppsBase','Animation','Sequence','Execute','Opportunity','CompressLeft','CompressRight','SpeedRun','Land','LocoBasic','Fall_Flail','Fall_Land','Fall_Damage','FallingHitGround','OnGround','InAir','JumpLeft','JumpRight']:
    add(s)
hxd=read_hxd(Path(r'C:/Program Files (x86)/Steam/steamapps/common/Bully Scholarship Edition/Anim/MAINPED.HXD'))
anims={a['name_hash']:a for a in hxd['animations']}
for a in hxd['animations']: add(a['name'])

def label(h):
    values=sorted(names.get(h,set()),key=lambda s:(len(s),s))
    return values[0] if values else f'#{h:08x}'

class Cat:
    def __init__(self,path):
        self.path=path;self.d=path.read_bytes();self.h=struct.unpack_from('<8I',self.d)
        self.data,self.strings,self.end=self.h[1:4]
        assert self.h[0]<=len(self.d) and not any(self.d[self.h[0]:])
        self.p=32;self.relocs={};self.tables=[]
        for t in range(2):
            count=self.r('I');table=[]
            for _ in range(count):
                value,k=self.r('IH');refs=[self.r('I') for _ in range(k)]
                table.append(dict(value=value,refs=refs))
                if t==0:
                    text=self.text(value)
                    for ref in refs:self.relocs[ref]=text
            self.tables.append(table)
        self.counts=dict(b=0,n=0,l=0,i=0,r=0)
        self.root=self.node(chr(self.r('B')),'')
        assert self.p==self.end,(hex(self.p),hex(self.end))
        assert self.counts['b']-1==self.h[4]
        assert self.counts['n']+self.counts['l']==self.h[5]
        assert self.counts['i']+self.counts['r']==self.h[6]
        assert self.counts['l']==self.h[7]
    def r(self,f):
        v=struct.unpack_from('<'+f,self.d,self.p);self.p+=struct.calcsize('<'+f)
        return v[0] if len(v)==1 else v
    def text(self,o):
        p=self.strings+o;assert self.strings<=p<self.h[0]
        return self.d[p:self.d.index(0,p,self.h[0])].decode('ascii')
    def node(self,kind,parent):
        self.counts[kind]+=1;at=self.p-1;name=self.r('I')
        if kind in 'ir':
            result=dict(kind=kind,offset=at,name=self.text(name),target=self.text(self.r('I')))
            result['path']=parent+'/'+result['name'];return result
        nm=label(name&0x7fffffff) if name&0x80000000 else self.text(name)
        result=dict(kind=kind,offset=at,name=nm,name_hash=f'{name&0x7fffffff:08x}',path=parent+'/'+nm)
        result['condition_offsets']=[self.r('I') for _ in range(self.r('B'))]
        result['track_offsets']=[self.r('I') for _ in range(self.r('B'))] if kind in 'nl' else []
        result['children']=[self.node(chr(self.r('B')),result['path']) for _ in range(self.r('H'))]
        return result
    def track(self,offset,seen=None):
        seen=set() if seen is None else seen
        assert offset not in seen;seen.add(offset)
        p=self.data+offset;assert self.data<=p<self.strings
        base=struct.unpack_from('<H',self.d,p)[0];p+=2
        attrs=self.track(offset+base,seen)['attrs'].copy() if base else {}
        while True:
            w=struct.unpack_from('<H',self.d,p)[0];p+=2
            size=1<<((w>>1)&3);pos=w>>3;raw=self.d[p:p+size];assert len(raw)==size
            desc=dict(hex=raw.hex(),uint=int.from_bytes(raw,'little'),file_offset=p)
            if size==4:
                desc['float']=struct.unpack('<f',raw)[0]
                h=desc['uint']&0x7fffffff
                if h in names:desc['hash_names']=sorted(names[h])
                if desc['uint'] in anims:
                    a=anims[desc['uint']];desc['animation']={k:a[k] for k in ['name','duration','index']}
            if p-self.data in self.relocs:desc['string']=self.relocs[p-self.data]
            attrs[str(pos)]=desc;p+=size
            if not(w&1):break
        return dict(offset=offset,base=base,type=label(attrs.get('0',{}).get('uint',0)&0x7fffffff),attrs=attrs)
    def flat(self,n=None):
        n=self.root if n is None else n
        yield n
        for c in n.get('children',[]):yield from self.flat(c)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('names',nargs='*',default=['JumpActions','Player','GlobalLoco','Locomotion'])
    parser.add_argument('--output-report',required=True,type=Path,help='New JSON destination; existing files are refused.')
    args=parser.parse_args()
    if args.output_report.exists():
        parser.error(f'Output already exists: {args.output_report}')
    outputs=[]
    for name in args.names:
        if Path(name).name!=name or name in ['.','..']:
            parser.error(f'Expected CAT stem, not a path: {name}')
        p=ROOT/'local/extracted/Act/Act'/f'{name}.cat';cat=Cat(p)
        for n in cat.flat():
            n['tracks']=[cat.track(o) for o in n.get('track_offsets',[])]
        out=dict(source=str(p.relative_to(ROOT)),sha256=hashlib.sha256(cat.d).hexdigest(),header=list(cat.h),counts=cat.counts,tree=cat.root)
        outputs.append(out)
    args.output_report.parent.mkdir(parents=True,exist_ok=True)
    with args.output_report.open('x',encoding='utf-8') as f:
        json.dump({'classification':'experimental_CAT_diagnostic_not_gameplay_runtime','files':outputs},f,indent=2)
        f.write('\n')
    print(args.output_report,len(outputs),'trees')


if __name__=='__main__':
    main()
