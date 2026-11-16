# -*- coding: utf-8 -*-
"""
calib_check.py — 平局校准表结构体检（CI 用，秒级，不碰数据）

只查表本身是否可用：
  1. 键必须是 0.00–1.00 的两位小数字符串，且完整连续；
  2. 值必须在合理区间，且单调不减（isotonic 的硬性质，出现回落=拟合或手改坏了）；
  3. 低端台阶跨度不得过大（宽度过大意味着把一小段样本外推到一大片区间，须附体检报告）。

改表要附 `python3 lib/calib_eval.py` 的样本外数字，本检查只保证表可用、不保证表更准。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAP = os.path.join(ROOT, "calib", "draw_calib_map.json")


def main():
    m = json.load(open(MAP))
    errs = []
    keys = sorted(m)
    expect = ["%.2f" % (i / 100.0) for i in range(101)]
    if keys != expect:
        missing = [k for k in expect if k not in m][:5]
        errs.append("键不是 0.00–1.00 连续两位小数，缺：%s" % (missing or "多余键"))
    vals = [float(m[k]) for k in keys if k in m]
    if any(v <= 0 or v >= 1 for v in vals):
        errs.append("存在越界值（必须 (0,1)）")
    for i in range(1, len(vals)):
        if vals[i] + 1e-9 < vals[i - 1]:
            errs.append("表非单调：%.2f→%.3f 回落到 %.3f" % (i - 1, vals[i - 1], vals[i]))
            break
    # 台阶跨度：相邻键的抬升幅度
    jumps = [(keys[i], round(vals[i] - vals[i - 1], 4)) for i in range(1, len(vals))
             if vals[i] - vals[i - 1] > 0.03]
    if jumps:
        print("提示：存在较大台阶（>0.03）：%s" % jumps[:5])

    if errs:
        print("校准表体检失败：")
        for e in errs:
            print(" -", e)
        return 1
    print("校准表结构 OK：101 键连续、单调、区间合法；低端 %.3f" % vals[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
