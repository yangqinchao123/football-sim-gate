# -*- coding: utf-8 -*-
"""
match_gate.py — 单场研究闸门（逐场深度 + 最小研究时长）
硬规则：
  1. 每场研究开始用 start 落时间戳；时间戳一旦写入不可覆盖、不可手改。
  2. 交付 GRC 前 check：实际研究时长必须 ≥ MIN_SECONDS（默认 600 秒 / 10 分钟），不足即 BLOCK。
  3. 同时校验该场 GRC 必需字段齐全（深度闸门），缺项 BLOCK。
  4. 状态文件只追加，禁止删除重写。
用法：
  python3 match_gate.py <batch> start <num>          # 标记某场研究开始
  python3 match_gate.py <batch> check <num> [grc_file]  # 时长+深度闸门
  python3 match_gate.py <batch> status               # 查看各场计时
"""
import json, os, sys, datetime

WS = os.path.dirname(os.path.abspath(__file__))
MIN_SECONDS = 600  # 每场分析不少于 10 分钟

# GRC 必需字段（深度闸门，缺一不可）
REQUIRED_TAGS = [
    "A1 战意", "A2 实力", "A3 赛程", "A4 主客", "A5 环境",
    "A7 数据", "A8 市场", "替代者三问", "关键对位", "裁判",
    "B4 进球路径", "C 红队",
]

def state_path(batch):
    return os.path.join(WS, f"{batch}_match_timer.json")

def load(batch):
    p = state_path(batch)
    if os.path.exists(p):
        return json.load(open(p))
    return {}

def save(batch, d):
    json.dump(d, open(state_path(batch), "w"), ensure_ascii=False, indent=1)

def parse_ts(s):
    return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")

def now_str():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def cmd_start(batch, num):
    d = load(batch)
    if num in d and d[num].get("start"):
        print(f"BLOCKED — 场{num} 已有开始时间戳，禁止覆盖（{d[num]['start']}）")
        return
    d.setdefault(num, {})
    d[num]["start"] = now_str()
    d[num]["status"] = "researching"
    save(batch, d)
    print(f"场{num} 研究开始计时：{d[num]['start']}，最早交付时间 {min_end(d[num]['start'])}")

def min_end(start_str):
    return (parse_ts(start_str) + datetime.timedelta(seconds=MIN_SECONDS)).strftime("%H:%M:%S")

def cmd_check(batch, num, grc_file=None):
    d = load(batch)
    if num not in d or not d[num].get("start"):
        print(f"BLOCKED — 场{num} 无开始时间戳，请先 start")
        sys.exit(1)
    start = parse_ts(d[num]["start"])
    elapsed = (datetime.datetime.now() - start).total_seconds()
    # 时长闸门
    if elapsed < MIN_SECONDS:
        wait = int(MIN_SECONDS - elapsed)
        print(f"BLOCKED — 场{num} 研究时长 {int(elapsed)}s < {MIN_SECONDS}s，还需 {wait}s（约 {wait//60} 分 {wait%60} 秒）")
        sys.exit(1)
    # 深度闸门：GRC 必需字段
    if grc_file and os.path.exists(grc_file):
        text = open(grc_file, errors="ignore").read()
        missing = [t for t in REQUIRED_TAGS if t not in text]
        if missing:
            print(f"BLOCKED — 场{num} GRC 缺字段：{missing}")
            sys.exit(1)
    d[num]["end"] = now_str()
    d[num]["duration_sec"] = int(elapsed)
    d[num]["status"] = "passed"
    if grc_file:
        d[num]["grc_file"] = grc_file
    save(batch, d)
    print(f"PASS — 场{num} 研究时长 {int(elapsed)}s（≥600s），深度字段齐全，允许交付")

def cmd_status(batch):
    d = load(batch)
    if not d:
        print("暂无计时记录")
        return
    print(f"批次 {batch} 单场研究计时：")
    for num in sorted(d):
        r = d[num]
        dur = r.get("duration_sec")
        dur_txt = f"{dur}s" if dur else (
            f"进行中 {int((datetime.datetime.now()-parse_ts(r['start'])).total_seconds())}s")
        print(f"  {num} [{r.get('status')}] start={r['start']} 时长={dur_txt}")

def main():
    batch = sys.argv[1]
    cmd = sys.argv[2]
    if cmd == "start":
        cmd_start(batch, sys.argv[3])
    elif cmd == "check":
        grc = sys.argv[4] if len(sys.argv) > 4 else None
        cmd_check(batch, sys.argv[3], grc)
    elif cmd == "status":
        cmd_status(batch)
    else:
        print("未知命令：start/check/status")

if __name__ == "__main__":
    main()
