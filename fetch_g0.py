# -*- coding: utf-8 -*-
"""
D42 取数流程 · 第1层：官方四池SP + 去水 + 500 fid映射
====================================================================================
跑法：python3 d3_fetch_g0.py
产出：g0D42_raw.json(官方原始) / g0D42_parsed.json(结构化,含去水与peripheral占位)
外围(亚盘/欧赔初始→即时/大小球O2.5/BTTS/进球线/首发)不在本脚本——见 d3_fetch_peripheral 浏览器步骤。
所有金额口径：SP 为含本金返还的小数赔率；fair=去水概率；or=水位(overround, sum(1/SP)-1)。
"""
import json, re, time, urllib.request, datetime, os

WS = os.path.dirname(os.path.abspath(__file__))
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
OFFICIAL = ("https://webapi.sporttery.cn/gateway/jc/football/getMatchCalculatorV1.qry"
            "?poolCode=had,hhad,crs,ttg&channel=c")
P500 = "https://trade.500.com/jczq/"

def fetch(url, referer, decode="utf-8", timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Referer": referer,
        "Accept": "application/json,text/html,*/*", "Accept-Language": "zh-CN,zh;q=0.9"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw.decode(decode, "ignore")

def devig3(h, d, a):
    """三向(胜平负/让球)去水；输入SP字符串/数值，返回(fair字典,水位)。"""
    try:
        ih, idd, ia = 1/float(h), 1/float(d), 1/float(a)
        s = ih+idd+ia
        return {"h": round(ih/s, 4), "d": round(idd/s, 4), "a": round(ia/s, 4)}, round(s-1, 4)
    except (TypeError, ValueError, ZeroDivisionError):
        return None, None

def devig_map(spmap):
    """多结果(总进球8档/比分)去水。"""
    vals = {k: float(v) for k, v in spmap.items() if v not in (None, "", "0")}
    if not vals:
        return None, None
    inv = {k: 1/v for k, v in vals.items()}
    s = sum(inv.values())
    return {k: round(x/s, 4) for k, x in inv.items()}, round(s-1, 4)

# ---------- 1. 竞彩官方 ----------
def fetch_official():
    txt = fetch(OFFICIAL, "https://www.sporttery.cn/jc/jsq/zqspf/")
    raw = json.loads(txt)
    json.dump(raw, open(os.path.join(WS, "g0D42_raw.json"), "w"), ensure_ascii=False)
    rows = []
    for blk in raw["value"]["matchInfoList"]:
        for m in blk["subMatchList"]:
            had, hhad, crs, ttg = (m.get(k) or {} for k in ("had", "hhad", "crs", "ttg"))
            score = {}
            for hh in range(6):
                for aa in range(6):
                    k = "s%02ds%02d" % (hh, aa)
                    if crs.get(k):
                        score["%d-%d" % (hh, aa)] = float(crs[k])
            for k, lab in [("s1sh", "胜其他"), ("s1sd", "平其他"), ("s1sa", "负其他")]:
                if crs.get(k):
                    score[lab] = float(crs[k])
            ttgmap = {}
            for i in range(8):
                v = ttg.get("s%d" % i)
                if v:
                    ttgmap["%d%s" % (i, "+" if i == 7 else "")] = float(v)
            had_f, had_or = devig3(had.get("h"), had.get("d"), had.get("a"))
            hh_f, hh_or = devig3(hhad.get("h"), hhad.get("d"), hhad.get("a"))
            ttg_f, ttg_or = devig_map(ttgmap)
            rows.append({
                "num": m.get("matchNumStr"), "league": m.get("leagueAbbName"),
                "home": m.get("homeTeamAbbName"), "away": m.get("awayTeamAbbName"),
                "homeAll": m.get("homeTeamAllName"), "awayAll": m.get("awayTeamAllName"),
                "time": m.get("matchTime"), "single": m.get("bettingSingle"),
                "sellStatus": m.get("sellStatus"), "matchStatus": m.get("matchStatus"),
                "had": {"h": had.get("h"), "d": had.get("d"), "a": had.get("a"),
                        "fair": had_f, "or": had_or},
                "hhad": {"line": hhad.get("goalLine"), "h": hhad.get("h"),
                         "d": hhad.get("d"), "a": hhad.get("a"), "fair": hh_f, "or": hh_or},
                "ttg": {"sp": ttgmap, "fair": ttg_f, "or": ttg_or},
                "crs": score,
                "src500": None,
                "peripheral": {"yazhi": None, "ouzhi_init_live": None, "daxiao": None,
                               "o25_fair_over": None, "btts": None, "team_goallines": None,
                               "lineup": None, "sources_n": 0, "collected_at": None,
                               "hard_signal": False, "l1_triggered": False,
                               "note": "未采外围,L1硬信号不激活,仅走L2"},
            })
    return rows

# ---------- 2. 500 列表(fid + 第二源SP) ----------
def fetch_500():
    h = fetch(P500, "https://www.500.com/", decode="gb18030")
    open(os.path.join(WS, "D42_500_list.html"), "w").write(h)
    out = []
    # 以 betbtn 块回溯，逐场用队名锚点切分
    for mm in re.finditer(r'title="([^"]{2,20})">\s*([^<]{1,12})</a></span>\s*'
                          r'<i class="team-vs">VS</i>.*?fenxi/(?:shuju|yazhi|ouzhi)-(\d{5,})',
                          h, re.S):
        league, home, fid = mm.group(1), mm.group(2).strip(), mm.group(3)
        block = h[mm.start(): mm.start()+2600]
        away_m = re.search(r'team-r" title="([^"]+)"', block)
        nspf = dict(zip(("h", "d", "a"),
                        re.findall(r'data-type="nspf"[^>]*data-sp="([0-9.]+)"', block)[:3]))
        spf = dict(zip(("h", "d", "a"),
                       re.findall(r'data-type="spf"[^>]*data-sp="([0-9.]+)"', block)[:3]))
        out.append({"fid": fid, "league": league, "home": home,
                    "away": away_m.group(1) if away_m else None, "nspf": nspf, "spf": spf})
    return out

def norm(s):
    return re.sub(r"[\s\-_·•・U0-9]+", "", str(s or "")).replace("亚足", "").replace("女足", "女")

def match_500(rows, list500):
    used = set(); hit = 0
    for r in rows:
        best, bscore = None, 0
        for j, z in enumerate(list500):
            if j in used:
                continue
            for (a, b) in ((r["homeAll"], z["home"]), (r["home"], z["home"])):
                na, nb = norm(a), norm(b)
                if na and nb and (na in nb or nb in na or (len(na) >= 2 and na[:2] == nb[:2])):
                    score = len(set(na) & set(nb))
                    if score > bscore:
                        best, bscore = j, score
        if best is not None:
            z = list500[best]; used.add(best); r["src500"] = z; hit += 1
    return hit

# ---------- 主流程 ----------
PARSED = os.path.join(WS, "g0D42_parsed.json")

def merge_peripheral(rows):
    """重跑层1时，按 500 fid 保留已采集的外围(层2成果)，避免覆盖。"""
    if not os.path.exists(PARSED):
        return 0
    old = {}
    for o in json.load(open(PARSED, encoding="utf-8")):
        z = o.get("src500")
        pe = o.get("peripheral") or {}
        has = pe.get("hard_signal") or pe.get("sources_n", 0) > 0 or \
            pe.get("yazhi") or pe.get("daxiao") or pe.get("ouzhi_init_live")
        if z and has:  # 只保留真正采到内容的外围，空占位用新版重建
            old[str(z["fid"])] = pe
    kept = 0
    for r in rows:
        z = r.get("src500")
        if z and str(z["fid"]) in old:
            r["peripheral"] = old[str(z["fid"])]
            kept += 1
    return kept

def main():
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = fetch_official()
    list500 = fetch_500()
    hit = match_500(rows, list500)
    kept = merge_peripheral(rows)
    json.dump(rows, open(PARSED, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    sell = [r for r in rows if str(r["sellStatus"]) == "1"]
    print("采集时点:", ts)
    print("官方在售/全部: %d / %d 场；500列表 %d 场，fid映射命中 %d（%s）"
          % (len(sell), len(rows), len(list500), hit,
             ("%.0f%%" % (100*hit/max(1,len(rows))))))
    print("merge 保留已采外围场: %d" % kept)
    # 水位体检
    import statistics
    for key in ("had", "hhad", "ttg"):
        ors = [r[key]["or"] for r in sell if r[key]["or"] is not None]
        if ors:
            print("  %-4s 水位 中位 %.1f%% 区间 %.1f%%~%.1f%%"
                  % (key, 100*statistics.median(ors), 100*min(ors), 100*max(ors)))
    nofid = [r["num"]+r["home"] for r in sell if not r["src500"]]
    print("未映射500 fid 的在售场(%d): %s" % (len(nofid), " ".join(nofid[:20])))
    # 生成外围浏览器采集 URL 清单（优先：超低赔热门/深盘/单关，即 v5.8 最需要情境判定的场）
    urls = []
    for r in sell:
        z = r["src500"]
        if not z:
            continue
        sp = [r["had"]["h"], r["had"]["a"]]
        low = any((float(x) <= 1.50) for x in sp if x)
        deep = r["hhad"]["line"] not in (None,) and abs(int(r["hhad"]["line"])) >= 2
        single = str(r["single"]) == "1"
        if low or deep or single:
            urls.append((r["num"], r["home"]+"vs"+r["away"], z["fid"]))
    with open(os.path.join(WS, "D42_peripheral_urls.txt"), "w") as f:
        f.write("# 外围浏览器采集清单(候选场)；过EdgeOne后逐场开 亚/欧/大小 三页\n")
        for num, mm, fid in urls:
            f.write("%s %s\n  亚 https://odds.500.com/fenxi/yazhi-%s.shtml\n"
                    "  欧 https://odds.500.com/fenxi/ouzhi-%s.shtml\n"
                    "  大小 https://odds.500.com/fenxi/daxiao-%s.shtml\n"
                    % (num, mm, fid, fid, fid))
    print("外围候选场 %d 个，URL 清单 → D42_peripheral_urls.txt" % len(urls))

if __name__ == "__main__":
    main()
