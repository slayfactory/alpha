#!/usr/bin/env python3
"""П-42 TK-674: запись заморозки кандидатов отбора PS-1010 (`docs/research/freeze/tk674-frozen.json`): ключи клеток, `tsig`, карты-источники, пороги, бинарь, правила чтения. Без движка и без чтения января–июня.
  python3 tk674_freeze.py --sel <sel.json (tk674_select.py --out)> --out docs/research/freeze/tk674-frozen.json [--select-copy docs/findings/p42-joint-search-2026-11-22/select-674.json]
Запись неизменяема после коммита: правки — только новой версией файла и записью в летописи."""
from __future__ import annotations

import argparse
import hashlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
FIND = os.path.join(HERE, "..", "..", "docs", "findings", "p42-joint-search-2026-11-22")
NL = chr(10)                                                          # LF на любой ОС: sha файла не зависит от CRLF ПК
IDS = {("S", 1): "S1", ("S", 2): "S2", ("L", 1): "L1", ("L", 2): "L2"}
# снято на srv 25.11.2026 (sha256sum / md5sum файлов; содержимое январь–июнь не открывалось)
SRV = {
    "thr674": dict(path="/data/p42-tk674c/res/thr674.json", sha256="7205062cffcf08b14636c8e0909eab118299c15244de330ac9b2f8a05147dd82", same_as="/data/p42-tk674c/res/thr674-c539.json"),
    "thr38": dict(path="docs/findings/p42-turnover-grid-2026-11-20/res/thr38.json"),
    "bin_freeze": dict(path="/data/p42-tk683/bin/alpha-tk706memo-plain.357a66c1", md5="357a66c14605c3a228d18defbe35bb22", sha256="377df19cfaebda788bcc7637a95996af06d14d4eb7e2a78b14bfdf46a2c3de74",
                       gate="/data/p42-tk683/res/gate683gmm2.json", gate_sha256="8540f6dd3888707607565c8938f0137859430c61dacac7e992ebae2cbf0bce2e", gate_note="TK-706: 549 клеток байт в байт, bad 0, критик PASS"),
    "bin_s2": dict(path="/data/p42-tk683/bin/alpha-tk683l-plain.562f6899", md5="562f689967dde28ebce44f4a00f1b7af", sha256="03c9cfd4b45f057fe1b03f00b62c7c72f8793c5068e6a021d689f794f9ab2e8e",
                   gate="/data/p42-tk683/res/gate683l1.json", gate_sha256="745a8685819eca07866b5faaff360e07b3fa8d1b8726d1e83fc51c68475067e6"),
    "bin_pgo": dict(path="/data/p42-tk683/bin/alpha-tk706pgo-plain.06367f45", md5="06367f45fad0d7c5c1031d82dfa03615", sha256="96ea56fb68f3a7f13e8f78f5e996a7bd734eea6c48eaef6f8c307623f0cd2fb3", note="одобрен TK-706 для замены на границе партии; клетки отбора им не считались"),
    "eqbins": dict(path="/data/p42-tk674c/res/eqbins674.json", md5=["9387e0a9328ff606016ae5451fc7a682", "b328555bfc973b72fb091e55e37ebbe2", "0bb3b9313fc9c64764911125c9ff61e3", "c64d5ed7355fd85b2d924b4d595a6f48", "c400af112bda3396d807b2ba882aea8b",
                   "562f689967dde28ebce44f4a00f1b7af", "8759375e02b496f865b160b05e74726d", "357a66c14605c3a228d18defbe35bb22", "06367f45fad0d7c5c1031d82dfa03615"],
                   note="реестр бинарей, прошедших гейт «байт в байт» к бинарю h (c64d5ed7); клетки отбора посчитаны бинарями этого реестра"),
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def fwd(axes):
    """Что нужно вперёд (после 13.11): входы собираются (сделки aggTrades / минутки), ключи движка работают; признаки фильтра `F=` — столбцы таблицы `--ftab`, построителя вперёд для них нет (в `tk616_forward.py` построен только `n24`)."""
    feats = sorted(a[2:].rstrip("<>=") for a in axes if a.startswith("F:"))
    return dict(inputs="да: сделки aggTrades и минутки собираются вперёд (как у цепочки tk626 → tk691)", engine_keys="да: ключи движка клетки как в БД",
                feature_builder_forward=("не нужен: у клетки нет столбцов фильтра" if not feats else "НЕТ: " + ", ".join(feats) + " — построитель таблицы `--ftab` вперёд не сделан (задача до 30.11)"),
                pipeline_hook="НЕТ: `tk616_forward.py` не читает `tk674-frozen.json` (пятое звено цепочки — задача до 30.11)", computable_now=False,
                computable_after_tasks=True if not feats else "после построителя признаков вперёд")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sel", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--gate", default=None, help="результат шлюза (`tk674_gate_cells.py gate`, в git) — статус и sha в файл")
    ap.add_argument("--overlap", default=None, help="пересечение сделок (`tk674_gate_cells.py overlap`) — в файл (только печать, порога нет)")
    ap.add_argument("--select-copy", default=None, help="копия результата отбора рядом с картами (в git)")
    a = ap.parse_args()
    sel = json.load(open(a.sel, encoding="utf-8"))
    if a.select_copy:
        with open(a.select_copy, "w", encoding="utf-8", newline=NL) as f:
            json.dump(sel, f, ensure_ascii=False, indent=1)
    sel_path = a.select_copy or a.sel                                 # sha — копии в git (если есть)
    thr38 = os.path.join(HERE, "..", "..", SRV["thr38"]["path"])
    maps = {}
    cands = []
    for side in "LS":
        for c in sel[side]["candidates"]:
            w = c["cell"]
            maps.setdefault(c["src"], sha256(os.path.join(FIND, c["src"] + ".json")))
            cands.append(dict(id=IDS[(side, c["rank"])], side="long" if side == "L" else "short", rank=c["rank"], cid=w["cid"], ckey=w["ckey"], tsig=w["tsig"], axes=c["axes"], source=dict(map=c["src"] + ".json", pair=c["pair"]),
                              found=dict(trades=w["n"], usd=round(w["usd"], 2), sharpe=round(w["sharpe"], 3), maxdd_usd=round(w["maxdd"], 2), maxdd_pct_margin=round(100 * w["maxdd"] / 250, 1), gap_longest=w["gap_longest"], gap_mean=round(w["gap_mean"], 2),
                                         rr=round(w["rr"], 3), lstop_usd=round(w["lstop"], 3), worst_usd=round(w["worst"], 3), months=w["months"], plateau_nb_mean=round(w["nb_mean"], 3), noise=round(sel[side]["noise"], 2),
                                         base_sharpe=round(sel[side]["base"]["sharpe"], 3), base_usd=round(sel[side]["base"]["usd"], 2)), forward=fwd(c["axes"])))
    gate = dict(status="не запущен", evidence="клетки отбора — строки БД c539, посчитанные бинарями реестра `eqbins674.json`; канарейки байт в байт (L-419, L-425); гейт TK-706 для memo `357a66c1` — 549 клеток байт в байт",
                pending="повтор клеток-кандидатов замороженным бинарём: `tools/p42/tk674_gate_cells.py gate` (окно на srv, один движок за раз)")
    if a.gate:
        g = json.load(open(a.gate, encoding="utf-8"))
        gate = dict(status="пройден" if g["passed"] else "НЕ ПРОЙДЕН", tool="tools/p42/tk674_gate_cells.py gate", result=os.path.basename(a.gate), result_sha256=sha256(a.gate), binary=g["binary"], code_sha256=g["code"], thr674_sha256=g["thr674"],
                    cells={k: dict(ok=v["ok"], tsig_equal=v["tsig_equal"], windows={w: x["equal"] for w, x in v["windows"].items()}, trades=v["metrics"]["trades"], usd=v["metrics"]["usd"]) for k, v in g["cells"].items()},
                    what="каждая клетка × три окна (wa / wb / wc) замороженным бинарём `357a66c1`, средой `env-s4.sh`, ключом клетки и таблицами фильтров партии: файлы сделок / суток / монет / отказов байт в байт с клеткой БД, `trades_sig` = `tsig`")
    overlap = dict(status="не посчитано")
    if a.overlap:
        overlap = dict(json.load(open(a.overlap, encoding="utf-8")), note="общих сделок / сделок (2); только печать, порога нет (PS-1010 п. 6)", result=os.path.basename(a.overlap), result_sha256=sha256(a.overlap))
    out = dict(
        version="tk674-frozen-1",
        protocol="П-42 TK-674: отбор кандидатов вперёд (≤ 2 на сторону) — PS-1010 (поправка к PS-999 п. 5 и prereg-13.73 §6.3); результат — L-426; код `tools/p42/tk674_select.py`",
        mark="найдено на этих данных (c539, 01.07–13.11, ставка 500 $, маржа 250 $); прирост Шарпа к базе у всех четырёх ниже шума отбора; описательно",
        read_before_commit="январь–июнь 2026 не читались и не считались этой записью: отбор — только по картам окна 01.07–13.11; движок не запускался",
        selection=dict(sel_sha256=sha256(sel_path), copy=os.path.basename(a.select_copy) if a.select_copy else None, maps=maps,
                       noise=dict(L=round(sel["L"]["noise"], 2), S=round(sel["S"]["noise"], 2)), m=dict(L=sel["L"]["m"], S=sel["S"]["m"]), stats=dict(L=sel["L"]["stats"], S=sel["S"]["stats"])),
        bases=dict(L=dict(ckey=sel["L"]["base"]["ckey"], trades=sel["L"]["base"]["n"], usd=round(sel["L"]["base"]["usd"], 2), sharpe=round(sel["L"]["base"]["sharpe"], 3)),
                   S=dict(ckey=sel["S"]["base"]["ckey"], trades=sel["S"]["base"]["n"], usd=round(sel["S"]["base"]["usd"], 2), sharpe=round(sel["S"]["base"]["sharpe"], 3))),
        candidates=cands,
        thresholds=dict(rule="пороги меток `_q…` и значения фильтров — числа в `ckey` кандидата (`F=<признак><знак><порог>`); квантильные файлы только происхождение; вперёд не пересчитываются",
                        thr674=dict(path=SRV["thr674"]["path"], sha256=SRV["thr674"]["sha256"], same_as=SRV["thr674"]["same_as"]), thr38=dict(path=SRV["thr38"]["path"], sha256=sha256(thr38))),
        binary=dict(freeze=SRV["bin_freeze"], also_used=SRV["bin_s2"], pgo_approved=SRV["bin_pgo"], equivalence=SRV["eqbins"]),
        environment=dict(file="/data/p42-tk674c/env-s4.sh", engine="breakout tick, ключи клетки `ckey` как в БД c539; TK674_UNI=c539, TK674_REBASE=1", universe="c539 (539 крипто-контрактов; tk691-frozen.json `universe`, contracts_sha256 4b51e9aad05b4652c50bc6030ba11aaa1fa362ecf5e3b877b1bac735e514d328)"),
        cost_model="как `docs/research/freeze/tk691-frozen.json` `cost_model` (ставка 500 $, плечо 2, комиссия тейкер 3,5 бп минус 10 % возврата, проскальзывание 1 бп на ногу, задержка 4 мс `lat=4 latx=4`)",
        gate=gate,
        overlap=overlap,
        reading_rule=dict(
            window="январь–июнь 2026 после заморозки; правило — prereg-13.73 §8: n ≥ 100, Σ > 0 (блочный бутстреп по суткам, блок 5, 20 000, seed 674), лучше базы стороны (Холм)",
            status="по В-183 п. 3 январь–июнь после счёта TK-692 / TK-700 видены, не чистая отложенная выборка: «подтверждено» там — описательно",
            clean_check="первая чистая проверка — точка вперёд 30.11 (перезаморозка на крипте, `tk691-frozen.json`); для этих клеток нужны построители признаков вперёд — отдельной задачей",
            trading="торговля — только словом владельца"),
        ref="kb/task/TK-674.md; PS-1010; L-425; L-426")
    with open(a.out, "w", encoding="utf-8", newline=NL) as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("записано:", a.out, "кандидатов", len(cands), "sha256", sha256(a.out))


if __name__ == "__main__":
    main()
