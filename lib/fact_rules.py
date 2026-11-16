# -*- coding: utf-8 -*-
"""
fact_rules.py — 事实卡片的硬校验（防"具体但错"）

背景：只查字段齐全，等于允许手写数字。积分榜填错照样过门禁。
本模块校验三类东西：
  1. 结构：必须整表直出（两行数据无法自证），带 snapshot_at / source_rows / generated_by
  2. 不变量：Σ胜=Σ负、Σ进球=Σ失球、每人 场次=胜+平+负、积分=3胜+平、净胜=进-失、场次极差≤2
  3. 溯源：若源 CSV 存在，按 snapshot_at 之前的比赛重算整表，逐队比对；不一致即失败
"""
import csv
import json
import os

MIN_TEAMS = 8


def _int(v):
    try:
        return int(v)
    except Exception:
        return None


def recompute_from_csv(csv_path, snapshot_at):
    rows = []
    for r in csv.DictReader(open(csv_path, encoding="utf-8-sig")):
        if r.get("FTR") in ("H", "D", "A") and r.get("FTHG") != "" and r.get("Date", "") <= snapshot_at:
            rows.append(r)
    tab = {}
    for r in rows:
        for t, gf, ga in ((r["HomeTeam"], int(r["FTHG"]), int(r["FTAG"])),
                          (r["AwayTeam"], int(r["FTAG"]), int(r["FTHG"]))):
            p = tab.setdefault(t, {"played": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0})
            p["played"] += 1
            p["w"] += 1 if gf > ga else 0
            p["d"] += 1 if gf == ga else 0
            p["l"] += 1 if gf < ga else 0
            p["gf"] += gf
            p["ga"] += ga
    return tab


def check(card, data_dir=None):
    """返回错误列表，空列表=通过。"""
    errs = []
    table = card.get("table")
    if not isinstance(table, list) or len(table) < MIN_TEAMS:
        return ["缺整表直出（table 少于 %d 队）：两行数据无法自证，须由脚本直出全表" % MIN_TEAMS]
    if not card.get("snapshot_at"):
        errs.append("缺 snapshot_at（不知道数字是哪一轮的，就无法和赛程对齐）")
    if _int(card.get("source_rows")) is None:
        errs.append("缺 source_rows（源数据行数，用于溯源）")
    if card.get("generated_by") != "lib/fact_sheet.py":
        errs.append("generated_by 不是 lib/fact_sheet.py（手写卡片不认）")

    teams = 0
    sw = sl = sgf = sga = 0
    plays = []
    for row in table:
        t = row.get("team")
        w, d, l = _int(row.get("w")), _int(row.get("d")), _int(row.get("l"))
        p, gf, ga = _int(row.get("played")), _int(row.get("gf")), _int(row.get("ga"))
        pts, gd = _int(row.get("points")), _int(row.get("gd"))
        if None in (w, d, l, p, gf, ga, pts, gd):
            errs.append("%s 字段缺失或非整数" % t)
            continue
        teams += 1
        plays.append(p)
        sw += w
        sl += l
        sgf += gf
        sga += ga
        if p != w + d + l:
            errs.append("%s 场次 %d ≠ 胜%d+平%d+负%d" % (t, p, w, d, l))
        if pts != 3 * w + d:
            errs.append("%s 积分 %d ≠ 3×%d+%d" % (t, pts, w, d))
        if gd != gf - ga:
            errs.append("%s 净胜球 %d ≠ %d-%d" % (t, gd, gf, ga))
    if sw != sl:
        errs.append("全表 Σ胜 %d ≠ Σ负 %d（联赛内胜负必须相等）" % (sw, sl))
    if sgf != sga:
        errs.append("全表 Σ进球 %d ≠ Σ失球 %d" % (sgf, sga))
    if plays and max(plays) - min(plays) > 2:
        errs.append("各队场次极差 %d > 2（同一轮次的积分榜不可能差这么多，疑似拼接）" % (max(plays) - min(plays)))

    st = card.get("standings") or {}
    for t in (card.get("home"), card.get("away")):
        row = next((x for x in table if x.get("team") == t), None)
        if row is None:
            errs.append("积分榜整表里没有 %s" % t)
        elif st.get(t) and _int(st[t].get("points")) != _int(row.get("points")):
            errs.append("%s 摘录积分 %s 与整表 %s 不一致" % (t, st[t].get("points"), row.get("points")))

    form = card.get("form") or {}
    for t, games in form.items():
        if not isinstance(games, list) or not games:
            errs.append("%s 形态缺失" % t)
            continue
        if len(games) > 10:
            errs.append("%s 形态 %d 场，超过 10 场不合理" % (t, len(games)))
        for g in games:
            if g.get("res") not in ("H", "D", "A"):
                errs.append("%s 形态结果取值非法：%s" % (t, g.get("res")))
                break

    if data_dir:
        src = str(card.get("source") or "")
        code = None
        for token in src.replace("（", "(").split():
            if "football_data/" in token:
                code = token.split("/")[-1].split("(")[0]
        if code:
            fp = os.path.join(data_dir, code)
            if os.path.exists(fp):
                got = recompute_from_csv(fp, card["snapshot_at"])
                for t in (card.get("home"), card.get("away")):
                    a, b = got.get(t), next((x for x in table if x.get("team") == t), None)
                    if not a or not b:
                        errs.append("溯源复算缺 %s" % t)
                        continue
                    for k in ("played", "w", "d", "l", "gf", "ga"):
                        if _int(b.get(k)) != a[k]:
                            errs.append("%s 的 %s 与源 CSV 复算不一致（卡片 %s / 复算 %s）"
                                        % (t, k, b.get(k), a[k]))
    return errs
