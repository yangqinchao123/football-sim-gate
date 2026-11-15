#!/usr/bin/env python3
"""
pre_ticket_gate.py — 出票/锁定前强制完整性闸门（v2.2）

用法:
  python3 pre_ticket_gate.py d35 daily/20261107_D35            # 出票前检查
  python3 pre_ticket_gate.py d35 daily/20261107_D35 --with-live # 含 M9/LOCKED 临场检查

v2.2 变化（堵 D35 形式合规漏洞）：
  - 新增结构化证据文件 {batch}_grc_evidence.json，机器校验实质内容
  - 平局方向（D）必须命中 4 条硬证据里的 ≥2 条，否则 BLOCK
  - 市场平局概率由本脚本独立从官方 SP 计算，不采信人工填值
  - M0 断档场（>21 天）必须有 recent_manual_model（近期手动模型），否则 BLOCK
  - 第三方媒体/网站比分预测一律不算硬证据，detail 含"媒体/预测"字样判无效
  - GRC markdown 里手写的"F 放行"不被采信
只读检查，不修改任何文件。
"""
import sys, os, json, glob
from collections import Counter

WS = "/home/user/.doubao/agent_mode/workspace"
BANNED_WORDS = ("媒体", "预测", "网站", "独立模型", "tipster", "forecast", "prediction")

def main():
    if len(sys.argv) < 3:
        print("用法: pre_ticket_gate.py <批次> <业务日目录> [--with-live]")
        sys.exit(2)
    batch, daydir = sys.argv[1], sys.argv[2]
    with_live = "--with-live" in sys.argv
    day = os.path.join(WS, daydir)
    fails, warns = [], []

    def need(cond, msg):
        if not cond: fails.append(msg)

    # 1. 早盘基准
    g0_path = os.path.join(WS, f"g0{batch}_parsed.json")
    need(os.path.exists(g0_path), f"缺早盘基准 g0{batch}_parsed.json")
    g = {m["num"][-3:]: m for m in json.load(open(g0_path))} if os.path.exists(g0_path) else {}

    # 2. v2.1 M0 断档记录
    m0 = glob.glob(os.path.join(day, "*M0*"))
    need(bool(m0), "缺 M0 数据健康检查")
    if m0:
        txt = open(m0[0], encoding="utf-8", errors="ignore").read()
        need("断档" in txt, "M0 未记录断档结论")

    # 3. 模型脚本必须调用 penaltyblog + 输出存在
    mscripts = glob.glob(os.path.join(WS, f"{batch}_model*.py"))
    pb = any("penaltyblog" in open(p, errors="ignore").read() for p in mscripts)
    need(bool(mscripts), "缺模型脚本")
    need(pb, "模型未调用 penaltyblog")
    need(os.path.exists(os.path.join(WS, f"{batch}_model_out.json")), "缺模型输出")

    # 4. GRC markdown 覆盖全部场次（仅作留痕，放行结论不采信它）
    grc = glob.glob(os.path.join(day, "*GRC*"))
    need(len(grc) >= 3, "GRC 文件不足")
    grc_text = "".join(open(p, errors="ignore").read() for p in grc)
    all_nums = ("003","004","005","006","008","009","010","011","012",
                "013","014","015","016","019","020","022","023","024","025","026","027","028")
    missing_grc = [n for n in all_nums if n not in grc_text]
    need(not missing_grc, f"GRC 未覆盖: {missing_grc}")

    # 4.5 v2.5 GRC 颗粒度机器校验（堵"结构齐全但关键项缺失"漏洞，针对实际进票腿）
    ticket_legs_grc = ('005','011','012','013','017','022','026','027')
    import re as _re
    # 按 "## NNN" 标题切分各场段落
    blocks = {}
    for m in _re.finditer(r'##\s*(\d{3})[\s\S]*?(?=##\s*\d{3}|$)', grc_text):
        blocks[m.group(1)] = m.group(0)
    required_tags = [
        ("预测首发", "完整预测首发11人"),
        ("关键对位", "3-5个关键对位"),
        ("替代者三问", "伤停替代者三问"),
        ("C4", "C4追问重开"),
        ("F7", "F7平局门槛"),
    ]
    for n in ticket_legs_grc:
        blk = blocks.get(n, "")
        need(bool(blk), f"场{n} 缺独立 GRC 段落")
        for tag, desc in required_tags:
            need(tag in blk, f"场{n} GRC 缺【{desc}】")
        # 裁判项：必须出现"裁判"，且明确写名或诚实标 null
        need("裁判" in blk, f"场{n} GRC 缺裁判项")
        if "裁判" in blk:
            ref_seg = blk.split("裁判", 1)[1][:40]
            need(("null" in ref_seg) or any('一' <= ch <= '鿿' for ch in ref_seg) or _re.search(r'[A-Za-z]{3,}', ref_seg) is not None,
                 f"场{n} 裁判项既未写名也未标null")

    # 5. 最终方向 + 红队
    need(bool(glob.glob(os.path.join(day, "*最终方向*"))), "缺最终方向决策")
    need(bool(glob.glob(os.path.join(day, "*红队*"))), "缺红队交叉裁决")

    # ===== 6. v2.2 结构化证据实质校验（核心）=====
    ev_path = os.path.join(WS, f"{batch}_grc_evidence.json")
    need(os.path.exists(ev_path), f"缺结构化证据文件 {batch}_grc_evidence.json（v2.2 必需）")
    if os.path.exists(ev_path):
        evidence = json.load(open(ev_path))
        ev_map = {e["num"]: e for e in evidence}
        need(set(ev_map) >= set(all_nums), f"证据未覆盖: {sorted(set(all_nums)-set(ev_map))}")

        for n in all_nums:
            e = ev_map.get(n)
            if not e: continue
            gap = e.get("data_gap_days", 0)
            direction = e.get("direction")

            # 6a. 断档场必须有近期手动模型
            if gap > 21:
                rmm = e.get("recent_manual_model")
                need(bool(rmm) and rmm.get("lambda") and rmm.get("hda"),
                     f"场{n} 断档{gap}天但缺 recent_manual_model，M0 未闭环，禁止进票")

            # 6b. 平局方向：4 条硬证据机器校验
            if direction == "D":
                de = e.get("draw_evidence", {})
                had = g.get(n, {}).get("had")
                mkt_pd = had["fair"]["d"] if had and had.get("fair") else None
                need(mkt_pd is not None, f"场{n} 无官方HAD，无法独立校验市场平局概率")

                hits = 0
                # ① 近期 λ 双低
                if de.get("low_lambda", {}).get("hit") and rmm_check_below(e, 1.1):
                    hits += 1
                # ② 守平结构（detail 具体且不含禁用词）
                ds = de.get("defensive_structure", {})
                detail = str(ds.get("detail", ""))
                if ds.get("hit") and detail and not any(w in detail.lower() for w in BANNED_WORDS):
                    hits += 1
                elif ds.get("hit") and any(w in detail.lower() for w in BANNED_WORDS):
                    fails.append(f"场{n} 守平结构引用媒体/预测，不算硬证据")
                # ③ 市场去水平局 ≥25%（脚本独立值）
                if mkt_pd is not None and mkt_pd >= 0.25:
                    hits += 1
                # ④ 近5次交锋平局率 ≥40%（样本≥5）
                h2h = de.get("h2h_draw_pct", {})
                if h2h.get("hit") and h2h.get("sample", 0) >= 5 and h2h.get("value", 0) >= 0.40:
                    hits += 1

                need(hits >= 2, f"场{n} 判平但硬证据仅 {hits} 条（需≥2），禁止主选平")

    # 7. 配单纪律真实计算
    tp = os.path.join(WS, f"{batch}_tickets.json")
    pp = os.path.join(WS, f"{batch}_final_picks.json")
    if os.path.exists(tp) and os.path.exists(pp):
        tickets = json.load(open(tp)); picks = json.load(open(pp))
        total = sum(t["stake"] for t in tickets)
        load = Counter()
        for t in tickets:
            leagues = [picks[n][2] for n in t["legs"]]
            need(len(set(leagues)) == len(leagues), f'{t["name"]} 同联赛配对')
            for n in t["legs"]: load[n] += t["stake"]
        for n, c in load.items():
            need(c / total <= 0.50, f"场{n} 单腿承载 {c/total:.0%} >50%")
            if float(picks[n][1]) > 3.5:
                need(c / total <= 0.30, f"高风险场{n} 承载 {c/total:.0%} >30%")
        balance = 4347.80  # D35 期末真实余额（修正原硬编码 4897.80）
        need(total / balance <= 0.40, f"总注码占余额 {total/balance:.0%} >40%")
        warns.append(f"配单 {len(tickets)} 张，总注码 {total}，占余额 {total/balance:.0%}")
    else:
        fails.append("缺结构化配单/玩法文件")

    # ===== 7.5 v2.4 双独立子 agent 三道关强制校验（堵"独立审核机器不拦"漏洞）=====
    ticket_legs = ('005','011','012','013','017','022','023','026','027')
    def load_audit(subdir, fname):
        p = os.path.join(WS, subdir, fname)
        if not os.path.exists(p):
            fails.append(f"缺独立审核产物 {subdir}/{fname}（子 agent 未完成或未隔离）")
            return None
        return json.load(open(p))
    a1 = load_audit("audit_g1", "audit_g1_result.json")
    a2 = load_audit("audit_g2", "audit_g2_result.json")
    def normalize_audit(a):
        # 统一转成 {编号: 记录}，兼容 list / 含legs的dict / 编号为键的dict
        if isinstance(a, list):
            items = a
        elif isinstance(a, dict) and "legs" in a:
            items = a["legs"]
        else:
            return {k: v for k, v in a.items() if isinstance(v, dict)}
        out = {}
        for x in items:
            num = x.get("num") or x.get("id")
            out[num] = x
        return out
    def get_dir(rec):
        return rec.get("direction") or rec.get("final_direction")
    # 校验两份独立产物覆盖全部进票腿且各有唯一方向
    for tag, a in (("关1", a1), ("关2", a2)):
        if not a: continue
        amap = normalize_audit(a)
        miss = [n for n in ticket_legs if n not in amap or not get_dir(amap[n])]
        need(not miss, f"{tag}独立审核未覆盖/缺方向: {miss}")
    # 分歧场必须有 ≥2 轮当庭对质记录
    clash_path = os.path.join(WS, "audit_g1", "clash_record.json")
    if a1 and os.path.exists(os.path.join(WS, "d36_final_picks.json")):
        picks = json.load(open(os.path.join(WS, "d36_final_picks.json")))
        amap = normalize_audit(a1)
        clash = json.load(open(clash_path)) if os.path.exists(clash_path) else {}
        for n in ticket_legs:
            if n in amap and get_dir(amap[n]) != picks[n][0]:
                rounds = clash.get(n, {}).get("clash_rounds", 0)
                need(rounds >= 2, f"场{n} 主结论与关1分歧但对质轮次 {rounds} <2，禁止单轮收口")

    # 8. 临场（仅 --with-live）
    if with_live:
        need(bool(glob.glob(os.path.join(day, "*M9*")) or glob.glob(os.path.join(day, "*临场*"))),
             "缺 M9 临场核查")
        need(bool(glob.glob(os.path.join(day, "*LOCKED*"))), "缺 LOCKED 锁定单")

    if warns:
        print("WARN:")
        for w in warns: print("  -", w)
    if fails:
        print("GATE BLOCKED — 禁止出票/锁定:")
        for f in fails: print("  [FAIL]", f)
        sys.exit(1)
    print("GATE PASS — v2.2 实质硬门全部通过" + ("（含临场）" if with_live else "（临场项待 M9/LOCKED）"))
    sys.exit(0)


def rmm_check_below(e, threshold):
    rmm = e.get("recent_manual_model") or {}
    lam = rmm.get("lambda")
    return bool(lam) and len(lam) == 2 and lam[0] < threshold and lam[1] < threshold


if __name__ == "__main__":
    main()
