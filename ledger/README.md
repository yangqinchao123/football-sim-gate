# 命中台账 · 准确率的唯一数据源

> 门禁能证明"流程没被绕过"，证明不了"预测准不准"。这个目录补后者。

## 文件

| 文件 | 说明 |
|---|---|
| `direction_ledger.jsonl` | 逐场记录，只追加不改写；一行一场 |
| `exclusions.json` | 不计入准确率样本的批次与原因（必须有 reason） |
| `<batch>_settlement.json`（批次根目录） | 赛果结构化记录：`nums.{场次}.score` = `H:G`，带 source 与 observed_at |

## 一条记录

```json
{"batch":"D43","num":"周日001","date":"2026-11-16","locked_at":"2026-11-16 20:05:00",
 "dir":"H","score_pred":"2-1","result":"H","score":"2:1","hit":true,
 "model":[0.52,0.27,0.21],"market":{"h":0.48,"d":0.28,"a":0.24},
 "counted":true,"exclude_reason":"","result_source":"...","filled_at":"..."}
```

## 三条硬规则

1. **hit 由脚本现算**（`tools/ledger.py validate`），文件里写好的 hit 一律不采信；对不上即 `S11` BLOCK。
2. **只有 `counted=true` 才进样本**；`counted` 要求 `_meta.locked_at` 存在且批次不在 `exclusions.json`。
   未锁定预测的批次（如 D42）方向可能是赛后补的，命中率不可核，一律排除。
3. **必须带市场概率**。`counted=true` 缺 `market.h/d/a` → S11 BLOCK：没有市场基准，"准"就没有参照物。

## 命令

```
python3 tools/ledger.py fill D42 --date 2026-11-15   # 抓 500 赛果（或 --html 离线解析）
python3 tools/ledger.py append D42                   # 结算+方向+模型/市场 → 台账
python3 tools/ledger.py validate                     # 复算 hit，查改写
python3 tools/ledger.py stats                        # 命中率 / RPS，模型 vs 市场
```

## 看数字的纪律

- 样本 <50 场不下结论，只记录；命中率差几个百分点在 7 场里等于噪音。
- 报"模型更准"必须同时给市场基准那一栏；只报自己那一栏等于没比。
- 排除批次的命中率不许拿来当成绩，也不许当"已修好"的证据。
