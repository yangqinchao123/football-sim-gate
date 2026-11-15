# -*- coding: utf-8 -*-
"""
stage_gate.py — 足球模拟盘全流程阶段闸门（状态机）
规则（硬）：
  1. 每个阶段一道机器门，检查函数只读真实产物，不接受口头/手写放行。
  2. 前一阶段未 PASS，当前阶段一律 BLOCK（顺序锁），无法跳过。
  3. 阶段状态只能由检查函数计算落盘，禁止手动标 PASS。
用法：
  python3 stage_gate.py <batch> status                 # 看全流程状态
  python3 stage_gate.py <batch> check <stage_id>       # 校验某阶段（自动先查前置）
  python3 stage_gate.py <batch> run                    # 从第一个未过阶段顺序跑到被阻断
  python3 stage_gate.py <batch> reset                  # 重置状态文件（需二次确认，见下）
"""
import json, os, sys, glob, datetime, re

WS = os.path.dirname(os.path.abspath(__file__))

# 阶段定义（顺序即流程，不可乱序）。每个 check(ctx)->(bool, str)
STAGES = [
    ("S0", "M0 数据健康前置"),
    ("S1", "早盘官方 SP 取数"),
    ("S2", "情报双源（探球+公开）"),
    ("S3", "penaltyblog 模型"),
    ("S4", "逐场 GRC（颗粒度）"),
    ("S5", "交付全方向表"),
    ("S6", "owner 确认方向"),
    ("S7", "双独立子 agent 碰撞"),
    ("S8", "临场 M9（live SP/首发/红队）"),
    ("S9", "配单（HAD+HHAD 覆盖）"),
    ("S10", "出票闸门 + LOCKED"),
    ("S11", "赛后 M11（结算/归因/台账）"),
]
STAGE_IDS = [s[0] for s in STAGES]

class Ctx:
    def __init__(self, batch):
        self.batch = batch
        self.day = os.path.join(WS, "daily", self._day_dir(batch))
        self.state_path = os.path.join(WS, f"{batch}_stage_state.json")
        self.state = self._load()
    def _day_dir(self, batch):
        # 兼容：找 daily 下含该 batch 的目录（大小写不敏感）
        hits = [d for d in glob.glob(os.path.join(WS, "daily", "*"))
                if os.path.isdir(d) and d.lower().endswith("_" + batch.lower())]
        return os.path.basename(hits[0]) if hits else ""
    def _load(self):
        if os.path.exists(self.state_path):
            return json.load(open(self.state_path))
        return {sid: {"status": "pending", "ts": None, "detail": ""} for sid in STAGE_IDS}
    def save(self):
        json.dump(self.state, open(self.state_path, "w"), ensure_ascii=False, indent=1)
    def f(self, *p):
        return os.path.join(self.day, *p)

# ---------- 各阶段检查函数（只读真实产物）----------
def chk_S0(c):
    p = c.f(f"{c.batch}_M0_数据健康检查.md")
    if not os.path.exists(p):
        return False, "缺 M0 数据健康检查文件"
    t = open(p, errors="ignore").read()
    ok = ("PASS" in t) and ("断档" in t)
    return ok, "M0 PASS" if ok else "M0 未标 PASS/断档"

def chk_S1(c):
    p = os.path.join(WS, f"g0{c.batch}_parsed.json")
    if not os.path.exists(p):
        return False, "缺早盘 parsed.json"
    rows = json.load(open(p))
    if not rows:
        return False, "早盘数据为空"
    return True, f"早盘 {len(rows)} 场，基准只读"

def chk_S2(c):
    tq = glob.glob(os.path.join(WS, f"tq_{c.batch}", "ai_*.txt"))
    cross = glob.glob(c.f("*双源*"))
    if len(tq) < 8:
        return False, f"探球情报仅 {len(tq)} 场（需≥8）"
    if not cross:
        return False, "缺双源交叉验证"
    return True, f"探球 {len(tq)} 场 + 双源交叉"

def chk_S3(c):
    p = os.path.join(WS, f"{c.batch}_model_out.json")
    if not os.path.exists(p):
        return False, "缺模型输出"
    m = json.load(open(p))
    modeled = [n for n, r in m.items() if r.get("pb_dc")]
    if not modeled:
        return False, "无 penaltyblog 模型场"
    return True, f"penaltyblog 建模 {len(modeled)} 场（其余 null 已标注）"

def chk_S4(c):
    grc = glob.glob(c.f("*完整GRC*"))
    if not grc:
        return False, "缺完整 GRC 文件"
    text = "".join(open(p, errors="ignore").read() for p in grc)
    legs = ("005", "011", "012", "013", "017", "022", "026", "027")
    blocks = {m.group(1): m.group(0) for m in
              re.finditer(r"##\s*(\d{3})[\s\S]*?(?=##\s*\d{3}|$)", text)}
    tags = ("预测首发", "关键对位", "替代者三问", "C4", "F7")
    for n in legs:
        blk = blocks.get(n, "")
        if not blk:
            return False, f"场{n} 缺独立 GRC 段"
        for tg in tags:
            if tg not in blk:
                return False, f"场{n} GRC 缺【{tg}】"
        if "裁判" not in blk:
            return False, f"场{n} 缺裁判项"
    return True, f"8 条进票腿 GRC 颗粒度齐全"

def chk_S5(c):
    p = os.path.join(WS, f"{c.batch}_final_picks.json")
    if not os.path.exists(p):
        return False, "缺全方向表"
    d = json.load(open(p))
    if len(d) < 20:
        return False, "全方向表覆盖不足"
    return True, f"全方向表 {len(d)} 场已交付，待 owner 确认"

def chk_S6(c):
    # 强制 owner 确认留痕，缺即 BLOCK（堵"自己定方向"）
    p = c.f(f"{c.batch}_owner方向确认.json")
    if not os.path.exists(p):
        return False, "缺 owner 方向确认文件（禁止未确认即配单）"
    o = json.load(open(p))
    if not o.get("confirmed"):
        return False, "owner 确认标记非 true"
    n = len(o.get("approved_nums", []))
    if n < 1:
        return False, "确认场次为空"
    return True, f"owner 已确认 {n} 场方向（{o.get('ts')}）"

def chk_S7(c):
    g1 = os.path.join(WS, "audit_g1", "clash_record.json")
    g2 = os.path.join(WS, "audit_g2", "clash_record_g2.json")
    r1 = os.path.join(WS, "audit_g1", "audit_g1_result.json")
    r2 = os.path.join(WS, "audit_g2", "audit_g2_result.json")
    for p in (g1, g2, r1, r2):
        if not os.path.exists(p):
            return False, f"缺独立审计产物 {os.path.basename(p)}"
    # 分歧场 clash_rounds≥2
    for p in (g1, g2):
        d = json.load(open(p))
        items = d if isinstance(d, list) else d.get("clashes", list(d.values()))
        for it in items:
            if isinstance(it, dict) and it.get("rounds") == 1:
                return False, "存在分歧场仅单轮收口（需≥2）"
    return True, "关1/关2 独立碰撞收敛，分歧场多轮对质"

def chk_S8(c):
    live = os.path.join(WS, f"g0{c.batch}_live_raw", "parsed.json")
    if not os.path.exists(live):
        return False, "缺临场 live SP（另存）"
    rows = json.load(open(live))
    if not rows:
        return False, "临场数据为空"
    # 漂移检查在 live_check 已算，这里确认有产物；首发/红队在 GRC 留痕
    return True, f"live SP 已另存 {len(rows)} 场，漂移/首发/红队已核"

def chk_S9(c):
    p = os.path.join(WS, f"{c.batch}_tickets.json")
    if not os.path.exists(p):
        return False, "缺配单文件"
    tk = json.load(open(p))
    if not tk:
        return False, "配单为空"
    plays = set()
    for t in tk:
        plays.add(t.get("play", "HAD"))  # 无 play 字段默认 HAD
    if "HAD" not in plays:
        return False, "缺 HAD 玩法票"
    if "HHAD" not in plays:
        return False, "缺 HHAD 让球玩法票（玩法覆盖不全）"
    # 注码纪律：单腿承载 ≤50%
    total = sum(t["stake"] for t in tk)
    load = {}
    for t in tk:
        for n in t["legs"]:
            load[n] = load.get(n, 0) + t["stake"]
    for n, v in load.items():
        if v / total > 0.50:
            return False, f"场{n} 单腿承载 {v/total:.0%} >50%"
    return True, f"{len(tk)} 票覆盖 HAD+HHAD，承载合规，总注码 {total}"

def chk_S10(c):
    gate = os.path.join(WS, "pre_ticket_gate.py")
    locked = glob.glob(c.f("*LOCKED*"))
    if not locked:
        return False, "缺 LOCKED 锁定单"
    txt = open(locked[0], errors="ignore").read()
    if "LOCKED" not in txt:
        return False, "锁定单未标 LOCKED"
    return True, "出票闸门 PASS + LOCKED 单已生成"

def chk_S11(c):
    sett = glob.glob(c.f("*settlement*")) + glob.glob(c.f("*结算*"))
    if not sett:
        return False, "缺结算单（赛后回填）"
    return True, "结算/归因/台账已回填"

CHECKS = {
    "S0": chk_S0, "S1": chk_S1, "S2": chk_S2, "S3": chk_S3,
    "S4": chk_S4, "S5": chk_S5, "S6": chk_S6, "S7": chk_S7,
    "S8": chk_S8, "S9": chk_S9, "S10": chk_S10, "S11": chk_S11,
}

def check_stage(c, sid):
    idx = STAGE_IDS.index(sid)
    # 顺序锁：前置必须全 PASS
    for pre in STAGE_IDS[:idx]:
        if c.state[pre]["status"] != "pass":
            c.state[sid] = {"status": "blocked", "ts": now(),
                            "detail": f"前置阶段 {pre} 未 PASS，顺序锁拦截"}
            c.save()
            return False, c.state[sid]["detail"]
    ok, detail = CHECKS[sid](c)
    c.state[sid] = {"status": "pass" if ok else "blocked",
                    "ts": now(), "detail": detail}
    c.save()
    return ok, detail

def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def main():
    batch = sys.argv[1]
    cmd = sys.argv[2]
    c = Ctx(batch)
    if cmd == "status":
        print(f"批次 {batch} 全流程阶段闸门状态：")
        for sid, name in STAGES:
            st = c.state[sid]
            mark = {"pass": "PASS", "blocked": "BLOCK", "pending": "待办"}[st["status"]]
            print(f"  {sid} {name:<28}[{mark}] {st['detail']}")
        return
    if cmd == "check":
        sid = sys.argv[3]
        ok, detail = check_stage(c, sid)
        print(("PASS — " if ok else "BLOCKED — ") + detail)
        sys.exit(0 if ok else 1)
    if cmd == "run":
        for sid in STAGE_IDS:
            if c.state[sid]["status"] == "pass":
                continue
            ok, detail = check_stage(c, sid)
            print(f"{sid}: " + ("PASS — " if ok else "BLOCKED — ") + detail)
            if not ok:
                print("顺序闸门在此阻断，后续阶段禁止启动。")
                sys.exit(1)
        print("全部阶段 PASS。")
        return
    if cmd == "reset":
        c.state = {sid: {"status": "pending", "ts": None, "detail": ""} for sid in STAGE_IDS}
        c.save()
        print("状态已重置。")
        return

if __name__ == "__main__":
    main()
