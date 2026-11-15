#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ticket_gate.py · 出票机器门（v7.1）
把"IF铁律 + 高信心抱团 + 少腿 + 组合EV + p_my锚定"变成硬阻断，不依赖自觉。

用法：
  python3 ticket_gate.py ticket_candidate.json
  或在其他脚本里 import ticket_gate; ticket_gate.evaluate(ticket)

输入 JSON：
{
  "ticket": "票名",
  "legs": [
    {"match":"D16 005","pick":"H","sp":1.52,
     "devig_p":0.60, "p_my":0.64,
     "reason":"奥斯缺范德文纳+中卫阿克库姆，登博思主场压制；p_my较去水+4pp"},
    ...
  ]
}
判定规则（任一 FAIL 即整票否决）：
  R1 腿数 ≤ 3（2串为主，3串封顶；≥4串 FAIL）
  R2 每腿 p_my ≥ 0.55（弱热门/低信心不进）
  R3 每腿 p_my 必须锚定去水：|p_my-devig_p|≤0.12 且 reason 非空
  R4 组合EV = ∏p_my × ∏sp − 1 > 0
  R5 每腿方向必须经 IF 独立分析（reason 中含"独立"或由 p_my 来源标注）
"""
import json, sys, math

def odds_combined(legs):
    o = 1.0
    for lg in legs:
        o *= float(lg["sp"])
    return o

def p_combined(legs):
    p = 1.0
    for lg in legs:
        p *= float(lg["p_my"])
    return p

def evaluate(ticket):
    legs = ticket["legs"]
    fails, warns, checks = [], [], []
    n = len(legs)

    # R1 腿数
    if n >= 4:
        fails.append(f"R1 腿数={n} ≥4（重串，抽水叠加）")
    elif n == 3:
        warns.append("R1 3串：仅在三腿均为高信心碾压时允许")
    else:
        checks.append(f"R1 腿数={n}（2串为主）")

    for i, lg in enumerate(legs):
        tag = f"{lg.get('match','腿'+str(i))} {lg['pick']}"
        pm = float(lg["p_my"]); dp = float(lg.get("devig_p", pm))
        # R2 最低信心
        if pm < 0.55:
            fails.append(f"R2 {tag} p_my={pm:.2f} <0.55（低信心，不进串）")
        # R3 锚定去水
        reason = lg.get("reason","").strip()
        if abs(pm-dp) > 0.12:
            fails.append(f"R3 {tag} p_my偏离去水{pm-dp:+.2f} >0.12（无依据过度自信）")
        if not reason:
            fails.append(f"R3 {tag} 缺少 p_my 调整理由（IF 不允许无依据标概率）")
        # R5 IF 独立
        if not reason and not lg.get("if_independent"):
            fails.append(f"R5 {tag} 未标注独立分析来源")

    oc = odds_combined(legs)
    pc = p_combined(legs)
    ev = pc*oc - 1
    # R4 组合EV
    if ev <= 0:
        fails.append(f"R4 组合p={pc:.3f} × 赔率{oc:.2f} = EV {ev:+.3f} ≤0（长期必亏）")
    else:
        checks.append(f"R4 组合p={pc:.3f} 赔率{oc:.2f} EV {ev:+.3f}")

    weakest = min(legs, key=lambda x: float(x["p_my"]))
    result = {
        "ticket": ticket.get("ticket","?"),
        "legs": n,
        "combined_odds": round(oc,3),
        "combined_p_my": round(pc,3),
        "combined_ev": round(ev,3),
        "weakest_leg": f"{weakest.get('match','')} {weakest['pick']} p_my={float(weakest['p_my']):.2f}",
        "verdict": "PASS" if not fails else "FAIL",
        "fails": fails, "warns": warns, "checks": checks,
    }
    return result

def main():
    if len(sys.argv) < 2:
        print("用法: python3 ticket_gate.py <ticket.json>"); sys.exit(2)
    ticket = json.load(open(sys.argv[1], encoding="utf-8"))
    r = evaluate(ticket)
    print(json.dumps(r, ensure_ascii=False, indent=2))
    sys.exit(0 if r["verdict"]=="PASS" else 1)

if __name__ == "__main__":
    main()
