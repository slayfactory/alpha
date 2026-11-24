# TK-702: функции .rs с одинаковым телом (≥ 6 строк, без комментариев и пробелов) в нескольких местах. Запуск из корня: python -I tools/rs-dups.py
import re, os, sys, collections, hashlib

files = []
for d, _, fs in os.walk('src'):
    for f in fs:
        if f.endswith('.rs'):
            files.append(os.path.join(d, f).replace(os.sep, '/'))
fnre = re.compile(r'^([ \t]*)(?:pub(?:\([a-z]+\))?\s+)?(?:const\s+|async\s+|unsafe\s+)*fn\s+([A-Za-z_][A-Za-z0-9_]*)', re.M)
groups = collections.defaultdict(list)
for f in files:
    t = open(f, encoding='utf-8').read()
    lines = t.split('\n')
    for m in fnre.finditer(t):
        indent = m.group(1)
        start = t[:m.start()].count('\n')
        # find end: first line after start that equals indent + '}' (closing)
        end = None
        for i in range(start, min(len(lines), start + 400)):
            if lines[i].startswith(indent + '}') and len(lines[i].rstrip()) == len(indent) + 1 and i > start:
                end = i
                break
            if i == start and lines[i].rstrip().endswith('}') and '{' in lines[i]:
                end = i
                break
        if end is None:
            continue
        body = lines[start:end + 1]
        if len(body) < 6:
            continue
        norm = re.sub(r'\s+', ' ', ' '.join(l.split('//')[0] for l in body[1:]))
        h = hashlib.md5(norm.encode()).hexdigest()
        groups[h].append((f, start + 1, m.group(2), len(body)))
out = []
for h, v in groups.items():
    if len(v) >= 2:
        out.append((v[0][3] * (len(v) - 1), v))
for sc, v in sorted(out, reverse=True):
    print(sc, ' | '.join('%s:%d %s' % (a, b, c) for a, b, c, d in v))
