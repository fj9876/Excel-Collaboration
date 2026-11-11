# -*- coding: utf-8 -*-
"""
UI 兼容层
=========
* Win10/11：使用 tkwin11 的 Win11 Fluent 主题与原生控件
* Win7/8/8.1：跳过 Win11 专属 DWM 调用，改用 clam 主题 + 手工配色（tkwin11 控件仍可用）
* tkwin11 万一不可用：提供 ttk 版 FluentButton / SettingsCard / Spreadsheet 兜底
"""

import os
import sys
import tkinter as tk
from tkinter import ttk

try:
    import tkwin11 as ui
except Exception:                                  # pragma: no cover
    ui = None


def is_legacy_windows():
    """Windows 7 / 8 / 8.1 走兼容路径（Win10 起才支持 Fluent/DWM 新特性）

    设置环境变量 LANEXCEL_WIN7_MODE=1 可在 Win10/11 上强制模拟 Win7 兼容模式，
    便于验证降级界面（打包后的 exe 同样有效）。
    """
    if os.environ.get("LANEXCEL_WIN7_MODE") == "1":
        return True
    if sys.platform != "win32":
        return False
    try:
        v = sys.getwindowsversion()
        return (v.major, v.minor) < (10, 0)
    except Exception:
        return False


LEGACY = is_legacy_windows()
FONT = "Microsoft YaHei" if LEGACY else "Microsoft YaHei UI"


class SimpleSheet(ttk.Frame):
    """无 tkwin11 时的兜底电子表格：Treeview + 单元格 Entry 编辑"""

    def __init__(self, master, rows=60, columns=26, headers=None,
                 column_width=104, row_height=27):
        ttk.Frame.__init__(self, master)
        self.rows, self.columns = rows, columns
        self.headers = headers or [chr(65 + i % 26) for i in range(columns)]
        self.data = [["" for _ in range(columns)] for _ in range(rows)]
        self.active = (0, 0)
        self.locked = set()        # 隐私遮罩格（0-based），只读
        self._cols = [str(i) for i in range(columns)]
        self.tree = ttk.Treeview(self, columns=self._cols,
                                 show="tree headings", height=25)
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=48, stretch=False)
        for i, c in enumerate(self._cols):
            self.tree.heading(c, text=self.headers[i])
            self.tree.column(c, width=column_width, stretch=False)
        vs = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        hs = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vs.grid(row=0, column=1, sticky="ns")
        hs.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        try:
            ttk.Style(self).configure("Treeview", rowheight=row_height)
        except Exception:
            pass
        for r in range(rows):
            self.tree.insert("", "end", iid=str(r + 1), text=str(r + 1),
                             values=tuple(self.data[r]))
        self.tree.bind("<Double-1>", self._begin_edit)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

    def _on_select(self, event=None):
        sel = self.tree.selection()
        if sel:
            self.active = (int(sel[0]) - 1, 0)
        self.event_generate("<<SheetSelect>>")

    def _begin_edit(self, event):
        if self.tree.identify_region(event.x, event.y) != "cell":
            return
        row = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        if not row or not col or col == "#0":
            return
        try:
            r, c = int(row) - 1, int(col.lstrip("#")) - 1
        except ValueError:
            return
        if not (0 <= r < self.rows and 0 <= c < self.columns):
            return
        if (r, c) in self.locked:
            self.event_generate("<<CellLocked>>")
            return
        bbox = self.tree.bbox(row, col)
        if not bbox:
            return
        self.active = (r, c)
        e = tk.Entry(self.tree)
        e.insert(0, str(self.data[r][c]))
        e.place(x=bbox[0], y=bbox[1], width=bbox[2], height=bbox[3])
        e.focus_set()
        e.select_range(0, "end")

        def commit(_ev=None):
            val = e.get()
            try:
                e.destroy()
            except Exception:
                pass
            self.data[r][c] = val
            self.tree.set(row, self._cols[c], val)
            self.event_generate("<<SheetChanged>>")

        e.bind("<Return>", commit)
        e.bind("<FocusOut>", commit)
        e.bind("<Escape>", lambda _ev: e.destroy())

    def set_locked(self, cells):
        """隐私遮罩格：只读（Treeview 整行着色，遮罩仅由 *** 文本体现）"""
        self.locked = set(tuple(x) for x in (cells or ()))
        try:
            self.tree.tag_configure("masked", foreground="#9AA0A8")
        except Exception:
            pass

    def set_cell(self, r, c, text):
        if 0 <= r < self.rows and 0 <= c < self.columns:
            self.data[r][c] = "" if text is None else str(text)
            self.tree.set(str(r + 1), self._cols[c], self.data[r][c])

    def set_data(self, matrix, rows=None, columns=None):
        for r in range(self.rows):
            for c in range(self.columns):
                val = ""
                if r < len(matrix) and c < len(matrix[r]) and matrix[r][c] is not None:
                    val = str(matrix[r][c])
                self.data[r][c] = val
            self.tree.item(str(r + 1), values=tuple(self.data[r]))

    def get_data(self):
        return [list(row) for row in self.data]


def init_theme(root):
    """应用主题；返回 (是否使用 tkwin11, 是否 Win7 兼容模式)"""
    if ui is not None:
        try:
            ui.enable_dpi_awareness()
        except Exception:
            pass
        root = root._root() if hasattr(root, "_root") else root
    if ui is not None:
        try:
            ui.apply_theme(root)
        except Exception:
            pass
        if not LEGACY:
            try:
                ui.configure_fluent(root)     # Win11 圆角 / Fluent 卡片
            except Exception:
                pass
    style = ttk.Style(root)
    if ui is None or LEGACY:
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure(".", background="#F7FAFC", foreground="#1A1A1A",
                        font=(FONT, 10))
        style.configure("TFrame", background="#F7FAFC")
        style.configure("TLabel", background="#F7FAFC", foreground="#1A1A1A")
        style.configure("TLabelframe", background="#F7FAFC")
        style.configure("TLabelframe.Label", background="#F7FAFC",
                        foreground="#1A1A1A")
        style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF")
        style.configure("TButton", padding=(10, 6))
        style.configure("Primary.TButton", background="#0067C0",
                        foreground="#FFFFFF", padding=(10, 6), borderwidth=0)
        style.map("Primary.TButton",
                  background=[("active", "#1975C5"), ("disabled", "#B5BAC4")],
                  foreground=[("disabled", "#FFFFFF")])
        style.configure("Nav.TButton", padding=(14, 9), anchor="w")
        style.configure("NavActive.TButton", background="#0067C0",
                        foreground="#FFFFFF", padding=(14, 9), anchor="w")
        style.map("NavActive.TButton", background=[("active", "#1975C5")])
    else:
        style.configure("Nav.TButton", padding=(14, 9), anchor="w")
        style.configure("NavActive.TButton", padding=(14, 9), anchor="w")
    try:
        style.configure("Muted.TLabel", foreground="#606060")
    except Exception:
        pass
    return ui is not None, LEGACY


def make_button(master, text, command, primary=False):
    style = "Primary.TButton" if primary else "TButton"
    if ui is not None and hasattr(ui, "FluentButton"):
        try:
            return ui.FluentButton(master, text=text, command=command, style=style)
        except Exception:
            pass
    return ttk.Button(master, text=text, command=command, style=style)


def make_card(master, title, description="", symbol=None):
    if ui is not None and hasattr(ui, "SettingsCard"):
        try:
            return ui.SettingsCard(master, title, description,
                                   symbol=symbol or "settings")
        except Exception:
            pass
    f = ttk.LabelFrame(master, text="  " + title + "  ", padding=10)
    ttk.Label(f, text=description, style="Muted.TLabel").pack(anchor="w", pady=(0, 8))
    f.actions = ttk.Frame(f)
    f.actions.pack(fill="x")
    return f


def make_sheet(master, rows, columns, headers, column_width=104, row_height=27):
    if ui is not None and hasattr(ui, "Spreadsheet"):
        try:
            return ui.Spreadsheet(master, rows=rows, columns=columns,
                                  headers=headers, column_width=column_width,
                                  row_height=row_height)
        except Exception:
            pass
    return SimpleSheet(master, rows=rows, columns=columns, headers=headers,
                       column_width=column_width, row_height=row_height)


def make_photo(b64, master=None):
    """PNG base64 → Tk PhotoImage（Tk 8.6 自动识别 PNG/GIF）"""
    try:
        return tk.PhotoImage(master=master, data=b64)
    except Exception:
        return None
