
# 本文件由「采购专家引擎」自动生成，请勿手改。
# 算法与系统内完全一致（同源生成）；如需调整，请修改上游引擎后重新生成。

"""金额与税率计算（防原系统"浮点对账差几分钱"的坑）。

原则：
  ① 一律 Decimal，禁止 float 参与金额运算
  ② 量化规则集中在此（默认 2 位，ROUND_HALF_UP）
  ③ 税率按"含税/不含税"两种口径显式区分，不靠调用方心算
  ④ 分配算法保证"分项之和 == 合计"（尾差落在最大项，而不是丢掉）
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

CENT = Decimal("0.01")
_TAX_BASE = Decimal("100")


def to_decimal(value, *, allow_none: bool = False) -> Decimal | None:
    """把任意输入安全转成 Decimal。float 走 str() 中转，避免二进制误差被带进来。"""
    if value is None or value == "":
        if allow_none:
            return None
        raise ValueError("金额不能为空")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"金额必须是有限数，收到 {value}")
        return value
    if isinstance(value, bool):
        raise TypeError("布尔值不是金额")
    if isinstance(value, float):
        # 关键：float -> str -> Decimal，而不是 Decimal(float)
        d = Decimal(str(value))
        if not d.is_finite():
            raise ValueError(f"金额必须是有限数，收到 {value!r}")
        return d
    if isinstance(value, int):
        return Decimal(value)
    try:
        d = Decimal(str(value).strip().replace(",", "").replace("¥", "").replace("￥", ""))
    except InvalidOperation as e:
        raise ValueError(f"无法解析为金额: {value!r}") from e
    # NaN / Infinity 必须在入口拦掉：否则会一路穿到数据库与报表
    # （实测 q2("NaN") 会返回 Decimal('NaN')，展示成 "¥NaN"）
    if not d.is_finite():
        raise ValueError(f"金额必须是有限数，收到 {value!r}")
    return d


def q2(value) -> Decimal:
    """量化到分（四舍五入）。所有入库与展示前的必经一步。"""
    return to_decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def normalize_tax_rate(value) -> Decimal:
    """把税率归一成小数（0.13 表示 13%）。

    约定（写死，避免"1 到底算 1% 还是 100%"这种歧义）：
      · 0 < v <= 1     → 已是小数，直接用（0.13 = 13%，1 = 100%）
      · 1 < v <= 100   → 百分数，除以 100（13 = 13%，1.5 = 1.5%）
      · 其它（负数 / >100 / 非有限）→ 报错

    ⚠️ 边界说明：v == 1 按小数解释 = 100%。
       若业务上要表达 1%（如小规模纳税人征收率），**必须传 0.01**。
       这是显式约定，不是猜——`is_ratio_ambiguous()` 可用于提示前端。

    历史 bug：原实现用 `if r > 1: r = r/100`，导致传 1 时被当成 100%，
    1000 元的净额算出 1000 元税额（应为 10 元）。
    """
    r = to_decimal(value)
    if not r.is_finite():
        raise ValueError(f"税率必须是有限数，收到 {value!r}")
    if r < 0:
        raise ValueError(f"税率不能为负：{r}")
    if r == 0:
        return Decimal("0")
    if r <= 1:
        return r
    if r <= _TAX_BASE:
        return r / _TAX_BASE
    raise ValueError(f"税率超出合理范围（0–100 或 0–1）：{r}")


def is_ratio_ambiguous(value) -> bool:
    """1 与 0.01 这类"既能读成百分数又能读成小数"的值，供前端提示确认。

    真正的歧义点：v 恰好等于 1 时，既可以读成 100%（小数口径）也可以读成 1%（百分数口径）。
    """
    try:
        return to_decimal(value) == 1
    except (ValueError, TypeError):
        return False


def line_amount(qty, unit_price, *, tax_rate=None, price_includes_tax: bool = False) -> dict:
    """单行金额计算。

    qty 允许小数（如 1.5 吨）。返回：
      {"net": 不含税金额, "tax": 税额, "gross": 价税合计}
    当 tax_rate 为空时，税额为 0，net == gross（表示未涉税）。
    """
    q = to_decimal(qty)
    p = to_decimal(unit_price)
    raw = q * p

    if tax_rate in (None, ""):
        net = q2(raw)
        return {"net": net, "tax": Decimal("0.00"), "gross": net}

    r = normalize_tax_rate(tax_rate)

    if price_includes_tax:
        gross = q2(raw)
        net = q2(gross / (Decimal("1") + r))
        tax = q2(gross - net)
    else:
        net = q2(raw)
        tax = q2(net * r)
        gross = q2(net + tax)
    return {"net": net, "tax": tax, "gross": gross}


def allocate(total, weights) -> list[Decimal]:
    """按权重把合计拆成若干项，保证分项之和恰好等于合计（尾差给最大项）。

    用途：运费分摊、多物料按金额分摊税额。
    """
    t = q2(total)
    ws = [to_decimal(w) for w in weights]
    if not ws:
        return []
    s = sum(ws, Decimal("0"))
    if s == 0:
        raise ValueError("权重之和为 0，无法分摊")

    parts = [q2(t * w / s) for w in ws]
    diff = q2(t - sum(parts, Decimal("0")))
    if diff != 0:
        # 把尾差加到权重最大的一项，保持"总额守恒"
        idx = max(range(len(ws)), key=lambda i: ws[i])
        parts[idx] = q2(parts[idx] + diff)
    return parts


def summarize(rows, amount_key: str = "gross") -> dict:
    """汇总多行金额。返回 {net, tax, gross, count}，用于单据头。"""
    net = tax = gross = Decimal("0.00")
    for row in rows:
        net = q2(net + to_decimal(row.get("net", 0)))
        tax = q2(tax + to_decimal(row.get("tax", 0)))
        gross = q2(gross + to_decimal(row.get(amount_key, 0)))
    return {"net": net, "tax": tax, "gross": gross, "count": len(list(rows))}


def fmt(value, *, symbol: str = "¥") -> str:
    """展示用格式化：千分位 + 2 位小数。"""
    return f"{symbol}{q2(value):,.2f}"
