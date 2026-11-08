#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""采购算账引擎 · 命令行入口（零第三方依赖，只用标准库）。

═══ 为什么要有 CLI ═══

引擎本身是 Python 模块，但用户和 AI 助手都不该去 `import` 它 —— 他们要的是
"给一份数据，拿走一份结果"。所以这里做一层薄薄的命令行包装：

    python cli.py quote    --in 报价.json      --format md
    python cli.py score    --in 供应商.json    --format xlsx
    python cli.py contract --in 合同.txt       --format txt
    python cli.py ledger   --in 单据.json  --doc-kind invoice --format csv
    python cli.py fields   --category 化工液体原料

    # 不想准备数据？每条都带示例，直接跑：
    python cli.py quote --demo
    python cli.py contract --demo

═══ 三个务实处理 ═══

① **Windows 中文输出**：默认 stdout 是 GBK，中文会抛 UnicodeEncodeError。
   启动时把 stdout 重设为 UTF-8（`errors=replace` 兜底），而不是让用户去改代码页。
② **没给 --out 时**：文本格式（md/txt）直接打到屏幕（方便粘贴）；
   二进制格式（xlsx/csv）写到当前目录并告知路径 —— 二进制不该往终端里灌。
③ **xlsx 需要 openpyxl**：缺了就**明确告诉他装什么**，并提示改用 csv（不静默降级、
   不报一堆栈让他猜）。

退出码：0 = 成功；2 = 输入/参数问题；1 = 运行错误。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

# ① Windows 控制台编码兜底：必须在任何 print 之前
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # pragma: no cover
        pass

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from export import EXT, export_result  # noqa: E402
from contract_check import check_contract  # noqa: E402
from ledger_fields import build_ledger_row, fields_for  # noqa: E402
from quote_normalize import normalize_quotes  # noqa: E402
from supplier_score import score_suppliers  # noqa: E402

TEXT_FORMATS = ("md", "txt")

DEMO = {
    "quote": {
        "annual_demand": 120000,
        "baseline_unit_cost": "11.68",
        "baseline_source": "现合同价",
        "quotes": [
            {"supplier": "沪连", "unit_price": "12.50", "tax_rate": 0.13,
             "price_includes_tax": True, "qty_per_batch": 30000, "freight_total": "3000"},
            {"supplier": "深科", "unit_price": "10.80", "qty_per_batch": 30000,
             "freight_total": "2400"},
            {"supplier": "苏恒", "unit_price": "12.20", "qty_per_batch": 30000,
             "freight_total": "0"},
        ],
    },
    "score": {
        "gates_required": ["business_license", "hazmat"],
        "criteria": [
            {"key": "price", "name": "到厂价格", "weight": 0.5, "direction": "cost"},
            {"key": "otd", "name": "准时交付率", "weight": 0.3, "direction": "benefit"},
            {"key": "quality", "name": "质量合格率", "weight": 0.2, "direction": "benefit"},
        ],
        "suppliers": [
            {"name": "苏恒", "gates": {"business_license": True, "hazmat": False},
             "metrics": {"price": "12.20", "otd": "0.95", "quality": "0.99"}},
            {"name": "甬新", "gates": {"business_license": True},
             "metrics": {"price": "11.60", "otd": "0.92"}},
            {"name": "深科", "gates": {"business_license": True, "hazmat": True},
             "metrics": {"price": "10.80", "otd": "0.88", "quality": "0.97"}},
        ],
    },
    "contract": (
        "采购合同（摘录）\n"
        "第二条 付款方式：买方应于本合同签订之日起 3 日内向卖方支付全额货款，"
        "卖方于全款到账后 15 日内发货。货到后由买方签收，签收即视为验收合格。\n"
        "第三条 价格：如遇原材料价格上涨，供方有权单方调整价格，如有调整另行通知。\n"
        "第四条 争议解决：双方如发生争议，提交有关仲裁机构解决。\n"
        "第五条 因市场缺货或原材料上涨导致不能交货的，供方不承担违约责任。\n"
    ),
    "ledger": {
        "kind": "invoice", "date": "2026-09-02", "doc_no": "01024871",
        "supplier": "某化工", "material": "二甘醇", "spec": "工业级 99.5%",
        "qty": 30, "unit": "吨", "unit_price": "4180", "tax_rate": 0.13,
        "price_includes_tax": True,
    },
    "fields": "化工液体原料",
}


def _load(path: str, *, text: bool = False):
    """读输入。JSON 用 utf-8-sig（容忍记事本存的 BOM）；文本直接读。"""
    p = pathlib.Path(path)
    if not p.exists():
        raise SystemExit(f"找不到输入文件：{path}")
    raw = p.read_text(encoding="utf-8-sig", errors="replace")
    if text:
        return raw
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} 不是合法 JSON：{e.msg}（第 {e.lineno} 行）\n"
                         f"提示：字段名与括号要成对，别用中文引号。")


def _emit(body: bytes, fmt: str, stem: str, out: str | None) -> int:
    """③ 输出策略：文本进终端，二进制落到文件。"""
    if out == "-" or (out is None and fmt in TEXT_FORMATS):
        if fmt not in TEXT_FORMATS:
            print(f"❌ {fmt} 是二进制格式，不能打到终端。请加 --out 指定文件名。",
                  file=sys.stderr)
            return 2
        print(body.decode("utf-8"))
        return 0

    target = pathlib.Path(out) if out else pathlib.Path(f"{stem}.{EXT[fmt]}")
    if target.exists() and out is None:
        target = pathlib.Path(f"{stem}_1.{EXT[fmt]}")
    target.write_bytes(body)
    print(f"✅ 已写出：{target.resolve()}（{len(body) / 1024:.1f} KB）")
    return 0


def _xlsx_guard(fmt: str) -> None:
    """③ 提前拦 openpyxl 缺失，给可执行的建议，而不是抛栈。"""
    if fmt != "xlsx":
        return
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        raise SystemExit(
            "xlsx 需要 openpyxl，本机没装。二选一：\n"
            "  ① 装：pip install openpyxl\n"
            "  ② 改用 CSV：--format csv（Excel 能直接打开，中文不乱码）")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="cli.py", description="采购算账引擎（比价归一 / 供应商评分 / 合同排雷 / 台账行）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _common(p):
        p.add_argument("--in", dest="inp", help="输入文件（JSON；contract 用纯文本）")
        p.add_argument("--demo", action="store_true", help="用内置示例跑一遍，不需要准备数据")
        p.add_argument("--format", default="md", choices=("md", "txt", "csv", "xlsx"),
                       help="输出格式，默认 md")
        p.add_argument("--out", help="输出文件；文本格式省略则打到屏幕，'-' 强制打屏幕")

    p = sub.add_parser("quote", help="比价归一：含税/未税拉平 + 运费摊薄 + 年度总成本 + 节降")
    _common(p)

    p = sub.add_parser("score", help="供应商评分：门槛一票否决 + min-max 加权")
    _common(p)

    p = sub.add_parser("contract", help="合同排雷：16 类风险条款 + 缺失检查")
    _common(p)

    p = sub.add_parser("ledger", help="台账行：发票/申请/报价 → 可直接粘贴的一行")
    _common(p)
    p.add_argument("--doc-kind", default="invoice", choices=("invoice", "request", "quote"),
                   help="单据类型（也可写在 JSON 的 kind 里），默认 invoice")

    p = sub.add_parser("fields", help="询价字段库：通用 10 字段 + 品类专属")
    p.add_argument("--category", help="品类，如 化工液体原料")
    p.add_argument("--format", default="md", choices=("md", "txt", "csv"),
                   help="输出格式，默认 md")
    p.add_argument("--out")
    p.add_argument("--demo", action="store_true")

    args = ap.parse_args(argv)

    # ── fields：不是"结果"，单独处理（它是字段清单，没有结论/告警）
    if args.cmd == "fields":
        cat = args.category or (DEMO["fields"] if args.demo else None)
        data = fields_for(cat)
        lines = [f"# 询价必填字段{f'（{data['category']}）' if data['category'] else ''}", ""]
        if data.get("unknown_category"):
            lines += [f"> ⚠️ 不认识的品类「{data['category']}」。已知品类："
                      f"{'、'.join(data['known_categories'])}", ""]
        lines += ["## 通用 10 字段（每单都要问）", ""]
        lines += [f"{i}. **{c['name']}**" for i, c in enumerate(data["common"], 1)]
        if data["extra"]:
            lines += ["", f"## {data['category']} 专属必填", ""]
            lines += [f"- {x}" for x in data["extra"]]
        lines += ["", f"> {data['note']}"]
        body = ("\n".join(lines) + "\n").encode("utf-8")
        return _emit(body, args.format if args.format != "xlsx" else "md",
                     f"询价字段_{data['category'] or '通用'}", args.out)

    _xlsx_guard(args.format)

    # ── 取输入
    try:
        if args.demo:
            payload = None
        elif args.cmd == "contract":
            payload = _load(args.inp, text=True) if args.inp else None
        else:
            payload = _load(args.inp) if args.inp else None
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 2

    if payload is None and not args.demo:
        print(f"❌ 请给 --in <文件>，或加 --demo 用示例跑一遍。", file=sys.stderr)
        return 2

    # ── 算
    try:
        if args.cmd == "quote":
            result = normalize_quotes(payload if payload is not None else DEMO["quote"])
            kind = "quote"
        elif args.cmd == "score":
            result = score_suppliers(payload if payload is not None else DEMO["score"])
            kind = "score"
        elif args.cmd == "contract":
            text = payload if payload is not None else DEMO["contract"]
            result = check_contract({"text": text})
            kind = "contract"
        else:  # ledger
            data = dict(payload if payload is not None else DEMO["ledger"])
            data.setdefault("kind", args.doc_kind)
            result = build_ledger_row(data["kind"], data)
            kind = "ledger"
    except ValueError as e:
        print(f"❌ 输入有问题：{e}", file=sys.stderr)
        return 2

    # ── 出
    try:
        body, stem, _mime = export_result(kind, result, args.format)
    except ValueError as e:
        print(f"❌ 导出失败：{e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        return 1

    rc = _emit(body, args.format, stem, args.out)
    if rc == 0 and args.format not in TEXT_FORMATS:
        print(f"   结论：{result.get('conclusion') or '（见文件）'}")
    return rc


if __name__ == "__main__":
    sys.exit(main())
