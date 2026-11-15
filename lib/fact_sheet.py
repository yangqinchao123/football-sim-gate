# -*- coding: utf-8 -*-
"""
事实卡片生成 fact_sheet（2026-11-09 新增，防"具体但错误"的事实）
- 原则：数字/记录类事实一律由脚本从 football_data 原始 CSV 直出，MainAgent 不手写、不转述，
  只在卡片下做解读，从机制上消除记串/写错/张冠李戴。
- 直出内容：积分榜、近 5 场形态、主客拆分、进失球、交锋记录（含 xG 若有）。
- 输出：{batch}_fact_{num}.json，作为 GRC 强制附件；GRC 引用此文件而非自写数字。
用法: python3 fact_sheet.py <league_code> <home_team> <away_team> [out_path]
"""
import csv, json, os, sys

def load_rows(code):
    fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'football_data', f'{code}_2627.csv')
    return list(csv.DictReader(open(fp, encoding='utf-8-sig')))

def standings(rows):
    tab = {}
    for r in rows:
        for t, gf, ga in ((r['HomeTeam'], int(r['FTHG']), int(r['FTAG'])),
                          (r['AwayTeam'], int(r['FTAG']), int(r['FTHG']))):
            p = tab.setdefault(t, {'played': 0, 'w': 0, 'd': 0, 'l': 0, 'gf': 0, 'ga': 0})
            p['played'] += 1; p['w'] += gf > ga; p['d'] += gf == ga; p['l'] += gf < ga
            p['gf'] += gf; p['ga'] += ga
    table = []
    for t, p in tab.items():
        table.append({'team': t, 'points': p['w'] * 3 + p['d'], 'gd': p['gf'] - p['ga'], **p})
    table.sort(key=lambda x: (-x['points'], -x['gd']))
    for i, x in enumerate(table):
        x['rank'] = i + 1
    return table

def team_form(rows, team, n=5):
    games = []
    for r in rows:
        if r['HomeTeam'] == team:
            games.append({'date': r['Date'], 'venue': 'H', 'opp': r['AwayTeam'],
                          'gf': int(r['FTHG']), 'ga': int(r['FTAG']), 'res': r['FTR']})
        elif r['AwayTeam'] == team:
            games.append({'date': r['Date'], 'venue': 'A', 'opp': r['HomeTeam'],
                          'gf': int(r['FTAG']), 'ga': int(r['FTHG']),
                          'res': {'H': 'A', 'A': 'H', 'D': 'D'}[r['FTR']]})
    return games[-n:]

def home_away_split(rows, team):
    out = {'home': {'n': 0, 'gf': 0, 'ga': 0}, 'away': {'n': 0, 'gf': 0, 'ga': 0}}
    for r in rows:
        if r['HomeTeam'] == team:
            out['home']['n'] += 1; out['home']['gf'] += int(r['FTHG']); out['home']['ga'] += int(r['FTAG'])
        elif r['AwayTeam'] == team:
            out['away']['n'] += 1; out['away']['gf'] += int(r['FTAG']); out['away']['ga'] += int(r['FTHG'])
    return out

def h2h(rows, h, a):
    return [{'date': r['Date'], 'home': r['HomeTeam'], 'away': r['AwayTeam'],
             'score': f"{r['FTHG']}-{r['FTAG']}", 'res': r['FTR']}
            for r in rows if {r['HomeTeam'], r['AwayTeam']} >= {h, a}]

def main(code, home, away, out=None):
    rows = load_rows(code)
    table = standings(rows)
    card = {
        'league': code, 'home': home, 'away': away,
        'standings': {x['team']: {'rank': x['rank'], 'points': x['points'], 'gd': x['gd'],
                                  'played': x['played']}
                      for x in table if x['team'] in (home, away)},
        'form': {home: team_form(rows, home), away: team_form(rows, away)},
        'home_away_split': {home: home_away_split(rows, home), away: home_away_split(rows, away)},
        'h2h_2627': h2h(rows, home, away),
        'source': f'football_data/{code}_2627.csv（脚本直出，无人工转述）',
    }
    if out:
        json.dump(card, open(out, 'w'), ensure_ascii=False, indent=1)
        print('事实卡片已生成', out)
    else:
        print(json.dumps(card, ensure_ascii=False, indent=1))

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else None)
