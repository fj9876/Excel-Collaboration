# -*- coding: utf-8 -*-
"""
保真样式表格控件
================
按原始 xlsx 的样式元数据渲染：合并单元格、字体/字号/粗斜体/字色、填充色、
水平垂直对齐与自动换行、四边框线与线型、逐列列宽与逐行行高。

编辑只改「值」，绝不改动任何样式。对外接口与 tkwin11.Spreadsheet 保持一致：
    set_data(matrix, rows=, columns=) / set_cell(r,c,text) / get_data()
    active / <<SheetChanged>> / <<SheetSelect>>

纯 tkinter 标准库实现，Win7 + Python 3.8 可用。
"""

import bisect
import tkinter as tk
from tkinter import ttk, font as tkfont

HEAD_W = 46
HEAD_H = 26
GRID_LINE = "#D7DEE8"
HEAD_BG = "#F3F6FA"
HEAD_FG = "#5A6472"
ACCENT = "#0067C0"
MASK_BG = "#F1F3F6"     # 隐私遮罩格背景
MASK_FG = "#9AA0A8"     # 隐私遮罩格文字（***）


def col_name(c):
    s = ""
    while c > 0:
        s = chr(65 + (c - 1) % 26) + s
        c = (c - 1) // 26
    return s


class StyledSheet(ttk.Frame):
    def __init__(self, master, rows=60, columns=26, headers=None,
                 column_width=104, row_height=27):
        ttk.Frame.__init__(self, master)
        self.rows, self.columns = max(1, rows), max(1, columns)
        self.headers = list(headers or [])
        self.default_col, self.default_row = column_width, row_height
        self.data = [["" for _ in range(self.columns)] for _ in range(self.rows)]
        self.active = (0, 0)
        self.style = None
        self.base_font = None
        self._col_w = [column_width] * self.columns
        self._row_h = [row_height] * self.rows
        self._cell_styles = {}
        self.locked = set()        # {(r,c)} 0-based 隐私遮罩格：禁止编辑
        self._merges = []
        self._covered = {}
        self._fonts = {}
        self._head_font = tkfont.Font(self, size=9)
        self._x = [0]
        self._y = [0]
        self._min_rows, self._min_cols = self.rows, self.columns
        self._draw_job = None
        self.editor = None
        self._editor_item = None
        self._edit_cell = None

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        opts = dict(background="#FFFFFF", highlightthickness=0, borderwidth=0)
        self.corner = tk.Canvas(self, width=HEAD_W, height=HEAD_H, **opts)
        self.top = tk.Canvas(self, height=HEAD_H, **opts)
        self.left = tk.Canvas(self, width=HEAD_W, **opts)
        self.canvas = tk.Canvas(self, takefocus=True, **opts)
        self.corner.grid(row=0, column=0, sticky="nsew")
        self.top.grid(row=0, column=1, sticky="ew")
        self.left.grid(row=1, column=0, sticky="ns")
        self.canvas.grid(row=1, column=1, sticky="nsew")
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self._yview)
        self.hbar = ttk.Scrollbar(self, orient="horizontal", command=self._xview)
        self.vbar.grid(row=1, column=2, sticky="ns")
        self.hbar.grid(row=2, column=1, sticky="ew")
        self.canvas.configure(xscrollcommand=self._xscroll,
                              yscrollcommand=self._yscroll)

        self.canvas.bind("<Configure>", lambda e: self.schedule())
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<Double-Button-1>", lambda e: self.start_edit())
        self.canvas.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<Shift-MouseWheel>",
                         lambda e: self._wheel(e, horizontal=True))
        for key, delta in (("Left", (0, -1)), ("Right", (0, 1)),
                           ("Up", (-1, 0)), ("Down", (1, 0))):
            self.canvas.bind("<" + key + ">",
                             lambda e, d=delta: self._navigate(d))
        for key in ("Return", "F2"):
            self.canvas.bind("<" + key + ">", lambda e: self.start_edit())
        self.canvas.bind("<Delete>", lambda e: self._clear())
        self.canvas.bind("<Control-v>", lambda e: self.paste())
        self.canvas.bind("<Control-c>", lambda e: self.copy())
        self.canvas.bind("<Control-Home>", lambda e: self.select(0, 0))
        self.canvas.bind("<Home>", lambda e: self.select(self.active[0], 0))
        self._recalc()

    # ------------------------------------------------------------ 尺寸 / 样式
    def _recalc(self):
        self._x = [0]
        for w in self._col_w:
            self._x.append(self._x[-1] + w)
        self._y = [0]
        for h in self._row_h:
            self._y.append(self._y[-1] + h)
        total_w, total_h = self._x[-1], self._y[-1]
        for cv in (self.canvas, self.top, self.left):
            cv.configure(scrollregion=(0, 0, total_w, total_h))
        self.top.configure(scrollregion=(0, 0, total_w, HEAD_H))
        self.left.configure(scrollregion=(0, 0, HEAD_W, total_h))

    def _resize(self, rows, cols):
        rows = max(1, rows, self._min_rows)
        cols = max(1, cols, self._min_cols)
        if rows > self.rows:
            for _ in range(rows - self.rows):
                self.data.append([""] * self.columns)
                self._row_h.append(self.default_row)
        if cols > self.columns:
            for row in self.data:
                row.extend([""] * (cols - self.columns))
            self._col_w.extend([self.default_col] * (cols - self.columns))
        self.rows, self.columns = rows, cols

    def _ensure_arrays(self):
        while len(self._col_w) < self.columns:
            self._col_w.append(self.default_col)
        while len(self._row_h) < self.rows:
            self._row_h.append(self.default_row)

    def apply_style(self, style):
        """装载 xlsx 样式元数据（不改变数据）"""
        self.finish_edit(False)
        if not style:
            self.style = None
            self._cell_styles = {}
            self._merges = []
            self._covered = {}
            self._col_w = [self.default_col] * self.columns
            self._row_h = [self.default_row] * self.rows
            self._fonts = {}
            self._recalc()
            self.schedule()
            return None
        self.style = style
        self.base_font = style.get("base_font") or {
            "family": "宋体", "size": 11, "bold": False,
            "italic": False, "underline": False, "color": None}
        self._min_rows = max(self.rows, int(style.get("rows", 1)) + 2, 60)
        self._min_cols = max(self.columns, int(style.get("cols", 1)) + 2, 26)
        self._resize(self._min_rows, self._min_cols)
        self._ensure_arrays()
        self.default_col = int(style.get("default_col") or 96)
        self.default_row = int(style.get("default_row") or 26)
        widths = style.get("col_widths") or {}
        heights = style.get("row_heights") or {}
        for c in range(self.columns):
            self._col_w[c] = int(widths.get(col_name(c + 1)) or self.default_col)
        for r in range(self.rows):
            self._row_h[r] = int(heights.get(str(r + 1)) or self.default_row)
        self._cell_styles = {}
        for key, rec in (style.get("cells") or {}).items():
            try:
                a, b = key.split(",")
                self._cell_styles[(int(a) - 1, int(b) - 1)] = rec
            except (ValueError, AttributeError):
                continue
        self._merges = []
        self._covered = {}
        for item in (style.get("merged") or []):
            try:
                r1, c1, r2, c2 = (int(x) - 1 for x in item[:4])
            except (ValueError, TypeError):
                continue
            m = (r1, c1, r2, c2)
            self._merges.append(m)
            for r in range(r1, r2 + 1):
                for c in range(c1, c2 + 1):
                    self._covered[(r, c)] = m
        self._fonts = {}
        self._recalc()
        self.schedule()
        return style

    def _font_for(self, rec):
        f = (rec or {}).get("font") or self.base_font or {}
        key = (f.get("family"), int(f.get("size") or 11),
               bool(f.get("bold")), bool(f.get("italic")),
               bool(f.get("underline")))
        if key not in self._fonts:
            try:
                self._fonts[key] = tkfont.Font(
                    self, family=key[0] or "宋体", size=max(6, key[1]),
                    weight="bold" if key[2] else "normal",
                    slant="italic" if key[3] else "roman",
                    underline=1 if key[4] else 0)
            except Exception:
                self._fonts[key] = tkfont.Font(self, size=10)
        return self._fonts[key]

    @staticmethod
    def _font_color(rec):
        return ((rec or {}).get("font") or {}).get("color") or "#1A1A1A"

    # ------------------------------------------------------------ 滚动
    def _xview(self, *args):
        self.finish_edit(False)
        return self.canvas.xview(*args)

    def _yview(self, *args):
        self.finish_edit(False)
        return self.canvas.yview(*args)

    def _xscroll(self, first, last):
        self.hbar.set(first, last)
        self.top.xview_moveto(first)
        self.schedule()

    def _yscroll(self, first, last):
        self.vbar.set(first, last)
        self.left.yview_moveto(first)
        self.schedule()

    def _wheel(self, event, horizontal=False):
        if horizontal:
            self.canvas.xview_scroll(-int(event.delta / 120) * 3, "units")
        else:
            self.canvas.yview_scroll(-int(event.delta / 120) * 3, "units")
        return "break"

    # ------------------------------------------------------------ 绘制
    def schedule(self):
        if self._draw_job is None:
            try:
                self._draw_job = self.after_idle(self._draw)
            except Exception:
                self._draw_job = None

    def _visible(self):
        x0, y0 = self.canvas.canvasx(0), self.canvas.canvasy(0)
        w, h = self.canvas.winfo_width(), self.canvas.winfo_height()
        c0 = max(0, bisect.bisect_right(self._x, x0) - 1)
        c1 = min(self.columns, bisect.bisect_right(self._x, x0 + w) + 1)
        r0 = max(0, bisect.bisect_right(self._y, y0) - 1)
        r1 = min(self.rows, bisect.bisect_right(self._y, y0 + h) + 1)
        return r0, r1, c0, c1

    def _box(self, r1, c1, r2=None, c2=None):
        r2 = r1 if r2 is None else r2
        c2 = c1 if c2 is None else c2
        x, y = self._x[c1], self._y[r1]
        w = self._x[min(c2 + 1, self.columns)] - x
        h = self._y[min(r2 + 1, self.rows)] - y
        return x, y, w, h

    def _draw_border(self, x, y, w, h, rec):
        b = (rec or {}).get("border") or {}
        sides = (("l", x, y, x, y + h), ("r", x + w, y, x + w, y + h),
                 ("t", x, y, x + w, y), ("b", x, y + h, x + w, y + h))
        for key, x1, y1, x2, y2 in sides:
            spec = b.get(key)
            if not spec:
                continue
            try:
                _, color, width, dash = spec
                kw = {"fill": color or "#7F7F7F", "width": max(1, int(width or 1))}
                if dash:
                    kw["dash"] = tuple(dash)
                self.canvas.create_line(x1, y1, x2, y2, **kw)
            except Exception:
                pass

    def _draw_text(self, x, y, w, h, text, rec, font_obj, masked=False):
        if text is None:
            return
        text = str(text)
        if not text:
            return
        al = (rec or {}).get("align") or {}
        halign = al.get("h") or "general"
        valign = al.get("v") or "bottom"
        if halign == "general":
            try:
                float(text.replace(",", ""))
                halign = "right"
            except ValueError:
                halign = "left"
        numfmt = (rec or {}).get("numfmt") or ""
        if "%" in numfmt:
            try:
                text = "%.2f%%" % (float(text) * 100)
            except ValueError:
                pass
        if halign == "center":
            tx, ax = x + w / 2, "center"
        elif halign == "right":
            tx, ax = x + w - 5, "e"
        else:
            tx, ax = x + 5, "w"
        if valign == "center":
            ty, ay = y + h / 2, "center"
        elif valign == "top":
            ty, ay = y + 3, "n"
        else:
            ty, ay = y + h - 3, "s"
        anchor = {
            ("n", "w"): "nw", ("n", "center"): "n", ("n", "e"): "ne",
            ("center", "w"): "w", ("center", "center"): "center",
            ("center", "e"): "e",
            ("s", "w"): "sw", ("s", "center"): "s", ("s", "e"): "se",
        }.get((ay, ax), "w")
        lines = [text]
        if al.get("wrap"):
            try:
                lh = max(10, font_obj.metrics("linespace"))
                max_lines = max(1, int((h - 4) // lh))
                lines, cur = [], ""
                for ch in text:
                    if font_obj.measure(cur + ch) > w - 10 and cur:
                        lines.append(cur)
                        cur = ""
                        if len(lines) >= max_lines:
                            break
                    cur += ch
                if cur and len(lines) < max_lines:
                    lines.append(cur)
                if len(lines) >= max_lines and len("".join(lines)) < len(text):
                    lines[-1] = lines[-1][:-1] + "…"
            except Exception:
                lines = [text]
        else:
            try:
                limit = w - 10
                if font_obj.measure(text) > limit:
                    cut = text
                    while len(cut) > 1 and font_obj.measure(cut + "…") > limit:
                        cut = cut[:-1]
                    lines = [cut + "…"]
            except Exception:
                pass
        if masked:
            tx, ax, ty, ay = x + w / 2, "center", y + h / 2, "center"
            anchor = "center"
            lines = [text]
        self.canvas.create_text(tx, ty, text="\n".join(lines), anchor=anchor,
                                font=font_obj,
                                fill=MASK_FG if masked else self._font_color(rec),
                                justify="center" if ax == "center" else "left")

    def _draw(self):
        self._draw_job = None
        if self.editor is not None:
            return                 # 编辑期间不重绘，避免销毁编辑框
        for cv in (self.canvas, self.top, self.left, self.corner):
            cv.delete("all")
        r0, r1, c0, c1 = self._visible()
        # 列头 / 行头
        head_font = self._head_font
        for c in range(c0, c1):
            x, w = self._x[c], self._col_w[c]
            self.top.create_rectangle(x, 0, x + w, HEAD_H, fill=HEAD_BG,
                                      outline=GRID_LINE)
            self.top.create_text(x + w / 2, HEAD_H / 2, text=col_name(c + 1),
                                 font=head_font, fill=HEAD_FG)
        for r in range(r0, r1):
            y, h = self._y[r], self._row_h[r]
            self.left.create_rectangle(0, y, HEAD_W, y + h, fill=HEAD_BG,
                                       outline=GRID_LINE)
            self.left.create_text(HEAD_W / 2, y + h / 2, text=str(r + 1),
                                  font=head_font, fill=HEAD_FG)
        self.corner.create_rectangle(0, 0, HEAD_W, HEAD_H, fill=HEAD_BG,
                                     outline=GRID_LINE)
        # 普通单元格（合并区由后面统一绘制）
        for r in range(r0, r1):
            for c in range(c0, c1):
                if (r, c) in self._covered:
                    continue
                x, y, w, h = self._box(r, c)
                rec = self._cell_styles.get((r, c))
                locked = (r, c) in self.locked
                self.canvas.create_rectangle(
                    x, y, x + w, y + h,
                    fill=MASK_BG if locked else ((rec or {}).get("fill") or "#FFFFFF"),
                    outline=GRID_LINE, width=1)
                self._draw_border(x, y, w, h, rec)
                self._draw_text(x, y, w, h, self.data[r][c], rec,
                                self._font_for(rec), masked=locked)
        # 合并单元格
        for (mr1, mc1, mr2, mc2) in self._merges:
            if mr2 < r0 or mr1 >= r1 or mc2 < c0 or mc1 >= c1:
                continue
            x, y, w, h = self._box(mr1, mc1, mr2, mc2)
            rec = self._cell_styles.get((mr1, mc1))
            self.canvas.create_rectangle(
                x, y, x + w, y + h,
                fill=(rec or {}).get("fill") or "#FFFFFF",
                outline=GRID_LINE, width=1)
            self._draw_border(x, y, w, h, rec)
            self._draw_text(x, y, w, h, self.data[mr1][mc1], rec,
                            self._font_for(rec))
        # 选中框
        ar, ac = self.active
        m = self._covered.get((ar, ac))
        if m:
            x, y, w, h = self._box(m[0], m[1], m[2], m[3])
        else:
            x, y, w, h = self._box(ar, ac)
        self.canvas.create_rectangle(x + 1, y + 1, x + w - 1, y + h - 1,
                                     outline=ACCENT, width=2)

    # ------------------------------------------------------------ 交互
    def _cell_at(self, ex, ey):
        x, y = self.canvas.canvasx(ex), self.canvas.canvasy(ey)
        c = max(0, min(self.columns - 1, bisect.bisect_right(self._x, x) - 1))
        r = max(0, min(self.rows - 1, bisect.bisect_right(self._y, y) - 1))
        return r, c

    def _press(self, event):
        r, c = self._cell_at(event.x, event.y)
        self.select(r, c)
        self.canvas.focus_set()

    def select(self, r, c):
        m = self._covered.get((r, c))
        if m:
            r, c = m[0], m[1]
        self.active = (max(0, min(self.rows - 1, r)),
                       max(0, min(self.columns - 1, c)))
        self.event_generate("<<SheetSelect>>")
        self.schedule()

    def _navigate(self, delta):
        r, c = self.active
        m = self._covered.get((r, c))
        if m:
            r, c = (m[2] + 1, c) if delta[0] > 0 else (
                (m[0] - 1, c) if delta[0] < 0 else (r, m[2] + 1))
        else:
            r, c = r + delta[0], c + delta[1]
        self.select(max(0, min(self.rows - 1, r)),
                    max(0, min(self.columns - 1, c)))
        self.see()

    def see(self):
        r, c = self.active
        if self._y[r] < self.canvas.canvasy(0):
            self.canvas.yview_moveto(self._y[r] / max(1, self._y[-1]))
        elif self._y[r + 1] > self.canvas.canvasy(0) + self.canvas.winfo_height():
            self.canvas.yview_moveto(
                self._y[r + 1] / max(1, self._y[-1]))
        if self._x[c] < self.canvas.canvasx(0):
            self.canvas.xview_moveto(self._x[c] / max(1, self._x[-1]))
        elif self._x[c + 1] > self.canvas.canvasx(0) + self.canvas.winfo_width():
            self.canvas.xview_moveto(self._x[c + 1] / max(1, self._x[-1]))
        self.schedule()

    def start_edit(self):
        self.finish_edit(False)
        r, c = self.active
        m = self._covered.get((r, c))
        r1, c1, r2, c2 = m if m else (r, c, r, c)
        if (r1, c1) in getattr(self, "locked", ()):
            self.event_generate("<<CellLocked>>")   # 隐私遮罩格：只读
            return
        x, y, w, h = self._box(r1, c1, r2, c2)
        e = tk.Entry(self.canvas, justify="left", relief="solid", bd=1,
                     font=self._font_for(self._cell_styles.get((r1, c1))))
        e.insert(0, str(self.data[r1][c1]))
        self._editor_item = self.canvas.create_window(
            x, y, window=e, width=max(30, w), height=max(18, h), anchor="nw")
        self.editor = e
        self._edit_cell = (r1, c1)
        e.focus_set()
        e.select_range(0, "end")
        e.bind("<Return>", lambda ev: self.finish_edit(True))
        e.bind("<FocusOut>", lambda ev: self.finish_edit(True))
        e.bind("<Escape>", lambda ev: self.finish_edit(False))

    def finish_edit(self, commit=True):
        e = self.editor
        if e is None:
            return
        self.editor = None
        cell = self._edit_cell
        self._edit_cell = None
        try:
            value = e.get() if commit else None
        except Exception:
            value = None
        try:
            if self._editor_item is not None:
                self.canvas.delete(self._editor_item)
        except Exception:
            pass
        self._editor_item = None
        try:
            e.destroy()
        except Exception:
            pass
        if commit and value is not None and cell:
            r, c = cell
            if self.data[r][c] != value:
                self.data[r][c] = value
                self.event_generate("<<SheetChanged>>")
        self.schedule()

    def _clear(self):
        r, c = self.active
        if (r, c) in getattr(self, "locked", ()):
            self.event_generate("<<CellLocked>>")
            return
        if self.data[r][c] != "":
            self.data[r][c] = ""
            self.event_generate("<<SheetChanged>>")
            self.schedule()

    def copy(self):
        r, c = self.active
        try:
            self.clipboard_clear()
            self.clipboard_append(str(self.data[r][c]))
        except Exception:
            pass
        return "break"

    def paste(self):
        try:
            text = self.clipboard_get()
        except Exception:
            return "break"
        r0, c0 = self.active
        changed = False
        for i, line in enumerate(text.replace("\r\n", "\n").split("\n")):
            for j, val in enumerate(line.split("\t")):
                r, c = r0 + i, c0 + j
                if 0 <= r < self.rows and 0 <= c < self.columns:
                    merge = self._covered.get((r, c))
                    if merge and (r, c) != (merge[0], merge[1]):
                        continue          # 合并区内部格只读，保护原样式
                    if (r, c) in getattr(self, "locked", ()):
                        continue          # 隐私遮罩格不可粘贴
                    if self.data[r][c] != val:
                        self.data[r][c] = val
                        changed = True
        if changed:
            self.event_generate("<<SheetChanged>>")
            self.schedule()
        return "break"

    # ------------------------------------------------------------ 数据接口
    def set_locked(self, cells):
        """设置隐私遮罩格（0-based 坐标集合）：只读、灰底、不可粘贴"""
        new = set(tuple(x) for x in (cells or ()))
        if new == getattr(self, "locked", None):
            return
        self.locked = new
        if self.editor is not None and self._edit_cell in new:
            self.finish_edit(False)
        self.schedule()

    def set_cell(self, r, c, text):
        if 0 <= r < self.rows and 0 <= c < self.columns:
            self.data[r][c] = "" if text is None else str(text)
            self.schedule()

    def set_data(self, matrix, rows=None, columns=None):
        self.finish_edit(False)
        self._resize(max(rows or 0, len(matrix)),
                     max(columns or 0, max((len(x) for x in matrix), default=0)))
        self._ensure_arrays()
        for r in range(self.rows):
            for c in range(self.columns):
                self.data[r][c] = ""
                if r < len(matrix) and c < len(matrix[r]) and matrix[r][c] is not None:
                    self.data[r][c] = str(matrix[r][c])
        self._recalc()
        self.schedule()

    def get_data(self):
        return [list(row) for row in self.data]
