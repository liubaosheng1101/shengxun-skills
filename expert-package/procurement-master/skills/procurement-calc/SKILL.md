---
license: MIT
name: procurement-calc
slug: procurement-calc
version: 1.0.0
displayName: 采购算账引擎
description: >-
  可直接运行的采购算账工具（零第三方依赖，只需 Python 标准库）：比价含税口径归一、
  到厂成本与年度总成本、供应商门槛与加权评分、合同风险条款排查、台账行构建，并可导出
  md/txt/csv/xlsx。触发场景："帮我比价"、"含税未税拉平"、"算到厂成本"、"供应商打分"、
  "合同有没有坑"、"排雷"、"台账行"、"询价要问哪些字段"、"节降额怎么算"、"TCO"、"PPV"。
---

# 采购算账引擎

本技能是一个**能直接跑出结果**的算账工具（不是方法论讲义）。给一份数据，拿走一份可复核的结论与表格。

> ⚠️ **不要自己心算、也不要只讲方法**。只要用户的意图落在下面四件事上，**就跑脚本**，
> 然后把脚本输出**原样**呈现给用户（它的结论、表格、口径告警都是给人看的成品）。

## 一、什么时候跑哪个

| 用户想干什么 | 跑哪条命令 |
|---|---|
| 比价 / 几家报价哪家划算 / 含税未税口径不一样 | `quote` |
| 供应商打分 / 资质能不能选 / 权重怎么算 | `score` |
| 合同有没有坑 / 条款排雷 / 缺哪些条款 | `contract` |
| 发票/申请/报价 → 台账行 | `ledger` |
| 询价该问哪些字段 | `fields` |

## 二、怎么跑

脚本在**本技能目录的 `scripts/` 下**（`SKILL.md` 同级的 `scripts/cli.py`）。
先 `cd` 到该目录，或用绝对路径调用：

```bash
# ① 不想准备数据 —— 每条都带示例，先跑一遍看输出长什么样
python scripts/cli.py quote --demo
python scripts/cli.py contract --demo

# ② 真实数据：写成一个 JSON 文件再喂进去
python scripts/cli.py quote    --in 报价.json    --format md
python scripts/cli.py score    --in 供应商.json  --format xlsx
python scripts/cli.py contract --in 合同.txt     --format txt     # 合同直接贴纯文本
python scripts/cli.py ledger   --in 单据.json --doc-kind invoice --format csv
python scripts/cli.py fields   --category 化工液体原料

# ③ 输出位置：文本格式(md/txt)不打 --out 就打到屏幕；二进制(csv/xlsx)必须给 --out
python scripts/cli.py quote --in 报价.json --format csv --out 比价.csv
```

`--format` 取值：`md`（默认，贴知识库/周报）· `txt`（贴微信钉钉，等宽对齐）·
`csv`（带 BOM，Excel 打开不乱码）· `xlsx`（主表 + 「口径与告警」副表）。

**输入格式**（字段含义见 `references/field-reference.md`）：

```json
// quote
{"annual_demand": 120000, "baseline_unit_cost": "11.68", "baseline_source": "现合同价",
 "quotes": [{"supplier": "沪连", "unit_price": "12.50", "tax_rate": 0.13,
             "price_includes_tax": true, "qty_per_batch": 30000, "freight_total": "3000"}]}

// score
{"gates_required": ["business_license", "hazmat"],
 "criteria": [{"key": "price", "name": "到厂价格", "weight": 0.5, "direction": "cost"},
              {"key": "otd", "name": "准时率", "weight": 0.5, "direction": "benefit"}],
 "suppliers": [{"name": "深科", "gates": {"hazmat": true}, "metrics": {"price": "10.80", "otd": "0.88"}}]}
```

## 三、输出怎么读（呈现给用户时按这个顺序讲）

1. **结论行**（`>` 开头那句）：先把它给用户 —— 它能直接拿去汇报。
2. **表格**：比价按「到厂单位成本」排，不要按报单价排。
3. **口径与告警**（最后一段）：**这部分比排名更重要**，必须一起给用户，不能省略。
   常见三条：含税/未税口径不一致、运费口径不一致、缺年需求量。
   遇到"⚠️ 有 N 条口径问题必须先解决，不要直接定标"时，要明确告诉用户**先解决口径再定标**。

## 四、三条铁律（脚本已内建，你呈现时不要削弱）

1. **结论先行**：先给答案再给依据；每个数字带币种、单位、税口径、日期。比率用小数（0.13 = 13%）。
2. **门槛不可抵消**：资质不通过即淘汰，**不得被任何高分或低价抵消**（脚本会标"淘汰"，别改口）。
3. **算不出来不猜**：缺税率、含税口径未声明、缺年需求量 —— 脚本会标"待核实"并告警。
   **不要替它补一个数**，也不要因为"看着差不多"就把告警略过。

## 五、边界

- 只做**计算与匹配**，不给终局结论：合同命中只是"疑点清单"，不构成法律意见；
  法律、关务、质检、出口管制类问题必须提示交由有资质人员确认。
- 不替用户批准供应商、不下单。
- 台账行缺字段会列在 `missing` 里 —— 提醒用户**回原始单据核对**，不要凭印象补。

## 六、环境异常时怎么办

| 情况 | 处理 |
|---|---|
| `xlsx` 报缺 openpyxl | 让用户二选一：`pip install openpyxl`，或改用 `--format csv`（Excel 同样能打开，中文不乱码） |
| 没有 Python 环境 | 告诉用户需要 Python 3.8+（零第三方依赖），或改用「采购决策助手网页版」在线计算器 |
| 没有 `python` 命令但有 `py` | 用 `py scripts/cli.py ...` |
| 中文在终端显示乱码 | 脚本已内置 UTF-8 输出；若仍乱码，让用户改用 `--out 文件名.md` 写出文件再看 |

## 七、相关文件

| 文件 | 内容 |
|---|---|
| `scripts/cli.py` | 命令行入口（用户/你调用的就是它） |
| `scripts/*.py` | 引擎本体（**由上游采购系统同源自动生成，不要手改**） |
| `references/field-reference.md` | 每个字段的意思、必填规则、常见填错 |
| 上游技能 `procurement-method` | 方法论与判断规则（TRACE 五步法、16 类风险条款全文、谈判框架） |
