
# 本文件由「采购专家引擎」自动生成，请勿手改。
# 算法与系统内完全一致（同源生成）；如需调整，请修改上游引擎后重新生成。

"""询价字段库与台账行构建：通用 10 字段 + 品类专属。

═══ 为什么要有这个模块 ═══

询价单最常出的问题不是"没发"，而是**该问的没问** —— 等到比价时才发现
人家报的是未税价、运费谁付没说、MOQ 没问、交货地没写。
所以字段库的作用是**发询价前就把口径钉住**，让报回来的东西天然可比。

两件事：
  ① `fields_for(category)` —— 通用 10 字段 + 品类专属必填项，并标注哪些是关键口径项
  ② `build_ledger_row(kind, payload)` —— 把发票 / 采购申请 / 报价转成**可直接粘贴的一行**
     （字段顺序固定，与台账模板一致；缺字段显式列出，不猜不补）

═══ 纪律 ═══

· **不猜数字**：算不出来的字段返回 None 并进 `missing`，绝不填 0 或估算值。
· 金额一律 `components.money` 处理（Decimal / 量化到分）。
· 台账行的"口径提示"要跟着行一起走：口径不明的行贴进台账就是一颗雷。
"""
from __future__ import annotations

from _money import line_amount, q2, to_decimal

__all__ = ["COMMON_FIELDS", "CATEGORIES", "fields_for", "build_ledger_row"]

# ── 通用 10 字段（每条询价单都该有）
COMMON_FIELDS: tuple[tuple[str, str, bool], ...] = (
    ("spec", "规格型号", True),
    ("qty", "数量", True),
    ("moq", "MOQ（最小起订量）", True),
    ("tax_basis", "含税口径（含税/未税、税率、发票类型、可否抵扣）", True),
    ("delivery_place", "交货地", True),
    ("freight", "运费归属（供方/需方、是否含运）", True),
    ("payment_term", "账期（付款方式与节点）", True),
    ("quote_validity", "报价有效期", True),
    ("quality_window", "退换 / 质量异议期", True),
    ("qualification", "资质要求（强制认证清单）", True),
)

# ── 品类专属必填（KB2）
CATEGORIES: dict[str, tuple[str, ...]] = {
    "化工液体原料": (
        "纯度 / 执行标准号", "色度 / 水分等关键指标", "包装方式（散水 / 吨桶 / 槽车）",
        "危化品经营许可证", "SDS 与安全标签", "运输车辆危化资质", "密度（换算用）",
    ),
    "化工固体原料": (
        "主含量 / 纯度", "粒度 / 外观", "包装与单包重量", "防潮要求",
        "是否危化品及资质", "托盘方式", "执行标准号",
    ),
    "阀门": (
        "类型（闸/截/球/蝶/止回）", "通径 DN", "压力等级 PN", "材质（阀体/阀芯/密封）",
        "连接方式（法兰/螺纹/焊接）", "法兰标准", "驱动方式（手动/气动/电动）",
        "执行标准", "防护 / 防爆要求",
    ),
    "法兰与紧固件": (
        "法兰类型与标准（HG/T、GB）", "通径与压力等级", "材质与材质单",
        "密封面形式", "紧固件等级（如 8.8 级）", "垫片材质", "钢印要求",
    ),
    "电气与仪表": (
        "型号 / 规格", "额定电压 / 功率 / 防护等级", "防爆等级（危化区域）",
        "计量精度 / 量程", "信号制式", "安装方式", "认证（CCC / 防爆合格证）",
    ),
    "劳保与 MRO": (
        "规格 / 尺码 / 型号", "适用标准（如安全鞋 GB 21148）", "数量梯度",
        "保质期（如手套 / 滤盒）", "品牌兼容要求", "是否需批次检测报告",
    ),
}

# 台账行字段顺序（**固定**：与台账模板一致，才能"贴了就能用"）
LEDGER_COLUMNS: tuple[str, ...] = (
    "kind", "date", "doc_no", "supplier", "material", "spec", "qty", "unit",
    "unit_price", "tax_rate", "amount_net", "amount_gross", "tax_basis_note",
)


def fields_for(category: str | None = None) -> dict:
    """返回询价单必填字段（通用 10 + 品类专属）。品类不认识就只给通用项，并提示可选品类。"""
    cat = str(category or "").strip()
    extra = CATEGORIES.get(cat, ())
    return {
        "ok": True,
        "category": cat or None,
        "known_categories": list(CATEGORIES.keys()),
        "common": [{"key": k, "name": n, "critical": c} for k, n, c in COMMON_FIELDS],
        "extra": list(extra),
        "note": "**含税口径、运费归属、交货地、MOQ、报价有效期**这五项一定写进询价单 —— "
                "少一项，报回来的价就不可比，后面比价要返工。",
        "unknown_category": bool(cat) and cat not in CATEGORIES,
    }


def build_ledger_row(kind: str, payload: dict) -> dict:
    """把一张单据转成台账行。kind: invoice / request / quote。

    **不猜数字**：算不出的字段进 `missing`，`amount_gross` 由数量 × 单价推出，
    并标注推算依据；税率/含税口径缺失时不给金额结论，只给"待核实"。
    """
    kind = str(kind or "").strip()
    if kind not in ("invoice", "request", "quote"):
        raise ValueError("kind 必须是 invoice / request / quote")

    missing: list[str] = []

    def get(key, required=False):
        v = payload.get(key, None)
        if v in (None, ""):
            if required:
                missing.append(key)
            return None
        return v

    qty_raw = get("qty", required=True)
    price_raw = get("unit_price", required=True)
    tax_rate = get("tax_rate")
    # ⚠️ 含税口径**未声明时不许默认一种**：宁可给出数字同时标注"按未税处理，请核原件"，
    #    也不静默猜 —— 猜错方向会让整张台账的金额与税都对不上。
    includes_raw = payload.get("price_includes_tax", None)
    includes = bool(includes_raw) if includes_raw is not None else False
    basis_unstated = includes_raw is None

    row: dict = {
        "kind": kind,
        "date": get("date", required=True),
        "doc_no": get("doc_no", required=True),
        "supplier": get("supplier", required=True),
        "material": get("material", required=True),
        "spec": get("spec"),
        "qty": str(qty_raw) if qty_raw is not None else None,
        "unit": get("unit"),
        "unit_price": None,
        "tax_rate": str(tax_rate) if tax_rate is not None else None,
        "amount_net": None,
        "amount_gross": None,
        "tax_basis_note": "",
    }

    if qty_raw is not None and price_raw is not None:
        q = to_decimal(qty_raw)
        p = to_decimal(price_raw)
        row["unit_price"] = str(q2(p))
        if tax_rate in (None, ""):
            # 未涉税：金额 = 数量 × 单价，但口径要说清楚
            amt = q2(q * p)
            row["amount_net"] = str(amt)
            row["amount_gross"] = str(amt)
            row["tax_basis_note"] = "未提供税率：金额按数量×单价计，**税口径待核实**"
            missing.append("tax_rate")
        else:
            calc = line_amount(q, p, tax_rate=tax_rate, price_includes_tax=includes)
            row["amount_net"] = str(calc["net"])
            row["amount_gross"] = str(calc["gross"])
            if basis_unstated:
                row["tax_basis_note"] = ("**含税口径未声明**：暂按未税价处理"
                                         "（净额=数量×单价），请回单据核对原件")
                missing.append("price_includes_tax")
            else:
                row["tax_basis_note"] = ("报价为含税价，已按税率拆出净额与税额"
                                         if includes else "报价为未税价，已按税率计出含税额")

    # 台账列顺序提示（供"贴了就能用"）
    ordered = {c: row.get(c) for c in LEDGER_COLUMNS}
    return {
        "ok": True,
        "row": ordered,
        "ordered_columns": list(LEDGER_COLUMNS),
        "missing": sorted(set(missing)),
        "basis": ["实测"] if not missing else ["实测", "待核实"],
        "note": "缺字段已列在 missing 里 —— **不要凭印象补**，回原始单据核对；"
                "口径不明的行贴进台账会变成对账时的雷。",
    }
