# -*- coding: utf-8 -*-
"""
calib_compare.py — 平局校准：方法对照 + 支撑度体检

要回答两件事：
  1. 那 101 个键的阶跃，每段到底有多少样本撑着？（之前从没算过）
  2. isotonic / beta / Platt / 不校准，谁在样本外更准？差异是否超出噪声？

口径：2526 拟合，2627 只验证不调参；七联赛；滚动重拟合步长 stride。
同时给出"纯模型"和"融合 w=30%（方向实际用的口径）"两套指标。

用法：python3 research/calib_compare.py --stride 10
"""
import argparse
import csv
import json
import os

import numpy as np
from penaltyblog.models import DixonColesGoalModel
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "football_data")
if not os.path.isdir(DATA):
    DATA = os.path.join(os.path.dirname(ROOT), "football_data")
LEAGUES = ["E0", "SP1", "D1", "I1", "F1", "N1", "P1"]
W_BLEND = 0.30


def load(league, season):
    fp = os.path.join(DATA, "%s_%s.csv" % (league, season))
    if not os.path.exists(fp):
        return None
    return [r for r in csv.DictReader(open(fp, encoding="utf-8-sig"))
            if r.get("FTR") in ("H", "D", "A") and r.get("FTHG") != ""]


def devig(h, d, a):
    try:
        h, d, a = float(h), float(d), float(a)
    except Exception:
        return None
    inv = 1.0 / h + 1.0 / d + 1.0 / a
    return np.array([(1.0 / h) / inv, (1.0 / d) / inv, (1.0 / a) / inv]) if inv > 0 else None


def market_of(row):
    for c in (("B365CH", "B365CD", "B365CA"), ("B365H", "B365D", "B365A")):
        p = devig(row.get(c[0]), row.get(c[1]), row.get(c[2]))
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
                    p = np.array(list(m.predict(g["HomeTeam"], g["AwayTeam"]).home_draw_away))
                except Exception:
                    continue
                if not np.isfinite(p).all():
                    continue
                out.append((p / p.sum(), {"H": 0, "D": 1, "A": 2}[g["FTR"]], market_of(g)))
    return out


def rebuild(p, new_d):
    new_d = float(np.clip(new_d, 0.02, 0.80))
    rest = 1.0 - new_d
    ha = p[0] + p[2]
    if ha <= 0:
        return np.array([rest / 2, new_d, rest / 2])
    return np.array([rest * p[0] / ha, new_d, rest * p[2] / ha])


def rps(p, y):
    c1, c2 = p[0], p[0] + p[1]
    y1 = 1.0 if y == 0 else 0.0
    y2 = 1.0 if y <= 1 else 0.0
    return ((c1 - y1) ** 2 + (c2 - y2) ** 2) / 2.0


def brier(p, y):
    return float(np.sum((p - np.eye(3)[y]) ** 2))


def logloss(p, y):
    return float(-np.log(max(p[y], 1e-12)))


def beta_features(x):
    x = np.clip(x, 1e-6, 1 - 1e-6)
    return np.column_stack([np.log(x / (1 - x)), np.log(1 - x)])


def eval_rows(rows, fn, w=None):
    vals_r, vals_b, vals_l, hits = [], [], [], []
    for p, y, mk in rows:
        q = fn(p)
        if w is not None and mk is not None:
            q = w * q + (1 - w) * mk
            q = q / q.sum()
        vals_r.append(rps(q, y))
        vals_b.append(brier(q, y))
        vals_l.append(logloss(q, y))
        hits.append(1 if int(np.argmax(q)) == y else 0)
    return np.mean(vals_r), np.mean(vals_b), np.mean(vals_l), 100.0 * np.mean(hits)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tune", default="2526")
    ap.add_argument("--test", default="2627")
    ap.add_argument("--stride", type=int, default=10)
    ap.add_argument("--min_support", type=int, default=300)
    a = ap.parse_args()

    tune = collect(a.tune, "2425", a.stride)
    test = collect(a.test, a.tune, a.stride)
    print("训练 %s %d 场 / 验证 %s %d 场（融合口径 w=%.2f）" %
          (a.tune, len(tune), a.test, len(test), W_BLEND))

    pd_tr = np.array([p[1] for p, _, _ in tune])
    y_tr = np.array([1 if y == 1 else 0 for _, y, _ in tune])
    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0).fit(pd_tr, y_tr)
    beta = LogisticRegression(C=1e6, max_iter=1000).fit(beta_features(pd_tr), y_tr)
    platt = LogisticRegression(C=1e6, max_iter=1000).fit(pd_tr.reshape(-1, 1), y_tr)

    bins = [0.0, 0.06, 0.10, 0.12, 0.14, 0.16, 0.18, 0.20, 0.22, 0.26, 0.30, 0.35, 1.01]
    print("\n=== 训练集 P(D) 分段：样本量与实际平局率 ===")
    counts = []
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i + 1]
        sel = (pd_tr >= lo) & (pd_tr < hi)
        n = int(sel.sum())
        counts.append(n)
        dr = 100.0 * float(np.mean(y_tr[sel])) if n else float("nan")
        print("P(D) %.2f–%.2f：%5d 场  实际平局 %.1f%%%s" %
              (lo, hi, n, dr, "   ← 支撑不足(<%d)" % a.min_support if n < a.min_support else ""))

    edges = np.array(bins)

    def support_gate(p):
        idx = int(np.clip(np.searchsorted(edges, p[1], side="right") - 1, 0, len(counts) - 1))
        if counts[idx] < a.min_support:
            return p
        return rebuild(p, float(iso.predict([p[1]])[0]))

    def iso_fn(p):
        return rebuild(p, float(iso.predict([p[1]])[0]))

    def beta_fn(p):
        return rebuild(p, float(beta.predict_proba(beta_features([p[1]]))[0][1]))

    def platt_fn(p):
        return rebuild(p, float(platt.predict_proba([[p[1]]])[0][1]))

    cur = None
    tp = os.path.join(ROOT, "calib", "draw_calib_map.json")
    if os.path.exists(tp):
        m = json.load(open(tp))

        def cur_fn(p):
            return rebuild(p, float(m.get("%.2f" % float(p[1]), p[1])))

    methods = [("不做校准", lambda p: p), ("isotonic(本季拟合)", iso_fn),
               ("beta(本季拟合)", beta_fn), ("Platt(本季拟合)", platt_fn),
               ("isotonic+支撑门槛", support_gate)]
    if cur:
        methods.append(("现行 101 键表", cur_fn))

    for w, tag in ((None, "纯模型口径"), (W_BLEND, "融合口径 w=0.30")):
        print("\n=== %s · %s 验证（不调参）===" % (a.test, tag))
        print("%-22s %7s %7s %8s %7s" % ("方案", "RPS", "Brier", "LogLoss", "命中%"))
        for name, fn in methods:
            r, b, l, h = eval_rows(test, fn, w)
            print("%-22s %7.4f %7.4f %8.4f %6.1f" % (name, r, b, l, h))
        mk_r, mk_b, mk_l, mk_h = eval_rows(test, lambda p: p, 0.0)
        print("%-22s %7.4f %7.4f %8.4f %6.1f" % ("市场收盘（基准）", mk_r, mk_b, mk_l, mk_h))
        sup_rows = [r for r in test if r[2] is not None]
        print("（融合口径参与市场对比的场次 %d/%d）" % (len(sup_rows), len(test)))


if __name__ == "__main__":
    main()
