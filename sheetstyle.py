# -*- coding: utf-8 -*-
"""
Excel 样式保真模块
==================
协作系统只同步「单元格的值」，样式永远以原始 xlsx 为模板，不被修改。

* extract_style()       : 从 xlsx 提取可序列化的样式元数据（供 UI 渲染 / 跨机同步）
* write_values()        : 以模板为基底，只写回指定单元格的值，其余一切原样保留
* describe()            : 给界面用的样式摘要
* read_values()         : 读取某个工作表的显示值（公式取缓存结果）

保留范围：字体/字号/粗斜体/字色、填充色、对齐与自动换行、四边框线与线型、
列宽行高、隐藏行列、合并单元格、数字格式、冻结窗格、条件格式、数据验证、
打印区域与页面设置、图片与图表、以及工作簿里的其它工作表与公式。

只依赖 openpyxl（已内置在 lib/），兼容 Python 3.8 / Win7。
"""

import copy
import os
import re
import warnings
import zipfile
import xml.etree.ElementTree as ET

import openpyxl
from openpyxl.utils import get_column_letter

# 渲染用的默认尺寸（像素）
DEFAULT_COL_PX = 96
DEFAULT_ROW_PX = 26
MAX_STYLE_CELLS = 30000          # 超大表限制样式导出量，避免同步消息膨胀

_BORDER_WIDTH = {"hair": 1, "thin": 1, "medium": 2, "thick": 3,
                 "double": 2, "dotted": 1, "dashed": 1,
                 "dashDot": 1, "dashDotDot": 1, "slantDashDot": 1}
_BORDER_DASH = {"dotted": (1, 2), "dashed": (4, 3), "dashDot": (5, 2, 1, 2),
                "dashDotDot": (5, 2, 1, 2, 1, 2), "slantDashDot": (6, 2, 2, 2),
                "double": None}


def _load(path, data_only=False):
    """静默加载：原表可能含 openpyxl 不支持的 WMF/EMF 图形，
    它只影响「读取」，保存走 ZIP 补丁，图形仍会原样保留。"""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return openpyxl.load_workbook(path, data_only=data_only)


def _rgb(color):
    """openpyxl 颜色 → #RRGGBB；主题色/索引色暂退化为默认色"""
    if color is None:
        return None
    try:
        if getattr(color, "type", None) == "rgb":
            v = str(color.rgb or "")
            if len(v) == 8 and all(ch in "0123456789abcdefABCDEF" for ch in v):
                return "#" + v[2:].upper()
            if len(v) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in v):
                return "#" + v.upper()
    except Exception:
        pass
    return None


def _side(side):
    """边框单边 → [线型, 颜色, 线宽, dash] 或 None"""
    if side is None:
        return None
    style = getattr(side, "style", None)
    if not style:
        return None
    return [style, _rgb(getattr(side, "color", None)) or "#7F7F7F",
            _BORDER_WIDTH.get(style, 1), _BORDER_DASH.get(style)]


def _font(font):
    if font is None:
        return None
    try:
        return {
            "family": font.name or "宋体",
            "size": float(font.sz or 11),
            "bold": bool(font.b),
            "italic": bool(font.i),
            "underline": bool(font.u),
            "color": _rgb(font.color),
        }
    except Exception:
        return None


def _fill(fill):
    try:
        if fill is not None and fill.fill_type:
            color = _rgb(fill.fgColor) or _rgb(getattr(fill, "bgColor", None))
            if color:
                return color
    except Exception:
        pass
    return None


def _align(align):
    if align is None:
        return None
    return {
        "h": align.horizontal or "general",
        "v": align.vertical or "bottom",
        "wrap": bool(align.wrap_text),
        "indent": int(align.indent or 0),
    }


def _col_px(width):
    """Excel 列宽（字符数）→ 像素，按 Calibri 11 的经验换算"""
    try:
        return max(18, int(round(float(width) * 7 + 5)))
    except (TypeError, ValueError):
        return DEFAULT_COL_PX


def _row_px(height):
    """Excel 行高（磅）→ 像素（96dpi）"""
    try:
        return max(14, int(round(float(height) * 96.0 / 72.0)))
    except (TypeError, ValueError):
        return DEFAULT_ROW_PX


def extract_style(path, sheet_name=None):
    """提取样式元数据，返回纯 JSON 可序列化 dict（坐标均为 1-based）"""
    wb = _load(path, data_only=False)
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.active.title
    ws = wb[sheet_name]
    try:
        base_font = _font(wb._fonts[0]) if wb._fonts else None
    except Exception:
        base_font = None
    max_col_idx = min(int(ws.max_column or 1) + 2, 60)

    col_widths = {}
    default_col = DEFAULT_COL_PX
    try:
        if ws.sheet_format and ws.sheet_format.defaultColWidth:
            default_col = _col_px(ws.sheet_format.defaultColWidth)
    except Exception:
        pass
    for key, dim in ws.column_dimensions.items():
        if dim.hidden:
            continue
        # 形如 "A:H" 的区间 Dimensions 展开成单列
        try:
            from openpyxl.utils import column_index_from_string, get_column_letter
            start, _, end = str(key).partition(":")
            a = column_index_from_string(start)
            b = column_index_from_string(end) if end else a
            if dim.width:
                for idx in range(a, min(b, max_col_idx) + 1):
                    col_widths[get_column_letter(idx)] = _col_px(dim.width)
        except Exception:
            pass

    default_row = DEFAULT_ROW_PX
    try:
        if ws.sheet_format and ws.sheet_format.defaultRowHeight:
            default_row = _row_px(ws.sheet_format.defaultRowHeight)
    except Exception:
        pass
    row_heights = {}
    for idx, dim in ws.row_dimensions.items():
        try:
            if not dim.hidden and dim.height:
                row_heights[str(int(idx))] = _row_px(dim.height)
        except Exception:
            pass

    merged = []
    covered = {}
    for rng in ws.merged_cells.ranges:
        r1, c1, r2, c2 = rng.min_row, rng.min_col, rng.max_row, rng.max_col
        merged.append([r1, c1, r2, c2])
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                covered["%d,%d" % (r, c)] = [r1, c1, r2, c2]

    cells = {}
    limit = min(int(ws.max_row or 1) * int(ws.max_column or 1), MAX_STYLE_CELLS)
    count = 0
    for row in ws.iter_rows():
        for cell in row:
            if count >= limit:
                break
            count += 1
            key = "%d,%d" % (cell.row, cell.column)
            rec = {}
            f = _font(cell.font)
            if f and f != base_font:
                rec["font"] = f
            bg = _fill(cell.fill)
            if bg:
                rec["fill"] = bg
            al = _align(cell.alignment)
            if al and (al["h"] != "general" or al["v"] != "bottom" or al["wrap"]):
                rec["align"] = al
            b = cell.border
            border = {"l": _side(b.left), "r": _side(b.right),
                      "t": _side(b.top), "b": _side(b.bottom)}
            if any(border.values()):
                rec["border"] = border
            if cell.number_format and cell.number_format != "General":
                rec["numfmt"] = cell.number_format
            if rec:
                cells[key] = rec
        if count >= limit:
            break

    return {
        "sheet": sheet_name,
        "active": wb.active.title,
        "base_font": base_font,
        "sheets": list(wb.sheetnames),
        "rows": max(int(ws.max_row or 1), 1),
        "cols": max(int(ws.max_column or 1), 1),
        "col_widths": col_widths,
        "row_heights": row_heights,
        "default_col": default_col,
        "default_row": default_row,
        "merged": merged,
        "covered": covered,
        "cells": cells,
        "freeze": str(ws.freeze_panes) if ws.freeze_panes else "",
    }


def read_values(path, sheet_name=None):
    """读取工作表显示值：公式取缓存结果（不把公式文本当成协作内容）"""
    wb = _load(path, data_only=True)
    if sheet_name not in wb.sheetnames:
        sheet_name = wb.active.title
    ws = wb[sheet_name]
    out = {}
    for row in ws.iter_rows():
        for cell in row:
            if cell.value is None:
                continue
            v = cell.value
            if isinstance(v, (int, float, str, bool)):
                pass
            else:
                try:
                    v = v.isoformat(sep=" ")[:19]
                except AttributeError:
                    v = str(v)
            if v != "":
                out[(cell.row, cell.column)] = v
    return {"sheet": sheet_name, "rows": max(int(ws.max_row or 1), 1),
            "cols": max(int(ws.max_column or 1), 1), "values": out}


# ---------------------------------------------------------------------------
# ZIP 层最小补丁：只替换目标单元格的 XML，其余部件（drawings / media /
# printerSettings / sharedStrings / docProps / styles.xml …）原字节复制。
# openpyxl 重新 save 会丢弃 EMF 图形、打印机设置等部件，故不用它作为主路径。
# ---------------------------------------------------------------------------
_NS_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_NS_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_CELL_RE = re.compile(
    rb'<c\b[^>]*?\br="([A-Z]+\d+)"[^>]*?(?:/>|>(.*?)</c>)', re.S)
_ROW_RE = re.compile(rb'(<row\b[^>/]*?\br="(\d+)"[^>/]*>)(.*?)(</row>)', re.S)
_ROW_SELF_RE = re.compile(rb'<row\b[^>]*?\br="(\d+)"[^>]*/>', re.S)
_MERGE_RE = re.compile(rb'<mergeCell\s+ref="([A-Z]+\d+):([A-Z]+\d+)"[^>]*>', re.S)
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _parse_coord(coord):
    if isinstance(coord, bytes):
        coord = coord.decode("ascii", "ignore")
    i = 0
    while i < len(coord) and coord[i].isalpha():
        i += 1
    col = 0
    for ch in coord[:i].upper():
        col = col * 26 + (ord(ch) - 64)
    try:
        return int(coord[i:]), col
    except ValueError:
        return 0, 0


def _coord(r, c):
    return (get_column_letter(c) + str(r)).encode("ascii")


def _esc(text):
    s = _ILLEGAL_XML.sub("", str(text))
    s = s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return s.encode("utf-8")


def _cell_xml(coord, value, old_xml=b""):
    """生成单元格 XML，保留原有 s="样式索引"，其它属性一律不碰"""
    sm = re.search(rb'\ss="(\d+)"', old_xml)
    style_attr = b' s="' + sm.group(1) + b'"' if sm else b""
    head = b'<c r="' + coord + b'"' + style_attr
    if value is None or value == "":
        return head + b"/>"
    if isinstance(value, bool):
        return head + b' t="b"><v>' + (b"1" if value else b"0") + b"</v></c>"
    text = str(value)
    if text.startswith("="):
        return head + b"><f>" + _esc(text[1:]) + b"</f><v></v></c>"
    if isinstance(value, (int, float)):
        num = str(value) if isinstance(value, int) else repr(float(value))
        return head + b"><v>" + _esc(num) + b"</v></c>"
    return (head + b' t="inlineStr"><is><t xml:space="preserve">'
            + _esc(text) + b"</t></is></c>")


def _merge_covered(xml):
    covered = set()
    for a, b in _MERGE_RE.findall(xml):
        r1, c1 = _parse_coord(a)
        r2, c2 = _parse_coord(b)
        if not r1 or not r2:
            continue
        for r in range(r1, r2 + 1):
            for c in range(c1, c2 + 1):
                if (r, c) != (r1, c1):
                    covered.add((r, c))
    return covered


def _patch_sheet(xml, values, skip_merged=True):
    """只改 sheetData 里指定坐标的 <c> 元素，其余字节一字不动"""
    if not values:
        return xml
    m = re.search(rb'(<sheetData\b[^>]*>)(.*?)(</sheetData>)', xml, re.S)
    if not m:
        raise ValueError("worksheet 缺少 sheetData")
    covered = _merge_covered(xml) if skip_merged else set()
    wanted = {}
    for (r, c), v in values.items():
        if r < 1 or c < 1 or (r, c) in covered:
            continue
        wanted.setdefault(r, {})[c] = v

    def repl_row(rm):
        rnum = int(rm.group(2))
        targets = wanted.pop(rnum, None)
        if not targets:
            return rm.group(0)
        inner = rm.group(3)
        pieces, last = [], 0
        for cm in _CELL_RE.finditer(inner):
            pieces.append(inner[last:cm.start()])
            pr, pc = _parse_coord(cm.group(1))
            if pr == rnum and pc in targets:
                pieces.append(_cell_xml(cm.group(1), targets.pop(pc), cm.group(0)))
            else:
                pieces.append(cm.group(0))
            last = cm.end()
        pieces.append(inner[last:])
        for c in sorted(targets):
            pieces.append(_cell_xml(_coord(rnum, c), targets[c]))
        return rm.group(1) + b"".join(pieces) + b"</row>"

    def repl_self_row(rm):
        rnum = int(rm.group(1))
        targets = wanted.pop(rnum, None)
        if not targets:
            return rm.group(0)
        tag = rm.group(0)[:-2].rstrip() + b">"
        cells = b"".join(_cell_xml(_coord(rnum, c), v)
                         for c, v in sorted(targets.items()))
        return tag + cells + b"</row>"

    body = _ROW_SELF_RE.sub(repl_self_row, m.group(2))
    new_body = _ROW_RE.sub(repl_row, body)
    extra = []
    for rnum in sorted(wanted):
        cells = b"".join(_cell_xml(_coord(rnum, c), v)
                         for c, v in sorted(wanted[rnum].items()))
        extra.append(b'<row r="' + str(rnum).encode("ascii")
                     + b'">' + cells + b"</row>")
    if extra:
        new_body += b"".join(extra)
    return xml[:m.start(2)] + new_body + xml[m.end(2):]


def _sheet_paths(zf):
    wb = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    relmap = {rel.get("Id"): (rel.get("Target") or "") for rel in rels}
    out = {}
    sheets = wb.find(_NS_MAIN + "sheets")
    if sheets is None:
        return out
    for sh in sheets:
        name, rid = sh.get("name"), sh.get(_NS_REL + "id")
        target = relmap.get(rid, "")
        if not target:
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        elif target.startswith("xl/"):
            path = target
        else:
            path = "xl/" + target.lstrip("/")
        out[name] = os.path.normpath(path).replace("\\", "/")
    return out


def _write_values_zip(template_path, out_path, sheet_values, skip_merged=True):
    with zipfile.ZipFile(template_path, "r") as zin:
        paths = _sheet_paths(zin)
        patched = {}
        for name, values in (sheet_values or {}).items():
            p = paths.get(name)
            if not p or not values or p not in zin.namelist():
                continue
            patched[p] = _patch_sheet(zin.read(p), values, skip_merged)
        tmp = out_path + ".tmp"
        with zipfile.ZipFile(tmp, "w") as zout:
            for info in zin.infolist():
                data = patched.get(info.filename)
                if data is None:
                    data = zin.read(info.filename)
                zi = zipfile.ZipInfo(info.filename, date_time=info.date_time)
                zi.compress_type = info.compress_type
                zi.external_attr = info.external_attr
                zi.internal_attr = info.internal_attr
                zi.create_system = info.create_system
                zi.comment = info.comment
                zout.writestr(zi, data)
        os.replace(tmp, out_path)
    return out_path


def _write_values_openpyxl(template_path, out_path, sheet_values, skip_merged=True):
    """ZIP 补丁不可用时的兜底（会丢失 openpyxl 不支持的部件）"""
    wb = _load(template_path, data_only=False)
    for sheet_name, values in (sheet_values or {}).items():
        if sheet_name not in wb.sheetnames or not values:
            continue
        ws = wb[sheet_name]
        covered = set()
        if skip_merged:
            for rng in ws.merged_cells.ranges:
                for r in range(rng.min_row, rng.max_row + 1):
                    for c in range(rng.min_col, rng.max_col + 1):
                        if (r, c) != (rng.min_row, rng.min_col):
                            covered.add((r, c))
        for (r, c), v in values.items():
            if r < 1 or c < 1 or (r, c) in covered:
                continue
            try:
                ws.cell(row=r, column=c).value = (None if v == "" else v)
            except (AttributeError, ValueError):
                continue
    wb.save(out_path)
    return out_path


def write_values(template_path, out_path, sheet_values, skip_merged=True):
    """
    以模板为基底写回协作编辑过的值。

    sheet_values: {sheet_name: {(row, col): value}}，只写这些坐标。
    采用 ZIP 最小补丁，除目标单元格外整个工作簿（样式、合并、公式、图形、
    图片、打印设置、其它工作表、文档属性）全部按原字节保留。
    """
    if os.path.abspath(template_path) == os.path.abspath(out_path):
        raise ValueError("输出路径不能覆盖样式模板")
    try:
        return _write_values_zip(template_path, out_path, sheet_values,
                                 skip_merged)
    except Exception:
        return _write_values_openpyxl(template_path, out_path, sheet_values,
                                      skip_merged)


def describe(style):
    """界面用的简短样式摘要"""
    if not style:
        return ""
    bordered = sum(1 for rec in style.get("cells", {}).values() if "border" in rec)
    return "%d 个工作表 · %d 个合并区域 · %d 个带边框单元格" % (
        len(style.get("sheets", [])), len(style.get("merged", [])), bordered)


def audit(template_path, out_path, sheet_name=None):
    """比对模板与输出文件的样式指纹，用于自检/测试"""
    a = extract_style(template_path, sheet_name)
    b = extract_style(out_path, sheet_name)
    keys = ("sheet", "sheets", "rows", "cols", "col_widths", "row_heights",
            "merged", "cells", "freeze")
    return {k: (a.get(k) == b.get(k)) for k in keys}
