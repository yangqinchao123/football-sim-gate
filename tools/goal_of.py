# -*- coding: utf-8 -*-
"""取批次目标阶段的较大值：max(base 分支上的 <batch>_goal.json, 当前 HEAD 上的)。

为什么：goal 若完全由 PR 自己声明，PR 就能把自己降到容易过的阶段（审计 P8 洞）。
规则=只准升、不准降。用法: python3 tools/goal_of.py <base_sha> <batch>
"""
import json, subprocess, sys


def stage_idx(v):
    try:
        return int(str(v).replace("S", ""))
    except Exception:
        return -1


def read(rev, batch):
    try:
        raw = subprocess.check_output(
            ["git", "show", "%s:%s_goal.json" % (rev, batch)],
            stderr=subprocess.DEVNULL,
        ).decode("utf-8")
        return stage_idx(json.loads(raw).get("goal_stage", "S0"))
    except Exception:
        return -1


def main():
    if len(sys.argv) < 3:
        print("S0")
        return 0
    base, batch = sys.argv[1], sys.argv[2]
    a, b = read(base, batch), read("HEAD", batch)
    print("S%d" % max(a, b))
    return 0


if __name__ == "__main__":
    sys.exit(main())
