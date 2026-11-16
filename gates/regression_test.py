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


# ---------- S7 独立性 ----------
def s7_fixtures(root, rounds, with_agent=True, same_producer=False, same_run=False,
                share_input=False, same_probs=False):
    rec1 = {"num": "001", "rounds": rounds, "verdict": "agrees", "final": "H",
            "probs": [0.42, 0.28, 0.30]}
    if rounds >= 1:
        rec1["round1"] = "红队：主队近5场1胜，主场优势被高估"
    if rounds >= 2:
        rec1["round2"] = "主方：对手头号射手停赛，替代者三问已核，主队xG 1.8"
    d1 = {"items": [rec1],
          "provenance": {"producer": "sub-red-001" if not same_producer else "sub-x",
                         "run_id": "run-A" if not same_run else "run-X",
                         "started_at": "2026-01-01T09:00", "finished_at": "2026-01-01T09:12",
                         "output_sha256": "o1"},
          "independence": {"inputs": [{"name": "g0D99_parsed.json", "sha256": "h1"},
                                      {"name": "D99_model_out.json", "sha256": "h2"}]}}
    if with_agent:
        d1["agent"] = "sub-red-001"
    d2 = {"_agent": "sub-audit-002",
          "provenance": {"producer": "sub-audit-002" if not same_producer else "sub-x",
                         "run_id": "run-B" if not same_run else "run-X",
                         "started_at": "2026-01-01T09:20", "finished_at": "2026-01-01T09:31",
                         "output_sha256": "o2"},
          "independence": {"inputs": [{"name": "g0D99_parsed.json", "sha256": "h1"},
                                      {"name": "clash_record.json", "sha256": "o1" if share_input else "h3"}]},
          "周日001": {"clash_rounds": rounds, "verdict": "agrees",
                      "round1": "审计：流程缺伤停双源", "round2": "主方：已补双源记录",
                      "probs": [0.42, 0.28, 0.30] if same_probs else [0.38, 0.30, 0.32]}}
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
case("S7 两份产物 producer 相同 → BLOCK", False, "S7", lambda r: s7_fixtures(r, 2, same_producer=True))
case("S7 两份产物 run_id 相同 → BLOCK", False, "S7", lambda r: s7_fixtures(r, 2, same_run=True))
case("S7 一方输入含另一方产物哈希 → BLOCK", False, "S7", lambda r: s7_fixtures(r, 2, share_input=True))
case("S7 两份概率逐场相同（复制）→ BLOCK", False, "S7", lambda r: s7_fixtures(r, 2, same_probs=True))


# ---------- S11 篡改案 ----------
def s11_tamper(root):
    lock(root, picks(reject_ok=True))
    w(os.path.join(root, "daily", DAY, "D99_结算.md"), {"note": "完场 2-1"})
    p = os.path.join(root, "%s_final_picks.json" % BATCH)
    d = json.load(open(p))
    d["周日001"]["dir"] = "H"      # 赛后把方向改成命中
    w(p, d)


case("S11 赛后改方向 → BLOCK", False, "S11", s11_tamper)


# ---------- S4 事实层 ----------
MATCHES = [("A队", "B队", 2, 1), ("C队", "D队", 1, 1), ("E队", "F队", 0, 3),
           ("G队", "H队", 2, 2), ("I队", "J队", 1, 0),
           ("B队", "A队", 1, 1), ("D队", "C队", 2, 0), ("F队", "E队", 1, 1),
           ("H队", "G队", 0, 1), ("J队", "I队", 3, 2)]


def synth_table(matches):
    """按比赛结果生成积分榜：胜=负、进球=失球由构造保证，便于测试不变量。"""
    tab = {}
    for h, a, gh, ga in matches:
        for t, gf, gc in ((h, gh, ga), (a, ga, gh)):
            p = tab.setdefault(t, {"team": t, "played": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0})
            p["played"] += 1
            p["w"] += 1 if gf > gc else 0
            p["d"] += 1 if gf == gc else 0
            p["l"] += 1 if gf < gc else 0
            p["gf"] += gf
            p["ga"] += gc
    rows = []
    for p in tab.values():
        p["points"] = 3 * p["w"] + p["d"]
        p["gd"] = p["gf"] - p["ga"]
        rows.append(p)
    rows.sort(key=lambda x: (-x["points"], -x["gd"]))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return rows


def s4_fixtures(root, with_fact=True, cite=True, conflict=False, mutate=None):
    w(os.path.join(root, "%s_config.json" % BATCH), {"legs": ["005"], "min_picks": 1})
    fact_name = "%s_fact_005.json" % BATCH
    inj_name = "%s_injury_005.json" % BATCH
    if with_fact:
        table = synth_table(MATCHES)
        by = {r["team"]: r for r in table}
        card = {
            "league": "E0", "home": "A队", "away": "B队", "table": table,
            "snapshot_at": "2026-01-01", "source_rows": 10, "generated_by": "lib/fact_sheet.py",
            "standings": {t: {k: by[t][k] for k in ("rank", "points", "gd", "played", "w", "d", "l", "gf", "ga")}
                          for t in ("A队", "B队")},
            "form": {"A队": [{"res": "H"}, {"res": "D"}], "B队": [{"res": "A"}, {"res": "H"}]},
            "source": "football_data/E0_2627.csv（脚本直出）"}
        if mutate:
            mutate(card)
        w(os.path.join(root, fact_name), card)
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


def _tamper_points(card):
    card["table"][0]["points"] += 1


def _tamper_goals(card):
    card["table"][0]["gf"] += 1


def _drop_table(card):
    card.pop("table")


def _drop_snapshot(card):
    card.pop("snapshot_at")


def _handwritten(card):
    card["generated_by"] = "手写"


case("S4 进票腿无事实卡片 → BLOCK", False, "S4", lambda r: s4_fixtures(r, with_fact=False))
case("S4 GRC 未引用事实卡片 → BLOCK", False, "S4", lambda r: s4_fixtures(r, cite=False))
case("S4 伤停双源状态冲突 → BLOCK", False, "S4", lambda r: s4_fixtures(r, conflict=True))
case("S4 事实卡片+伤停双源+GRC 引用 → PASS", True, "S4", lambda r: s4_fixtures(r))
case("S4 卡片积分被改（3胜+平对不上）→ BLOCK", False, "S4",
     lambda r: s4_fixtures(r, mutate=_tamper_points))
case("S4 卡片进球被改（Σ进≠Σ失）→ BLOCK", False, "S4", lambda r: s4_fixtures(r, mutate=_tamper_goals))
case("S4 卡片只给两行、不入整表 → BLOCK", False, "S4", lambda r: s4_fixtures(r, mutate=_drop_table))
case("S4 卡片缺 snapshot_at → BLOCK", False, "S4", lambda r: s4_fixtures(r, mutate=_drop_snapshot))
case("S4 手写卡片（generated_by 非脚本）→ BLOCK", False, "S4", lambda r: s4_fixtures(r, mutate=_handwritten))


# ---------- S6 改序三案 ----------
def s6_fixtures(root, dir_="A", override=None, blend_dir=None, model_dir_a=False):
    w(os.path.join(root, "%s_model_out.json" % BATCH), {
        "周日001": {"pb_dc": ([0.28, 0.30, 0.42] if model_dir_a else [0.52, 0.27, 0.21])},
        "周日002": {"pb_dc": [0.45, 0.30, 0.25]},
        "周日003": {"pb_dc": [0.46, 0.28, 0.26]}})
    o = {"batch": BATCH, "confirmed": True, "approved_nums": ["周日001"],
         "directions": {"周日001": {"dir": dir_}}, "ts": "2026-01-01 12:00"}
    if override:
        o["overrides"] = {"周日001": override}
    w(os.path.join(root, "daily", DAY, "%s_owner方向确认.json" % BATCH), o)
    if blend_dir:
        w(os.path.join(root, "%s_blend.json" % BATCH),
          {"_meta": {"w_model": 0.3}, "周日001": {"p": [0.3, 0.3, 0.4], "dir": blend_dir}})


case("S6 owner 方向=模型最高项 → PASS", True, "S6", lambda r: s6_fixtures(r, "H"))
case("S6 owner 改序无记录 → BLOCK", False, "S6", lambda r: s6_fixtures(r, "A"))
case("S6 owner 改序三要素齐全 → PASS", True, "S6",
     lambda r: s6_fixtures(r, "A", {"evidence": "B队头号射手停赛", "falsifiable": "若首发名单有他却输则翻车",
                                    "replacement": "替补边路改三中卫"}))


# ---------- S11 五案：赛果与台账 ----------
def s11_fixtures(root, drop_num=None, ledger=True, tamper=False, no_market=False):
    lock(root, picks(reject_ok=True))          # 锁定方向 001=A 002=H 003=H
    nums = {"周日001": {"score": "0:2", "teams": ["甲", "乙"], "ended": True},
            "周日002": {"score": "1:1", "teams": ["丙", "丁"], "ended": True},
            "周日003": {"score": "2:0", "teams": ["戊", "己"], "ended": True}}
    if drop_num:
        nums.pop(drop_num, None)
    w(os.path.join(root, "%s_settlement.json" % BATCH),
      {"schema": "settlement/1", "source": "unit", "observed_at": "2026-01-02 08:00", "nums": nums})
    w(os.path.join(root, "daily", DAY, "D99_settlement.md"), {"note": "结算单"})
    w(os.path.join(root, "%s_owner方向确认.json" % BATCH),
      {"batch": BATCH, "confirmed": True, "approved_nums": ["周日001", "周日002", "周日003"],
       "directions": {"周日001": {"dir": "A"}, "周日002": {"dir": "H"}, "周日003": {"dir": "H"}},
       "ts": "2026-01-01 12:00"})
    if not ledger:
        return
    rows = []
    for num, s in nums.items():
        res = sg._score_result(s["score"])
        d = {"周日001": "A", "周日002": "H", "周日003": "H"}[num]
        hit = (d == res)
        if tamper and num == "周日002":
            hit = True                        # 002 实为平，写成命中
        rows.append({"batch": BATCH, "num": num, "dir": d, "result": res, "score": s["score"],
                     "hit": hit, "counted": True,
                     "market": None if no_market else {"h": 0.4, "d": 0.3, "a": 0.3}})
    os.makedirs(os.path.join(root, "ledger"), exist_ok=True)
    with open(os.path.join(root, "ledger", "direction_ledger.jsonl"), "w") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


case("S11 合法：逐场赛果+台账齐全 → PASS", True, "S11", lambda r: s11_fixtures(r))
case("S11 赛果少一场 → BLOCK", False, "S11", lambda r: s11_fixtures(r, drop_num="周日003"))
case("S11 无台账 → BLOCK", False, "S11", lambda r: s11_fixtures(r, ledger=False))
case("S11 台账 hit 被写成命中 → BLOCK", False, "S11", lambda r: s11_fixtures(r, tamper=True))
case("S11 计入样本却缺市场概率 → BLOCK", False, "S11", lambda r: s11_fixtures(r, no_market=True))

case("S6 方向≠融合基准且无举证 → BLOCK", False, "S6",
     lambda r: s6_fixtures(r, "A", model_dir_a=True, blend_dir="H"))
case("S6 方向≠融合基准但三要素齐全 → PASS", True, "S6",
     lambda r: s6_fixtures(r, "A", model_dir_a=True, blend_dir="H",
                           override={"evidence": "对手中卫停赛，替代者头球争顶成功率 38%",
                                     "falsifiable": "若该中卫首发则本判断作废",
                                     "replacement": "替补改三中卫，边路传中占比提升"}))


# ---------- S8/S9/S10 空仓豁免 ----------
import hashlib


def _nb(root):
    return os.path.join(root, "%s_no_bet.json" % BATCH)


def _sha(root, rel=None):
    return hashlib.sha256(open(_nb(root), "rb").read()).hexdigest()


def no_ticket_file(root, with_gate=True, decl_eligible=None, vetoes=None, bad_sha=False):
    if decl_eligible is None:
        decl_eligible = ["周日003"]
    o = {"batch": BATCH, "tickets": 0, "reason": "全员否决：模型最高项均未达门槛或出票层否决",
         "declared_by": "main-agent", "declared_at": "2026-01-01 20:00 +08:00"}
    if with_gate:
        o["gate_run"] = {"cmd": "python3 no_bet_gate.py %s" % BATCH, "batch": BATCH,
                         "output_file": "%s_no_bet.json" % BATCH,
                         "output_sha256": "0" * 64 if bad_sha else _sha(root)}
    o["eligible"] = decl_eligible
    if vetoes is not None:
        o["vetoes"] = vetoes
    w(os.path.join(root, "%s_no_ticket.json" % BATCH), o)


def all_no_bet(root):
    w(_nb(root), {"周日001": {"decision": "NO_BET", "reasons": ["N2"], "top_prob": 0.38},
                  "周日002": {"decision": "NO_BET", "reasons": ["N3"], "top_prob": 0.51},
                  "周日003": {"decision": "NO_BET", "reasons": ["N2"], "top_prob": 0.39}})


case("S8 空仓豁免无 gate_run 依据 → BLOCK", False, "S8",
     lambda r: no_ticket_file(r, with_gate=False, vetoes=[{"num": "周日003", "reason": "EV负", "evidence": "nb:003"}]))
case("S8 豁免声称无可进票但产物有 ELIGIBLE → BLOCK", False, "S8",
     lambda r: no_ticket_file(r, decl_eligible=[], vetoes=[]))
case("S8 有可进票但缺逐场否决理由 → BLOCK", False, "S8",
     lambda r: no_ticket_file(r, decl_eligible=["周日003"], vetoes=[]))
case("S8 哈希与产物不符 → BLOCK", False, "S8",
     lambda r: no_ticket_file(r, bad_sha=True,
                              vetoes=[{"num": "周日003", "reason": "EV负", "evidence": "nb:003"}]))
case("S8 合法豁免（产物全 NO_BET + 哈希吻合）→ PASS", True, "S8",
     lambda r: (all_no_bet(r), no_ticket_file(r, decl_eligible=[])))
case("S9 有可进票但未逐场否决，豁免不成立 → BLOCK", False, "S9",
     lambda r: no_ticket_file(r, decl_eligible=["周日003"], vetoes=[]))
case("S10 合法豁免 → PASS", True, "S10",
     lambda r: (all_no_bet(r), no_ticket_file(r, decl_eligible=[])))


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
