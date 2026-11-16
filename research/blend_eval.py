# -*- coding: utf-8 -*-
"""
blend_eval.py — 对照实验：纯模型 / 时间衰减模型 / 模型与收盘市场按权融合

外部依据（不是自创）：DC 论文与主流实现都用时间衰减权重（近场权重高）；
公开预测系统普遍与市场概率融合或直接与庄家对照；评估用 proper scoring rules。

口径：
- 训练集 = 上赛季全部 + 本赛季已赛（滚动，步长 stride）
- 市场基准 = B365 收盘三向赔率去水（B365CH/CD/CA）
- 权重衰减 = exp(-xi * 场次年龄)，xi 用每场口径
- 融合 = w * 模型 + (1-w) * 市场，w 在调参季选，验证季只用不调
用法：
  python3 research/blend_eval.py --tune 2526 --test 2627 --stride 10
"""
import argparse
import csv
import os

import numpy as np
from penaltyblog.models import DixonColesGoalModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "football_data")
if not os.path.isdir(DATA):
    DATA = os.path.join(os.path.dirname(ROOT), "football_data")
LEAGUES = ["E0", "SP1", "D1", "I1", "F1", "N1", "P1"]


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


def probs_of(rows, k, weights=None):
    tr = rows[:k]
    m = DixonColesGoalModel(goals_home=[int(r["FTHG"]) for r in tr],
                            goals_away=[int(r["FTAG"]) for r in tr],
                            teams_home=[r["HomeTeam"] for r in tr],
                            teams_away=[r["AwayTeam"] for r in tr],
                            weights=weights)
    m.fit()
    return m


def rps(p, y):
    yi = {"H": 0, "D": 1, "A": 2}[y]
    c1, c2 = p[0], p[0] + p[1]
    y1 = 1.0 if yi == 0 else 0.0
    y2 = 1.0 if yi <= 1 else 0.0
    return ((c1 - y1) ** 2 + (c2 - y2) ** 2) / 2.0


def logloss(p, y):
    return float(-np.log(max(p[{"H": 0, "D": 1, "A": 2}[y]], 1e-12)))


def collect(season, prev, stride, xis):
    """返回 [(variant_name, p_model, p_market, y)]"""
    out = []
    for L in LEAGUES:
        cur = load(L, season)
        if not cur:
            continue
        hist = load(L, prev) or []
        for k in range(15, len(cur), stride):
            prefix = hist + cur[:k]
            ages = np.arange(len(prefix) - 1, -1, -1, dtype=float)
            for xi in xis:
                w = np.exp(-xi * ages) if xi else None
                try:
                    m = probs_of(prefix, len(prefix), w)
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
                    out.append((xi, pm / pm.sum(), mk, g["FTR"]))
    return out


def score(rows, w_model):
    r, b, ll, hit, n = 0.0, 0.0, 0.0, 0, 0
    for xi, pm, mk, y in rows:
        p = pm if w_model is None else w_model * pm + (1 - w_model) * mk
        p = p / p.sum()
        r += rps(p, y)
        yi = {"H": 0, "D": 1, "A": 2}[y]
        b += float(np.sum((p - np.eye(3)[yi]) ** 2))
        ll += logloss(p, y)
        hit += 1 if int(np.argmax(p)) == yi else 0
        n += 1
    return {"n": n, "rps": r / n, "brier": b / n, "ll": ll / n, "hit": hit / n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", default="2526")
    ap.add_argument("--test", default="2627")
    ap.add_argument("--stride", type=int, default=10)
    a = ap.parse_args()

    xis = [0.0, 0.0035, 0.007]
    errors = []
    for tag, season, prev in (("调参季", a.tune, "2425"), ("验证季", a.test, a.tune)):
        rows = collect(season, prev, a.stride, xis)
        errors.append((tag, season, rows))
        print("\n=== %s %s：%d 场 ===" % (tag, season, len(rows)))
        print("%-34s %6s %7s %7s %7s" % ("方案", "命中%", "RPS", "Brier", "LogLoss"))
        print("%-34s %6.1f %7.4f %7.4f %7.4f" % ("市场收盘（基准）", 100 * score(rows, 0.0)["hit"],
              score(rows, 0.0)["rps"], score(rows, 0.0)["brier"], score(rows, 0.0)["ll"]))
        best = None
        for xi in xis:
            sub = [r for r in rows if r[0] == xi]
            for wm in (1.0, 0.7, 0.6, 0.5, 0.4, 0.3):
                s = score(sub, wm)
                label = "模型 xi=%.4f w=%.0f%%" % (xi, 100 * wm) if wm < 1 else "纯模型 xi=%.4f" % xi
                print("%-34s %6.1f %7.4f %7.4f %7.4f" % (label, 100 * s["hit"], s["rps"], s["brier"], s["ll"]))
                if best is None or s["rps"] < best[1]["rps"]:
                    best = (label, s, xi, wm)
        print("→ 本季 RPS 最佳：%s（%.4f）" % (best[0], best[1]["rps"]))

    # 只用调参季选出的方案看验证季
    tune = errors[0][2]
    cands = []
    for xi in xis:
        sub = [r for r in tune if r[0] == xi]
        for wm in (1.0, 0.7, 0.6, 0.5, 0.4, 0.3):
            cands.append((score(sub, wm)["rps"], xi, wm))
    cands.sort()
    best_rps, xi, wm = cands[0]
    test = errors[1][2]
    sub = [r for r in test if r[0] == xi]
    s = score(sub, wm)
    base_pure = score([r for r in test if r[0] == 0.0], 1.0)
    base_mkt = score(test, 0.0)
    print("\n=== 交叉验证结论（调参季 %s 选参，验证季 %s 只用不调）===" % (a.tune, a.test))
    print("选中方案：xi=%.4f，模型权重 %.0f%%" % (xi, 100 * wm))
    print("验证季：命中 %.1f%%  RPS %.4f  Brier %.4f" % (100 * s["hit"], s["rps"], s["brier"]))
    print("对照：纯模型(不衰减) RPS %.4f / 市场 %.4f" % (base_pure["rps"], base_mkt["rps"]))
    print("结论：%s" % ("优于两者" if s["rps"] < min(base_pure["rps"], base_mkt["rps"]) else "未优于两者，不建议改"))


if __name__ == "__main__":
    main()
