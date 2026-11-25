import os,sys,csv,collections
d=sys.argv[1]
G=collections.defaultdict(list)  # (sym,entry_ms)->[hold_ms,...]
nvar=0
for v in os.listdir(d):
    f=f'{d}/{v}/short-trades.csv'
    if not os.path.exists(f): continue
    nvar+=1
    with open(f) as fh:
        r=csv.reader(fh); h=next(r); i0=h.index('sym'); ie=h.index('entry_ms'); ix=h.index('exit_ms')
        for row in r:
            G[(row[i0],int(row[ie]))].append(int(row[ix])-int(row[ie]))
n=sum(len(v) for v in G.values()); tot=sum(sum(v) for v in G.values())
mx=sum(max(v) for v in G.values())
print('variants',nvar,'walks',n,'distinct entries',len(G),'walks/entry %.1f'%(n/len(G)))
print('sum hold s %.0f'%(tot/1000),'sum over entries of max hold s %.0f'%(mx/1000),'ideal shared-trunk factor %.2fx'%(tot/mx))
# per-entry distribution of group size
c=collections.Counter(min(len(v),2000)//100*100 for v in G.values())
print('group-size hist (x100):',sorted(c.items()))
# median group-size-weighted
import statistics
sz=[len(v) for v in G.values()]
print('group size median',statistics.median(sz),'mean %.1f'%statistics.mean(sz),'max',max(sz))
# the same but truncated at 5th/10th percentile exits: if half the variants exit before X
