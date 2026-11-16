# -*- coding: utf-8 -*-
"""intel_rules.py — 情报卡片的机器校验（治"战意/伤停写反了没人拦"）

背景（2026-11-16 D43 事故）：8 场方向一路绿灯过到 S7，但 001 情报卡把
"法罗打平即锁第二"写进方向依据，实际是摩尔多瓦 7 分第 2、法罗 5 分第 3，
必须取胜的是法罗。全流程没有一道机器门发现，最后靠外部探球原文撞出来的。

本模块把"情报"变成可校验结构：
  R1 结构：claims 逐条 {type,text,sources[]}，sources 必须是 http(s) 链接
  R2 战意数字：战意类 claim 必须带 points{home,away}，且与探球原文里的"积N分"一致，
     与事实卡分组积分表一致；任何一方对不上即 BLOCK（001 就是死在这条）
  R3 伤停人名：卡片里每个 name 必须在探球原文出现，否则须在 single_source 里
     写明理由（单源不许用来改方向）
  R4 方向一致：卡片结论方向必须与锁定方向表一致（防"卡写 H、表写 A"）
"""
import json
import os
import re

HTTP = re.compile(r"https?://")
POINTS = re.compile(r"积\s*(\d+)\s*分")
RECORD = re.compile(r"(\d+)\s*战\s*(\d+)\s*胜\s*(\d+)\s*平\s*(\d+)\s*负")


def _norm(s):
    return re.sub(r"[^\u4e00-\u9fa50-9A-Za-z]", "", str(s or ""))


def _tq_points(tq_text):
    """从探球原文里抽出其给出的积分数字集合（用于交叉核对）。"""
    vals = {int(x) for x in POINTS.findall(tq_text)}
    for m in RECORD.finditer(tq_text):
        w, d = int(m.group(2)), int(m.group(3))
        vals.add(3 * w + d)
    return vals


def _team_points(tq_text, team):
    """按队名就近抽取积分：找队名，再在其后 30 字内找"积N分"或"X战Y胜Z平"。

    为什么不只用全局数字集合：001 事故里 5 和 7 两个数字都在原文出现过，
    只看集合会把"7 分属于法罗"这种张冠李戴放过。
    """
    if not team:
        return set()
    out = set()
    variants = [team, team[:3], team[:2]]
    for v in {x for x in variants if x}:
        for m in re.finditer(re.escape(v), tq_text):
            seg = tq_text[m.end():m.end() + 30]
            for x in POINTS.findall(seg):
                out.add(int(x))
            for r in RECORD.finditer(seg):
                out.add(3 * int(r.group(2)) + int(r.group(3)))
    return out


def _cn(fact, side):
    """事实卡里 team 是英文、display 是中文：情报卡没写队名时用 display 兜底。"""
    if not fact:
        return ""
    t = fact.get(side) or ""
    return (fact.get("display") or {}).get(t, t)


def check(card, tq_text="", fact=None, picks_dir=None):
    errs = []
    claims = card.get("claims")
    if not isinstance(claims, list) or not claims:
        return ["缺 claims：情报必须逐条结构化，散文字无法校验"]
    if not card.get("collected_at"):
        errs.append("缺 collected_at（过期情报当现状）")

    for i, c in enumerate(claims):
        if not isinstance(c, dict):
            errs.append("claims[%d] 不是对象" % i)
            continue
        if not str(c.get("text") or "").strip():
            errs.append("claims[%d] 缺 text" % i)
        srcs = c.get("sources")
        if not isinstance(srcs, list) or not srcs:
            errs.append("claims[%d] 缺 sources（每条情报必须有来源，禁止无源断言）" % i)
        elif not all(HTTP.match(str(s)) for s in srcs):
            errs.append("claims[%d] sources 里有非 http 链接" % i)
        if str(c.get("type") or "").startswith("战意"):
            pts = c.get("points") or {}
            if not isinstance(pts, dict) or pts.get("home") is None or pts.get("away") is None:
                # 定性战意可以写，但必须自证为什么拿不到数字，且不得作为改序依据
                if not c.get("no_points_reason"):
                    errs.append("claims[%d] 战意缺 points{home,away}，且未写 no_points_reason"
                                "（没有积分数字的战意就是叙事，001 事故）" % i)
                continue
            hp, ap = int(pts["home"]), int(pts["away"])
            if hp < ap and str(c.get("leader") or "") == "home":
                errs.append("claims[%d] 主队积分 %d 低于客队 %d，却声明主队领先/只需平局" % (i, hp, ap))
            if ap < hp and str(c.get("leader") or "") == "away":
                errs.append("claims[%d] 客队积分 %d 低于主队 %d，却声明客队领先/只需平局" % (i, ap, hp))
            if tq_text:
                hname = card.get("home") or _cn(fact, "home")
                aname = card.get("away") or _cn(fact, "away")
                th = _team_points(tq_text, hname)
                ta = _team_points(tq_text, aname)
                if th and hp not in th:
                    errs.append("claims[%d] 主队%r 积分 %d 与探球原文（就近抽取 %s）不符"
                                % (i, hname, hp, sorted(th)))
                if ta and ap not in ta:
                    errs.append("claims[%d] 客队%r 积分 %d 与探球原文（就近抽取 %s）不符"
                                % (i, aname, ap, sorted(ta)))
            psrc = str(c.get("points_source") or "fact")
            fact_incomplete = bool((fact.get("meta") or {}).get("data_lag_flag")) or bool(fact.get("incomplete"))
            if psrc == "fact" and fact_incomplete:
                errs.append("claims[%d] 事实卡本地表轮次不全，却仍以 fact 为积分来源" % i)
            if psrc == "tq" and not fact_incomplete and fact:
                errs.append("claims[%d] 事实卡完整却用探球积分（禁止绕开本地可复算数据）" % i)
            if psrc == "tq" and not tq_text:
                errs.append("claims[%d] 积分来源标 tq 但无探球原文，无法核对" % i)
            if fact and psrc == "fact":
                for side, val in (("home", hp), ("away", ap)):
                    t = fact.get(side)
                    st = (fact.get("standings") or {}).get(t) or {}
                    if st.get("points") is not None and int(st["points"]) != val:
                        errs.append("claims[%d] %s 积分 %d 与事实卡分组积分表 %s 冲突"
                                    % (i, t, val, st.get("points")))
        names = c.get("players") or []
        if names and tq_text:
            ntq = _norm(tq_text)
            ack = {_norm(x) for x in (card.get("single_source") or [])}
            for nm in names:
                if _norm(nm) and _norm(nm) not in ntq and _norm(nm) not in ack:
                    errs.append("claims[%d] 人名 %s 在探球原文中找不到，也未登记 single_source" % (i, nm))

    if picks_dir:
        fp = os.path.join(picks_dir, "%s_final_picks.json" % card.get("batch", ""))
        if os.path.exists(fp):
            p = json.load(open(fp))
            k = card.get("num")
            rec = (p.get(k) or p.get(str(k)[-3:]) or {})
            d1 = str((card.get("conclusion") or {}).get("dir", "")).strip().upper()[:1]
            d2 = str(rec.get("dir", "")).strip().upper()[:1]
            if d1 and d2 and d1 != d2:
                errs.append("情报卡结论方向 %s 与方向表 %s 不一致" % (d1, d2))
    return errs
