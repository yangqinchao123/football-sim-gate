# -*- coding: utf-8 -*-
"""
伤停双源核查 injury_check（2026-11-09 新增，防"具体但错误"的外部事实）
- 规则：伤停/复出属外部事实，禁止单源裸写。必须提供官方源 + 独立源两份名单，机器比对：
  两源都列出 = confirmed（确认）；仅单源 = unconfirmed（待核实），禁止当确定事实、禁止据此看弱。
- 每条带采集时间，防过期信息当现状；临场首发以官宣为准（另字段）。
输入 JSON 结构:
{
 "num":"023","team":"Como","collected_at":"2026-11-09T20:00",
 "source_official":[{"player":"Kean","status":"injured"}, ...],
 "source_independent":[{"player":"Kean","status":"injured"}, ...]
}
用法: python3 injury_check.py <input.json>
"""
import json, sys

def check(d):
    off = {x['player']: x.get('status', '') for x in d.get('source_official', [])}
    ind = {x['player']: x.get('status', '') for x in d.get('source_independent', [])}
    confirmed = sorted(set(off) & set(ind))
    only_one = sorted(set(off) ^ set(ind))
    # 状态冲突（两源都有但 status 不一致）
    conflict = sorted(p for p in confirmed if off[p] and ind[p] and off[p] != ind[p])
    return {
        'num': d.get('num'), 'team': d.get('team'), 'collected_at': d.get('collected_at'),
        'confirmed': confirmed,
        'unconfirmed': only_one,
        'status_conflict': conflict,
        # 只有确认名单可用于判断；有待核实/冲突项则不许据此看弱
        'usable_for_judgment': len(only_one) == 0 and len(conflict) == 0,
    }

if __name__ == '__main__':
    d = json.load(open(sys.argv[1]))
    r = check(d)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    if not r['usable_for_judgment']:
        print('BLOCK — 存在待核实/冲突伤停，禁止当确定事实写进结论，需补第二来源')
        sys.exit(1)
    print('PASS — 伤停双源一致，可作为确认事实')
