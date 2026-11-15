# -*- coding: utf-8 -*-
"""
平局概率校准（模型层，2026-11-09 新增）
- 背景：滚动实证（429 样本）发现 penaltyblog DC 并非整体低估平局，而是局部偏差——
  低进球/最均势区高估 P(D)，高进球/悬殊区低估 P(D)。
- 做法：用多赛季（2223–2627）赛季内滚动预测，收集 (模型 P(D), 实际是否平)，
  拟合 isotonic 校准映射，落盘为密集查找表 draw_calib_map.json，供模型脚本调用。
- 校准后重建概率：D 用校准值，剩余概率在 H/A 间按原比例归一。
- 只修正概率，不改变比分矩阵生成方式；不做全局平局基准率硬回补（实证会过头）。
"""
import csv, json, os, numpy as np
from sklearn.isotonic import IsotonicRegression
from penaltyblog.models import DixonColesGoalModel
from penaltyblog.metrics import rps_average, multiclass_brier_score

FD = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'football_data')
LEAGUES = ['E0', 'I1', 'SP1', 'D1', 'F1', 'P1', 'N1']
SEASONS = ['2223', '2324', '2425', '2526', '2627']

def collect():
    recs = []
    for L in LEAGUES:
        for S in SEASONS:
            fp = os.path.join(FD, f'{L}_{S}.csv')
            if not os.path.exists(fp):
                continue
            rows = list(csv.DictReader(open(fp, encoding='utf-8-sig')))
            for k in range(7, len(rows)):
                tr = rows[:k]
                try:
                    m = DixonColesGoalModel(
                        goals_home=[int(r['FTHG']) for r in tr],
                        goals_away=[int(r['FTAG']) for r in tr],
                        teams_home=[r['HomeTeam'] for r in tr],
                        teams_away=[r['AwayTeam'] for r in tr])
                    m.fit()
                    teams = set(x.replace('attack_', '') for x in m.get_params() if x.startswith('attack_'))
                    r = rows[k]
                    if r['HomeTeam'] not in teams or r['AwayTeam'] not in teams:
                        continue
                    p = list(m.predict(r['HomeTeam'], r['AwayTeam']).home_draw_away)
                    y = {'H': 0, 'D': 1, 'A': 2}[r['FTR']]
                    recs.append((p, y))
                except Exception:
                    continue
    return recs

if __name__ == '__main__':
    recs = collect()
    P = np.array([x[0] for x in recs]); y = np.array([x[1] for x in recs])
    print('多赛季滚动样本:', len(P), '实际平局率 %.3f' % (y == 1).mean())
    print('校准前 RPS %.4f Brier %.4f' % (rps_average(P, y), multiclass_brier_score(P, y)))

    iso = IsotonicRegression(out_of_bounds='clip')
    pd_cal = iso.fit_transform(P[:, 1], (y == 1).astype(float))
    rest = 1 - pd_cal; orig_rest = P[:, 0] + P[:, 2]
    Pc = np.column_stack([rest * P[:, 0] / orig_rest, pd_cal, rest * P[:, 2] / orig_rest])
    print('校准后 RPS %.4f Brier %.4f' % (rps_average(Pc, y), multiclass_brier_score(Pc, y)))

    # 落盘密集查找表（0.00–1.00，步长0.01）
    grid = np.round(np.arange(0, 1.001, 0.01), 2)
    table = {f'{x:.2f}': round(float(iso.predict([x])[0]), 4) for x in grid}
    json.dump(table, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'draw_calib_map.json'), 'w'),
              ensure_ascii=False, indent=1)
    print('映射已落盘 draw_calib_map.json；抽样:', {k: table[k] for k in ['0.10', '0.20', '0.25', '0.35', '0.45']})
