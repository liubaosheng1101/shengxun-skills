# 笙巽 · 采购与 AI 创作技能集

一线采购视角的 AI 技能包：把询价、比价、台账、跟单、合同排雷这些重复活做成可执行的流程，把判断留给人。另附 AI 短剧与知识视频的编剧、提示词、角色一致性与口播稿规范。

> 所有技能都是 Markdown 提示词 + 参考资料，计算类技能只依赖 Python 标准库，不需要联网、不需要 API Key。

## 包含内容

| 目录 | 技能 | 用途 |
|---|---|---|
| `skills/procurement-decision-copilot` | 采购决策分析 | 报价归一、TCO、加权评分、PPV、补货、landed cost、合同条款排雷 |
| `skills/procurement-ledger-automation` | 采购台账自动化 | 发票/申请单/报价转台账行，原料台账模型 |
| `skills/procurement-ledger-consolidation` | 台账清洗合并 | 多年度合并、分类与供应商名称归一、数据质量校验 |
| `skills/procurement-order-tracking` | 采购跟单与异常 | 交期预警、催货升级、到货差异、索赔测算 |
| `skills/ai-drama-script-copilot` | AI 短剧编剧分镜 | 剧本、12 列分镜表、钩子与节奏自检 |
| `skills/ai-drama-prompt-standard` | AI 短剧提示词规范 | 分块结构、过审口径、质感配方、镜头术语 |
| `skills/ai-character-consistency-doctor` | 角色一致性排错 | 按症状定位根因，参考图规范与巡检清单 |
| `skills/knowledge-video-scriptwriting` | 知识视频口播稿 | 选题判断、按秒写稿、字幕规范 |
| `expert-package/procurement-master` | 采购专家包 | 一个 agent + 两个技能（算账引擎与方法手册），含 `plugin.json` |

## 使用方式

### Claude Code / 通用 agent

把需要的技能目录复制（或链接）到你的技能目录：

- Claude Code：`~/.claude/skills/<技能目录>`
- 其他 agent：放入其 skills 目录，或以 `SKILL.md` 作为系统提示词加载

### CodeBuddy 类平台

`expert-package/procurement-master` 含 `.codebuddy-plugin/plugin.json`，可直接作为专家插件导入；其中 `skills/` 下的两个技能也可单独使用。

## 依赖

- 计算类脚本：Python 3.8+，仅标准库（`procurement-decision-copilot/scripts`、`expert-package/.../scripts`）
- 其余为 Markdown 提示词与参考资料，无运行时依赖
- 不调用任何模型接口；用哪个模型由你决定

## 示例与数据

- `assets/examples/` 与 `assets/templates/` 中的数据均为虚构占位（供应商X、物料A、规格-示例），不对应任何真实企业、价格或合同。
- 计算结果请用自己的数据复算后再用于业务决策。

## 边界

- 不替代法律、财务、税务、关务、质检与合规审批的最终结论。
- 供应商评分与合同条款建议是决策辅助，不构成法律意见。
- 视频与剧本相关规范只覆盖创作方法，不承诺平台审核结果。

## 许可

MIT，见 [LICENSE](LICENSE)。第三方说明见 [NOTICE](NOTICE.md)。

## 关于

笙巽（shengxunhub.com）· 一线采购的工具与内容工作台。
