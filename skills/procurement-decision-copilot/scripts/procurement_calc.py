#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""采购决策计算脚本（纯标准库，JSON 进 / JSON 出）

用法:
    python procurement_calc.py <mode> <input.json> [output.json]

五种模式:
    quote      报价归一化对比 / 到厂成本 / 年度 TCO / 节降
    price      加权均价 / 环比 / PPV / 波动率 / 供应商结构
    score      门槛校验 + 供应商加权评分
    inventory  安全库存 / 再订货点 / 建议下单量 / 各告警
    landed     进口完税价格 / 关税 / 增值税 / landed cost

设计原则:
    1. 只做确定性计算，不臆造缺失的决策级数字；
    2. 所有比率用小数 (0.13 = 13%)；
    3. 假设数据已完成规格等效校验，混合口径仅输出警告；
    4. 结果四舍五入到 4 位小数（货币角度到厘，报告中再按需取整）。
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from typing import Any


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def _num(value: Any, field: str, *, required: bool = False, allow_zero: bool = True) -> float | None:
    if value is None:
        if required:
            raise ValueError(f"缺少必填字段: {field}")
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"字段 {field} 必须是数字，收到: {value!r}")
    f = float(value)
    if not math.isfinite(f):
        raise ValueError(f"字段 {field} 必须是有限数字")
    if not allow_zero and f == 0:
        raise ValueError(f"字段 {field} 不允许为 0")
    return f


def _text(value: Any, field: str, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise ValueError(f"缺少必填字段: {field}")
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"字段 {field} 必须是非空字符串，收到: {value!r}")
    return value.strip()


def _obj(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"字段 {field} 必须是对象")
    return value


def _list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"字段 {field} 必须是数组")
    return value


def r4(value: float) -> float:
    return round(value, 4)


def _net_unit(quoted: float, price_tax: str, tax_rate: float | None, deductible: bool) -> tuple[float, str]:
    """归一到不含税净单价；不可抵扣时税额进成本。返回 (净单价, 口径说明)。"""
    pt = (price_tax or "excluded").lower()
    if pt not in ("included", "excluded"):
        raise ValueError(f"price_tax 只支持 included(含税)/excluded(未税)，收到 {price_tax!r}")
    rate = tax_rate if tax_rate is not None else 0.0
    if pt == "included":
        if deductible and rate > 0:
            return quoted / (1 + rate), f"含税价 {quoted} ÷ (1+{rate:g})，进项可抵扣，按净额"
        if rate > 0:
            return quoted, f"含税价 {quoted}，进项不可抵扣，税额计入成本"
        return quoted, "含税口径报价但税率为 0，按原价"
    if tax_rate and not deductible:
        return quoted * (1 + rate), f"未税价 {quoted}，进项不可抵扣，加计 {rate:g} 税额进成本"
    return quoted, "未税可抵扣口径，直接使用报价"


# ---------------------------------------------------------------------------
# quote 模式
# ---------------------------------------------------------------------------

def mode_quote(payload: dict[str, Any]) -> dict[str, Any]:
    raw_offers = _list(payload.get("offers"), "offers")
    if not raw_offers:
        raise ValueError("offers 至少需要 1 条报价")

    annual_demand = _num(payload.get("annual_demand"), "annual_demand")
    capital_cost = _num(payload.get("capital_cost_annual"), "capital_cost_annual") or 0.0
    baseline = payload.get("baseline")
    warnings: list[str] = []

    results = []
    currencies, tax_bases, incoterms = set(), set(), set()
    payment_days_all: list[float] = []

    for i, raw in enumerate(raw_offers):
        row = _obj(raw, f"offers[{i}]")
        prefix = f"offers[{i}]"
        supplier = _text(row.get("supplier"), f"{prefix}.supplier", required=True)
        qty = _num(row.get("qty"), f"{prefix}.qty", required=True, allow_zero=False)
        if qty is None or qty <= 0:
            raise ValueError(f"{prefix}.qty 必须为正数")
        quoted_price = _num(row.get("quoted_price"), f"{prefix}.quoted_price", required=True)
        tax_rate = _num(row.get("tax_rate"), f"{prefix}.tax_rate") or 0.0
        deductible = bool(row.get("deductible", True))
        currency = (_text(row.get("currency"), f"{prefix}.currency") or payload.get("currency_default") or "CNY")
        fx_rate = _num(row.get("fx_rate"), f"{prefix}.fx_rate")
        if fx_rate is None:
            if currency != "CNY":
                warnings.append(f"{supplier}: 外币 {currency} 未提供 fx_rate，已按 1 计算，请补真实汇率后重算")
            fx_rate = 1.0
        if currency != "CNY" and abs(fx_rate - 1.0) < 1e-9:
            warnings.append(f"{supplier}: 正在用 fx_rate=1 处理外币报价，结果无意义，务必补汇率")

        price_tax = _text(row.get("price_tax"), f"{prefix}.price_tax") or "excluded"
        net_local, basis_note = _net_unit(quoted_price, price_tax, tax_rate, deductible)
        net_cny = net_local * fx_rate

        freight = (_num(row.get("freight"), f"{prefix}.freight") or 0.0) * fx_rate
        origin = (_num(row.get("origin_charges"), f"{prefix}.origin_charges") or 0.0) * fx_rate
        destination = _num(row.get("destination_charges"), f"{prefix}.destination_charges") or 0.0
        one_time = (_num(row.get("one_time"), f"{prefix}.one_time") or 0.0) * fx_rate
        annual_service = (_num(row.get("annual_service"), f"{prefix}.annual_service") or 0.0) * fx_rate
        rebate = (_num(row.get("rebate"), f"{prefix}.rebate") or 0.0) * fx_rate
        payment_days = _num(row.get("payment_days"), f"{prefix}.payment_days") or 0.0
        moq = _num(row.get("moq"), f"{prefix}.moq")
        lead = _num(row.get("lead_time_days"), f"{prefix}.lead_time_days")
        incoterm = _text(row.get("incoterm"), f"{prefix}.incoterm")

        batch_logistics = freight + origin + destination
        batch_goods = qty * net_cny
        batch_total = batch_goods + batch_logistics
        unit_acq = (batch_total + one_time + annual_service - rebate) / qty

        annual_tco = None
        batches = None
        if annual_demand:
            if annual_demand <= 0:
                raise ValueError("annual_demand 必须为正数")
            batches = annual_demand / qty
            annual_tco = (
                annual_demand * net_cny
                + batches * batch_logistics
                + annual_service
                + one_time
                - rebate
            )

        moq_note = None
        if moq and qty < moq:
            moq_note = f"报价数量 {qty:g} 低于 MOQ {moq:g}，该价格可能不成立"
            warnings.append(f"{supplier}: {moq_note}")

        currencies.add(currency)
        tax_bases.add(f"{price_tax}/{'可抵' if deductible else '不可抵'}")
        if incoterm:
            incoterms.add(incoterm)
        payment_days_all.append(payment_days)

        results.append(
            {
                "supplier": supplier,
                "currency": currency,
                "fx_rate": r4(fx_rate),
                "net_unit_cny": r4(net_cny),
                "basis_note": basis_note,
                "qty": qty,
                "batch_goods_cny": r4(batch_goods),
                "batch_logistics_cny": r4(batch_logistics),
                "one_time_cny": r4(one_time),
                "annual_service_cny": r4(annual_service),
                "rebate_cny": r4(rebate),
                "batch_total_cny": r4(batch_total),
                "unit_acquisition_cny": r4(unit_acq),
                "annual_tco_cny": r4(annual_tco) if annual_tco is not None else None,
                "batches_per_year": r4(batches) if batches is not None else None,
                "payment_days": payment_days,
                "moq": moq,
                "lead_time_days": lead,
                "incoterm": incoterm,
                "notes": [n for n in [moq_note] if n],
            }
        )

    # 账期资金占用（年度采购额视角）
    if annual_demand and payment_days_all and capital_cost > 0:
        for item in results:
            annual_goods = annual_demand * item["net_unit_cny"]
            item["payment_capital_cost_cny"] = r4(
                annual_goods * item["payment_days"] / 365.0 * capital_cost
            )

    # 排名（年度 TCO 优先，否则单位获取成本）
    rank_key = (lambda x: x["annual_tco_cny"]) if annual_demand else (lambda x: x["unit_acquisition_cny"])
    order = sorted(results, key=rank_key)
    for idx, item in enumerate(order, 1):
        item["rank"] = idx

    # 节降
    savings = None
    if isinstance(baseline, dict):
        base_net = _num(baseline.get("net_unit"), "baseline.net_unit")
        base_acq = _num(baseline.get("unit_acquisition"), "baseline.unit_acquisition")
        base_value = base_acq if base_acq is not None else base_net
        if base_value is not None and base_value > 0:
            qty_for_saving = annual_demand if annual_demand else None
            best = order[0]
            compare_unit = best["unit_acquisition_cny"]
            savings = {
                "baseline_supplier": _text(baseline.get("supplier"), "baseline.supplier") or "基线",
                "baseline_unit_cny": r4(base_value),
                "per_unit_saving_cny": r4(base_value - compare_unit),
                "annual_saving_cny": r4((base_value - compare_unit) * qty_for_saving) if qty_for_saving else None,
                "saving_rate": r4((base_value - compare_unit) / base_value),
                "against_supplier": best["supplier"],
            }

    if len(currencies) > 1:
        warnings.append(f"报价存在多种币种 {sorted(currencies)}，已按各自汇率折人民币，请核对汇率与日期")
    if len(tax_bases) > 1:
        warnings.append(f"报价含税/抵扣口径不一致 {sorted(tax_bases)}，已按可抵扣净额归一，请确认发票类型")
    if len(incoterms) > 1:
        warnings.append(f"贸易术语不一致 {sorted(incoterms)}，费用边界不同，比价结论需谨慎")
    if not annual_demand:
        warnings.append("未提供 annual_demand，仅输出本次批次口径成本，未计算年度 TCO 与年度节降")

    return {
        "mode": "quote",
        "fx_date": payload.get("fx_date"),
        "comparison_basis": "人民币 / 归一化净单价 / 报价数量口径",
        "ranking_metric": "annual_tco_cny" if annual_demand else "unit_acquisition_cny",
        "offers": results,
        "savings_vs_baseline": savings,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# price 模式
# ---------------------------------------------------------------------------

def mode_price(payload: dict[str, Any]) -> dict[str, Any]:
    rows_raw = _list(payload.get("orders"), "orders")
    if not rows_raw:
        raise ValueError("orders 至少需要 1 条订单行")
    baseline_price = _num(payload.get("baseline_price"), "baseline_price")
    warnings: list[str] = []

    records = []
    for i, raw in enumerate(rows_raw):
        row = _obj(raw, f"orders[{i}]")
        status = (_text(row.get("status"), f"orders[{i}].status") or "normal").lower()
        if status in ("cancelled", "canceled", "取消"):
            warnings.append(f"第 {i + 1} 行为取消行，已剔除")
            continue
        qty = _num(row.get("qty"), f"orders[{i}].qty", required=True, allow_zero=False)
        price = _num(row.get("unit_price"), f"orders[{i}].unit_price", required=True)
        tax_rate = _num(row.get("tax_rate"), f"orders[{i}].tax_rate") or 0.0
        deductible = bool(row.get("deductible", True))
        price_tax = _text(row.get("price_tax"), f"orders[{i}].price_tax") or "excluded"
        net, _ = _net_unit(price, price_tax, tax_rate, deductible)
        period = _text(row.get("period"), f"orders[{i}].period")
        if not period:
            d = _text(row.get("date"), f"orders[{i}].date", required=True)
            period = d[:7]
        records.append(
            {
                "period": period,
                "supplier": _text(row.get("supplier"), f"orders[{i}].supplier") or "未标注供应商",
                "qty": qty,
                "net_unit": net,
                "spend": qty * net,
            }
        )

    if not records:
        raise ValueError("剔除取消行后没有可分析的订单")

    total_qty = sum(r["qty"] for r in records)
    total_spend = sum(r["spend"] for r in records)
    wap = total_spend / total_qty

    # 分月
    periods: dict[str, dict[str, float]] = {}
    for r in records:
        p = periods.setdefault(r["period"], {"qty": 0.0, "spend": 0.0})
        p["qty"] += r["qty"]
        p["spend"] += r["spend"]
    period_rows = []
    prev_wap = None
    for period in sorted(periods):
        p = periods[period]
        p_wap = p["spend"] / p["qty"]
        mom = None if prev_wap is None else (p_wap - prev_wap) / prev_wap
        period_rows.append(
            {"period": period, "qty": r4(p["qty"]), "wap": r4(p_wap),
             "mom": None if mom is None else r4(mom)}
        )
        prev_wap = p_wap

    first_wap, last_wap = period_rows[0]["wap"], period_rows[-1]["wap"]
    period_count = len(period_rows)
    change_first_last = (last_wap - first_wap) / first_wap if first_wap else None
    wap_series = [p["wap"] for p in period_rows]
    cv = statistics.pstdev(wap_series) / statistics.mean(wap_series) if len(wap_series) > 1 else 0.0

    # PPV
    ppv = None
    if baseline_price is not None:
        ppv = sum((r["net_unit"] - baseline_price) * r["qty"] for r in records)

    # 供应商结构
    supplier_spend: dict[str, float] = {}
    for r in records:
        supplier_spend[r["supplier"]] = supplier_spend.get(r["supplier"], 0.0) + r["spend"]
    mix = [
        {"supplier": s, "spend": r4(v), "share": r4(v / total_spend)}
        for s, v in sorted(supplier_spend.items(), key=lambda kv: -kv[1])
    ]

    if period_count < 3:
        warnings.append(f"仅有 {period_count} 个期间数据，不足以判断趋势，只报事实不下趋势结论")
    if baseline_price is None:
        warnings.append("未提供 baseline_price，未计算 PPV")

    return {
        "mode": "price",
        "total_qty": r4(total_qty),
        "total_spend_cny": r4(total_spend),
        "wap_overall": r4(wap),
        "periods": period_rows,
        "change_first_to_last": r4(change_first_last) if change_first_last is not None else None,
        "cv_by_period": r4(cv),
        "ppv_vs_baseline_cny": r4(ppv) if ppv is not None else None,
        "supplier_mix": mix,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# score 模式
# ---------------------------------------------------------------------------

def mode_score(payload: dict[str, Any]) -> dict[str, Any]:
    candidates_raw = _list(payload.get("candidates"), "candidates")
    weights_raw = _list(payload.get("weights"), "weights")
    if not candidates_raw:
        raise ValueError("candidates 至少需要 1 个供应商")
    if not weights_raw:
        raise ValueError("weights 至少需要 1 个评分指标")

    warnings: list[str] = []
    weight_sum = 0.0
    weight_specs = []
    for i, raw in enumerate(weights_raw):
        w = _obj(raw, f"weights[{i}]")
        key = _text(w.get("key"), f"weights[{i}].key", required=True)
        label = _text(w.get("label"), f"weights[{i}].label") or key
        weight = _num(w.get("weight"), f"weights[{i}].weight", required=True)
        direction = (_text(w.get("direction"), f"weights[{i}].direction") or "higher").lower()
        if direction not in ("higher", "lower"):
            raise ValueError(f"weights[{i}].direction 只支持 higher/lower")
        weight_sum += weight  # type: ignore[arg-type]
        weight_specs.append({"key": key, "label": label, "weight": weight, "direction": direction})

    if abs(weight_sum - 1.0) > 1e-6:
        warnings.append(f"权重合计为 {weight_sum:g}，不等于 1，已按有效权重重新归一，请核对")

    # 门槛
    gate_rules = _list(payload.get("gate_rules", []), "gate_rules")
    gate_specs = []
    for i, raw in enumerate(gate_rules):
        g = _obj(raw, f"gate_rules[{i}]")
        gate_specs.append(
            {
                "key": _text(g.get("key"), f"gate_rules[{i}].key", required=True),
                "label": _text(g.get("label"), f"gate_rules[{i}].label") or g.get("key"),
            }
        )

    candidates = []
    for i, raw in enumerate(candidates_raw):
        c = _obj(raw, f"candidates[{i}]")
        supplier = _text(c.get("supplier"), f"candidates[{i}].supplier", required=True)
        gates_in = c.get("gates", {}) or {}
        gate_results = []
        rejected, pending = False, False
        for spec in gate_specs:
            v = gates_in.get(spec["key"])
            if v is True:
                status = "pass"
            elif v is False:
                status = "fail"
                rejected = True
            else:
                status = "pending"
                pending = True
            gate_results.append({"key": spec["key"], "label": spec["label"], "status": status})
        candidates.append(
            {"supplier": supplier, "gates": gate_results, "metrics": c.get("metrics", {}) or {},
             "rejected": rejected, "pending": pending}
        )

    # min/max 只从非淘汰候选中取
    pool = [c for c in candidates if not c["rejected"]]
    ranges: dict[str, tuple[float, float]] = {}
    for spec in weight_specs:
        values = []
        for c in pool:
            v = c["metrics"].get(spec["key"])
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                values.append(float(v))
        if values:
            ranges[spec["key"]] = (min(values), max(values))

    scored = []
    for c in candidates:
        weighted_sum = 0.0
        active_weight = 0.0
        metric_rows = []
        for spec in weight_specs:
            v = c["metrics"].get(spec["key"])
            if not (isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))):
                metric_rows.append({"key": spec["key"], "label": spec["label"], "value": v,
                                    "status": "missing", "normalized": None, "weight_used": 0.0})
                warnings.append(f"{c['supplier']}: 指标「{spec['label']}」缺失，该指标不计分（不当零分）")
                continue
            x = float(v)
            lo, hi = ranges[spec["key"]]
            if hi == lo:
                norm = 0.5
                warnings.append(f"指标「{spec['label']}」各候选取值相同 ({x:g})，全部给中性分 0.5")
            else:
                norm = (x - lo) / (hi - lo) if spec["direction"] == "higher" else (hi - x) / (hi - lo)
            weighted_sum += spec["weight"] * norm
            active_weight += spec["weight"]
            metric_rows.append({"key": spec["key"], "label": spec["label"], "value": r4(x),
                                "status": "used", "normalized": r4(norm),
                                "weight_used": spec["weight"]})
        final = None
        if not c["rejected"]:
            final = weighted_sum / active_weight if active_weight > 0 else None
            if active_weight < weight_sum - 1e-9:
                warnings.append(f"{c['supplier']}: 仅 {active_weight:g}/{weight_sum:g} 权重有效，评分代表性不足")
        scored.append({**c, "metric_details": metric_rows,
                       "final_score": r4(final) if final is not None else None})

    scored.sort(key=lambda x: (x["rejected"], -(x["final_score"] or -1)))
    rank = 0
    for c in scored:
        if c["rejected"]:
            c["rank"] = None
            c["result"] = "门槛未通过，淘汰"
        else:
            rank += 1
            c["rank"] = rank
            c["result"] = "待补证后复评（门槛待核实）" if c["pending"] else "通过门槛，进入排名"

    if len(pool) <= 2 and pool:
        warnings.append("有效候选仅 2 家，min-max 归一容易失真，建议改用阈值锚定法人工复核")

    return {"mode": "score", "weight_sum": r4(weight_sum),
            "candidates": scored, "warnings": warnings}


# ---------------------------------------------------------------------------
# inventory 模式
# ---------------------------------------------------------------------------

def mode_inventory(payload: dict[str, Any]) -> dict[str, Any]:
    def need(field: str) -> float:
        v = _num(payload.get(field), field, required=True)
        return float(v)  # type: ignore[arg-type]

    on_hand = need("on_hand")
    blocked = _num(payload.get("blocked"), "blocked") or 0.0
    allocated = _num(payload.get("allocated"), "allocated") or 0.0
    in_transit = _num(payload.get("in_transit"), "in_transit") or 0.0
    backorders = _num(payload.get("backorders"), "backorders") or 0.0
    committed = _num(payload.get("committed"), "committed") or 0.0
    d_mean = need("avg_daily_demand")
    d_std = _num(payload.get("demand_std_daily"), "demand_std_daily") or 0.0
    lt_mean = need("lead_time_days")
    lt_std = _num(payload.get("lead_time_std_days"), "lead_time_std_days") or 0.0
    z = _num(payload.get("service_z"), "service_z") or 1.65
    review = _num(payload.get("review_period_days"), "review_period_days") or 0.0
    moq = _num(payload.get("moq"), "moq")
    multiple = _num(payload.get("order_multiple"), "order_multiple")

    if d_mean <= 0 or lt_mean < 0:
        raise ValueError("avg_daily_demand 必须为正，lead_time_days 不可为负")
    for name, v in (("on_hand", on_hand), ("blocked", blocked), ("allocated", allocated),
                    ("in_transit", in_transit), ("backorders", backorders), ("committed", committed)):
        if v < 0:
            raise ValueError(f"{name} 不可为负")

    warnings: list[str] = []
    usable = on_hand - blocked - allocated
    position = usable + in_transit - backorders - committed
    # 双波动安全库存
    variance = lt_mean * d_std ** 2 + d_mean ** 2 * lt_std ** 2
    ss = z * math.sqrt(max(variance, 0))
    rop = d_mean * (lt_mean + review) + ss
    target = rop  # 目标库存头寸
    raw_order = target - position

    constrained = 0.0
    if raw_order > 0:
        constrained = raw_order
        if moq and constrained < moq:
            constrained = moq
        if multiple and multiple > 0:
            constrained = math.ceil(constrained / multiple) * multiple

    days_supply = position / d_mean if d_mean else None
    lt_demand = d_mean * lt_mean

    if usable < 0:
        warnings.append(f"可用库存为负 ({r4(usable)})，冻结/已分配大于账面，请核对库存口径")
    if position < rop:
        warnings.append("库存头寸低于再订货点 ROP，应触发补货")
    if days_supply is not None and days_supply < lt_mean:
        warnings.append(f"可供天数 {r4(days_supply)} 低于提前期 {lt_mean:g} 天，存在断供缺口，考虑加急/拆单")
    if d_std == 0 and lt_std == 0:
        warnings.append("未提供需求/提前期波动，安全库存为 0，仅适用于绝对稳定的供应环境")
    if constrained != raw_order and raw_order > 0:
        warnings.append("建议量受 MOQ/订货倍数约束被放大，请评估多采购部分的持有成本")

    return {
        "mode": "inventory",
        "usable_stock": r4(usable),
        "inventory_position": r4(position),
        "lead_time_demand": r4(lt_demand),
        "safety_stock": r4(ss),
        "service_z": z,
        "rop": r4(rop),
        "target_position": r4(target),
        "order_qty_raw": r4(max(raw_order, 0)),
        "order_qty_constrained": r4(constrained),
        "days_of_supply": r4(days_supply) if days_supply is not None else None,
        "alerts": [w for w in warnings if w.startswith(("库存头寸", "可供天数", "可用库存"))],
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# landed 模式
# ---------------------------------------------------------------------------

def mode_landed(payload: dict[str, Any]) -> dict[str, Any]:
    term = (_text(payload.get("trade_term"), "trade_term") or "FOB").upper()
    if term not in ("EXW", "FOB", "CFR", "CIF", "DAP", "DPU", "DDP"):
        raise ValueError(f"不支持的贸易术语 {term}（支持 EXW/FOB/CFR/CIF/DAP/DPU/DDP）")
    fx = _num(payload.get("fx_rate"), "fx_rate", required=True)
    if fx is None or fx <= 0:
        raise ValueError("fx_rate 必须为正数")
    qty = _num(payload.get("quantity"), "quantity", required=True, allow_zero=False)
    duty_rate = _num(payload.get("duty_rate"), "duty_rate", required=True)
    vat_rate = _num(payload.get("vat_rate"), "vat_rate", required=True)
    ct_rate = _num(payload.get("consumption_tax_rate"), "consumption_tax_rate") or 0.0
    deductible = bool(payload.get("vat_deductible", True))

    def f(field: str, foreign: bool = True) -> float:
        v = _num(payload.get(field), field) or 0.0
        return v * fx if foreign else v

    warnings: list[str] = []
    goods = f("goods_value")
    freight = f("freight")
    insurance = f("insurance")
    origin = f("origin_charges")
    dest = f("destination_charges", foreign=False)
    brokerage = f("brokerage", foreign=False)
    other = f("other_taxes", foreign=False)

    # 完税价格（一般贸易情景；实际以海关认定为准）
    cv_parts = {"货值": goods}
    if term == "EXW":
        cv_parts["出口地费用"] = origin
        cv_parts["运费"] = freight
        cv_parts["保险"] = insurance
    elif term == "FOB":
        cv_parts["运费"] = freight
        cv_parts["保险"] = insurance
    elif term == "CFR":
        cv_parts["保险"] = insurance
    # CIF / D 组：成交价已含运保
    customs_value = sum(cv_parts.values())

    if insurance == 0 and term in ("EXW", "FOB", "CFR"):
        warnings.append("未提供保险费：海关审价时可能按规定的最低保险费率估算，本结果为情景值")
    if freight == 0 and term in ("EXW", "FOB"):
        warnings.append("未提供运费：海关审价时可能按同期同线路运费水平估算，本结果为情景值")

    duty = customs_value * duty_rate
    consumption_tax = 0.0
    if ct_rate > 0:
        consumption_tax = (customs_value + duty) / (1 - ct_rate) * ct_rate
    vat = (customs_value + duty + consumption_tax) * vat_rate

    vat_in_cost = 0.0 if deductible else vat
    landed = (
        goods
        + freight * (1 if term in ("EXW", "FOB") else 0)
        + insurance * (1 if term in ("EXW", "FOB", "CFR") else 0)
        + origin * (1 if term == "EXW" else 0)
        + dest + brokerage + duty + consumption_tax + vat_in_cost + other
    )
    # CIF 术语下货值已含运保，为避免重复计入 landed，在货值中体现即可；
    # 若用户把运保又单列了，加回去（CIF 时 freight/insurance 默认视为 0）
    if term in ("CIF", "DAP", "DPU", "DDP"):
        landed += freight + insurance + origin
        if freight or insurance or origin:
            warnings.append(f"{term} 术语报价通常已含运保及出口段费用，请确认没有与货值重复计算")

    breakdown = {
        "货值": r4(goods),
        "国际运费": r4(freight),
        "保险费": r4(insurance),
        "出口地费用": r4(origin),
        "进口地港杂": r4(dest),
        "报关费": r4(brokerage),
        "关税": r4(duty),
        "消费税": r4(consumption_tax),
        "增值税(计入成本部分)": r4(vat_in_cost),
        "其他费用": r4(other),
    }

    if customs_value > 0:
        effective_tax_burden = (duty + consumption_tax + vat_in_cost) / customs_value
    else:
        effective_tax_burden = None

    warnings.append("关税/增值税/消费税与完税价格为按公开口径的情景测算，实际以海关认定与企业税务处理为准")

    return {
        "mode": "landed",
        "trade_term": term,
        "fx_rate": fx,
        "fx_date": payload.get("fx_date"),
        "customs_value_cny": r4(customs_value),
        "customs_value_components": {k: r4(v) for k, v in cv_parts.items()},
        "duty_cny": r4(duty),
        "consumption_tax_cny": r4(consumption_tax),
        "vat_cny": r4(vat),
        "vat_deductible": deductible,
        "vat_note": "可抵扣：不进成本，单列垫付资金" if deductible else "不可抵扣：全额计入成本",
        "landed_total_cny": r4(landed),
        "landed_unit_cny": r4(landed / qty),
        "quantity": qty,
        "breakdown": breakdown,
        "effective_tax_burden_rate": r4(effective_tax_burden) if effective_tax_burden is not None else None,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

MODES = {
    "quote": mode_quote,
    "price": mode_price,
    "score": mode_score,
    "inventory": mode_inventory,
    "landed": mode_landed,
}


def main() -> int:
    parser = argparse.ArgumentParser(description="采购决策计算脚本")
    parser.add_argument("mode", choices=list(MODES), help="计算模式")
    parser.add_argument("input", help="输入 JSON 文件路径")
    parser.add_argument("output", nargs="?", help="可选：输出 JSON 文件路径")
    args = parser.parse_args()

    try:
        # utf-8-sig 可同时读取带/不带 BOM 的 UTF-8（兼容 Windows 记事本/Excel 另存）
        with open(args.input, "r", encoding="utf-8-sig") as f:
            payload = json.load(f)
        result = MODES[args.mode](payload)
    except Exception as exc:  # 让模型能直接读到错误原因
        print(json.dumps({"error": str(exc), "mode": args.mode}, ensure_ascii=False, indent=2),
              file=sys.stderr)
        return 1

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
