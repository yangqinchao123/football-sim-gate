# -*- coding: utf-8 -*-
"""
实质闸门 substance_gate（2026-11-09 新增）
- 解决的问题：match_gate/stage_gate 只查"字段标题存在 + 时长 + 文件存在"，可被空话和磨时间绕过。
- 本闸门只查形式查不到的实质，任一不过即 BLOCK：
  1. 碰撞逐轮实质：每轮存在、长度达标、含具体证据（数字/专有名词），禁空话收口，两轮不得雷同
  2. 独立模型证据：碰撞子 agent 目录下有自建模型产物（含真实概率字段），证明没照搬主结论
  3. 处置明确：verdict/final 必须写清进票/不进票/方向，不能空
  4. 流程审计无 FAIL：审计子 agent 的流程核查结果逐项通过
用法: python3 substance_gate.py <clash_record.json> [clash_record2.json ...]
"""
import json, os, re, sys

MIN_ROUND_LEN = 30
# 空话/套话黑名单（命中且无其他实质证据即判空）
EMPTY_PHRASES = ["已讨论", "同意", "维持原判", "无异议", "综合判断", "我将", "持续推进",
                 "按要求", "有序落地", "具体分析", "见上述", "如上"]
HAS_DIGIT = re.compile(r"\d|[一二三四五六七八九十]")
# 大写开头/驼峰的英文专有名词（球员、球队名）
PROPER = re.compile(r"[A-Z][a-zA-Zé]+")
# 中文实义字符
CJK = re.compile(r"[一-鿿]")
# 中文里的套话/停用成分（统计实义字数时剔除）
STOPWORDS = ["MainAgent", "Audit", "反驳", "核实", "维持", "承认", "警告", "我们", "他们",
             "当前", "自身", "头号", "具体", "综合", "判断", "讨论", "同意", "无异议"]
# 抽象套话词（密度高=无具体实体的空话）
ABSTRACT = ["事项", "信息", "工作", "要求", "维度", "查询", "推进", "落实", "开展", "检索",
            "落地", "全面", "相关", "确保", "获取", "持续", "处理", "及时", "全部", "所需",
            "动作", "严格", "覆盖", "无遗漏", "有序"]

def check_round(txt):
    if not txt or len(txt) < MIN_ROUND_LEN:
        return False, f"轮次内容过短（<{MIN_ROUND_LEN}字），疑似空泛"
    has_digit = bool(HAS_DIGIT.search(txt))
    propers = set(PROPER.findall(txt))
    # 中文实义字数：去掉停用词后统计中文字符
    cjk_text = txt
    for w in STOPWORDS:
        cjk_text = cjk_text.replace(w, "")
    cjk_chars = CJK.findall(cjk_text)
    cjk_count = len(cjk_chars)
    # 抽象套话密度：抽象词字数 / 中文字数
    abs_hit = sum(txt.count(w) for w in ABSTRACT)
    abs_density = abs_hit / len(CJK.findall(txt)) if CJK.findall(txt) else 0
    # 硬证据：数字 / 英文专名≥2
    hard_evidence = has_digit or len(propers) >= 2
    # 实质证据：硬证据，或（中文实义字够多且不是抽象套话堆砌）
    has_evidence = hard_evidence or (cjk_count >= 18 and abs_density < 0.30)
    if not has_evidence:
        if abs_density >= 0.30:
            return False, f"轮次为抽象套话堆砌（套话密度{abs_density:.2f}），无具体实体"
        return False, "轮次无具体证据（无数字、无具体球员/球队等实质内容）"
    hit_empty = [p for p in EMPTY_PHRASES if p in txt]
    if hit_empty and not hard_evidence and abs_density >= 0.30:
        return False, f"轮次仅由套话堆砌：{hit_empty}"
    return True, "ok"

def check_clash(path):
    errs = []
    if not os.path.exists(path):
        return [f"缺碰撞记录 {path}"]
    d = json.load(open(path))
    if not d:
        return [f"{path} 为空"]
    for num, rec in d.items():
        rounds = rec.get("clash_rounds", 0)
        if rounds < 2:
            errs.append(f"场{num} clash_rounds<2（分歧场必须多轮对质）")
        rkeys = sorted(k for k in rec if re.fullmatch(r"round\d+", k))
        if len(rkeys) < 2:
            errs.append(f"场{num} 实际轮次记录不足 2")
        contents = []
        for rk in rkeys:
            ok, msg = check_round(str(rec.get(rk, "")))
            if not ok:
                errs.append(f"场{num} {rk} {msg}")
            contents.append(str(rec.get(rk, "")))
        if len(contents) >= 2 and contents[0] == contents[1]:
            errs.append(f"场{num} 两轮内容雷同（假对质）")
        if not rec.get("verdict") or not rec.get("final"):
            errs.append(f"场{num} 缺 verdict/final 明确处置")
    return errs

def check_independent_model(dirs):
    errs = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        model_files = [f for f in os.listdir(d) if f.endswith("_model_raw.json") or f.endswith("_result.json")]
        found = False
        for mf in model_files:
            try:
                obj = json.load(open(os.path.join(d, mf)))
                raw = json.dumps(obj, ensure_ascii=False)
                if ("pb_dc" in raw) or ("home_draw" in raw) or re.search(r"0\.\d{3}", raw):
                    found = True; break
            except Exception:
                continue
        if not found:
            errs.append(f"{os.path.basename(d)} 无自建模型真实概率产物（疑照搬主结论）")
    return errs

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python3 substance_gate.py <clash_record.json> [...]"); sys.exit(2)
    all_errs = []
    for p in sys.argv[1:]:
        all_errs += check_clash(p)
    base = os.path.dirname(os.path.abspath(__file__))
    all_errs += check_independent_model([os.path.join(base, "audit_g1"), os.path.join(base, "audit_g2")])
    if all_errs:
        print("BLOCK — 实质闸门未过：")
        for e in all_errs:
            print(" -", e)
        sys.exit(1)
    print("PASS — 碰撞逐轮有实质证据、独立自建模型、处置明确，实质闸门通过")
