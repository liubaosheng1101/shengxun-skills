
# 本文件由「采购专家引擎」自动生成，请勿手改。
# 算法与系统内完全一致（同源生成）；如需调整，请修改上游引擎后重新生成。

"""比价归一化：把"看着不可比"的报价拉到同一口径。

═══ 算账链条（KB1 的 A 步）═══

    纸面报价 → 净单价 → 到厂单位成本 → 年度总成本 → 节降额/率

① 净单价（含税口径归一，比率用小数）
   · 含税价且进项**可抵扣** → 净单价 = 含税价 ÷ (1 + 税率)
   · 含税价但**不可抵扣**   → 净单价 = 含税价（税额进成本）
   · 未税价                → 净单价 = 报价单价
② 单位运费 = 运费总额 ÷ 报价数量
③ 到厂单位成本 = 净单价 + 单位运费
④ 年批次数 = 年需求量 ÷ 每批数量
   年度总获取成本 = 年需求量 × 净单价 + 年批次数 × 每批运费
   ⚠️ **只含发票价加运费的结果不得称为 TCO** —— 对外一律叫"年度总获取成本"。
⑤ 节降额 = (基线单位成本 − 最优单位成本) × 口径数量；节降率 = 差值 ÷ 基线单位成本

═══ 为什么必须有"口径告警" ═══

实测最容易出事的不是算错，而是**比的根本不是一件事**：
一家报含税、一家报未税；一家含运费、一家不含；报价已过期。
所以只要同一次比价里口径不一致，就必须告警 —— 分数再漂亮也不能直接定标。
"""
from __future__ import annotations

from decimal import Decimal

from _errors import BusinessError
from _money import fmt, normalize_tax_rate, q2, to_decimal

__all__ = ["normalize_quotes", "NormalizeError"]


class NormalizeError(BusinessError):
    """比价输入不合法 → 400。

    状态码来自**异常类型**，不靠消息文本匹配（沿用「异常类型决定状态码」的既有铁律：
    消息里出现"不存在"三个字曾经把状态码翻成 404）。
    """

    code = "expert_quote_invalid"


def _row(quote: dict, idx: int) -> dict:
    """把一条报价算成一行可比数据。缺什么就标什么，不猜。"""
    name = str(quote.get("supplier") or f"报价{idx + 1}").strip()

    unit_price = to_decimal(quote.get("unit_price"))
    if unit_price <= 0:
        raise NormalizeError(f"{name}：报单价必须大于 0")

    tax_rate_raw = quote.get("tax_rate", None)
    price_includes_tax = bool(quote.get("price_includes_tax", False))
    deductible = quote.get("tax_deductible", True)

    basis = ["实测"] if quote.get("unit_price") is not None else ["待核实"]
    alerts: list[str] = []

    # ① 净单价
    if tax_rate_raw in (None, ""):
        if price_includes_tax:
            raise NormalizeError(f"{name}：报的是含税价，必须给税率才能算净单价")
        net = q2(unit_price)
        tax_note = "未涉税报价"
        basis.append("推算")
    else:
        rate = normalize_tax_rate(tax_rate_raw)
        if price_includes_tax:
            if deductible:
                net = q2(unit_price / (Decimal("1") + rate))
                tax_note = f"含税 ÷ (1+{rate}) → 可抵扣"
            else:
                net = q2(unit_price)
                tax_note = "含税且不可抵扣 → 税额进成本"
                alerts.append("该家进项不可抵扣，成本口径与其他家不同")
        else:
            net = q2(unit_price)
            tax_note = "未税价直接采用"
        basis.append("推算")

    # ② 单位运费
    freight_total = to_decimal(quote.get("freight_total", 0))
    qty = to_decimal(quote.get("qty_per_batch", quote.get("qty", 0)))
    if qty <= 0:
        unit_freight = q2(Decimal("0"))
        alerts.append("未给报价数量，单位运费按 0 计 —— 若有运费会低估到厂成本")
        basis.append("待核实")
    else:
        unit_freight = q2(freight_total / qty)
    landed = q2(net + unit_freight)

    # ③ 年度总获取成本（给了年需求量才算）
    annual_demand = to_decimal(quote.get("annual_demand", 0))
    batch_count = quote.get("batch_count", None)
    annual_total = None
    if annual_demand > 0:
        if batch_count in (None, "", 0):
            if qty > 0:
                batch_count = int((annual_demand / qty).to_integral_value(rounding="ROUND_CEILING"))
            else:
                batch_count = None
        if batch_count:
            annual_total = q2(annual_demand * net + Decimal(int(batch_count)) * freight_total)
        else:
            alerts.append("给了年需求量但算不出批次数（缺每批数量）")
    else:
        alerts.append("未给年需求量 —— 只能比到厂单位成本，给不出年度口径")

    return {
        "supplier": name,
        "unit_price": unit_price,
        "price_includes_tax": price_includes_tax,
        "tax_rate": (normalize_tax_rate(tax_rate_raw) if tax_rate_raw not in (None, "") else None),
        "tax_note": tax_note,
        "net_unit_price": net,
        "freight_total": freight_total,
        "qty_per_batch": qty,
        "unit_freight": unit_freight,
        "landed_unit_cost": landed,
        "batch_count": int(batch_count) if batch_count else None,
        "annual_total": annual_total,
        "basis": sorted(set(basis)),
        "alerts": alerts,
        "note": str(quote.get("note") or ""),
    }


def normalize_quotes(payload: dict) -> dict:
    """主入口：不抛异常就用返回值表达一切判断。"""
    quotes = payload.get("quotes") or []
    if not isinstance(quotes, list) or not quotes:
        raise NormalizeError("至少要有 1 条报价")
    if len(quotes) > 50:
        raise NormalizeError("一次最多比 50 条报价")

    annual_demand = to_decimal(payload.get("annual_demand", 0))
    # 年需求量可写在头部，下发给每一条
    norm: list[dict] = []
    for i, qt in enumerate(quotes):
        if not isinstance(qt, dict):
            raise NormalizeError(f"第 {i + 1} 条报价必须是对象")
        merged = dict(qt)
        if annual_demand > 0 and not merged.get("annual_demand"):
            merged["annual_demand"] = annual_demand
        norm.append(_row(merged, i))

    # ── 排名：先按门槛外的口径比（到厂单位成本），有年度口径则同时给
    ranked = sorted(norm, key=lambda r: r["landed_unit_cost"])
    for pos, r in enumerate(ranked, 1):
        r["rank_by_landed"] = pos

    # ── 口径一致性告警（这是本模块最值钱的部分）
    head_alerts: list[str] = []
    tax_flags = {r["price_includes_tax"] for r in norm}
    if len(tax_flags) > 1:
        head_alerts.append("含税/未税口径不一致：部分家报含税、部分家报未税 —— 已按各自的税口径归一，请复核税率是否填对")
    if any(r["tax_rate"] is None and r["price_includes_tax"] for r in norm):
        head_alerts.append("有报价标了含税但没给税率，无法归一")
    has_freight = [r["unit_freight"] > 0 for r in norm]
    if any(has_freight) and not all(has_freight):
        head_alerts.append("运费口径不一致：部分家含运费、部分家未计入 —— 未计入的到厂成本会被低估")
    if not any(r["annual_total"] is not None for r in norm):
        head_alerts.append("缺少年需求量，本次只给出到厂单位成本口径，未给年度总获取成本")

    # ── 节降
    savings = None
    baseline_raw = payload.get("baseline_unit_cost", None)
    if baseline_raw not in (None, "") and ranked:
        base = to_decimal(baseline_raw)
        best = ranked[0]["landed_unit_cost"]
        unit_gap = q2(base - best)
        savings = {
            "baseline_unit_cost": base,
            "baseline_source": str(payload.get("baseline_source") or "未声明"),
            "best_unit_cost": best,
            "unit_gap": unit_gap,
            "rate": (float(q2(unit_gap / base * 100)) if base > 0 else None),
            "note": "基线来源必须声明（上次成交价/现合同价/预算价/行情基线）；"
                    "硬节降、成本规避、现金流改善、一次性费用**不得混报**",
        }
        if not payload.get("baseline_source"):
            head_alerts.append("给了比较基线但没声明基线来源 —— 节降额不能这么报")

    # ── 计算前对账（KB1：异常行登记不得静默丢弃）
    reconcile = {
        "quote_count": len(norm),
        "dropped": 0,
        "with_annual": sum(1 for r in norm if r["annual_total"] is not None),
        "rule": "异常行一律登记在 alerts 里，不静默丢弃；报告须写明分母口径",
    }

    return {
        "ok": True,
        "basis": ["推算"],
        "unit_hint": "金额单位与输入报价同一单位（元），比率为小数",
        "annual_demand": annual_demand if annual_demand > 0 else None,
        "rows": norm,
        "ranking": [r["supplier"] for r in ranked],
        "best": ranked[0]["supplier"] if ranked else None,
        "savings": savings,
        "alerts": head_alerts,
        "reconcile": reconcile,
        "conclusion": _conclude(ranked, head_alerts, savings),
    }


def _conclude(ranked: list[dict], alerts: list[str], savings: dict | None) -> str:
    """结论先行：一句话能直接拿去汇报。"""
    if not ranked:
        return "没有可比数据"
    best = ranked[0]
    line = (f"按到厂单位成本，**{best['supplier']}** 最低（{fmt(best['landed_unit_cost'])}/单位）"
            f"；次优 {ranked[1]['supplier']} {fmt(ranked[1]['landed_unit_cost'])}"
            if len(ranked) > 1 else
            f"只有 1 家可比：{best['supplier']} 到厂 {fmt(best['landed_unit_cost'])}/单位")
    if savings and savings.get("rate") is not None:
        line += f"；比基线（{fmt(savings['baseline_unit_cost'])}）每单位省 {fmt(savings['unit_gap'])}，降幅 {savings['rate']}%"
    if alerts:
        line += f"。⚠️ 但有 {len(alerts)} 条口径问题必须先解决，**不要直接定标**"
    return line
