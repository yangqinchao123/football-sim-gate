#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lock_predictions.py — 预测锁定工具（先锁后看，命中率才可核）

作用：给 <batch>_final_picks.json 写入 _meta 锁定块（locked_at + predictions_sha256），
      S5 校验锁定存在，S11 复核指纹未变。锁定后改方向=哈希不符=本批命中率作废。

用法：
  python3 tools/lock_predictions.py D43                  # 首次锁定
  python3 tools/lock_predictions.py D43 --note "S5 出表后锁定"
  python3 tools/lock_predictions.py D43 --relock --note "errata E-01 更正 010 方向"

纪律：
  - 只写 _meta，不动任何比赛条目；锁定后改预测必须走 errata 并 --relock 留痕。
  - 每场必须有方向（dir ∈ H/D/A），缺方向不许锁。
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = ROOT


def pick_sha(items):
    raw = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("batch")
    ap.add_argument("--note", default="")
    ap.add_argument("--relock", action="store_true")
    a = ap.parse_args()

    path = os.path.join(WS, "%s_final_picks.json" % a.batch)
    if not os.path.exists(path):
        print("找不到 %s" % path)
        return 2
    d = json.load(open(path))
    items = {k: v for k, v in d.items() if not str(k).startswith("_")}
    if not items:
        print("方向表为空，拒绝锁定")
        return 2
    bad = [n for n, r in items.items()
           if not re.match(r"^[HDA](?:$|[（(\s：:\-])", str(r.get("dir", "")).strip().upper())]
    if bad:
        print("以下场次缺方向，拒绝锁定：%s" % "、".join(bad))
        return 2

    meta = d.get("_meta") or {}
    if meta.get("locked_at") and not a.relock:
        print("本批已锁定于 %s，指纹 %s" % (meta["locked_at"], meta.get("predictions_sha256")))
        print("如需更正，请走 errata 后加 --relock，不要直接改方向。")
        return 1

    sha = pick_sha(items)
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    history = meta.get("history") or []
    if meta.get("locked_at"):
        history.append({"relocked_at": ts, "prev_sha256": meta.get("predictions_sha256"),
                        "note": a.note})
    d["_meta"] = {"locked_at": meta.get("locked_at") or ts,
                  "predictions_sha256": sha,
                  "items": len(items),
                  "note": a.note,
                  "history": history}
    with open(path, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=1, sort_keys=False)
    print("已锁定 %s：%d 场，指纹 %s（%s）" % (a.batch, len(items), sha[:16], ts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
