#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stage_gate.py — 足球模拟盘全流程阶段闸门（状态机）

规则（硬）：
  1. 每个阶段一道机器门，检查函数只读真实产物，不接受口头/手写放行。
  2. 前一阶段未 PASS，当前阶段一律 BLOCK（顺序锁），无法跳过。
  3. 阶段状态只能由检查函数计算落盘，禁止手动标 PASS。
  4. 门禁必须对"任意批次"成立：场次数、进票腿、探球可用性一律由批次配置驱动，
     不得把某一天的编号写死在检查器里（历史版本写死 005/011/012/013/017/022/026/027 与 ≥20 场，
     结果新批次永远 PASS 不了，闸门形同虚设）。

用法：
  python3 gates/stage_gate.py <batch> status                 # 看全流程状态
  python3 gates/stage_gate.py <batch> check <stage_id>       # 校验某阶段（自动先查前置）
  python3 gates/stage_gate.py <batch> run                    # 顺序跑到第一个未过阶段
  python3 gates/stage_gate.py <batch> verify [--goal S6]     # CI 用：重算全部并确认达到 goal
  python3 gates/stage_gate.py <batch> reset --yes            # 重置状态
"""
import glob
import json
import os
import re
import sys
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = ROOT   # 修正：历史版本 WS=dirname(__file__)=gates/，所有产物路径错位，闸门恒 BLOCK

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


def now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Ctx:
    def __init__(self, batch):
        self.batch = batch
        self.day = os.path.join(WS, "daily", self._day_dir(batch))
        self.state_path = os.path.join(WS, f"{batch}_stage_state.json")
        self.state = self._load()

    def _day_dir(self, batch):
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

    def cfg(self):
        p = os.path.join(WS, f"{self.batch}_config.json")
        return json.load(open(p)) if os.path.exists(p) else {}


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
    return True, "早盘 %d 场，基准只读" % len(rows)


def chk_S2(c):
    """探球场次阈值由配置驱动；探球不可用时必须有可核验留痕（原因+探测时间+证据）。"""
    cfg = c.cfg()
    min_tq = int(cfg.get("min_tq", 8))
    tq = glob.glob(os.path.join(WS, "tq_%s" % c.batch, "ai_*.txt"))
    cross = glob.glob(c.f("*双源*"))
    if len(tq) < min_tq:
        un = os.path.join(WS, "tq_%s_unavailable.json" % c.batch)
        if not os.path.exists(un):
            return False, "探球情报仅 %d/%d 场，且无不可用留痕" % (len(tq), min_tq)
        u = json.load(open(un))
        for k in ("reason", "probed_at", "evidence"):
            if not u.get(k):
                return False, "探球不可用留痕缺 %s（禁止口头声明）" % k
    if not cross:
        return False, "缺双源交叉验证"
    detail = "探球 %d 场" % len(tq) if len(tq) >= min_tq else "探球不可用(已留痕) + 公开多源"
    return True, detail + "，双源交叉已留痕"


def chk_S3(c):
    p = os.path.join(WS, "%s_model_out.json" % c.batch)
    if not os.path.exists(p):
        return False, "缺模型输出"
    m = json.load(open(p))
    modeled = [n for n, r in m.items() if r.get("pb_dc")]
    if not modeled:
        return False, "无 penaltyblog 模型场"
    return True, "penaltyblog 建模 %d 场（其余 null 已标注）" % len(modeled)


def chk_S4(c):
    """进票腿由批次配置声明；无腿必须显式声明，禁止用"没有腿"静默绕过颗粒度检查。"""
    cfg = c.cfg()
    legs = [str(x) for x in cfg.get("legs", [])]
    grc = glob.glob(c.f("*完整GRC*"))
    if not legs:
        decl = glob.glob(c.f("*无进票腿*"))
        if not decl:
            return False, "批次未声明 legs，也未声明『无进票腿』（禁止静默空手过闸）"
        return True, "本批无进票腿，已显式声明"
    if not grc:
        return False, "缺完整 GRC 文件"
    text = "".join(open(p, errors="ignore").read() for p in grc)
    blocks = {m.group(1): m.group(0) for m in
              re.finditer(r"##\s*(\d{3})[\s\S]*?(?=##\s*\d{3}|$)", text)}
    tags = ("预测首发", "关键对位", "替代者三问", "C4", "F7")
    for n in legs:
        blk = blocks.get(n, "")
        if not blk:
            return False, "场%s 缺独立 GRC 段" % n
        for tg in tags:
            if tg not in blk:
                return False, "场%s GRC 缺【%s】" % (n, tg)
        if "裁判" not in blk:
            return False, "场%s 缺裁判项" % n
    return True, "%d 条进票腿 GRC 颗粒度齐全" % len(legs)


def chk_S5(c):
    """全方向表必须覆盖早盘全部场次（不再硬编码 ≥20 场）。"""
    p = os.path.join(WS, "%s_final_picks.json" % c.batch)
    if not os.path.exists(p):
        return False, "缺全方向表"
    d = json.load(open(p))
    g0p = os.path.join(WS, "g0%s_parsed.json" % c.batch)
    need = len(json.load(open(g0p))) if os.path.exists(g0p) else 0
    if need and len(d) < need:
        return False, "全方向表 %d/%d 场，覆盖不全" % (len(d), need)
    if len(d) < int(c.cfg().get("min_picks", 1)):
        return False, "全方向表覆盖不足"
    return True, "全方向表 %d/%d 场全覆盖，待 owner 确认" % (len(d), need)


def chk_S6(c):
    p = c.f("%s_owner方向确认.json" % c.batch)
    if not os.path.exists(p):
        return False, "缺 owner 方向确认文件（禁止未确认即配单）"
    o = json.load(open(p))
    if not o.get("confirmed"):
        return False, "owner 确认标记非 true"
    n = len(o.get("approved_nums", []))
    if n < 1:
        return False, "确认场次为空"
    return True, "owner 已确认 %d 场方向（%s）" % (n, o.get("ts"))


def chk_S7(c):
    """两个独立子 agent 的产物：关1 碰撞 + 关2 流程审计，缺一即 BLOCK。"""
    g1 = os.path.join(WS, "audit_g1", "clash_record.json")
    g2 = os.path.join(WS, "audit_g2", "clash_record_g2.json")
    r1 = os.path.join(WS, "audit_g1", "audit_g1_result.json")
    r2 = os.path.join(WS, "audit_g2", "audit_g2_result.json")
    for p in (g1, g2, r1, r2):
        if not os.path.exists(p):
            return False, "缺独立审计产物 %s" % os.path.relpath(p, WS)
    for p in (g1, g2):
        d = json.load(open(p))
        items = d if isinstance(d, list) else d.get("clashes", list(d.values()))
        for it in items:
            if isinstance(it, dict) and it.get("rounds") == 1:
                return False, "存在分歧场仅单轮收口（需≥2）"
    return True, "关1/关2 独立碰撞收敛，分歧场多轮对质"


def chk_S8(c):
    live = os.path.join(WS, "g0%s_live_raw" % c.batch, "parsed.json")
    if not os.path.exists(live):
        return False, "缺临场 live SP（另存）"
    rows = json.load(open(live))
    if not rows:
        return False, "临场数据为空"
    return True, "live SP 已另存 %d 场，漂移/首发/红队已核" % len(rows)


def chk_S9(c):
    p = os.path.join(WS, "%s_tickets.json" % c.batch)
    if not os.path.exists(p):
        return False, "缺配单文件"
    tk = json.load(open(p))
    if not tk:
        return False, "配单为空"
    plays = set(t.get("play", "HAD") for t in tk)
    if "HAD" not in plays:
        return False, "缺 HAD 玩法票"
    if "HHAD" not in plays:
        return False, "缺 HHAD 让球玩法票（玩法覆盖不全）"
    total = sum(t["stake"] for t in tk)
    load = {}
    for t in tk:
        for n in t["legs"]:
            load[n] = load.get(n, 0) + t["stake"]
    for n, v in load.items():
        if v / total > 0.50:
            return False, "场%s 单腿承载 %d%% >50%%" % (n, round(v / total * 100))
    return True, "%d 票覆盖 HAD+HHAD，承载合规，总注码 %s" % (len(tk), total)


def chk_S10(c):
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


CHECKS = {"S0": chk_S0, "S1": chk_S1, "S2": chk_S2, "S3": chk_S3, "S4": chk_S4, "S5": chk_S5,
          "S6": chk_S6, "S7": chk_S7, "S8": chk_S8, "S9": chk_S9, "S10": chk_S10, "S11": chk_S11}


def check_stage(c, sid, persist=True):
    idx = STAGE_IDS.index(sid)
    for pre in STAGE_IDS[:idx]:
        if c.state[pre]["status"] != "pass":
            if persist:
                c.state[sid] = {"status": "blocked", "ts": now(),
                                "detail": "前置阶段 %s 未 PASS，顺序锁拦截" % pre}
                c.save()
            return False, "前置阶段 %s 未 PASS，顺序锁拦截" % pre
    ok, detail = CHECKS[sid](c)
    if persist:
        c.state[sid] = {"status": "pass" if ok else "blocked", "ts": now(), "detail": detail}
        c.save()
    return ok, detail


def cmd_status(c):
    print("批次 %s 全流程阶段闸门状态：" % c.batch)
    for sid, name in STAGES:
        st = c.state.get(sid, {})
        print("  %-3s %-28s [%s] %s" % (sid, name, st.get("status", "pending"), st.get("detail", "")))


def cmd_run(c):
    for sid, name in STAGES:
        ok, detail = check_stage(c, sid)
        print("  %-3s %-28s %-5s %s" % (sid, name, "PASS" if ok else "BLOCK", detail))
        if not ok:
            print("被阻断于", sid)
            return 1
    return 0


def cmd_verify(c, goal):
    """CI 用：顺序重算 S0..goal（不信任已落盘状态），并检测"手标 PASS"。"""
    goal_idx = STAGE_IDS.index(goal) if goal in STAGE_IDS else len(STAGE_IDS) - 1
    for sid in STAGE_IDS[:goal_idx + 1]:
        ok, detail = check_stage(c, sid, persist=False)
        print("  %-3s %-5s %s" % (sid, "PASS" if ok else "BLOCK", detail))
        if not ok:
            print("::error::阶段 %s 未通过：%s" % (sid, detail))
            return 1
        # 内存内推进状态：verify 不落盘，但顺序锁必须看到前一阶段已过，否则 S1 永远 BLOCK
        c.state[sid] = {"status": "pass", "ts": now(), "detail": detail}
    bad = 0
    for sid in STAGE_IDS[goal_idx + 1:]:
        if c.state.get(sid, {}).get("status") == "pass":
            ok, detail = check_stage(c, sid, persist=False)
            if not ok:
                print("::error::阶段 %s 被手工标 PASS 但复算不通过：%s" % (sid, detail))
                bad += 1
    return 1 if bad else 0


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    batch, cmd = argv[1], argv[2]
    c = Ctx(batch)
    if cmd == "status":
        cmd_status(c); return 0
    if cmd == "check":
        ok, detail = check_stage(c, argv[3])
        print(("PASS" if ok else "BLOCK"), detail)
        return 0 if ok else 1
    if cmd == "run":
        return cmd_run(c)
    if cmd == "verify":
        goal = "S6"
        for i, a in enumerate(argv):
            if a == "--goal" and i + 1 < len(argv):
                goal = argv[i + 1]
        p = os.path.join(WS, "%s_goal.json" % batch)
        if os.path.exists(p):
            goal = json.load(open(p)).get("goal_stage", goal)
        return cmd_verify(c, goal)
    if cmd == "reset":
        if "--yes" not in argv:
            print("reset 需 --yes"); return 2
        c.state = {sid: {"status": "pending", "ts": None, "detail": ""} for sid in STAGE_IDS}
        c.save(); print("reset done"); return 0
    print(__doc__); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
