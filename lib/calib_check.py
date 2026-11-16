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
    if any(v < 0 or v > 1 for v in vals):
        errs.append("存在越界值（必须 [0,1]）")
    for i in range(1, len(vals)):
        if vals[i] + 1e-9 < vals[i - 1]:
            errs.append("表非单调：%.2f→%.3f 回落到 %.3f" % (i - 1, vals[i - 1], vals[i]))
            break
    # 台阶跨度：相邻键的抬升幅度
    jumps = [(keys[i], round(vals[i] - vals[i - 1], 4)) for i in range(1, len(vals))
             if vals[i] - vals[i - 1] > 0.03]
    if jumps:
        print("提示：存在较大台阶（>0.03）：%s" % jumps[:5])

    # 支撑度与口径：没有支撑就不许有"校准"
    meta_p = os.path.join(ROOT, "calib", "calib_meta.json")
    sup_p = os.path.join(ROOT, "calib", "支撑度_2526.json")
    if not os.path.exists(meta_p):
        errs.append("缺 calib/calib_meta.json（没有口径说明的校准表视为不可审计）")
    else:
        meta = json.load(open(meta_p))["_meta"]
        for k in ("method", "status", "effect", "support_file"):
            if not meta.get(k):
                errs.append("calib_meta 缺字段 %s" % k)
        sup = json.load(open(os.path.join(ROOT, meta.get("support_file", "")))) if os.path.exists(
            os.path.join(ROOT, meta.get("support_file", ""))) else None
        if sup is None:
            errs.append("支撑度文件缺失或路径不对：%s" % meta.get("support_file"))
        else:
            if sum(b["n"] for b in sup["bins"]) != sup["n"]:
                errs.append("支撑度分段样本数与总数不一致")
            lo, hi = sup["bins"][0]["lo"], sup["bins"][-1]["hi"]
            if abs(lo) > 1e-9 or abs(hi - 1.01) > 1e-9 and abs(hi - 1.0) > 1e-9:
                errs.append("支撑度分段未覆盖 0–1（%.2f–%.2f）" % (lo, hi))
            for b in sup["bins"]:
                if b.get("supported"):
                    continue
                # 支撑不足的区段不允许出现大幅偏离（>0.02 即视为无据调整）
                for k in expect:
                    x = float(k)
                    if b["lo"] - 1e-9 <= x < b["hi"] and abs(float(m.get(k, x)) - x) > 0.02:
                        errs.append("支撑不足区段 %.2f–%.2f 内 %.2f→%.3f 偏离过大（<300 场样本撑不起这个调整）"
                                    % (b["lo"], b["hi"], x, float(m.get(k, x))))
                        break

    if errs:
        print("校准表体检失败：")
        for e in errs:
            print(" -", e)
        return 1
    print("校准表结构 OK：101 键连续、单调、区间合法；低端 %.3f" % vals[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
