
# 本文件由「采购专家引擎」自动生成，请勿手改。
# 算法与系统内完全一致（同源生成）；如需调整，请修改上游引擎后重新生成。

"""专家结果的导出：xlsx / csv / md / txt 四种格式。

═══ 为什么是这四种 ═══

对着采购岗的真实用法选的，不是为了凑格式：
  · **xlsx** —— 要接着算、要贴进自己的台账（主表 + 一张「口径与告警」副表）
  · **csv**  —— 喂给别的系统 / 导入台账（UTF-8 **带 BOM**，否则 Excel 打开中文乱码）
  · **md**   —— 贴进知识库、周报、群消息（保留表格结构）
  · **txt**  —— 贴进微信/钉钉对话（等宽对齐的纯文本，手机上也看得清）

**不做 PDF/DOCX**：这两样要额外依赖（reportlab / python-docx），而客户机是离线私有化部署，
每多一个依赖就多一份交付负担。真需要正式文档时，用 md/xlsx 二次加工更灵活。

═══ 两条纪律（与引擎一致）═══

① **导出必须重算，不接受客户端回传的结果** —— 否则"界面显示 10.75、导出文件写 9.99"
   这种事一定会发生，而对账场景里它就是致命错误。路由层用同一份输入重跑引擎再导出。
② **口径与告警必须跟着数据一起走** —— 每张表后面都带「证据依据 / 告警 / 免责」段，
   导出的文件离开系统之后仍然自带上下文，不会被当成"权威结论"误用。
"""
from __future__ import annotations

import csv
import io
import datetime as dt
from decimal import Decimal
from typing import Any

__all__ = ["FORMATS", "MIME", "EXT", "export_result"]

FORMATS = ("xlsx", "csv", "md", "txt")
EXT = {"xlsx": "xlsx", "csv": "csv", "md": "md", "txt": "txt"}
MIME = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "csv": "text/csv; charset=utf-8",
    "md": "text/markdown; charset=utf-8",
    "txt": "text/plain; charset=utf-8",
}

TITLES = {
    "quote": "比价归一结果",
    "score": "供应商评分结果",
    "contract": "合同风险排查结果",
    "ledger": "台账行",
}


# ────────────────────────────────── 表格化：每种结果 → 表头 + 行 + 附注

def _s(v: Any) -> str:
    """值 → 单元格文本。None 一律留空（不写 0、不写 N/A）。"""
    if v is None:
        return ""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, bool):
        return "是" if v else "否"
    return str(v)


def _join(v) -> str:
    """把 依据/措辞 之类的多值字段连成一行。

    **str 与 list 都要能吃**：曾经直接 "、".join(x["basis"])，而那里是字符串，
    结果 "实测" 被按字符拆成了 "实、测"。这种 bug 界面上一眼看不见（看着像缩写），
    所以这里做显式容错，而不是指望每个上游都记得用 list。
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return "、".join(str(x) for x in v)


def _quote_table(r: dict) -> tuple[list[str], list[list[str]], list[str]]:
    head = ["供应商", "报单价", "含税", "税率", "净单价", "运费总额",
            "每批数量", "单位运费", "到厂单位成本", "批次数", "年度总获取成本", "口径说明", "依据"]
    rows = []
    for x in sorted(r["rows"], key=lambda v: v["landed_unit_cost"]):
        rows.append([
            x["supplier"], _s(x["unit_price"]), "含税" if x["price_includes_tax"] else "未税",
            _s(x["tax_rate"]), _s(x["net_unit_price"]), _s(x["freight_total"]),
            _s(x["qty_per_batch"]), _s(x["unit_freight"]), _s(x["landed_unit_cost"]),
            _s(x["batch_count"]), _s(x["annual_total"]), x["tax_note"], _join(x["basis"]),
        ])
    notes: list[str] = []
    if r.get("savings"):
        s = r["savings"]
        notes.append(f"节降：基线 {_s(s['baseline_unit_cost'])}（来源：{s['baseline_source']}）→ "
                     f"最优 {_s(s['best_unit_cost'])}；每单位省 {_s(s['unit_gap'])}"
                     + (f"，降幅 {s['rate']}%" if s.get("rate") is not None else ""))
        notes.append("节降口径纪律：" + s["note"])
    notes += [f"口径告警：{a}" for a in r.get("alerts", [])]
    for x in r["rows"]:
        notes += [f"{x['supplier']}：{a}" for a in x["alerts"]]
    # 对账：**别把 dict 原样塞进给人看的文档**（曾经直接 f-string 出来一串花括号）
    rc = r.get("reconcile") or {}
    if rc:
        notes.append(f"对账：报价 {rc.get('quote_count', '—')} 条，"
                     f"其中给出年度口径的 {rc.get('with_annual', '—')} 条，"
                     f"剔除 {rc.get('dropped', 0)} 条（剔除行不静默丢弃，一律登记在告警里）")
    if r.get("ranking"):
        notes.append(f"排名（按到厂单位成本）：{' → '.join(r['ranking'])}")
    return head, rows, notes


def _score_table(r: dict) -> tuple[list[str], list[list[str]], list[str]]:
    head = ["排名", "供应商", "总分", "有效权重", "门槛判定", "未过门槛", "待核实门槛", "缺失指标", "依据"]
    rows = []
    for x in r["results"]:
        rows.append([
            _s(x.get("rank")), x["supplier"], _s(x["total"]), _s(x["effective_weight"]),
            x["verdict"], _join(x["failed_gates"]), _join(x["pending_gates"]),
            _join(x["missing_metrics"]), _join(x["basis"]),
        ])
    notes = [f"门槛规则：{r['rule']}"]
    notes += [f"告警：{a}" for a in r.get("alerts", [])]
    return head, rows, notes


def _contract_table(r: dict) -> tuple[list[str], list[list[str]], list[str]]:
    head = ["等级", "风险条款", "命中措辞", "风险原因", "修改方向"]
    rows = [[h["level"], h["name"], "、".join(h["matched"]), h["risk"], h["fix"]] for h in r["hits"]]
    notes = [f"排查条款数：{r['check_count']}；命中 {len(r['hits'])} 条（高风险 {r['high_count']} 条）"]
    if r["missing"]:
        notes.append("应有而未见的条款（需人工确认合同里到底有没有）：")
        # 用 —— 而不是 "  · "：这些行本身会被 markdown/csv 再套一层项目符号，
        # 前缀里带符号会变成 "-   · xxx" 这种双前缀
        notes += [f"{m['name']}：{m['why']}" for m in r["missing"]]
    notes += [f"告警：{a}" for a in r.get("alerts", [])]
    notes.append("免责：" + r["disclaimer"])
    return head, rows, notes


def _ledger_table(r: dict) -> tuple[list[str], list[list[str]], list[str]]:
    cols = r["ordered_columns"]
    rows = [[_s(r["row"].get(c)) for c in cols]]
    notes = [f"字段顺序固定为：{' | '.join(cols)}"]
    if r["missing"]:
        notes.append("缺字段（**不要凭印象补**，回原始单据核对）：" + "、".join(r["missing"]))
    notes.append("口径提示：" + str(r["row"].get("tax_basis_note") or "—"))
    notes.append(r["note"])
    return cols, rows, notes


_TABLES = {"quote": _quote_table, "score": _score_table,
           "contract": _contract_table, "ledger": _ledger_table}


# ────────────────────────────────── 各格式写出

def _cell(v: str):
    """xlsx 单元格：能转数字就写数字，否则写文本。"""
    if v == "":
        return ""
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return v


def _write_xlsx(title: str, head: list[str], rows: list[list[str]], notes: list[str],
                conclusion: str) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("导出 Excel 需要 openpyxl（pip install openpyxl）") from e

    wb = Workbook()
    ws = wb.active
    ws.title = "结果"
    ws.append([title])
    ws["A1"].font = Font(bold=True, size=13)
    ws.append([conclusion])
    ws.append([])
    ws.append(head)
    for c in range(1, len(head) + 1):
        ws.cell(row=4, column=c).font = Font(bold=True)
        ws.cell(row=4, column=c).fill = PatternFill("solid", fgColor="EEF3FB")
    for r_ in rows:
        ws.append([_cell(v) for v in r_])
    for c in range(1, len(head) + 1):
        width = 12
        for r_ in range(5, 5 + len(rows)):
            v = ws.cell(row=r_, column=c).value
            if v is not None:
                width = max(width, min(34, len(str(v)) + 2))
        ws.column_dimensions[get_column_letter(c)].width = width
    ws.freeze_panes = "A5"

    # 副表：口径与告警 —— 导出的文件离开系统后也要自带上下文
    ws2 = wb.create_sheet("口径与告警")
    ws2.append(["#", "说明"])
    ws2["A1"].font = Font(bold=True)
    ws2["B1"].font = Font(bold=True)
    for i, n in enumerate(notes, 1):
        ws2.append([i, n])
    ws2.column_dimensions["A"].width = 5
    ws2.column_dimensions["B"].width = 110
    for r_ in range(2, 2 + len(notes)):
        ws2.cell(row=r_, column=2).alignment = Alignment(wrap_text=True, vertical="top")

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _write_csv(head: list[str], rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(head)
    w.writerows(rows)
    # ★ UTF-8 **带 BOM**：不带的话 Excel 打开中文必乱码（实测最常见的投诉）
    return b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")


def _write_markdown(title: str, head: list[str], rows: list[list[str]],
                    notes: list[str], conclusion: str) -> bytes:
    out = [f"# {title}", "", f"> {conclusion}", "", "| " + " | ".join(head) + " |",
           "|" + "|".join(["---"] * len(head)) + "|"]
    for r_ in rows:
        out.append("| " + " | ".join((v or "—") for v in r_) + " |")
    if notes:
        out += ["", "## 口径与告警", ""]
        out += [f"- {n}" for n in notes]
    return ("\n".join(out) + "\n").encode("utf-8")


def _write_txt(title: str, head: list[str], rows: list[list[str]],
               notes: list[str], conclusion: str) -> bytes:
    """等宽纯文本：手机上看也整齐，直接贴微信/钉钉。"""
    widths = [max(len(head[i]), *(len(r_[i] or "") for r_ in rows)) if rows else len(head[i])
              for i in range(len(head))]

    def line(cells: list[str]) -> str:
        return "  ".join((c or "").ljust(widths[i]) for i, c in enumerate(cells))

    out = [title, "=" * min(78, max(24, sum(widths) + 2 * (len(widths) - 1))), conclusion, "",
           line(head), "-" * min(78, sum(widths) + 2 * (len(widths) - 1))]
    out += [line(r_) for r_ in rows]
    if notes:
        out += ["", "口径与告警", "-" * 20] + [f"· {n}" for n in notes]
    return ("\n".join(out) + "\n").encode("utf-8")


def export_result(kind: str, result: dict, fmt: str = "xlsx") -> tuple[bytes, str, str]:
    """主入口。返回 (内容字节, 建议文件名主干, mimetype)。

    kind: quote / score / contract / ledger；fmt: xlsx / csv / md / txt
    """
    kind = str(kind or "").strip().lower()
    fmt = str(fmt or "xlsx").strip().lower()
    if kind not in _TABLES:
        raise ValueError(f"不支持的导出类型：{kind}（可选 {', '.join(_TABLES)}）")
    if fmt not in FORMATS:
        raise ValueError(f"不支持的格式：{fmt}（可选 {', '.join(FORMATS)}）")
    if not isinstance(result, dict) or not result.get("ok"):
        raise ValueError("没有可导出的结果")

    title = TITLES[kind]
    head, rows, notes = _TABLES[kind](result)
    conclusion = str(result.get("conclusion") or "")

    if fmt == "xlsx":
        body = _write_xlsx(title, head, rows, notes, conclusion)
    elif fmt == "csv":
        body = _write_csv(head, rows)
    elif fmt == "md":
        body = _write_markdown(title, head, rows, notes, conclusion)
    else:
        body = _write_txt(title, head, rows, notes, conclusion)

    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M")
    return body, f"{title}_{stamp}", MIME[fmt]
