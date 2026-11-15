# -*- coding: utf-8 -*-
"""
smoke_test.py — 闸门与工具库导入冒烟测试（CI 门禁）
作用：确认所有 gates/lib 模块可被正常导入、无导入级错误，并验证状态机可启动。
用法：python3 gates/smoke_test.py
退出码：0 全通过；非 0 表示有模块无法加载。
"""
import glob
import importlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def to_module(path):
    rel = os.path.relpath(path, ROOT)
    return rel[:-3].replace(os.sep, ".")


def main():
    # 以脚本方式运行时，解释器默认只把 gates/ 加入搜索路径，需显式补仓库根目录
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    targets = sorted(glob.glob(os.path.join(ROOT, "gates", "*.py"))
                     + glob.glob(os.path.join(ROOT, "lib", "*.py")))
    # 本冒烟脚本自身不做导入断言
    targets = [p for p in targets if os.path.basename(p) != "smoke_test.py"]
    failures = []
    for path in targets:
        mod = to_module(path)
        try:
            importlib.import_module(mod)
            print("import OK:", mod)
        except Exception as exc:  # noqa: BLE001
            failures.append((mod, repr(exc)))
            print("import FAIL:", mod, repr(exc))

    # 验证状态机脚本能正常启动（status 不依赖真实产物，只读默认状态）
    stage = os.path.join(ROOT, "gates", "stage_gate.py")
    proc = subprocess.run(
        [sys.executable, stage, "SMOKE", "status"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if proc.returncode != 0 or "SMOKE" not in proc.stdout:
        failures.append(("stage_gate.status", proc.stderr.strip() or proc.stdout.strip()))
    else:
        print("state machine status OK")

    if failures:
        print("\n冒烟测试失败，共", len(failures), "项：")
        for mod, err in failures:
            print(" -", mod, err)
        sys.exit(1)
    print("\n全部闸门/工具库冒烟通过。")


if __name__ == "__main__":
    main()
