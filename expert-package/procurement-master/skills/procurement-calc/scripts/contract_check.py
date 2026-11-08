
# 本文件由「采购专家引擎」自动生成，请勿手改。
# 算法与系统内完全一致（同源生成）；如需调整，请修改上游引擎后重新生成。

"""合同条款排雷：16 类高风险条款 + 关键条款缺失检查。

═══ ⚠️ 先说清楚这个模块的边界 ═══

它是**关键词匹配**，给的是「**需要人工复核的疑点**」，不是法律结论。
命中 ≠ 条款无效；没命中 ≠ 没问题（可能换了个说法）。
所以输出里必须有 `disclaimer`，呈现层也要照原样显示 —— 这是证据分级的一部分。

═══ 两类检查，缺一不可 ═══

① **命中**：文本里出现高风险措辞（如"全额预付""签收即视为合格"）→ 高风险
② **缺失**：合同里**应有而没有**关键条款（如全文找不到"验收""质保"）→ 这是更隐蔽的坑，
   实测里"预付100% + 全款到账后发货"就是靠 ① 命中 pay_full_prepay、同时 ② missing 里带出"验收"。

条款库与修改方向来自作者方法论（`kb3-contract-rules.md`），不是从任何第三方合同模板抄的。
"""
from __future__ import annotations

import re

__all__ = ["check_contract", "TERMS", "COMBOS", "REQUIRED_CLAUSES"]

# ── 16 类高风险条款：(check_id, 名称, 等级, 触发词, 风险原因, 修改方向)
TERMS: tuple[tuple[str, str, str, tuple[str, ...], str, str], ...] = (
    ("pay_full_prepay", "全额预付", "高",
     ("全额预付", "预付100%", "100%预付", "全款预付", "款到发货", "全款到账后发货", "先款后货"),
     "全额预付造成资金占用，供方逾期/破产时无追偿保障。",
     "预付比例降至 30% 以内并与发货/验收节点挂钩；大额预付要求银行保函或履约担保；留 5%–10% 质保金。"),
    ("pay_no_terms", "付款进度不清", "中",
     ("一次性支付", "见票付款", "货到付款", "款到即发"),
     "付款进度与发票前提不清，易产生先款后票争议。",
     "约定「验收合格 + 收到合规发票后 X 日内付款」，分列预付/到货款/质保金。"),
    ("breach_one_sided", "责任不对等", "高",
     ("买方承担全部", "概不负责", "不承担任何责任", "买方单方"),
     "责任不对等，供方逾期交货等无约束。",
     "增加双向违约条款；逾期交货按日付违约金（如 0.05%/日），逾期超 X 天买方有权解除并索赔。"),
    ("breach_no_late_delivery", "缺逾期交货违约金", "高",
     ("不可抗力外", "逾期免责", "交货期可顺延"),
     "供方逾期无成本，交期无保障。",
     "增加逾期交货违约金条款及解约权。"),
    ("accept_no_period", "未约定检验期", "高",
     ("当场验收", "立即验收", "即视为验收合格"),
     "未约定检验期，质量问题举证困难。",
     "约定到货检验期与书面质量异议期（如 7 日/30 日）；异议期内未验收不视为放弃权利。"),
    ("accept_deemed", "签收即视为合格", "高",
     ("签收即视为合格", "验收合格后不得提出异议", "视为完全符合", "不得再提出异议"),
     "签收时无法完成内在质量检验，直接视为合格剥夺异议权。",
     "改为「外观、数量当场清点；内在质量以化验/使用验收为准，异议期内可提出」。"),
    ("warranty_none", "无质保条款", "高",
     ("不提供质保", "无质量保证", "质保期为零"),
     "质保期内故障无依据索赔。",
     "约定质保期（如验收合格起 12 个月）及质保期内免费维修/更换/退货责任。"),
    ("warranty_short", "质保期过短", "中",
     ("质保期7天", "质保期15天", "质保期30天", "质保一个月"),
     "质保期短于合理使用周期，风险转嫁买方。",
     "按行业惯例延长质保期；或约定质保期内故障免费更换并重算质保。"),
    ("jur_supplier_court", "供方所在地管辖", "中",
     ("供方所在地法院", "卖方所在地法院", "由供方所在地", "由卖方所在地"),
     "异地诉讼增加维权成本。",
     "改为合同签订地/买方所在地法院，或约定原告所在地法院。"),
    ("jur_arbitration_unclear", "仲裁机构约定不明", "中",
     ("仲裁机构另行商定", "由有关仲裁机构", "提交仲裁解决"),
     "仲裁机构约定不明可能导致条款无效。",
     "明确唯一仲裁机构全称及仲裁地，或改为法院诉讼。"),
    ("inv_no_type", "发票类型/税率不清", "中",
     ("开具发票", "提供发票"),
     "发票类型/税率不清影响价格与抵扣。",
     "写明发票种类（增值税专用发票）、税率、开具时间。"),
    ("inv_price_tax_unclear", "含税口径不明", "中",
     ("价格另议", "含税与否另定", "税费另计"),
     "含税口径不明，事后就税款承担扯皮。",
     "明确单价为含税价还是未税价，税款由谁承担。"),
    ("fm_none", "无不可抗力条款", "中",
     (),
     "突发事件下双方责任义务不清。",
     "增加标准不可抗力条款，含通知、证明、减损与解约规则。"),
    ("fm_supply_only", "不可抗力被滥用", "高",
     ("缺货不属于违约", "涨价不属违约", "市场波动免责", "原材料上涨不承担责任"),
     "商业风险（缺货/涨价）不属于法定不可抗力，等于免除供方基本供货责任。",
     "删除该约定；改为通过安全库存、备选供应商、价格调整公式分配市场风险。"),
    ("price_unilateral_up", "供方单方调价", "高",
     ("供方有权调整价格", "单方调整价格", "价格随行就市", "如有调整另行通知"),
     "锁量不锁价，预算完全失控。",
     "删除单方调价权；如需调价，约定双方书面确认 + 客观指数公式（如挂钩基准价变动幅度）与上限。"),
    ("price_lock_none", "无报价有效期", "中",
     ("随行就市", "以当日价格为准", "价格以发货日为准"),
     "无报价有效期，下单时价格可能已变。",
     "约定报价有效期（如 30 日），有效期内不得涨价。"),
)

# ── 组合规则：(check_id, 名称, 等级, 关键词组（组内 OR、组间 AND）, 风险原因, 修改方向) ──
# 为什么需要它：单一关键词表太脆 —— 合同不会正好写"原材料上涨不承担责任"，
# 常见写法是「因原材料价格上涨/市场缺货…供方不承担违约责任」。
# 对这类"语义由两个成分共同决定"的风险，必须用「组内 OR、组间 AND」匹配，
# 否则换个说法就漏检 —— 而**漏检比误报危险得多**（误报最多多点一次复核）。
COMBOS: tuple[tuple[str, str, str, tuple[tuple[str, ...], ...], str, str], ...] = (
    ("fm_supply_only", "不可抗力被滥用", "高",
     (("缺货", "涨价", "原材料上涨", "原材料价格", "市场波动", "市场价格变动", "行情变化"),
      ("不承担", "免责", "不属违约", "不属于违约", "不视为违约", "概不负责")),
     "商业风险（缺货/涨价）不属于法定不可抗力，等于免除供方基本供货责任。",
     "删除该约定；改为通过安全库存、备选供应商、价格调整公式分配市场风险。"),
    ("breach_no_late_delivery", "逾期免责 / 无交期约束", "高",
     (("逾期", "延期", "交货期", "交期", "发货"),
      ("免责", "不承担", "不视为违约", "不予追究", "顺延", "另行协商")),
     "供方逾期无成本，交期无保障。",
     "增加逾期交货违约金条款及解约权（如 0.05%/日，超 X 天可解约并索赔）。"),
    ("accept_deemed", "签收即视为合格", "高",
     (("签收", "收货", "到货"),
      ("即视为", "视为合格", "视为验收", "视为完全符合")),
     "签收时无法完成内在质量检验，直接视为合格剥夺异议权。",
     "改为「外观、数量当场清点；内在质量以化验/使用验收为准，异议期内可提出」。"),
    ("price_unilateral_up", "供方单方调价", "高",
     (("供方", "卖方", "乙方"),
      ("有权调整", "单方调整", "另行通知", "随行就市", "以当日价格", "以发货日")),
     "锁量不锁价，预算完全失控。",
     "删除单方调价权；如需调价，约定双方书面确认 + 客观指数公式与上限。"),
    ("jur_supplier_court", "供方所在地管辖", "中",
     (("管辖", "诉讼", "法院"),
      ("供方所在", "卖方所在", "乙方所在", "对方所在")),
     "异地诉讼增加维权成本。",
     "改为合同签订地/买方所在地法院，或约定原告所在地法院。"),
)


def _match_groups(norm: str, groups: tuple[tuple[str, ...], ...]) -> list[str]:
    """组内 OR、组间 AND。返回命中的措辞（每组的第一个命中词），未全中则返回空。"""
    picked: list[str] = []
    for g in groups:
        hit = next((w for w in g if w in norm), None)
        if hit is None:
            return []
        picked.append(hit)
    return picked


# ── 应有而未见的条款：(key, 名称, 该条款存在的判别词, 为什么重要)
REQUIRED_CLAUSES: tuple[tuple[str, str, tuple[str, ...], str], ...] = (
    ("acceptance", "验收条款", ("验收", "检验", "化验", "开箱", "抽检", "复检"),
     "没有验收条款 = 到货后质量争议无依据（本次方法论的实测基线里，"
     "「预付100% + 全款到账后发货」这一条就同时带出了『验收』缺失）"),
    ("quality", "质量标准", ("标准号", "GB", "HG/T", "执行标准", "技术协议", "规格型号", "纯度", "含水"),
     "没有质量标准 = 交付物可比性缺失，验收也无标可依"),
    ("warranty", "质保条款", ("质保", "保修", "三包", "免费更换"),
     "没有质保 = 质保期内故障无法索赔"),
    ("breach", "违约责任", ("违约", "违约金", "赔偿", "逾期"),
     "没有违约责任 = 对方逾期或不合格不承担成本"),
    ("invoice", "发票条款", ("发票", "增值税", "税率", "专票", "普票"),
     "没有发票条款 = 税口径与抵扣时点都可能扯皮"),
    ("delivery", "交货期", ("交货期", "交期", "到货时间", "工期", "发货日", "交货时间"),
     "没有交货期 = 无法计算逾期、无法启动违约金"),
    ("jurisdiction", "争议解决", ("管辖", "仲裁", "诉讼", "法院"),
     "没有争议解决条款 = 维权路径不明，异地成本高"),
    ("force_majeure", "不可抗力", ("不可抗力", "地震", "战争", "疫情", "政府行为"),
     "没有不可抗力 = 突发事件责任不清；但也要防止被滥用为商业风险的挡箭牌"),
)


def _prune_terms(seen: list[str]) -> list[str]:
    """去重保序 + **去掉被其他命中词包含的短词**。

    为什么必须在"合并之后"再调一次：同一条风险可能同时被单一关键词表与组合规则命中，
    合并时用 set 求并集会把 "单方调整" 与 "单方调整价格" 一起留下 ——
    只在匹配函数里清洗是不够的（这个坑实测踩过一次）。
    """
    uniq: list[str] = []
    for w in seen:
        if w and w not in uniq:
            uniq.append(w)
    return [w for w in uniq if not any(w != o and w in o for o in uniq)]


def _hit(text: str, words: tuple[str, ...]) -> list[str]:
    """返回命中的原文词（清洗见 `_prune_terms`）。"""
    return _prune_terms([w for w in words if w and w in text])


def check_contract(payload: dict) -> dict:
    text = str(payload.get("text") or "").strip()
    if not text:
        raise ValueError("请粘贴合同文本（可只贴关键条款）")
    if len(text) > 200_000:
        raise ValueError("文本过长（>20 万字），请只贴关键条款部分")

    norm = re.sub(r"\s+", "", text)

    hits: list[dict] = []
    for cid, name, level, words, risk, fix in TERMS:
        matched = _hit(norm, words)
        if matched:
            hits.append({
                "check_id": cid, "name": name, "level": level,
                "matched": matched, "risk": risk, "fix": fix,
                "basis": "实测",   # 原文命中
            })
    # 空触发词的条款（如 fm_none）改为"缺失型"：交给下面的 missing 判断
    hits = [h for h in hits if h["matched"]]

    # ② 组合规则（组内 OR、组间 AND）：覆盖"换个说法就漏检"的语义型风险
    by_id = {h["check_id"]: h for h in hits}
    for cid, name, level, groups, risk, fix in COMBOS:
        matched = _match_groups(norm, groups)
        if not matched:
            continue
        if cid in by_id:
            # 命中同一个 check_id：合并措辞，保留更高的等级
            by_id[cid]["matched"] = _prune_terms(by_id[cid]["matched"] + matched)
            if level == "高":
                by_id[cid]["level"] = "高"
        else:
            by_id[cid] = {"check_id": cid, "name": name, "level": level,
                          "matched": matched, "risk": risk, "fix": fix, "basis": "实测"}
    hits = sorted(by_id.values(), key=lambda h: (h["level"] != "高", h["check_id"]))

    missing: list[dict] = []
    for key, name, words, why in REQUIRED_CLAUSES:
        if not _hit(norm, words):
            missing.append({"key": key, "name": name, "why": why, "basis": "待核实"})

    high = [h for h in hits if h["level"] == "高"]
    alerts: list[str] = []
    if missing:
        alerts.append(f"有 {len(missing)} 项应有条款未在文本中出现："
                      f"{'、'.join(m['name'] for m in missing)}")
        alerts.append("若合同确实没有这些条款，签署前应要求补充 —— "
                      "「缺失」比「写错」更隐蔽，但也更难在事后补救")
    if not hits and not missing:
        alerts.append("未命中任何已知风险措辞、也未发现明显缺失 —— "
                      "**这不等于合同没问题**，只是没被本规则库覆盖")

    pay_hit = next((h for h in hits if h["check_id"] == "pay_full_prepay"), None)
    accept_missing = any(m["key"] == "acceptance" for m in missing)
    conclusion = (
        f"命中 {len(hits)} 条风险条款（高风险 {len(high)} 条），另有 {len(missing)} 项关键条款缺失"
        if (hits or missing) else "未发现已知风险项（不代表无风险）"
    )
    if pay_hit and accept_missing:
        conclusion += "。⚠️ 「全额预付 + 无验收条款」是最危险的组合：钱先付了、质量还没有抓手"

    return {
        "ok": True,
        "check_count": len(TERMS),
        "hits": hits,
        "missing": missing,
        "high_count": len(high),
        "alerts": alerts,
        "conclusion": conclusion,
        "disclaimer": "本结果为**关键词匹配出的疑点清单**，供人工复核使用，"
                      "不构成法律意见；命中不等于条款无效，未命中不等于没有风险。"
                      "法律、关务、质检类问题请交由有资质人员确认。",
    }
