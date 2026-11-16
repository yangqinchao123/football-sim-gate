#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_pins.py — 工作流里的 action 必须 pin 到完整 40 位 commit SHA

理由：tag/分支是可变量，被改了就等于把构建交给别人；SHA 才是不可变引用。
本地 action（./xxx）与 docker:// 不在本条约束内。
用法：python3 tools/check_pins.py   （CI 每次跑，违反即失败）
"""
import glob
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USE = re.compile(r"uses:\s*(\S+)")
SHA = re.compile(r"^[0-9a-f]{40}$")


def selftest():
    """反向用例：未 pin 的工作流必须被判失败，否则这个检查等于装饰。"""
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, ".github", "workflows"))
        with open(os.path.join(td, ".github", "workflows", "bad.yml"), "w") as f:
            f.write("jobs:\n  a:\n    steps:\n      - uses: actions/checkout@v4\n")
        with open(os.path.join(td, ".github", "workflows", "ok.yml"), "w") as f:
            f.write("jobs:\n  a:\n    steps:\n      - uses: actions/checkout@%s  # v4\n"
                    % ("a" * 40))
        rc_bad = scan(td, quiet=True)
        os.remove(os.path.join(td, ".github", "workflows", "bad.yml"))
        rc_ok = scan(td, quiet=True)
    print("selftest：未 pin → %s；已 pin → %s" % ("拦下" if rc_bad else "放行（错）",
                                                "放行" if rc_ok == 0 else "拦下（错）"))
    return 0 if (rc_bad != 0 and rc_ok == 0) else 1


def scan(root=ROOT, quiet=False):
    bad = []
    files = glob.glob(os.path.join(root, ".github", "workflows", "*.yml")) + \
            glob.glob(os.path.join(root, ".github", "workflows", "*.yaml"))
    for fp in files:
        for i, line in enumerate(open(fp, encoding="utf-8"), 1):
            m = USE.search(line)
            if not m:
                continue
            ref = m.group(1).split("#")[0].strip().strip('"').strip("'")
            if ref.startswith("./") or ref.startswith("docker://"):
                continue
            if "@" not in ref:
                bad.append("%s:%d 未带版本：%s" % (os.path.basename(fp), i, ref))
                continue
            sha = ref.split("@", 1)[1]
            if not SHA.match(sha):
                bad.append("%s:%d 用的是可移动引用（需 40 位 SHA）：%s" % (os.path.basename(fp), i, ref))
    if bad:
        if not quiet:
            print("工作流 pin 检查失败：")
            for b in bad:
                print(" -", b)
        return 1
    if not quiet:
        print("工作流 pin 检查 OK：%d 个文件，全部指向不可变 commit" % len(files))
    return 0


def main():
    if "--selftest" in sys.argv:
        return selftest()
    return scan()


if __name__ == "__main__":
    sys.exit(main())
