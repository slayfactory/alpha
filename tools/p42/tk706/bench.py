#!/usr/bin/env python3
# TK-706 fixture bench: one engine call from the saved light-call argv, with overridden knobs. Prints one JSON line.
import sys,os,json,time,resource,subprocess,shutil,re,argparse
ap=argparse.ArgumentParser()
ap.add_argument('--tag',required=True); ap.add_argument('--variants',required=True)
ap.add_argument('--every',type=int,default=1); ap.add_argument('--threads',type=int,default=7)
ap.add_argument('--cap-gb',type=int,default=24); ap.add_argument('--sig',default='')
ap.add_argument('--bin',default='/data/p42-tk683/bin/alpha-tk683l-plain.562f6899')
ap.add_argument('--wide',default="50"); ap.add_argument('--perf',default='')
ap.add_argument('--stat',default=''); ap.add_argument('--window',default='wc'); ap.add_argument('--sym-every',type=int,default=1)
a=ap.parse_args()
A=open('/root/perftest/cmd.bin','rb').read().split(b'\0')[:-1]
A=[x.decode() for x in A]
root='/root/perf706/bench'; os.makedirs(root,exist_ok=True)
out=f'{root}/out-{a.tag}'; shutil.rmtree(out,ignore_errors=True)
vf=f'{root}/var-{a.tag}.txt'
L=[l for l in open(a.variants) if l.strip()]
open(vf,'w').writelines(L[::a.every])
def setv(k,v):
    i=A.index(k); A[i+1]=v
A[0]=a.bin
i=A.index('--symbols'); A[i+1]=','.join(A[i+1].split(',')[::a.sym_every])
setv('--variants',vf); setv('--out',out); setv('--threads',str(a.threads)); setv('--cache',f'/data/p42-tk621/{a.window}/cache'); setv('--trades',f'/data/p42-tk621/{a.window}/aggtrades')
if '--cap-gb' in A: setv('--cap-gb',str(a.cap_gb))
else: A+=['--cap-gb',str(a.cap_gb)]
if '--sig-cache' in A:
    if a.sig: setv('--sig-cache',a.sig)
    else:
        i=A.index('--sig-cache'); del A[i:i+2]
elif a.sig: A+=['--sig-cache',a.sig]
T0={'wa':'2026-06-17','wb':'2026-07-25','wc':'2026-09-01'}
env=dict(os.environ); env.update({'P42_WIDE':a.wide,'TK674_UNI':'c539','P42_T0':T0[a.window],'P42_SIDES':'short'})
if a.wide=='0': env.pop('P42_WIDE')
cmd=A
if a.perf: cmd=['perf','record','-F','99','-o',a.perf,'--']+A
if a.stat: cmd=['perf','stat','-x,','-o',a.stat,'-e','cycles,instructions,branches,branch-misses,cache-misses,L1-dcache-load-misses,LLC-load-misses,dTLB-load-misses,page-faults','--']+A
r0=resource.getrusage(resource.RUSAGE_CHILDREN); t0=time.time()
p=subprocess.run(cmd,env=env,cwd='/data/p42-tk674c/code-s4',stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
wall=time.time()-t0; r1=resource.getrusage(resource.RUSAGE_CHILDREN)
err=p.stderr
import hashlib
def treehash(root):
    h=hashlib.sha256(); n=0
    for dp,dn,fn in sorted(os.walk(root)):
        dn.sort()
        for f in sorted(fn):
            fp=os.path.join(dp,f); h.update(os.path.relpath(fp,root).encode()); h.update(open(fp,"rb").read()); n+=1
    return h.hexdigest()[:16],n
th,tn=treehash(out) if os.path.isdir(out) else (None,0)
g=lambda pat:(re.search(pat,err) or [None,None])[1]
d=dict(tag=a.tag,rc=p.returncode,wall=round(wall,2),threads=a.threads,cap=a.cap_gb,every=a.every,nvar=len(L[::a.every]),
 user=round(r1.ru_utime-r0.ru_utime,2),sys=round(r1.ru_stime-r0.ru_stime,2),maxrss_gb=round(r1.ru_maxrss/1048576,2),
 minflt=r1.ru_minflt-r0.ru_minflt,majflt=r1.ru_majflt-r0.ru_majflt,nvcsw=r1.ru_nvcsw-r0.ru_nvcsw,nivcsw=r1.ru_nivcsw-r0.ru_nivcsw,
 load=g(r'загружено символов \d+ за ([\d.]+)'),ph1_end=g(r'фаза 1 готова за ([\d.]+)'),warm=g(r'разогрев суток сделок: ([\d.]+)'),
 ph2=g(r'фаза 2: стена ([\d.]+)'),total=g(r'готово: \d+ вариантов, ([\d.]+)'),reads=g(r'файлов прочитано (\d+)'),
 sc=g(r'sig-cache: (hit \d+ miss \d+)'),ws=g(r'(WS: .*)'),memo=g(r'(память исходов: .*)'),tree=th,nfiles=tn,bin=os.path.basename(a.bin))
print(json.dumps(d,ensure_ascii=False),flush=True)
open(f'{root}/err-{a.tag}.txt','w').write(err[-3000:])
shutil.rmtree(out,ignore_errors=True)
