#!/usr/bin/env python3
"""fact_check.py — 手动体检事实卡片（门禁里跑的是同一套 lib/fact_rules.py）
用法：python3 tools/fact_check.py daily/xxx/D43_fact_周日001.json [--data <football_data 目录>]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "lib"))
import fact_rules  # noqa: E402


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("用法：python3 tools/fact_check.py <事实卡片.json> [--data <dir>]")
        return 2
    data = None
    if "--data" in sys.argv:
        data = sys.argv[sys.argv.index("--data") + 1]
    card = json.load(open(args[0]))
    errs = fact_rules.check(card, data)
    if errs:
        print("事实卡片校验失败：")
        for e in errs:
            print(" -", e)
        return 1
    print("事实卡片校验 OK：整表不变量与溯源一致（%s）" % args[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
