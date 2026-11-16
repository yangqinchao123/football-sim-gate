# -*- coding: utf-8 -*-
"""fact_sheet_intl.py — 国家队（欧国联/预选赛）事实卡片直出

为什么单独一个：联赛版 fact_sheet 依赖 football_data/*.csv 的积分榜字段，
国家队赛事没有联赛积分榜，于是国家队批次的事实层长期是"手写+免检"，
2026-11-16 的 D43 就因此把"法罗打平即锁第二"这种反了积分表的结论写进了方向依据。

本脚本从公开国际赛结果库（results.csv）直出：
  1) 全表（窗口内所有队）：场次/胜平负/进失/净胜/积分
  2) 本场两队的摘录 + 近 5 场形态（含对手与比分）
  3) 交锋记录
  4) snapshot_at / window_start / source_rows / source_sha256，全部可复算
用法: python3 fact_sheet_intl.py <home> <away> <snapshot_at YYYY-MM-DD> [out.json]
"""
import csv, hashlib, json, os, sys
from datetime import datetime

MIN_WINDOW_START = "2025-01-01"


def csv_path():
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(os.environ.get("GATE_WS", ""), "football_data_intl"),
             os.path.join(here, "football_data_intl"),
             os.path.join(os.path.dirname(here), "football_data_intl")]
    for c in cands:
        p = os.path.join(c, "results.csv")
        if os.path.exists(p):
            return p
    raise SystemExit("找不到 football_data_intl/results.csv")


def resolve(name, teams):
    """队名映射：中文→CSV 英文名；映射不到就报错，禁止猜。"""
    if name in teams:
        return name
    mapf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "intl_names.json")
    m = json.load(open(mapf, encoding="utf-8"))
    en = m.get(name)
    if en and en in teams:
        return en
    raise SystemExit("队名 %r 无法映射到 results.csv（请补 lib/intl_names.json，禁止手写退而求其次）" % name)


def aggregate(rows):
    table, games = {}, {}
    for r in rows:
        hs, as_ = int(r["home_score"]), int(r["away_score"])
        for t, gf, ga, opp, res in ((r["home_team"], hs, as_, r["away_team"],
                                     "H" if hs > as_ else ("D" if hs == as_ else "A")),
                                    (r["away_team"], as_, hs, r["home_team"],
                                     "A" if hs > as_ else ("D" if hs == as_ else "H"))):
            p = table.setdefault(t, {"team": t, "played": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0})
            p["played"] += 1
            p["w"] += 1 if res == "H" else 0
            p["d"] += 1 if res == "D" else 0
            p["l"] += 1 if res == "A" else 0
            p["gf"] += gf
            p["ga"] += ga
            games.setdefault(t, []).append({"date": r["date"], "opp": opp, "res": res,
                                            "score": "%d:%d" % (gf, ga), "tournament": r["tournament"]})
    for p in table.values():
        p["gd"] = p["gf"] - p["ga"]
        p["points"] = 3 * p["w"] + p["d"]
    return table, games


def build(home, away, snapshot_at, window_start=MIN_WINDOW_START, edition_start="2026-09-01"):
    rows = [r for r in csv.DictReader(open(csv_path(), encoding="utf-8-sig"))
            if r["date"] <= snapshot_at and r["date"] >= window_start
            and r["home_score"] not in ("", "NA") and r["away_score"] not in ("", "NA")]
    teams = {r["home_team"] for r in rows} | {r["away_team"] for r in rows}
    cn_home, cn_away = home, away
    home, away = resolve(home, teams), resolve(away, teams)
    table, games = aggregate(rows)
    league_rows = [r for r in rows if "Nations League" in r["tournament"] and r["date"] >= edition_start]
    table_league, _ = aggregate(league_rows)
    tbl = sorted(table.values(), key=lambda x: (-x["played"], -x["points"], x["team"]))
    tbl_league = sorted(table_league.values(), key=lambda x: (-x["points"], -x["gd"], x["team"]))
    h2h = [g for g in games.get(home, []) if g["opp"] == away]
    card = {
        "league": "国际赛（欧国联/预选赛等）",
        "source_kind": "intl",
        "home": home, "away": away,
        "display": {home: cn_home, away: cn_away},
        "window_start": window_start, "snapshot_at": snapshot_at,
        "table": tbl,
        "table_league": tbl_league,
        "edition_start": edition_start,
        "standings": {t: {"points": table_league[t]["points"], "played": table_league[t]["played"],
                          "pos": next((i + 1 for i, x in enumerate(tbl_league) if x["team"] == t), None)}
                      for t in (home, away) if t in table_league},
        "form": {t: sorted(games.get(t, []), key=lambda g: g["date"], reverse=True)[:5]
                 for t in (home, away)},
        "h2h": h2h,
        "source": "football_data_intl/results.csv",
        "source_rows": len(rows),
        "source_sha256": hashlib.sha256(open(csv_path(), "rb").read()).hexdigest(),
        "generated_by": "lib/fact_sheet_intl.py",
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return card


if __name__ == "__main__":
    if len(sys.argv) < 4:
        raise SystemExit(__doc__)
    c = build(sys.argv[1], sys.argv[2], sys.argv[3])
    out = sys.argv[4] if len(sys.argv) > 4 else "%s_%s_fact.json" % (sys.argv[1], sys.argv[2])
    json.dump(c, open(out, "w"), ensure_ascii=False, indent=1)
    print("写 %s：%d 队全表，%s vs %s" % (out, len(c["table"]), c["home"], c["away"]))
