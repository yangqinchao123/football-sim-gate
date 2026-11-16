# -*- coding: utf-8 -*-
"""
空仓判定闸 no_bet_gate（2026-11-09 新增）
- 目的：把"模型在均势/爆冷/断档场没优势就该空仓"固化为机器可校验规则，
  防止为了"每场必给方向"而硬选、硬进票。
- 注意分层：GRC 预测层每场仍必给方向（研究不能停）；本闸只管【出票层进不进票】。
- 触发任一条件即该场空仓（不进票），并写明触发项：
  N1 数据断档且无可靠近期手动模型
  N2 模型最高概率 < 0.45（无明显优势；0.45 有 2627 样本外支撑）
  N3 H/A 去水概率差 < 0.14（均势）且无硬证据打破
  N4 纯泊松与 DC 首选方向不一致（模型内部分裂）
  N5 碰撞记录 final 判定"不进票"
用法: python3 no_bet_gate.py <batch>
"""
import json, os, sys, glob, re

# 产物在仓库根，不在 gates/ 内（此前指向 gates/ 导致根目录跑必 FileNotFoundError，闸门从未真正执行）
_HERE = os.path.dirname(os.path.abspath(__file__))
WS = os.environ.get("GATE_WS") or (
    os.path.dirname(_HERE) if os.path.basename(_HERE) == "gates" else _HERE
)  # 同一份脚本可在 仓库根/gates 与 工作目录根 两处运行，判定口径不分叉

def main(batch):
    model = json.load(open(os.path.join(WS, f"{batch}_model_out.json")))
    g0 = json.load(open(os.path.join(WS, f"g0{batch}_parsed.json")))
    def _numkey(n):
        m = re.search(r"(\d{3})$", str(n))
        return m.group(1) if m else str(n)

    fair = {_numkey(r["num"]): r["had"].get("fair") for r in g0 if r.get("had")}

    # 碰撞 final 不进票的场
    no_bet_clash = set()
    for cf in glob.glob(os.path.join(WS, "audit_*", "*clash*.json")):
        try:
            d = json.load(open(cf))
            for n, rec in d.items():
                if "不进票" in str(rec.get("final", "")):
                    no_bet_clash.add(n)
        except Exception:
            continue

    # 断档且无近期手动模型
    day = glob.glob(os.path.join(WS, "daily", f"*_{batch}"))
    gap_block = set()
    if day:
        for f in glob.glob(os.path.join(day[0], "*M0*")):
            txt = open(f, errors="ignore").read()
            if "断档" in txt and "PASS" not in txt:
                gap_block.add("ALL")
    manual = set()
    for f in glob.glob(os.path.join(WS, "**", "*recent_manual*"), recursive=True):
        manual.add(os.path.basename(f))

    result = {}
    for num, rec in model.items():
        reasons = []
        if not rec.get("pb_dc"):
            result[num] = {"decision": "NO_BET", "reasons": ["无模型数据(null)"]}
            continue
        dc = rec["pb_dc"]; poi = rec.get("pb_poisson")
        names = ["H", "D", "A"]
        pmax = max(dc)
        if pmax < 0.45:
            reasons.append("N2 模型最高概率<0.45")
        if poi:
            if names[max(range(3), key=lambda i: poi[i])] != names[max(range(3), key=lambda i: dc[i])]:
                reasons.append("N4 泊松与DC首选分裂")
        # N4b 三模型分裂：手动近期形态 / 红队模型与 pb_dc 首选方向不一致即记分裂
        # （框架『三模型分裂均不进票』一直没进脚本，是规则盲区，D42 审计后被补上）
        dcmax = names[max(range(3), key=lambda i: dc[i])]
        for key, lab in (("manual_recent_form", "手动近期形态"), ("red_dc", "红队DC")):
            alt = rec.get(key)
            if alt and len(alt) == 3:
                if names[max(range(3), key=lambda i: alt[i])] != dcmax:
                    reasons.append("N4b %s与pb_dc首选分裂(%s vs %s)" % (lab, names[max(range(3), key=lambda i: alt[i])], dcmax))
        f = fair.get(_numkey(num))
        if f:
            diff = abs(f["h"] - f["a"])
            if diff < 0.14:
                reasons.append("N3 H/A去水差<0.14均势")
        if num in no_bet_clash:
            reasons.append("N5 碰撞判定不进票")
        if "ALL" in gap_block and not manual:
            reasons.append("N1 数据断档无近期手动模型")
        result[num] = {"decision": "NO_BET" if reasons else "ELIGIBLE",
                       "reasons": reasons, "top_prob": round(pmax, 3)}

    bets = [n for n, v in result.items() if v["decision"] == "ELIGIBLE"]
    no = {n: v["reasons"] for n, v in result.items() if v["decision"] == "NO_BET"}
    print(f"批次 {batch}：可进票 {len(bets)} 场，空仓 {len(no)} 场")
    print("可进票:", sorted(bets, key=lambda x: int(_numkey(x))))
    for n, r in sorted(no.items(), key=lambda x: int(_numkey(x[0]))):
        print(f"  空仓 {n}: {r}")
    json.dump(result, open(os.path.join(WS, f"{batch}_no_bet.json"), "w"), ensure_ascii=False, indent=1)

if __name__ == "__main__":
    main(sys.argv[1])
