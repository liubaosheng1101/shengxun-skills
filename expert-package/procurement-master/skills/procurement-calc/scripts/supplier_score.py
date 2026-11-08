
# 本文件由「采购专家引擎」自动生成，请勿手改。
# 算法与系统内完全一致（同源生成）；如需调整，请修改上游引擎后重新生成。

"""供应商准入与评分：门槛一票否决 + min-max 加权。

═══ 铁律：门槛不可被高分或低价抵消 ═══

门槛项（强制认证、营业执照、环保安全、危化资质、强制准入）三态：
    true  → 通过
    false → **淘汰**（不进排名，哪怕它最便宜）
    缺省  → **待核实**（先决条件：补证后可批，不是淘汰，也不能当通过）

═══ 加权算法（KB1）═══

    效益型（越高越好）：归一化 = (x − min) ÷ (max − min)
    成本型（越低越好）：归一化 = (max − x) ÷ (max − min)
    总分 = Σ(权重 × 归一化得分) ÷ Σ(有效权重)

四条修正（都是实测会坑人的地方）：
  ① 缺失值**不当 0 分** —— 该指标权重从分母剔除后重新归一（否则"没提供"被算成"最差"）
  ② max == min 时全部给 0.5 中性分（否则 0/0）
  ③ 候选只有 2 家时 min-max 极易失真 → 返回告警，建议改阈值锚定法人工复核
  ④ 权重合计必须为 1（不满足直接报错，不"自动凑"）
"""
from __future__ import annotations

from decimal import Decimal

from _errors import BusinessError
from _money import q2, to_decimal

__all__ = ["score_suppliers", "ScoreError"]


class ScoreError(BusinessError):
    """评分输入不合法 → 400（状态码来自异常类型）。"""

    code = "expert_score_invalid"


def _norm_value(x: Decimal, lo: Decimal, hi: Decimal, direction: str) -> Decimal:
    if hi == lo:
        return Decimal("0.5")  # ② 中性分
    if direction == "cost":
        return (hi - x) / (hi - lo)
    return (x - lo) / (hi - lo)


def score_suppliers(payload: dict) -> dict:
    criteria = payload.get("criteria") or []
    suppliers = payload.get("suppliers") or []
    gates_required = list(payload.get("gates_required") or [])

    if not criteria:
        raise ScoreError("至少要给 1 个评分指标")
    if not suppliers:
        raise ScoreError("至少要给 1 家候选供应商")

    # ④ 权重合计 = 1
    total_w = sum((to_decimal(c.get("weight")) for c in criteria), Decimal("0"))
    if abs(total_w - Decimal("1")) > Decimal("0.000005"):
        raise ScoreError(f"权重合计必须等于 1，当前为 {total_w}（不自动凑，请自行归一）")

    for c in criteria:
        if c.get("direction") not in ("benefit", "cost"):
            raise ScoreError(f"指标 {c.get('key')} 的 direction 必须是 benefit 或 cost")

    # ── 门槛判定（独立于分数，先做）
    verdicts: dict[str, dict] = {}
    for s in suppliers:
        name = str(s.get("name") or "").strip() or "未命名"
        gates = s.get("gates") or {}
        failed, pending = [], []
        for g in gates_required:
            v = gates.get(g, None)
            if v is False:
                failed.append(g)
            elif v is None:
                pending.append(g)
        verdict = "淘汰" if failed else ("待核实" if pending else "通过")
        verdicts[name] = {
            "verdict": verdict,
            "failed_gates": failed,
            "pending_gates": pending,
            # basis 一律用**列表**：导出层直接 join，混用 str 会把它按字符拆开
            "basis": ["实测"] if not pending else ["待核实"],
        }

    # ── 各指标 min / max（只在**有值**的候选之间算）
    stat: dict[str, tuple[Decimal, Decimal]] = {}
    for c in criteria:
        vals = [to_decimal(s.get("metrics", {}).get(c["key"]))
                for s in suppliers if s.get("metrics", {}).get(c["key"]) not in (None, "")]
        if vals:
            stat[c["key"]] = (min(vals), max(vals))

    # ── 逐家算分（门槛失败的也照算，但标记为不计入排名 —— 便于展示差距）
    scored: list[dict] = []
    for s in suppliers:
        name = str(s.get("name") or "").strip() or "未命名"
        metrics = s.get("metrics") or {}
        parts, eff_w = [], Decimal("0")
        missing: list[str] = []
        for c in criteria:
            raw = metrics.get(c["key"], None)
            w = to_decimal(c.get("weight"))
            if raw in (None, "") or c["key"] not in stat:
                missing.append(c["key"])          # ① 缺失：权重剔除，不当 0 分
                continue
            lo, hi = stat[c["key"]]
            n = _norm_value(to_decimal(raw), lo, hi, c["direction"])
            parts.append((c["key"], w * n))
            eff_w += w
        total = (sum((p[1] for p in parts), Decimal("0")) / eff_w) if eff_w > 0 else None
        scored.append({
            "supplier": name,
            "total": q2(total) if total is not None else None,
            "effective_weight": q2(eff_w),
            "missing_metrics": missing,
            "verdict": verdicts[name]["verdict"],
            "failed_gates": verdicts[name]["failed_gates"],
            "pending_gates": verdicts[name]["pending_gates"],
            "basis": (verdicts[name]["basis"] if not missing else ["待核实"]),
        })

    rankable = [r for r in scored if r["verdict"] != "淘汰" and r["total"] is not None]
    rankable.sort(key=lambda r: r["total"], reverse=True)
    for pos, r in enumerate(rankable, 1):
        r["rank"] = pos

    alerts: list[str] = []
    if len(rankable) == 1:
        alerts.append("只有 1 家进入排名 —— 没有竞争，评分结论不具备比较意义")
    if len(rankable) == 2:
        alerts.append("只有 2 家候选：min-max 归一易失真，建议改**阈值锚定法**人工复核")
    for r in scored:
        if r["verdict"] == "淘汰":
            alerts.append(f"{r['supplier']} 门槛未过（{'、'.join(r['failed_gates'])}）→ 已淘汰，"
                          f"**不得被高分或低价抵消**")
        elif r["verdict"] == "待核实":
            alerts.append(f"{r['supplier']} 有 {len(r['pending_gates'])} 项门槛待核实"
                          f"（{'、'.join(r['pending_gates'])}）→ 先决条件，补证后可批")
        if r["missing_metrics"]:
            alerts.append(f"{r['supplier']} 缺指标：{'、'.join(r['missing_metrics'])}"
                          f" → 已从分母剔除（不计 0 分），其总分的可比性弱于其他家")

    best = rankable[0] if rankable else None
    conclusion = (
        f"**{best['supplier']}** 得分最高（{best['total']}），且门槛{'通过' if best['verdict'] == '通过' else '待核实'}"
        if best else "没有一家通过门槛，**本次无法定标**"
    )
    if best and best["verdict"] == "待核实":
        conclusion += " —— 补证齐全后才可批"
    if alerts:
        conclusion += f"。另有 {len(alerts)} 条需处理事项"

    return {
        "ok": True,
        "basis": ["实测", "推算"],
        "criteria": criteria,
        "verdicts": list(verdicts.values()),
        "results": sorted(scored, key=lambda r: (r["total"] is None, -(r["total"] or 0))),
        "ranking": [r["supplier"] for r in rankable],
        "best": best["supplier"] if best else None,
        "alerts": alerts,
        "conclusion": conclusion,
        "rule": "门槛优先于分数；缺失指标从分母剔除而非计 0 分",
    }
