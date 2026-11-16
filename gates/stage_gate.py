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
import hashlib
import json
import os
import re
import sys
import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = ROOT   # 修正：历史版本 WS=dirname(__file__)=gates/，所有产物路径错位，闸门恒 BLOCK

# ---- v3.2.1 门禁对齐：只豁免规则落地前已合并的历史批次，新批次一律适用 ----
GRANDFATHER_PRED_LOCK = {"D42"}      # D42 合并时无预测锁定机制；其命中率不得计入准确率样本
GRANDFATHER_S5_EVIDENCE = {"D42"}    # D42 合并时无「空仓场须有闸门实跑依据」要求


def pick_sha(items):
    """预测内容指纹：只算比赛条目，不含 _meta，排序后序列化，避免写法差异导致假阳性。"""
    raw = json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()

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
    """不再只做字符串匹配：必须有可复算的 gap JSON，且断档时必须有补救说明。"""
    p = c.f(f"{c.batch}_M0_数据健康检查.md")
    if not os.path.exists(p):
        return False, "缺 M0 数据健康检查文件"
    t = open(p, errors="ignore").read()
    if "PASS" not in t and "断档" not in t:
        return False, "M0 未标 PASS/断档"
    gj = os.path.join(WS, f"{c.batch}_g1_gap.json")
    if not os.path.exists(gj):
        return False, "缺 %s_g1_gap.json（gap 必须脚本实算，禁止手写 PASS）" % c.batch
    try:
        o = json.load(open(gj))
    except Exception as e:
        return False, "gap JSON 解析失败: %s" % e
    if not isinstance(o.get("gap_days"), int):
        return False, "gap_days 必须为整数（脚本计算）"
    if o["gap_days"] > o.get("threshold_days", 21):
        if not o.get("gap_triggered"):
            return False, "gap 超阈值但未标 gap_triggered"
        rem = o.get("remedy") or {}
        if not (rem.get("source") and rem.get("artifact")):
            return False, "断档但无补救来源/产物（应标 null 或不预测）"
    return True, "M0 PASS（gap_days=%d，可复算）" % o["gap_days"]


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


def _find_card(c, num, kind):
    """事实卡片/伤停结果定位：兼容 005 与 周日005 两种编号写法。"""
    for cand in ("%s_%s_%s.json" % (c.batch, kind, num),
                 "%s_%s_%s.json" % (c.batch, kind, str(num)[-3:])):
        p = os.path.join(WS, cand)
        if os.path.exists(p):
            return p
    return None


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

        # 事实层：数字必须脚本直出并被 GRC 引用，禁止手写（治"具体但错"）
        fact = _find_card(c, n, "fact")
        if not fact:
            return False, "场%s 缺事实卡片 %s_fact_%s.json（数字不得手写）" % (n, c.batch, n)
        card = json.load(open(fact))
        for k in ("league", "standings", "form", "source"):
            if not card.get(k):
                return False, "场%s 事实卡片缺字段 %s" % (n, k)
        st, teams = card.get("standings") or {}, (card.get("home"), card.get("away"))
        if len([t for t in teams if t and st.get(t)]) < 2:
            return False, "场%s 事实卡片缺双方积分榜数据" % n
        if os.path.basename(fact) not in blk:
            return False, "场%s GRC 未引用事实卡片 %s" % (n, os.path.basename(fact))

        # 伤停层：必须双源比对结果，冲突未解决不许进票
        inj = _find_card(c, n, "injury")
        if not inj:
            return False, "场%s 缺伤停双源结果 %s_injury_%s.json" % (n, c.batch, n)
        ij = json.load(open(inj))
        if not ij.get("collected_at"):
            return False, "场%s 伤停核查缺 collected_at（防过期信息当现状）" % n
        if not isinstance(ij.get("confirmed"), list) or not isinstance(ij.get("unconfirmed"), list):
            return False, "场%s 伤停核查须分 confirmed/unconfirmed 两名单" % n
        if ij.get("status_conflict"):
            return False, "场%s 伤停双源状态冲突未解决：%s" % (n, ij["status_conflict"])
        if os.path.basename(inj) not in blk:
            return False, "场%s GRC 未引用伤停核查结果 %s" % (n, os.path.basename(inj))
    return True, "%d 条进票腿 GRC 颗粒度齐全，事实卡片与伤停双源已引用" % len(legs)


def chk_S5(c):
    """全方向表：覆盖全部早盘场 + 每场有方向 + 空仓场有闸门实跑依据 + 预测已锁定。

    为什么要这三条：只数条目＝方向成了免检品（空仓场不分析也能填）；不锁预测＝
    命中率可事后改写；空仓场无实跑依据＝口头空仓。三者都会让"准确率"变成不可核数字。
    """
    p = os.path.join(WS, "%s_final_picks.json" % c.batch)
    if not os.path.exists(p):
        return False, "缺全方向表"
    d = json.load(open(p))
    items = {k: v for k, v in d.items() if not str(k).startswith("_")}
    g0p = os.path.join(WS, "g0%s_parsed.json" % c.batch)
    need = len(json.load(open(g0p))) if os.path.exists(g0p) else 0
    if need and len(items) < need:
        return False, "全方向表 %d/%d 场，覆盖不全" % (len(items), need)
    if len(items) < int(c.cfg().get("min_picks", 1)):
        return False, "全方向表覆盖不足"

    for n, rec in items.items():
        # 方向必须是规范字母开头；允许后缀标注（如 "H（让-4）"），但 "未知/待定" 一律拦
        if not re.match(r"^[HDA](?:$|[（(\s：:\-])", str(rec.get("dir", "")).strip().upper()):
            return False, "场%s 方向不合法（dir 须以 H/D/A 开头，实际 %r）" % (n, rec.get("dir"))

    # 非进票腿必须有 no_bet_gate 实跑依据：NO_BET 带 N 码，或 ELIGIBLE 但写清否决理由+证据
    if c.batch not in GRANDFATHER_S5_EVIDENCE:
        legs = [str(x) for x in c.cfg().get("legs", [])]
        nb_path = os.path.join(WS, "%s_no_bet.json" % c.batch)
        for n, rec in items.items():
            if n in legs:
                continue
            if not os.path.exists(nb_path):
                return False, "场%s 非进票腿，但缺 %s_no_bet.json（须 no_bet_gate 实跑）" % (n, c.batch)
            nb = json.load(open(nb_path))
            key = n if n in nb else (n[-3:] if n[-3:] in nb else None)
            if key is None:
                return False, "场%s 无 no_bet_gate 判定记录" % n
            e = nb[key] if isinstance(nb[key], dict) else {}
            dec = str(e.get("decision", ""))
            if dec == "NO_BET":
                if not e.get("reasons"):
                    return False, "场%s NO_BET 但无 N 规则理由" % n
                code = str(e["reasons"][0]).split()[0]
                if code not in str(rec.get("gate") or ""):
                    return False, "场%s 方向表未标触发规则 %s（空仓须写清是哪条 N）" % (n, code)
            elif dec == "ELIGIBLE":
                rej = rec.get("reject") or {}
                if not (rej.get("reason") and rej.get("evidence")):
                    return False, "场%s 闸门判 ELIGIBLE 却未进票，必须给 reject.reason + evidence（防空口否决）" % n
            else:
                return False, "场%s no_bet 判定异常：%r" % (n, dec)

    # 预测锁定：先锁方向再开赛，命中率才有分母
    if c.batch not in GRANDFATHER_PRED_LOCK:
        meta = d.get("_meta") or {}
        if not meta.get("locked_at"):
            return False, "缺 _meta.locked_at（预测未锁定，命中率不可核）"
        if not meta.get("predictions_sha256"):
            return False, "缺 _meta.predictions_sha256"
        if meta["predictions_sha256"] != pick_sha(items):
            return False, "预测内容与锁定哈希不符（锁定后被改写，须走 errata）"

    exempt = []
    if c.batch in GRANDFATHER_PRED_LOCK:
        exempt.append("无预测锁定")
    if c.batch in GRANDFATHER_S5_EVIDENCE:
        exempt.append("无空仓依据校验")
    tail = "（历史批次豁免：%s）" % "、".join(exempt) if exempt else "，空仓场有闸门依据，预测已锁"
    return True, "全方向表 %d/%d 场全覆盖%s" % (len(items), need, tail)


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


def _s7_items(d):
    """归一化逐项记录：兼容 list、{items/clashes/records: [...]}、{编号: 记录} 三种形态。"""
    if isinstance(d, list):
        return d, ""
    agent = str(d.get("agent") or d.get("_agent") or "")
    for k in ("items", "clashes", "records"):
        if isinstance(d.get(k), list) and d[k]:
            return d[k], agent
    cand = [v for k, v in d.items() if not str(k).startswith("_") and isinstance(v, dict)]
    return cand, agent


def chk_S7(c):
    """关1 碰撞 + 关2 流程审计：两份独立产物、每条都真对质。

    v3.2.1：取消 verdict="agrees" 的轮次豁免——只要写 agrees 就能 0 轮过闸，
    「红队无条件对立」会被形式满足。现在任何条目都须 rounds>=2 且有 round1/round2 记录。
    """
    g1 = os.path.join(WS, "audit_g1", "clash_record.json")
    g2 = os.path.join(WS, "audit_g2", "clash_record_g2.json")
    r1 = os.path.join(WS, "audit_g1", "audit_g1_result.json")
    r2 = os.path.join(WS, "audit_g2", "audit_g2_result.json")
    for p in (g1, g2, r1, r2):
        if not os.path.exists(p):
            return False, "缺独立审计产物 %s" % os.path.relpath(p, WS)
    for p in (g1, g2):
        d = json.load(open(p))
        items, agent = _s7_items(d)
        if not items:
            return False, "%s 无逐项记录（items/clashes/records 或编号键，不能为空）" % os.path.relpath(p, WS)
        if not agent:
            return False, "%s 缺 agent/_agent 署名（无法证明来自独立 agent）" % os.path.relpath(p, WS)
        for it in items:
            if not isinstance(it, dict):
                return False, "%s 存在非对象条目" % os.path.relpath(p, WS)
            label = it.get("num") or it.get("id") or "?"
            rounds = int(it.get("rounds") or it.get("clash_rounds") or 0)
            rkeys = [k for k in it if re.fullmatch(r"round\d+", str(k))]
            if rounds < 2:
                return False, "场%s 对质 %d 轮 <2（agrees 也须真对抗两轮）" % (label, rounds)
            if len(rkeys) < 2:
                return False, "场%s 实际只落 %d 轮记录（需 round1/round2）" % (label, len(rkeys))
    return True, "关1/关2 独立产物齐全，逐场 ≥2 轮真对质"


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
    # 复盘前再验一次预测指纹：赛后改方向＝把命中率做成可调数字
    if c.batch not in GRANDFATHER_PRED_LOCK:
        p = os.path.join(WS, "%s_final_picks.json" % c.batch)
        d = json.load(open(p))
        items = {k: v for k, v in d.items() if not str(k).startswith("_")}
        meta = d.get("_meta") or {}
        if meta.get("predictions_sha256") != pick_sha(items):
            return False, "预测在锁定后被改写（哈希不符），本批命中率作废、须走 errata"
    return True, "结算/归因/台账已回填，预测指纹未变"


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
