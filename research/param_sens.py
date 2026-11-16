# -*- coding: utf-8 -*-
"""
param_sens.py — 参数敏感性（样本外）：哪些卡点是真的稳，哪些是拍脑袋

口径（预注册，跑完不改）：
- 调参季 2526（上季 2425 滚动训练），验证季 2627（上季 2526 滚动）。参数只在 2526 上选。
- 主指标 RPS（概率质量）+ 命中率；命中率附二项标准误 SE=sqrt(p(1-p)/n)。
- 五个参数：融合权重 w、出手阈值 N2、均势跳过 N3、分歧场表现、联赛分层。
- 深盘让球不测：本数据无让球线，留空不猜。

用法：python3 research/param_sens.py [--stride 10]
"""
import argparse, csv, json, os
import numpy as np
from penaltyblog.models import DixonColesGoalModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "football_data")
if not os.path.isdir(DATA):
    DATA = os.path.join(os.path.dirname(ROOT), "football_data")
LEAGUES = ["E0", "SP1", "D1", "I1", "F1", "N1", "P1"]
CACHE = os.path.join(ROOT, "research", "cache_%s.json")


def load(league, season):
    fp = os.path.join(DATA, "%s_%s.csv" % (league, season))
    if not os.path.exists(fp):
        return None
    rows = list(csv.DictReader(open(fp, encoding="utf-8-sig")))
    return [r for r in rows if r.get("FTR") in ("H", "D", "A") and r.get("FTHG") != ""]


def devig(h, d, a):
    try:
        h, d, a = float(h), float(d), float(a)
    except Exception:
        return None
    inv = 1.0 / h + 1.0 / d + 1.0 / a
    if not np.isfinite(inv) or inv <= 0:
        return None
    return np.array([(1.0 / h) / inv, (1.0 / d) / inv, (1.0 / a) / inv])


def market_of(row):
    for cols in (("B365CH", "B365CD", "B365CA"), ("B365H", "B365D", "B365A")):
        p = devig(row.get(cols[0]), row.get(cols[1]), row.get(cols[2]))
        if p is not None:
            return p
    return None


def collect(season, prev, stride):
    out = []
    for L in LEAGUES:
        cur = load(L, season)
        if not cur:
            continue
        hist = load(L, prev) or []
        for k in range(15, len(cur), stride):
            prefix = hist + cur[:k]
            try:
                m = DixonColesGoalModel(goals_home=[int(r["FTHG"]) for r in prefix],
                                        goals_away=[int(r["FTAG"]) for r in prefix],
                                        teams_home=[r["HomeTeam"] for r in prefix],
                                        teams_away=[r["AwayTeam"] for r in prefix])
                m.fit()
            except Exception:
                continue
            for g in cur[k:k + stride]:
                try:
                    pm = np.array(list(m.predict(g["HomeTeam"], g["AwayTeam"]).home_draw_away))
                except Exception:
                    continue
                mk = market_of(g)
                if mk is None or not np.isfinite(pm).all():
                    continue
                out.append({"pm": [float(x) for x in pm / pm.sum()],
                            "mk": [float(x) for x in mk], "y": g["FTR"], "lg": L,
                            "home": g["HomeTeam"]})
        print("  %s %s 到 %d 场" % (season, L, len(out)), flush=True)
    return out


def get_rows(season, prev, stride):
    cp = CACHE % season
    if os.path.exists(cp):
        return json.load(open(cp))
    rows = collect(season, prev, stride)
    json.dump(rows, open(cp, "w"))
    return rows


IDX = {"H": 0, "D": 1, "A": 2}


def rps(p, y):
    yi = IDX[y]
    return ((p[0] - (1.0 if yi == 0 else 0.0)) ** 2 +
            (p[0] + p[1] - (1.0 if yi <= 1 else 0.0)) ** 2) / 2.0


def bl(pm, mk, w):
    p = np.array(pm) * w + np.array(mk) * (1 - w)
    return p / p.sum()


def stats(rows, w, sel=None):
    n = hit = 0
    r = 0.0
    for x in rows:
        p = bl(x["pm"], x["mk"], w)
        if sel is not None and not sel(x, p):
            continue
        n += 1
        r += rps(p, x["y"])
        hit += 1 if int(np.argmax(p)) == IDX[x["y"]] else 0
    if n == 0:
        return None
    h = hit / n
    return {"n": n, "hit": h, "se": (h * (1 - h) / n) ** 0.5, "rps": r / n}


def table(title, rows, w, sel=None):
    s = stats(rows, w, sel)
    if not s:
        print("  %-28s   无样本" % title)
        return None
    print("  %-28s n=%4d 命中 %5.1f%%±%.1f  RPS %.4f" % (title, s["n"], 100 * s["hit"], 100 * s["se"], s["rps"]))
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stride", type=int, default=10)
    a = ap.parse_args()
    tune = get_rows("2526", "2425", a.stride)
    test = get_rows("2627", "2526", a.stride)
    print("\n样本：调参季 %d 场，验证季 %d 场\n" % (len(tune), len(test)))

    print("① 融合权重 w（模型占比）—— 调参季选，验证季照抄")
    print("   调参季 2526")
    wt = {}
    for w in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]:
        s = table("w=%.1f" % w, tune, w)
        wt[w] = s
    best = min(wt, key=lambda k: wt[k]["rps"])
    print("   → 调参季 RPS 最优 w=%.1f（RPS %.4f）" % (best, wt[best]["rps"]))
    print("   验证季 2627（不再调参）")
    for w in [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6]:
        table("w=%.1f" % w, test, w)

    print("\n② 出手阈值 N2：最高概率 >= t 才出手（其余视为空仓）")
    for t in [0.35, 0.40, 0.45, 0.50]:
        table("t=%.2f 调参季" % t, tune, 0.3, lambda x, p, t=t: p.max() >= t)
    for t in [0.35, 0.40, 0.45, 0.50]:
        table("t=%.2f 验证季" % t, test, 0.3, lambda x, p, t=t: p.max() >= t)

    print("\n③ 均势跳过 N3：|P主-P客| < d 视为均势不出手")
    for d in [0.06, 0.10, 0.14]:
        table("d=%.2f 调参季" % d, tune, 0.3,
              lambda x, p, d=d: abs(x["mk"][0] - x["mk"][2]) >= d)
    for d in [0.06, 0.10, 0.14]:
        table("d=%.2f 验证季" % d, test, 0.3,
              lambda x, p, d=d: abs(x["mk"][0] - x["mk"][2]) >= d)

    print("\n④ 与市场分歧场（模型最高项 != 市场最高项）—— 真本事在这")
    for tag, rows in (("调参季", tune), ("验证季", test)):
        dis = lambda x, p: int(np.argmax(x["pm"])) != int(np.argmax(x["mk"])) and p.max() >= 0.40
        agr = lambda x, p: int(np.argmax(x["pm"])) == int(np.argmax(x["mk"])) and p.max() >= 0.40
        table("%s 分歧且>0.40" % tag, rows, 0.3, dis)
        table("%s 一致且>0.40" % tag, rows, 0.3, agr)

    print("\n⑤ 联赛分层（w=0.3，验证季）")
    for L in LEAGUES:
        table(L, test, 0.3, lambda x, p, L=L: x["lg"] == L)


if __name__ == "__main__":
    main()
