# -*- coding: utf-8 -*-
"""
blend.py — 生成"模型+收盘市场"的融合概率（方向决策的基准）

依据：2026-11-16 对照实验（2627 赛季 1607 场，2526 选参、2627 只验不调）
  市场收盘 RPS 0.1960 / 命中 55.4%
  纯 DC 模型  RPS 0.2068 / 命中 54.1%
  融合 w=30%  RPS 0.1966 / 命中 56.0%
即：纯模型打不过收盘市场；融合能把概率质量拉回市场水平，命中率略高（差异不显著，约 1.1 个标准误）。
结论：方向默认用融合概率定，纯模型只在"跟融合分歧"时作为增量信号，且必须举证。

用法：
  python3 tools/blend.py D43            # 默认 w=0：样本外纯市场 RPS 最优，模型仅作分歧提示
  python3 tools/blend.py D43 --w 0.4
输出：<batch>_blend.json
  {"_meta": {"w_model":0.3,"source":"pb_dc+market_fair","created_at":...},
   "周日001": {"p":[h,d,a], "dir":"H", "model_dir":"A", "market_dir":"H", "diverged":true}}
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_W = 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--w", type=float, default=DEFAULT_W)
    ap.add_argument("--ws", default=None)
    a = ap.parse_args()
    ws = a.ws or os.environ.get("GATE_WS") or ROOT

    mo_path = os.path.join(ws, "%s_model_out.json" % a.batch)
    if not os.path.exists(mo_path):
        sys.exit("缺 %s_model_out.json" % a.batch)
    models = json.load(open(mo_path))
    out, missing = {}, []
    for num, r in models.items():
        if str(num).startswith("_"):
            continue
        mk = r.get("market_fair") or {}
        dc = r.get("pb_dc")
        if not all(mk.get(k) for k in ("h", "d", "a")):
            missing.append("%s 无市场概率" % num)
            continue
        m = [float(mk["h"]), float(mk["d"]), float(mk["a"])]
        if isinstance(dc, list) and len(dc) == 3:
            p = [a.w * float(dc[i]) + (1 - a.w) * m[i] for i in range(3)]
            mdir, mkt = "HDA"[max(range(3), key=lambda i: dc[i])], "HDA"[max(range(3), key=lambda i: m[i])]
        else:
            p, mdir, mkt = m, None, "HDA"[max(range(3), key=lambda i: m[i])]
        s = sum(p)
        p = [x / s for x in p]
        d = "HDA"[max(range(3), key=lambda i: p[i])]
        out[num] = {"p": [round(x, 4) for x in p], "dir": d, "model_dir": mdir,
                    "market_dir": mkt, "diverged": bool(mdir and mdir != mkt)}
    out["_meta"] = {"w_model": a.w, "source": "pb_dc + market_fair(devig)",
                    "created_at": datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
                    "missing_market": missing,
                    "rule": "方向默认取 dir；model_dir 与 dir 不同即为与融合分歧，须举证"}
    p = os.path.join(ws, "%s_blend.json" % a.batch)
    json.dump(out, open(p, "w"), ensure_ascii=False, indent=1)
    div = [k for k, v in out.items() if isinstance(v, dict) and v.get("diverged")]
    print("写入 %s：%d 场；模型与市场分歧 %d 场%s%s" % (
        os.path.basename(p), len([k for k in out if not k.startswith("_")]), len(div),
        "（缺市场：%s）" % "；".join(missing) if missing else "", "；分歧场：" + "、".join(div) if div else ""))


if __name__ == "__main__":
    main()
