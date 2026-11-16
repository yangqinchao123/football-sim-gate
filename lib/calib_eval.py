# -*- coding: utf-8 -*-
"""
calib_eval.py — 平局校准表的样本外体检（改表之前必须先跑它）

用途：给任意一份 draw_calib_map.json 打分。流程：
  1. 用目标赛季之前的数据滚动重建模型（步长 stride，默认每 10 场重拟合一次，控制耗时）；
  2. 对目标赛季逐场预测 → 原始 P(H/D/A) 与套表后的 P(H/D/A) 各算 RPS / Brier；
  3. 单独统计低 P(D) 区（默认 <0.12），也就是"低端被抬高"最敏感的那一段。

纪律：任何对 calib/draw_calib_map.json 的修改，PR 必须附本脚本的输出。
没有样本外数字就改表，按"凭感觉调参"处理，不准合。

用法：
  python3 lib/calib_eval.py                       # 默认 2627 赛季 + 仓库内校准表
  python3 lib/calib_eval.py --season 2627 --stride 10 --map calib/draw_calib_map.json
  python3 lib/calib_eval.py --data /path/to/football_data
"""
import argparse
import bisect
import csv
import json
import os

import numpy as np
from penaltyblog.models import DixonColesGoalModel

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DATA = os.path.join(ROOT, "data")
LEAGUES = ["E0", "I1", "SP1", "D1", "F1", "P1", "N1", "B1", "T1", "G1"]


def load_map(path):
    m = json.load(open(path))
    keys = sorted(m)
    return m, keys


def apply_map(p, m, keys):
    """D 用表的校准值，剩余概率在 H/A 间按原比例归一（与模型脚本同口径）。"""
    p = np.asarray(p, dtype=float)
    k = "%.2f" % float(p[1])
    if k in m:
        nd = float(m[k])
    else:
        i = bisect.bisect(keys, k)
        nd = float(m[keys[min(i, len(keys) - 1)]])
    rest = 1.0 - nd
    ha = p[0] + p[2]
    if ha > 0:
        out = np.array([rest * p[0] / ha, nd, rest * p[2] / ha])
    else:
        out = np.array([rest / 2, nd, rest / 2])
    return out / max(out.sum(), 1e-9)


def rps(p, y):
    cp = np.cumsum(p[:-1])
    cy = np.cumsum([1.0 if i == y else 0.0 for i in range(len(p))])[:-1]
    return float(np.mean((cp - cy) ** 2))


def brier(p, y):
    oh = np.zeros(3)
    oh[y] = 1.0
    return float(np.mean((p - oh) ** 2))


def collect(data_dir, season, stride):
    recs = []
    for L in LEAGUES:
        fp = os.path.join(data_dir, "%s_%s.csv" % (L, season))
        if not os.path.exists(fp):
            continue
        rows = list(csv.DictReader(open(fp, encoding="utf-8-sig")))
        for k in range(stride, len(rows), stride):
            tr = rows[:k]
            try:
                mdl = DixonColesGoalModel(
                    goals_home=[int(x["FTHG"]) for x in tr],
                    goals_away=[int(x["FTAG"]) for x in tr],
                    teams_home=[x["HomeTeam"] for x in tr],
                    teams_away=[x["AwayTeam"] for x in tr])
                mdl.fit()
            except Exception:
                continue
            for g in rows[k:k + stride]:
                if g.get("FTR") not in ("H", "D", "A"):
                    continue
                try:
                    p = list(mdl.predict(g["HomeTeam"], g["AwayTeam"]).home_draw_away)
                except Exception:
                    continue
                if not all(np.isfinite(p)) or abs(sum(p) - 1.0) > 0.05:
                    continue
                recs.append((p, {"H": 0, "D": 1, "A": 2}[g["FTR"]]))
    return recs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2627")
    ap.add_argument("--stride", type=int, default=10)
    ap.add_argument("--map", default=os.path.join(ROOT, "calib", "draw_calib_map.json"))
    ap.add_argument("--data", default=DEFAULT_DATA)
    ap.add_argument("--low", type=float, default=0.12, help="低 P(D) 区阈值")
    a = ap.parse_args()

    recs = collect(a.data, a.season, a.stride)
    if not recs:
        print("无样本：检查 --data 下是否存在 %s_%s.csv" % ("<联赛>", a.season))
        return 2
    m, keys = load_map(a.map)
    P = np.array([r[0] for r in recs])
    Y = np.array([r[1] for r in recs])

    raw_rps = np.mean([rps(P[i] / P[i].sum(), Y[i]) for i in range(len(Y))])
    raw_b = np.mean([brier(P[i] / P[i].sum(), Y[i]) for i in range(len(Y))])
    mapped = np.array([apply_map(P[i], m, keys) for i in range(len(Y))])
    map_rps = np.mean([rps(mapped[i], Y[i]) for i in range(len(Y))])
    map_b = np.mean([brier(mapped[i], Y[i]) for i in range(len(Y))])

    low_idx = [i for i in range(len(Y)) if P[i][1] < a.low]
    print("赛季 %s，样本 %d 场，步长 %d" % (a.season, len(Y), a.stride))
    print("原始   RPS %.4f  Brier %.4f" % (raw_rps, raw_b))
    print("套表后 RPS %.4f  Brier %.4f  （ΔRPS %+.4f）" % (map_rps, map_b, map_rps - raw_rps))
    if low_idx:
        lraw = np.mean([rps(P[i] / P[i].sum(), Y[i]) for i in low_idx])
        lmap = np.mean([rps(mapped[i], Y[i]) for i in low_idx])
        print("低 P(D)<%.2f 区 %d 场（%.1f%%）：原始 %.4f → 套表 %.4f"
              % (a.low, len(low_idx), 100.0 * len(low_idx) / len(Y), lraw, lmap))
    verdict = "套表更好" if map_rps < raw_rps else "套表更差，需重拟合"
    print("结论：%s（ΔRPS %+.4f）" % (verdict, map_rps - raw_rps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
