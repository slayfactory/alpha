#!/usr/bin/env python3
"""TK-706: дублирование входов на трёх уровнях по архивам клеток (cells-arch/<окно>-<сторона>/*.tar.zst); только чтение.
  python3 dup3.py <каталог архивов> <side: short|long> [--limit N] -> строки итога в stdout
Ключ входа = (монета, entry_ms). Вес = время удержания (мс) — прокси числа сделок, которые проход читает."""
import sys,os,glob,subprocess,tarfile,collections,time,json
d=sys.argv[1]; side=sys.argv[2]; lim=int(sys.argv[sys.argv.index('--limit')+1]) if '--limit' in sys.argv else 10**9
files=sorted(glob.glob(os.path.join(d,'*.tar.zst')),key=os.path.getmtime)[:lim]
G={}                       # key -> [n_walks_total, hold_total, maxhold_union, n_cells]
S1_hold=0; S1_max=0        # внутри клетки: сумма удержаний всех ходов; сумма максимумов по входу внутри клетки
S2_max_cells=0             # сумма по клеткам и входам максимума в клетке (=S1_max)
reuse_by_day=collections.defaultdict(lambda:[0,0,0])  # день архива -> [вес новых, вес уже виденных, ходов]
vnames=collections.Counter(); ncell=0; nwalk=0
t0=time.time()
for fn in files:
    day=time.strftime('%m-%d',time.localtime(os.path.getmtime(fn)))
    p=subprocess.Popen(['zstd','-dc',fn],stdout=subprocess.PIPE)
    C={}
    try:
        tf=tarfile.open(fileobj=p.stdout,mode='r|')
        for m in tf:
            if not m.name.endswith(f'{side}-trades.csv'): continue
            parts=m.name.split('/')
            vnames[parts[-2]]+=1
            b=tf.extractfile(m).read()
            first=True
            for ln in b.split(b'\n'):
                if first: first=False; continue
                if not ln: continue
                c=ln.split(b',')
                try: e=int(c[-2]); x=int(c[-1])
                except: continue
                k=(c[0],e); h=x-e
                v=C.get(k)
                if v is None: C[k]=[1,h,h]
                else: v[0]+=1; v[1]+=h; v[2]=max(v[2],h)
    except Exception as ex:
        print('ERR',fn,ex,file=sys.stderr)
    p.stdout.close(); p.wait()
    if not C: continue
    ncell+=1
    rb=reuse_by_day[day]
    for k,v in C.items():
        nwalk+=v[0]; S1_hold+=v[1]; S1_max+=v[2]
        g=G.get(k)
        if g is None:
            G[k]=[v[0],v[1],v[2],1]; rb[0]+=v[2]
        else:
            g[0]+=v[0]; g[1]+=v[1]; g[2]=max(g[2],v[2]); g[3]+=1; rb[1]+=v[2]
        rb[2]+=v[0]
S3_max=sum(g[2] for g in G.values())
print(json.dumps(dict(cells=ncell,walks=nwalk,entries_union=len(G),entries_sum_per_cell=sum(g[3] for g in G.values()),
    hold_all_s=S1_hold/1000,sum_max_in_cell_s=S1_max/1000,sum_max_union_s=S3_max/1000,
    F1_in_call=S1_hold/S1_max,F2_across_calls=S1_max/S3_max,F12_total=S1_hold/S3_max,
    distinct_variant_dirs=len(vnames),variant_dirs_total=sum(vnames.values()),dup_variant_dirs=sum(c-1 for c in vnames.values() if c>1),
    secs=round(time.time()-t0)),ensure_ascii=False))
for day,(nw,old,w) in sorted(reuse_by_day.items()):
    tot=nw+old
    print(day,'walks',w,'weight(new)=%.0f s weight(already seen)=%.0f s  -> %.1f%% of walk-time already seen in earlier archives'%(nw/1000,old/1000,100*old/tot if tot else 0))
