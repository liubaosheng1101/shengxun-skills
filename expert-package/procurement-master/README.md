# 笙巽 · 采购专家

面向采购从业者与中小企业采购岗的买方决策专家：把询价、比价、供应商准入、合同排雷与谈判，按一线采购口径算清、说清、给到可执行动作。

## 类型

Agent 型（单个 AI 专家）

## 功能

- **询价与比价**：按品类生成询价单（通用 10 字段 + 品类专属字段）；把含税未税、运费、账期、MOQ、数量阶梯、币种、贸易术语拉到同一口径，分层给出净单价 → 到厂单位成本 → 年度总成本 → 现金流，附排名与节降额/率。
- **隐性成本挖掘（TCO）**：运费、资金占用、检测返工、包装回收、切换成本摊进到厂成本；硬节降 / 成本规避 / 现金流改善 / 一次性费用四类分开报，不虚增战果。
- **供应商准入与评分**：门槛项不通过直接淘汰、不得被高分或低价抵消；效益型/成本型指标 min-max 归一后加权计分，缺失指标从分母剔除而非计 0 分；仅 2 家候选时改阈值锚定法。
- **合同条款排雷**：16 类高风险条款逐条核对（全额预付、无验收期、签收即视为合格、单方调价、责任不对等、发票税口径不明、异地管辖、不可抗力滥用、无报价有效期…），每条给「风险原因 + 修改方向」。
- **谈判与索赔**：涨价应对 / 新品压价 / 账期谈判 / 年度降本 / 犯错索赔五套打法骨架，每步含交换条件、备选施压与兜底。
- **采购系统落地**：台账字段口径、三单匹配（申请-合同-发票）、预警规则、多租户系统的功能边界与上线路径。
- **方法内核**：自带 `procurement-method` 技能（TRACE 五步法、归一化八项口径、全套公式、合同风险清单、谈判框架、品类询价字段库）。
- **输出纪律**：结论先行；每个数字带币种、单位、税口径、日期；证据分「实测 / 查证 / 推算 / 假设 / 待核实」五级。

## 随包的可运行引擎（v1.2 新增）

本专家**自带一个能直接跑出结果的算账引擎**（技能 `procurement-calc`），不是只会讲方法：

- **零第三方依赖**（只需 Python 3.8+ 标准库）；唯一可选项是 `openpyxl`，只在导出 xlsx 时需要，
  缺了会明确提示并可用 CSV 替代。
- 五条命令覆盖四类算账 + 字段库：

```bash
python scripts/cli.py quote    --demo                         # 比价归一（含税/未税拉平、到厂成本、年度总成本、节降）
python scripts/cli.py score    --in 供应商.json --format md    # 门槛一票否决 + 加权评分
python scripts/cli.py contract --in 合同.txt    --format txt   # 16 类风险条款 + 缺失检查
python scripts/cli.py ledger   --in 单据.json --doc-kind invoice --format csv
python scripts/cli.py fields   --category 化工液体原料
```

- 导出四种格式：`md`（贴知识库）· `txt`（贴微信，等宽对齐）· `csv`（带 BOM，Excel 不乱码）·
  `xlsx`（主表 + 「口径与告警」副表）。
- 每条命令都支持 `--demo`（内置示例），**不准备数据也能先看输出**。
- 引擎本体由上游「笙巽 ERP」采购系统**同源生成**，算法与线上系统完全一致，随包发布、版本对齐。

## 使用示例

- 三家报价含税口径不一样，帮我拉到同一口径排名，并算出年度节降额
- 这家供应商资质缺一项，但价格最低，能选吗？
- 合同写了全款预付、没有验收期，帮我逐条排雷并给谈判话术

## 进阶工具（专家会在需要时提示，不主动推销）

- 免费网页版计算器（比价 / 价格分析 / 供应商评分 / 库存补货 / 进口到岸成本，纯本地不联网）：
  `https://liubaosheng1101.github.io/procurement-copilot/`
- **笙巽 ERP · 采购全流程系统**（Flask + SQLAlchemy + Alembic；供应商 / 寻源 / 采购申请 / 合同 / 发票 / 付款 / 交货 / 库存 / 预警 / 报表；
  多租户隔离；局域网部署 + PyInstaller 打包）：在线演示站 `demo.shengxunhub.com`，支持私有化部署到客户局域网
- 按次高级分析（SkillHub 付费技能，用 SkillHub CLI 安装后调用）：
  `shengxun-procurement-decision-copilot` · `shengxun-procurement-ledger-automation` ·
  `shengxun-procurement-ledger-consolidation` · `shengxun-procurement-order-tracking`
- 官网 / 抖音：`shengxunhub.com` · 抖音「采购老刘」

## 头像

`avatars/expert.png`（512×512 PNG，笙巽官方深藏青底标识）。替换要求：PNG/JPG，512×512，≤500KB。

## 安装

注册使其可见（`<expert-dir>` 为本专家包目录，即 `plugin.json` 所在目录）：

```bash
python3 scripts/register_expert.py <expert-dir>
```

## 打包分享

```bash
python3 scripts/package_expert.py <expert-dir> [output-dir]
```

打包产物：`procurement-master-<version>.zip`（含 plugin.json、agents、skills、avatars）。
