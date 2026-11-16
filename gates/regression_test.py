#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
regression_test.py — 门禁反向回归测试（证明漏洞真的被堵上，不是只改了注释）

覆盖 v3.2.1 三条修复：
  A. 空仓场只填方向、无 no_bet_gate 实跑依据 → S5 必须 BLOCK
  B. 预测未锁定 / 锁定后被改写 → S5、S11 必须 BLOCK
  C. 红队 verdict=agrees 且 0 轮 → S7 必须 BLOCK

做法：把 stage_gate.WS 指到临时目录，造最小批次产物，逐案断言 PASS/BLOCK。
用法：python3 gates/regression_test.py   退出码 0=全部符合预期
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import stage_gate as sg

BATCH = "D99"
DAY = "20260101_D99"


def w(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, ensure_ascii=False)


def setup(root):
    w(os.path.join(root, "g0%s_parsed.json" % BATCH),
      [{"num": "周日001"}, {"num": "周日002"}, {"num": "周日003"}])
    w(os.path.join(root, "%s_config.json" % BATCH), {"legs": [], "min_picks": 1})
    w(os.path.join(root, "%s_no_bet.json" % BATCH), {
        "周日001": {"decision": "NO_BET", "reasons": ["N2 模型最高概率<0.40"], "top_prob": 0.38},
        "周日002": {"decision": "NO_BET", "reasons": ["N3 H/A去水差<0.10均势"], "top_prob": 0.51},
        "周日003": {"decision": "ELIGIBLE", "reasons": [], "top_prob": 0.46},
    })
    w(os.path.join(root, "daily", DAY, "x.md"), {"note": "占位"})


def picks(gate_bad=False, reject_ok=False):
    d = {
        "周日001": {"dir": "A", "gate": "N2 0.38<0.40"},
        "周日002": {"dir": "H", "gate": "" if gate_bad else "N3 均势"},
        "周日003": {"dir": "H", "gate": "ELIGIBLE 但 EV 负"},
    }
    if reject_ok:
        d["周日003"]["reject"] = {"reason": "EV −0.12 负期望", "evidence": "D99_no_bet.json:003"}
    return d


def lock(root, d, sha_override=None):
    items = {k: v for k, v in d.items() if not str(k).startswith("_")}
    d["_meta"] = {"locked_at": "2026-01-01 12:00:00",
                  "predictions_sha256": sha_override or sg.pick_sha(items),
                  "items": len(items), "note": "回归测试"}
    w(os.path.join(root, "%s_final_picks.json" % BATCH), d)


CASES = []


def case(name, expect_pass, stage, mutate):
    CASES.append((name, expect_pass, stage, mutate))


# ---------- S5 三案 ----------
case("S5 空仓场未给 reject/证据 → BLOCK", False, "S5", lambda r: lock(r, picks(reject_ok=False)))
case("S5 空仓场未标 N 码 → BLOCK", False, "S5", lambda r: lock(r, picks(gate_bad=True, reject_ok=True)))
case("S5 合法：N 码齐全 + reject 有据 + 已锁 → PASS", True, "S5",
     lambda r: lock(r, picks(reject_ok=True)))


def no_lock(r):
    w(os.path.join(r, "%s_final_picks.json" % BATCH), picks(reject_ok=True))


def bad_sha(r):
    d = picks(reject_ok=True)
    lock(r, d, sha_override="0" * 64)


def missing_nb(r):
    os.remove(os.path.join(r, "%s_no_bet.json" % BATCH))
    lock(r, picks(reject_ok=True))


def bad_dir(r):
    d = picks(reject_ok=True)
    d["周日001"]["dir"] = ""
    lock(r, d)


case("S5 预测未锁定 → BLOCK", False, "S5", no_lock)
case("S5 锁定后改方向（哈希不符）→ BLOCK", False, "S5", bad_sha)
case("S5 缺 no_bet.json → BLOCK", False, "S5", missing_nb)
case("S5 场次缺方向 → BLOCK", False, "S5", bad_dir)


# ---------- S7 三案 ----------
def s7_fixtures(root, rounds, with_agent=True):
    rec1 = {"num": "001", "rounds": rounds, "verdict": "agrees", "final": "H"}
    if rounds >= 1:
        rec1["round1"] = "红队：主队近5场1胜，主场优势被高估"
    if rounds >= 2:
        rec1["round2"] = "主方：对手头号射手停赛，替代者三问已核，主队xG 1.8"
    d1 = {"items": [rec1]}
    if with_agent:
        d1["agent"] = "sub-red-001"
    d2 = {"_agent": "sub-audit-002", "周日001": {"clash_rounds": rounds, "verdict": "agrees",
                                                 "round1": "审计：流程缺伤停双源", "round2": "主方：已补双源记录"}}
    if rounds < 2:
        d2["周日001"].pop("round2", None)
    w(os.path.join(root, "audit_g1", "clash_record.json"), d1)
    w(os.path.join(root, "audit_g2", "clash_record_g2.json"), d2)
    w(os.path.join(root, "audit_g1", "audit_g1_result.json"), {"ok": True})
    w(os.path.join(root, "audit_g2", "audit_g2_result.json"), {"ok": True})


case("S7 agrees + 0 轮 → BLOCK", False, "S7", lambda r: s7_fixtures(r, 0))
case("S7 agrees + 1 轮 → BLOCK", False, "S7", lambda r: s7_fixtures(r, 1))
case("S7 agrees + 2 轮真对质 → PASS", True, "S7", lambda r: s7_fixtures(r, 2))
case("S7 缺 agent 署名 → BLOCK", False, "S7", lambda r: s7_fixtures(r, 2, with_agent=False))


# ---------- S11 篡改案 ----------
def s11_tamper(root):
    lock(root, picks(reject_ok=True))
    w(os.path.join(root, "daily", DAY, "D99_结算.md"), {"note": "完场 2-1"})
    p = os.path.join(root, "%s_final_picks.json" % BATCH)
    d = json.load(open(p))
    d["周日001"]["dir"] = "H"      # 赛后把方向改成命中
    w(p, d)


case("S11 赛后改方向 → BLOCK", False, "S11", s11_tamper)


# ---------- S4 事实层四案 ----------
def s4_fixtures(root, with_fact=True, cite=True, conflict=False):
    w(os.path.join(root, "%s_config.json" % BATCH), {"legs": ["005"], "min_picks": 1})
    fact_name = "%s_fact_005.json" % BATCH
    inj_name = "%s_injury_005.json" % BATCH
    if with_fact:
        w(os.path.join(root, fact_name), {
            "league": "E0", "home": "A队", "away": "B队",
            "standings": {"A队": {"rank": 3, "points": 20, "gd": 6, "played": 12},
                          "B队": {"rank": 11, "points": 12, "gd": -4, "played": 12}},
            "form": {"A队": [{"res": "W"}], "B队": [{"res": "L"}]},
            "source": "football_data/E0_2627.csv（脚本直出）"})
    w(os.path.join(root, inj_name), {
        "num": "005", "team": "A队", "collected_at": "2026-01-01T10:00",
        "confirmed": ["前锋甲"], "unconfirmed": [],
        "status_conflict": ["后卫乙"] if conflict else []})
    cite_txt = ("引用：%s ，伤停：%s" % (fact_name, inj_name)) if cite else "（未引用任何卡片）"
    body = ("# D99 完整GRC\n\n## 005\n预测首发已核\n关键对位：边路 vs 边卫\n"
            "替代者三问：谁顶替/踢法变/终结点\nC4 与 F7 已填\n裁判：张某，场均 4.2 黄\n" + cite_txt + "\n")
    pth = os.path.join(root, "daily", DAY, "D99_完整GRC.md")
    os.makedirs(os.path.dirname(pth), exist_ok=True)
    with open(pth, "w") as f:
        f.write(body)


def s4_write_grc(root, body):
    pth = os.path.join(root, "daily", DAY, "D99_完整GRC.md")
    with open(pth, "w") as f:
        f.write(body)


case("S4 进票腿无事实卡片 → BLOCK", False, "S4", lambda r: s4_fixtures(r, with_fact=False))
case("S4 GRC 未引用事实卡片 → BLOCK", False, "S4", lambda r: s4_fixtures(r, cite=False))
case("S4 伤停双源状态冲突 → BLOCK", False, "S4", lambda r: s4_fixtures(r, conflict=True))
case("S4 事实卡片+伤停双源+GRC 引用 → PASS", True, "S4", lambda r: s4_fixtures(r))


def main():
    fails = 0
    for name, expect_pass, stage, mutate in CASES:
        root = tempfile.mkdtemp()
        sg.WS = root
        try:
            setup(root)
            mutate(root)
            c = sg.Ctx(BATCH)
            ok, detail = sg.CHECKS[stage](c)
            good = (ok == expect_pass)
            print("%s %-46s got=%s want=%s  %s" % ("OK  " if good else "FAIL", name,
                                                  "PASS" if ok else "BLOCK",
                                                  "PASS" if expect_pass else "BLOCK", detail))
            if not good:
                fails += 1
        finally:
            shutil.rmtree(root, ignore_errors=True)
    print("\n回归用例 %d 条，失败 %d 条" % (len(CASES), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
