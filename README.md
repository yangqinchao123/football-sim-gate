# football-sim-gate

竞彩足球模拟盘的硬闸门 CI 仓库。目标：把 G1–G6 流程从"靠自觉"变成"云端强制"，检查不过无法合并。

## 目录
- `framework/`：硬框架 v3.2 全文 + **v3.2.1 门禁对齐补丁（当前口径，冲突以它为准）**
- `gates/`：闸门脚本（状态机、实质、空仓、出票前检查、反向回归测试）
- `lib/`：事实卡片、伤停核查、平局校准
- `calib/`：平局 isotonic 校准表
- `daily/`：每日产物（按业务日建子目录）
- `data/`：数据占位（大数据不入库，按需脚本拉取）
- `tools/`：goal 取值、预测锁定（`lock_predictions.py`）

## 目标：预测准确率（2026-11-16 校准）
门禁只服务一个目标——**方向判得准**（命中率、RPS、Brier、平局校准）。
ROI／注码／余额不参与过关判定；为提高准确率而增加的可核项优先做。

## 三道门禁（2026-11-15 起全部落地）
1. **服务端分支保护（强制点，绕不过）**：
   `required_pull_request_reviews`（PR 强制，0 审批）+ `required_status_checks: gates`（strict）
   + `enforce_admins: true` + 禁强推禁删。
   实测：直推 main 被服务端拒绝 ——
   `GH006: Protected branch update failed ... Changes must be made through a pull request`。
   **所以"建分支 → PR → CI 绿 → 合并"现在由服务端保证，不再靠自觉。**
2. **云端 CI 真判定**（`.github/workflows/gates.yml`）：
   除编译/泄密/冒烟外，新增 `Stage gate verify`：对本次变更涉及的每个批次，重算 S0..goal 全部阶段，
   任一 BLOCK 即红叉不可合并；同时检测"手标 PASS"（状态文件写 pass 但复算不过 → 直接失败）。
3. **本地 pre-push 硬门**（`tools/git_hooks/`，安装：`bash tools/git_hooks/install_hooks.sh`）：
   推送前跑编译 → 泄密检查 → 冒烟，任一不过拒绝推送（本地层，可绕，仅作加速反馈）。

## 批次配置（门禁必须对任意批次成立）
每个批次在仓库根放一个 `<batch>_config.json` 与 `<batch>_goal.json`：
```json
// D42_config.json
{"min_tq": 8, "legs": ["008", "010"], "min_picks": 1}
// D42_goal.json
{"goal_stage": "S6"}
```
- `legs`：本批真实进票腿（空数组时必须同时提交 `<batch>_无进票腿声明.md`，禁止静默空手过闸）。
- `goal_stage`：本批 PR 计划走到哪一阶段；CI 只校验到该阶段，避免"当天还没结算就永远红叉"。
- 历史版本的 `stage_gate.py` 把 `005/011/012/013/017/022/026/027` 与 `≥20 场` 写死在检查器里，
  导致任何新批次都 PASS 不了（等于闸门没生效），已改为配置驱动。

## 平台限制
- 私有免费仓库不支持服务端分支保护；本仓库为**公开**，故保护免费可用，已开启。

## 事实层：数字要能自证
- 事实卡片必须整表直出（`table` 全联盟），只给两行等于无法校验。
- 不变量：Σ胜=Σ负、Σ进球=Σ失球、场次=胜+平+负、积分=3胜+平、净胜=进-失、场次极差≤2。
- `snapshot_at` / `source_rows` / `generated_by=lib/fact_sheet.py` 缺一不可；手写卡片不认。
- 有源 CSV 时按 `snapshot_at` 重算整表逐队比对（`lib/fact_rules.py`，S4 与 `tools/fact_check.py` 同一套逻辑）。
- 回归用例：积分改 1 分、进球改 1 个、只给两行、缺日期、手写，五种都要被拦。

## CI 供应链
- action 一律 pin 到 40 位 commit SHA（`tools/check_pins.py` 强制，且带反向自测）。
- 工作流顶层 `permissions: contents: read`，不继承默认宽权限；同分支重复触发自动取消。
- `.github/dependabot.yml` 每周提 action 更新、每月提 pip 更新，人只看差异再合。

## 方向基准：融合，不是裸模型
- 实测（2627 共 1607 场，2526 选参）：收盘市场 RPS 0.1960、纯 DC 0.2068、融合 w=30% 0.1966。
  纯模型打不过市场，融合把概率质量拉回市场水平。
- `python3 tools/blend.py <批次>` 生成 `<批次>_blend.json`；方向默认取融合 `dir`。
- `S6`：方向与融合基准不一致，按改序处理，必须 evidence/falsifiable/replacement，缺一 BLOCK。
- 详见 `framework/外部对照_模型与收盘市场_20261116.md`；实验脚本 `research/blend_eval.py`。

## 平局校准：已冻结为不校准
- 2526 训练 2259 场：P(D)<0.22 每段样本均 <300，低端阶跃靠百来场撑着。
- 2627 验证 536 场：isotonic / beta / Platt / 不校准，RPS 差异 ≤0.0006，属噪声。
- 现表为恒等映射（value=key），`calib/calib_meta.json` 记恢复条件；`lib/calib_check.py` 拦截"支撑不足却大幅调值"。
- 详见 `framework/校准表结论_20261116_支撑度.md`。

## 准确率怎么看
- `ledger/direction_ledger.jsonl` 是命中率唯一数据源；`tools/ledger.py stats` 同时给"锁定方向/模型最高项/市场最高项"三栏与 RPS。
- 只有 `counted=true`（预测赛前锁定）的批次进样本；D42 因无锁定被排除，其命中率不作成绩、也不作"已修好"的证据。
- `S11` 已改为机器校验：逐场赛果结构化 + 台账齐全 + hit 可复算 + 计入样本须有市场概率，缺一 BLOCK。

## 脚本只有一份（本地不再存副本）
- 权威脚本在 `gates/`。工作目录里同名脚本是**薄封装**，只做一件事：设 `GATE_WS` 再调权威脚本。
- 判定口径不分叉：`WS = GATE_WS`，或按所在目录自动识别（`gates/` 下＝仓库根，否则＝脚本所在目录）。
- 复算历史批次：`GATE_WS=<仓库目录> python3 no_bet_gate.py D42`。
- 以前工作目录各存一份脚本，规则改在仓库、本地不动，导致本地空仓判定与 CI 不一致（D41/D42 已实证）。副本已移到 `_legacy_scripts_20261116/` 留档。

## 纪律
- 密码/密钥禁止入库，CI 与本地钩子都会拦截；探球密码只走环境变量 TQ_PW。
- 前闸不过不进下闸；独立碰撞红队无条件对立；风险点不解决不下桌。

## v3.2.1 新增的两道可核项
1. **预测锁定**：出表后跑 `python3 tools/lock_predictions.py <批次>`，写 `_meta.locked_at` + 指纹；
   S5 校验存在、S11 赛后复核指纹。**赛后改方向＝本批命中率作废。**
   D42 无锁定，按规定不计入准确率样本。
2. **空仓场必须举证**：非进票腿要么 NO_BET 且标 N 码，要么 ELIGIBLE 且写 `reject.reason + evidence`；
   只填方向的"免检方向"过不了 S5。
3. **红队不许靠 agrees 免对质**：任何条目都须 `rounds>=2` + `round1/round2`，两份产物须有 agent 署名。
4. CI 增加 `gates/regression_test.py`：12 条反向用例，漏洞改没改住由它说了算，不靠自述。
