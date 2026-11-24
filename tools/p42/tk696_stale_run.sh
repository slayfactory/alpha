#!/usr/bin/env bash
# П-42 TK-696: проверка кэша — профиль браузера, закэшировавший страницу «до обновления» (старый адрес пакетов, старая опись), после обновления грузит веер (лонг и шорт, «все»)
# без ручной очистки. На Софии, каталог /root/tk684: stand/ (index.html + data/fan/ с живой страницы), ml_page_server.py, tk696_stale_check.js. Код выхода 0 — всё как ожидалось.
#   этап 1: старая страница + плоский http.server + «старая» опись (с L-5 и другим размером S-3) — браузер кэширует опись;
#   этап 2: те же адреса, опись настоящая, старая страница → ожидаем ошибку (воспроизведение: «L-5: HTTP 404» / «Invalid typed array length»);
#   этап 3: новая страница + ml_page_server.py, тот же профиль, обычное обновление (F5) → оба «все» грузятся; контроль — чистый профиль.
cd /root/tk684
export PW=/root/arb-settings-test/node_modules/playwright-core CHROME=/usr/bin/google-chrome
PORT=18090; U=http://127.0.0.1:$PORT/; fail=0
python3 - <<'PY'
import json, re
h = open('stand/index.html', encoding='utf-8').read()
old = re.sub(r"const FAN_V = '\d*';", "const FAN_V = '';", h).replace("{cache: again ? 'reload' : 'no-cache'}", "undefined")
assert old != h and "FAN_V = ''" in old
open('stand/index.old.html', 'w', encoding='utf-8').write(old); open('stand/index.new.html', 'w', encoding='utf-8').write(h)
d = json.load(open('stand/data/fan/index.json')); fake = json.loads(json.dumps(d))
fake['files']['L-5'] = {'side': 'long', 'level': 5, 'n': 100, 'bytes': 0, 'gz': 0, 'meta_gz': 0, 'split': 0}; fake['files']['S-3']['n'] += 1000
json.dump(d, open('stand/index.real.json', 'w')); json.dump(fake, open('stand/index.fake.json', 'w'))
PY
stop() { [ -n "${SP:-}" ] && kill "$SP" 2>/dev/null; wait 2>/dev/null; SP=; }
run() { systemd-run --quiet --wait --pipe --nice=19 -p WorkingDirectory=/root/tk684 -E PW=$PW -E CHROME=$CHROME /usr/bin/node /root/tk684/tk696_stale_check.js "$U" "$@"; }
rm -rf prof-stale prof-clean
echo "== этап 1: старая страница, старая опись, плоский сервер"
cp stand/index.old.html stand/index.html; cp stand/index.fake.json stand/data/fan/index.json; touch -d '2 days ago' stand/index.html stand/data/fan/index.json
python3 -m http.server $PORT --bind 127.0.0.1 --directory stand >/dev/null 2>&1 & SP=$!; sleep 1
for s in long short; do run /root/tk684/prof-stale goto $s; done
echo "== этап 2: опись настоящая, страница старая (ожидаем ошибки — воспроизведение)"
cp stand/index.real.json stand/data/fan/index.json; touch -d '2 days ago' stand/data/fan/index.json
for s in long short; do run /root/tk684/prof-stale goto $s && { echo "НЕ ВОСПРОИЗВЕДЕНО: $s"; fail=1; }; done
stop
echo "== этап 3: новая страница, ml_page_server.py, тот же профиль, F5"
cp stand/index.new.html stand/index.html
python3 ml_page_server.py $PORT stand >/dev/null 2>&1 & SP=$!; sleep 1
for s in long short; do run /root/tk684/prof-stale reload $s || fail=1; done
echo "== контроль: чистый профиль, новая страница"
for s in long short; do run /root/tk684/prof-clean goto $s || fail=1; done
echo "== заголовки"; curl -sI "${U}index.html" | grep -i '^cache-control'; curl -sI "${U}data/fan/index.json" | grep -i '^cache-control'
stop
echo "итог: $([ $fail = 0 ] && echo OK || echo ПРОВАЛ)"; exit $fail
