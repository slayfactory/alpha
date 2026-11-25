import os,sys,csv,collections
d=sys.argv[1]
G=collections.defaultdict(list)
for v in os.listdir(d):
    f=f'{d}/{v}/short-trades.csv'
    if not os.path.exists(f): continue
    with open(f) as fh:
        r=csv.reader(fh); h=next(r); i0=h.index('sym'); ie=h.index('entry_ms'); ix=h.index('exit_ms'); ik=h.index('kind'); it=h.index('takes'); ig=h.index('gross')
        for row in r:
            G[(row[i0],int(row[ie]))].append((int(row[ix]),row[ik],row[it],row[ig]))
n=sum(len(v) for v in G.values())
dist=sum(len(set(v)) for v in G.values())
distexit=sum(len(set((x[0],x[1]) for x in v)) for v in G.values())
print('walks',n,'entries',len(G),'distinct outcomes (exit_ms,kind,takes,gross)',dist,'= %.1f%% of walks'%(100*dist/n),'| distinct (exit_ms,kind)',distexit)
# tick-work estimate: sum over entries of sum of distinct exit holds vs sum of all holds
tot=0;uniq=0;mx=0
for (sym,e),v in G.items():
    tot+=sum(x[0]-e for x in v)
    seen={}
    for x in v: seen[(x[0],x[1],x[2],x[3])]=x[0]-e
    uniq+=sum(seen.values()); mx+=max(x[0]-e for x in v)
print('hold sum all %.0f s; sum over distinct outcomes %.0f s (%.2fx less); trunk(max) %.0f s (%.1fx less)'%(tot/1000,uniq/1000,tot/uniq,mx/1000,tot/mx))
