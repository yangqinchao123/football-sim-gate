# -*- coding: utf-8 -*-
"""fact_rules_intl.py — 国家队事实卡片校验（整表 + 不变量 + 源 CSV 复算 + 覆盖度）

为什么需要：联赛版 fact_rules 只认 football_data/*.csv 的积分榜格式，
国家队批次没有对应文件，于是事实层长期被"无进票腿"豁免掉，
2026-11-16 D43 就出现了"法罗打平即锁第二"这类反了积分表的结论而无人拦。
"""
import hashlib
import json
import os

MIN_TEAMS_LEAGUE = 8


def _int(v):
    try:
        return int(v)
    except Exception:
        return None


def _check_table(tbl, label, errs):
    if not isinstance(tbl, list) or len(tbl) < MIN_TEAMS_LEAGUE:
        errs.append("%s 少于 %d 队（两行数据无法自证）" % (label, MIN_TEAMS_LEAGUE))
        return
    sw = sl = sgf = sga = 0
    plays = []
    for row in tbl:
        t = row.get("team")
        w, d, l = _int(row.get("w")), _int(row.get("d")), _int(row.get("l"))
        p, gf, ga = _int(row.get("played")), _int(row.get("gf")), _int(row.get("ga"))
        pts, gd = _int(row.get("points")), _int(row.get("gd"))
        if None in (w, d, l, p, gf, ga, pts, gd):
            errs.append("%s/%s 字段缺失或非整数" % (label, t))
            continue
        plays.append(p)
        sw += w
        sl += l
        sgf += gf
        sga += ga
        if p != w + d + l:
            errs.append("%s/%s 场次 %d ≠ 胜%d平%d负%d" % (label, t, p, w, d, l))
        if pts != 3 * w + d:
            errs.append("%s/%s 积分 %d ≠ 3×%d+%d" % (label, t, pts, w, d))
        if gd != gf - ga:
            errs.append("%s/%s 净胜 %d ≠ %d-%d" % (label, t, gd, gf, ga))
    if sw != sl:
        errs.append("%s Σ胜 %d ≠ Σ负 %d" % (label, sw, sl))
    if sgf != sga:
        errs.append("%s Σ进球 %d ≠ Σ失球 %d" % (label, sgf, sga))
    # 国际赛各队参赛场次天然不均（有的队一年 15 场、有的 2 场），
    # 联赛版的"场次极差"不变量在这里不成立，只保留胜负/进失球守恒。


def recompute(csv_path, snapshot_at, window_start, league_only=False, edition_start=None):
    rows = []
    for r in __import__("csv").DictReader(open(csv_path, encoding="utf-8-sig")):
        if r["date"] > snapshot_at or r["date"] < window_start:
            continue
        if r["home_score"] in ("", "NA") or r["away_score"] in ("", "NA"):
            continue
        if league_only and not ("Nations League" in r["tournament"] and r["date"] >= (edition_start or window_start)):
            continue
        rows.append(r)
    table = {}
    for r in rows:
        hs, as_ = int(r["home_score"]), int(r["away_score"])
        for t, gf, ga in ((r["home_team"], hs, as_), (r["away_team"], as_, hs)):
            p = table.setdefault(t, {"played": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0})
            p["played"] += 1
            p["w"] += 1 if gf > ga else 0
            p["d"] += 1 if gf == ga else 0
            p["l"] += 1 if gf < ga else 0
            p["gf"] += gf
            p["ga"] += ga
    for p in table.values():
        p["gd"] = p["gf"] - p["ga"]
        p["points"] = 3 * p["w"] + p["d"]
    return table, len(rows)


def check(card, csv_path=None):
    errs = []
    for k in ("home", "away", "snapshot_at", "window_start", "source_rows", "source_sha256"):
        if not card.get(k):
            errs.append("缺字段 %s" % k)
    if card.get("generated_by") != "lib/fact_sheet_intl.py":
        errs.append("generated_by 不是 lib/fact_sheet_intl.py（手写卡片不认）")
    if not csv_path or not os.path.exists(csv_path):
        errs.append("找不到源 results.csv，无法复算")
        return errs
    sha = hashlib.sha256(open(csv_path, "rb").read()).hexdigest()
    if sha != card.get("source_sha256"):
        errs.append("source_sha256 与当前 results.csv 不一致（数据已换版，须重出卡片）")
    _check_table(card.get("table"), "全表", errs)
    _check_table(card.get("table_league"), "欧国联分组表", errs)
    for lbl, only, tstart in (("全表", False, None), ("欧国联分组表", True, card.get("edition_start"))):
        if lbl == "全表" and not card.get("table"):
            continue
        if lbl != "全表" and not card.get("table_league"):
            continue
        got, nrows = recompute(csv_path, card["snapshot_at"], card["window_start"], only, tstart)
        src = card.get("table") if lbl == "全表" else card.get("table_league")
        for row in src:
            t = row.get("team")
            a = got.get(t)
            if not a:
                errs.append("%s/%s 在源 CSV 里复算不到" % (lbl, t))
                continue
            for k in ("played", "w", "d", "l", "gf", "ga", "points"):
                if _int(row.get(k)) != a[k]:
                    errs.append("%s/%s 的 %s 与源复算不一致（卡片 %s / 复算 %s）" % (lbl, t, k, row.get(k), a[k]))
    for t in (card.get("home"), card.get("away")):
        st = (card.get("standings") or {}).get(t)
        if not st:
            errs.append("standings 缺 %s（分组积分是战意判断的硬输入）" % t)
            continue
        lg = next((x for x in (card.get("table_league") or []) if x.get("team") == t), None)
        if lg is None:
            errs.append("分组表里没有 %s" % t)
        elif _int(st.get("points")) != _int(lg.get("points")):
            errs.append("%s 摘录积分 %s ≠ 分组表 %s" % (t, st.get("points"), lg.get("points")))
        if _int(st.get("played")) is not None and _int(st.get("played")) < 4:
            errs.append("%s 分组赛出场 %s 场 < 4：源数据轮次不全，战意结论不可用（须补数据或标 gap）"
                        % (t, st.get("played")))
    form = card.get("form") or {}
    for t in (card.get("home"), card.get("away")):
        games = form.get(t)
        if not isinstance(games, list) or not games:
            errs.append("form 缺 %s" % t)
            continue
        for g in games:
            if g.get("res") not in ("H", "D", "A"):
                errs.append("%s 形态结果非法：%s" % (t, g.get("res")))
                break
    return errs
