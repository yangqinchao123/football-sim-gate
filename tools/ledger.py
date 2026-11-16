# -*- coding: utf-8 -*-
"""
ledger.py — 方向命中台账（准确率的唯一数据源）

为什么要有：门禁再严，也只能证明"流程没被绕过"，证明不了"预测准不准"。
命中率散落在各批文件里就等于没有：分母不清、可事后改写、无法和基准比。

三件事：
  1. fill   <batch>：抓赛果 → 写 <batch>_settlement.json（带来源与抓取时间）
  2. append <batch>：结算 + 锁定方向 + 模型/市场概率 → 追加 ledger/direction_ledger.jsonl
  3. stats        ：滚动命中率 / RPS，并同时给出"模型最高项""市场最高项"两个基准

纪律：
- hit 一律由本脚本按 dir 与 result 现算，不采信文件里写好的 hit。
- 只追加、不修改；同一 batch+num 已存在则跳过（要更正走 errata 另起记录）。
- 未锁定预测的批次（无 _meta.locked_at）标 counted=false，不计入准确率样本。
用法：
  python3 tools/ledger.py fill D42 --date 2026-11-15
  python3 tools/ledger.py fill D42 --html /tmp/x.html        # 离线解析留档
  python3 tools/ledger.py append D42
  python3 tools/ledger.py validate
  python3 tools/ledger.py stats
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEDGER = os.path.join(ROOT, "ledger", "direction_ledger.jsonl")
EXCLUDE = os.path.join(ROOT, "ledger", "exclusions.json")
RESULT_URL = "https://trade.500.com/jczq/index.php?date=%s"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def now():
    return datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def load_json(p, default=None):
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)


# ---------------- 1. 赛果 ----------------

def parse_500(html):
    """解析 500 完场页：data-matchnum / data-isend / class=score。"""
    out = {}
    for b in html.split("<tr")[1:]:
        m = re.search(r'data-matchnum="([^"]+)"', b)
        if not m:
            continue
        num = m.group(1)
        sc = re.search(r'class="score"[^>]*>\s*(\d+)\s*:\s*(\d+)', b)
        tms = re.findall(r'class="team-[lr]"[^>]*>\s*([^<]+?)\s*<', b)
        if sc and 'data-isend="1"' in b:
            out[num] = {"score": "%s:%s" % sc.groups(),
                        "teams": tms[:2] if tms else [],
                        "ended": True}
    return out


def cmd_fill(batch, date=None, html=None):
    nums = {}
    if html:
        nums = parse_500(open(html, encoding="utf-8", errors="ignore").read())
    else:
        url = RESULT_URL % date
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        raw = urllib.request.urlopen(req, timeout=20).read()
        try:
            text = raw.decode("gb18030")
        except Exception:
            text = raw.decode("utf-8", "ignore")
        nums = parse_500(text)
    if not nums:
        sys.exit("未解析到任何完场赛果（页面无 data-isend=1 行），不写文件")
    rec = {"schema": "settlement/1", "source": url if not html else "local html: %s" % html,
           "observed_at": now(), "date": date, "ended": True,
           "nums": {k: v for k, v in sorted(nums.items())}}
    p = os.path.join(ROOT, "%s_settlement.json" % batch)
    save_json(p, rec)
    print("写入 %s：%d 场完赛（%s）" % (os.path.basename(p), len(nums), rec["observed_at"]))


# ---------------- 2. 台账 ----------------

def hits_dir(dir_):
    """方向首字母 → H/D/A（允许 'H（让-4）' 这类后缀）。"""
    d = str(dir_ or "").strip().upper()[:1]
    return d if d in ("H", "D", "A") else None


def result_of(score):
    try:
        h, a = [int(x) for x in str(score).replace("：", ":").split(":")]
    except Exception:
        return None
    return "H" if h > a else ("D" if h == a else "A")


def is_locked(batch):
    d = load_json(os.path.join(ROOT, "%s_final_picks.json" % batch), {}) or {}
    return bool((d.get("_meta") or {}).get("locked_at"))


def cmd_append(batch):
    sett = load_json(os.path.join(ROOT, "%s_settlement.json" % batch))
    picks = load_json(os.path.join(ROOT, "%s_final_picks.json" % batch), {}) or {}
    models = load_json(os.path.join(ROOT, "%s_model_out.json" % batch), {}) or {}
    if not sett:
        sys.exit("缺 %s_settlement.json，先 fill" % batch)

    excl = load_json(EXCLUDE, {}) or {}
    counted = is_locked(batch) and batch not in excl
    reason = "" if counted else (
        (excl.get(batch) or {}).get("reason") or "预测未锁定（无 _meta.locked_at），命中率不可核")

    existing = set()
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            existing.add((r["batch"], r["num"]))

    added = 0
    rows = []
    for num in sorted(set(list(sett.get("nums", {})) + [k for k in picks if not k.startswith("_")])):
        s = (sett.get("nums") or {}).get(num)
        p = picks.get(num) or {}
        if not s or not p:
            continue
        res = result_of(s.get("score"))
        d = hits_dir(p.get("dir"))
        m = models.get(num) or {}
        mk = m.get("market_fair") or {}
        row = {"batch": batch, "num": num, "date": sett.get("date"),
               "locked_at": (picks.get("_meta") or {}).get("locked_at"),
               "dir": d, "score_pred": p.get("score"), "result": res, "score": s.get("score"),
               "hit": (d == res) if (d and res) else None,
               "model": m.get("pb_dc"), "market": {"h": mk.get("h"), "d": mk.get("d"), "a": mk.get("a")} if mk else None,
               "counted": counted, "exclude_reason": reason,
               "result_source": sett.get("source"), "filled_at": now()}
        if (batch, num) in existing:
            continue
        rows.append(row)
        added += 1
    if not rows:
        print("无新增记录（已存在或结算缺场次）")
        return
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("追加 %d 条到台账（counted=%s）" % (added, counted))


# ---------------- 3. 校验与统计 ----------------

def read_ledger():
    if not os.path.exists(LEDGER):
        return []
    out = []
    for line in open(LEDGER, encoding="utf-8"):
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def cmd_validate():
    """重算 hit，检查是否被改写；确认 counted 记录都有 dir/result。"""
    rows = read_ledger()
    bad = []
    for r in rows:
        d, res = r.get("dir"), r.get("result")
        want = (d == res) if (d and res) else None
        if r.get("hit") != want:
            bad.append("%s %s: 文件 hit=%s，重算=%s" % (r["batch"], r["num"], r.get("hit"), want))
        if r.get("counted") and not (d and res):
            bad.append("%s %s: 计入样本但缺方向或赛果" % (r["batch"], r["num"]))
    if bad:
        print("台账校验失败：")
        for b in bad:
            print(" -", b)
        return 1
    print("台账 OK：%d 条，其中计入样本 %d 条" % (len(rows), sum(1 for r in rows if r.get("counted"))))
    return 0


def rps(p, y):
    try:
        h, d, a = [float(p["h"]), float(p["d"]), float(p["a"])]
    except Exception:
        return None
    s = h + d + a
    if s <= 0:
        return None
    h, d, a = h / s, d / s, a / s
    y = {"H": 0, "D": 1, "A": 2}[y]
    c1 = h
    c2 = h + d
    y1 = 1.0 if y == 0 else 0.0
    y2 = 1.0 if y <= 1 else 0.0
    return ((c1 - y1) ** 2 + (c2 - y2) ** 2) / 2.0


def cmd_stats():
    allrows = read_ledger()
    excl = [r for r in allrows if not r.get("counted")]
    if excl:
        by = {}
        for r in excl:
            by.setdefault(r["batch"], [0, r.get("exclude_reason", "")])
            by[r["batch"]][0] += 1
        for b, (n, why) in sorted(by.items()):
            h = sum(1 for r in excl if r["batch"] == b and r.get("hit"))
            print("排除 %s：%d 场（命中 %d）——%s" % (b, n, h, why))
    rows = [r for r in allrows if r.get("counted")]
    if not rows:
        print("暂无可计入样本的记录（未锁定批次已按纪律排除，等下一批锁定预测的比赛）")
        return 0
    n = len(rows)
    hit = sum(1 for r in rows if r.get("hit"))
    mod_arg = sum(1 for r in rows if r.get("model") and "HDA"[max(range(3), key=lambda i: r["model"][i])] == r["result"])
    mkt_arg = 0
    mr = []
    mm = []
    for r in rows:
        mk = r.get("market") or {}
        if all(mk.get(k) for k in ("h", "d", "a")):
            mkt_arg += 1 if "HDA"[max(range(3), key=lambda i: [mk["h"], mk["d"], mk["a"]][i])] == r["result"] else 0
            x = rps(mk, r["result"])
            if x is not None:
                mr.append(x)
        if r.get("model"):
            x = rps({"h": r["model"][0], "d": r["model"][1], "a": r["model"][2]}, r["result"])
            if x is not None:
                mm.append(x)
    print("计入样本 %d 场（%d 批）" % (n, len({r["batch"] for r in rows})))
    print("方向命中：锁定方向 %d/%d = %.1f%%" % (hit, n, 100.0 * hit / n))
    print("模型最高项   %d/%d = %.1f%%" % (mod_arg, n, 100.0 * mod_arg / n))
    if mr:
        print("市场最高项   %d/%d = %.1f%%" % (mkt_arg, n, 100.0 * mkt_arg / n))
        print("RPS：模型 %.4f vs 市场 %.4f（差 %+.4f，负=模型更好）"
              % (sum(mm) / len(mm), sum(mr) / len(mr), (sum(mm) / len(mm)) - (sum(mr) / len(mr))))
    else:
        print("市场概率缺失：本批无 market_fair，RPS 无法对比")
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fill")
    f.add_argument("batch")
    f.add_argument("--date")
    f.add_argument("--html")
    a = sub.add_parser("append")
    a.add_argument("batch")
    sub.add_parser("validate")
    sub.add_parser("stats")
    ns = ap.parse_args()
    if ns.cmd == "fill":
        return cmd_fill(ns.batch, ns.date, ns.html)
    if ns.cmd == "append":
        return cmd_append(ns.batch)
    if ns.cmd == "validate":
        return cmd_validate()
    return cmd_stats()


if __name__ == "__main__":
    sys.exit(main())
