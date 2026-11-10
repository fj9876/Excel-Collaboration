# -*- coding: utf-8 -*-
"""
LanExcel v3 桌面客户端
======================
* 最右侧竖向导航：协作表格 / 局域网群聊 / 在线成员 / 跨网段联机 / 我的资料
* 局域网群聊：建群、邀请、发言、把协作表格或本地 Excel 发到群里变成协作表格
* 卡通头像 / 自定义头像；成员之间可发送任意大小文件（流式分块）
* Win10/11 用 tkwin11 Fluent 主题；Win7/8 自动降级为 clam 兼容主题，功能完全一致
* 打包成独立 exe 后目标机无需安装 Python
"""

import json
import os
import sys
import time
import traceback
import webbrowser

APP_DIR = os.path.dirname(os.path.abspath(__file__))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

from node import Node, now_ns, CONFIG_PATH            # noqa: E402
from avatars import (CARTOON_COUNT, avatar_b64, cartoon_b64,   # noqa: E402
                     custom_avatar_b64)
from collab import human_size                        # noqa: E402
from sheetstyle import describe as style_summary     # noqa: E402
from styled_sheet import StyledSheet                 # noqa: E402

import tkinter as tk                                  # noqa: E402
from tkinter import ttk, messagebox, filedialog, simpledialog   # noqa: E402
from ui_compat import (init_theme, make_button, make_card,      # noqa: E402
                       make_sheet, make_photo, FONT, LEGACY)

SHEET_ROWS, SHEET_COLS = 60, 26
NAV_ITEMS = [("sheet", "协作表格"), ("chat", "局域网群聊"),
             ("peers", "在线成员"), ("net", "跨网段联机"),
             ("profile", "我的资料")]


def col_name(c):
    s = ""
    while c > 0:
        s = chr(65 + (c - 1) % 26) + s
        c = (c - 1) // 26
    return s


def to_value(text):
    text = "" if text is None else str(text)
    if text.startswith("="):
        return text
    try:
        if text.strip() and text.strip() == text:
            f = float(text)
            return int(f) if f.is_integer() else f
    except ValueError:
        pass
    return text


class LanExcelApp:
    def __init__(self, root, node):
        self.root = root
        self.node = node
        self.page = ""
        self.room = ""
        self.since = 0
        self.grid_state = {}
        self.view_rows, self.view_cols = SHEET_ROWS, SHEET_COLS
        self._style_version = -1
        self._sheet_names = []
        self._active_sheet = ""
        self._peers_sig = ""
        self._rooms_sig = ""
        self._groups_sig = ""
        self._peer_photos = {}
        self._photo_cache = {}
        self._avatar_photos = []
        self._pending_avatar = None

        self.pages = {}
        self.nav_buttons = {}

        self.chat_since = 0
        self.current_gid = ""
        self.group_ids = []
        self.group_data = []
        self._rendered_mids = set()
        self._need_rebuild = False

        root.title("LanExcel · 内网去中心化 Excel 协作 v3")
        root.geometry("1280x800")
        root.minsize(1020, 640)

        self.status = tk.StringVar(value="正在启动节点…")
        self.info = tk.StringVar(value="")
        self.scan_info = tk.StringVar(value="扫描状态: 初始化")
        self.name_var = tk.StringVar(value=node.name)
        self.note_var = tk.StringVar(value=node.note)
        self.room_var = tk.StringVar(value="")
        self.sheet_var = tk.StringVar(value="")
        self.subnet_var = tk.StringVar()
        self.favorite_var = tk.StringVar()
        self.chat_input = tk.StringVar()
        self.chat_title = tk.StringVar(value="群聊")

        self._build_header()
        body = ttk.Frame(root)
        body.pack(fill="both", expand=True, padx=(14, 0), pady=(0, 6))
        self.content = ttk.Frame(body)
        self.content.pack(side="left", fill="both", expand=True)
        self._build_nav(body)

        self._build_sheet_tab()
        self._build_chat_tab()
        self._build_peers_tab()
        self._build_network_tab()
        self._build_profile_tab()
        self._build_footer()

        self.node.on_file_received = self._on_file_received
        self.show_page("sheet")
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(400, self.refresh)

    # ------------------------------------------------------------ 布局
    def _page(self, key):
        f = ttk.Frame(self.content, padding=10)
        f.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.pages[key] = f
        return f

    def show_page(self, key):
        self.page = key
        self.pages[key].lift()
        for k, b in self.nav_buttons.items():
            try:
                b.configure(style="Primary.TButton" if k == key else "TButton")
            except Exception:
                pass

    def _build_header(self):
        h = ttk.Frame(self.root, padding=(16, 12, 16, 6))
        h.pack(fill="x")
        ttk.Label(h, text="LanExcel", font=(FONT, 17, "bold")).pack(side="left")
        ttk.Label(h, text="  内网去中心化 Excel 协作 · 群聊 / 文件传输 / 跨网段",
                  style="Muted.TLabel").pack(side="left")
        make_button(h, "打开浏览器视图", self.open_browser).pack(side="right")
        ttk.Label(h, textvariable=self.scan_info,
                  style="Muted.TLabel").pack(side="right", padx=14)

    def _build_nav(self, body):
        """需求 1：四个（现五个）菜单按钮放到最右侧竖向排列"""
        nav = ttk.Frame(body, width=200, padding=(12, 10, 14, 10))
        nav.pack(side="right", fill="y", padx=(8, 0))
        nav.pack_propagate(False)
        ttk.Label(nav, text="功能导航", style="Muted.TLabel").pack(anchor="w", pady=(0, 10))
        for key, label in NAV_ITEMS:
            b = ttk.Button(nav, text="  " + label, style="TButton",
                           command=lambda k=key: self.show_page(k))
            b.pack(fill="x", pady=4, ipady=7)
            self.nav_buttons[key] = b
        ttk.Separator(nav, orient="horizontal").pack(fill="x", pady=14)
        self.nav_avatar = ttk.Label(nav)
        self.nav_avatar.pack(pady=(0, 6))
        self.nav_name = ttk.Label(nav, text=self.node.name,
                                  style="Muted.TLabel", wraplength=160,
                                  justify="center")
        self.nav_name.pack()
        ttk.Label(nav, text="Win7 兼容模式" if LEGACY else "Fluent 界面",
                  style="Muted.TLabel").pack(pady=(4, 0))
        ttk.Label(nav, text="点击右侧按钮切换页面", style="Muted.TLabel",
                  wraplength=160, justify="center").pack(side="bottom", pady=8)
        self.update_nav_avatar()

    def _build_footer(self):
        b = ttk.Frame(self.root, padding=(16, 4, 16, 10))
        b.pack(fill="x")
        ttk.Label(b, textvariable=self.status, style="Muted.TLabel").pack(side="left")

    # ------------------------------------------------------------ 协作表格
    def _build_sheet_tab(self):
        f = self._page("sheet")
        bar = ttk.Frame(f)
        bar.pack(fill="x", pady=(0, 8))
        ttk.Label(bar, text="房间:").pack(side="left")
        self.room_cb = ttk.Combobox(bar, textvariable=self.room_var,
                                    state="readonly", width=18)
        self.room_cb.pack(side="left", padx=(4, 12))
        self.room_cb.bind("<<ComboboxSelected>>", lambda e: self.switch_room())
        for text, cmd, primary in [("新建房间", self.new_room, True),
                                   ("打开 Excel", self.open_xlsx, False),
                                   ("保存", lambda: self.save_xlsx(False), False),
                                   ("另存为", lambda: self.save_xlsx(True), False)]:
            make_button(bar, text, cmd, primary).pack(side="left", padx=3)
        ttk.Label(bar, text="工作表:").pack(side="left", padx=(10, 0))
        self.sheet_cb = ttk.Combobox(bar, textvariable=self.sheet_var,
                                     state="readonly", width=12)
        self.sheet_cb.pack(side="left", padx=4)
        self.sheet_cb.bind("<<ComboboxSelected>>", lambda e: self.switch_sheet())

        self.sheet_host = ttk.Frame(f)
        self.sheet_host.pack(fill="both", expand=True)
        headers = [col_name(c) for c in range(1, SHEET_COLS + 1)]
        self.plain_sheet = make_sheet(self.sheet_host, SHEET_ROWS, SHEET_COLS,
                                      headers, column_width=104, row_height=27)
        self.plain_sheet.pack(fill="both", expand=True)
        self.styled_sheet = StyledSheet(self.sheet_host, SHEET_ROWS, SHEET_COLS,
                                        headers, column_width=104, row_height=27)
        self.sheet = self.plain_sheet
        for widget in (self.plain_sheet, self.styled_sheet):
            widget.bind("<<SheetChanged>>", self.on_sheet_changed)
            widget.bind("<<SheetSelect>>", self.on_sheet_select)
        ttk.Label(f, textvariable=self.info, style="Muted.TLabel").pack(
            fill="x", pady=(6, 0))
        # Excel 式底部工作表标签栏：显示工作簿的全部工作表，点击即切换
        self.sheet_tabbar = ttk.Frame(f)
        self.sheet_tabbar.pack(side="bottom", fill="x", pady=(8, 0))

    # ------------------------------------------------------------ 群聊
    def _build_chat_tab(self):
        f = self._page("chat")

        left = ttk.LabelFrame(f, text="  群聊列表  ", padding=8, width=190)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        self.group_list = tk.Listbox(left, height=16, activestyle="none")
        self.group_list.pack(fill="both", expand=True, pady=(0, 8))
        self.group_list.bind("<<ListboxSelect>>", self.on_group_select)
        make_button(left, "新建群聊", self.create_group, True).pack(fill="x", pady=2)
        make_button(left, "邀请成员进群", self.invite_members).pack(fill="x", pady=2)
        make_button(left, "群设置 / 管理", self.show_group_settings).pack(fill="x", pady=2)
        make_button(left, "刷新", self.refresh_chat).pack(fill="x", pady=2)
        ttk.Label(left, text="提示：群内发的协作表格\n只有群成员能看到和编辑",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(10, 0))

        center = ttk.Frame(f, padding=(8, 0))
        center.pack(side="left", fill="both", expand=True)
        ttk.Label(center, textvariable=self.chat_title,
                  font=(FONT, 12, "bold")).pack(anchor="w", pady=(0, 6))
        box = ttk.Frame(center)
        box.pack(fill="both", expand=True)
        self.chat_text = tk.Text(box, wrap="word", state="disabled",
                                 font=(FONT, 10), bg="#FFFFFF",
                                 relief="flat", padx=8, pady=6)
        vsb = ttk.Scrollbar(box, orient="vertical", command=self.chat_text.yview)
        self.chat_text.configure(yscrollcommand=vsb.set)
        self.chat_text.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.chat_text.tag_configure("name", foreground="#0067C0")
        self.chat_text.tag_configure("body", foreground="#1A1A1A")
        self.chat_text.tag_configure("muted", foreground="#7A7A7A")
        self.chat_text.tag_configure("link", foreground="#0067C0", underline=1)
        self.chat_text.tag_configure("recalled", foreground="#9A9A9A")
        self.chat_text.tag_configure("atall", foreground="#C42B1C")
        self.chat_text.tag_configure("role", foreground="#8E562E")

        entry = ttk.Frame(center)
        entry.pack(fill="x", pady=(8, 4))
        self.chat_entry = ttk.Entry(entry, textvariable=self.chat_input)
        self.chat_entry.pack(side="left", fill="x", expand=True)
        self.chat_entry.bind("<Return>", lambda e: self.send_chat_text())
        make_button(entry, "发送", self.send_chat_text, True).pack(side="left", padx=6)
        self.atall_btn = make_button(entry, "@所有人", self.send_at_all)
        self.atall_btn.pack(side="left", padx=(0, 6))

        tools = ttk.Frame(center)
        tools.pack(fill="x")
        for text, cmd in [("发送协作表格", self.send_sheet_card),
                          ("本地表格转协作表", self.send_local_sheet),
                          ("发送文件到群", self.send_file_group)]:
            make_button(tools, text, cmd).pack(side="left", padx=(0, 6))
        ttk.Label(tools, text="群内表格仅成员可见 · 文件不限大小 · 右键消息可撤回",
                  style="Muted.TLabel").pack(side="left", padx=6)

        right = ttk.LabelFrame(f, text="  群成员 / 文件传输  ", padding=8, width=236)
        right.pack(side="right", fill="y", padx=(8, 0))
        right.pack_propagate(False)
        ttk.Label(right, text="群成员", style="Muted.TLabel").pack(anchor="w")
        self.member_list = tk.Listbox(right, height=8, activestyle="none")
        self.member_list.pack(fill="x", pady=(2, 10))
        ttk.Label(right, text="文件传输（不限大小）", style="Muted.TLabel").pack(anchor="w")
        self.transfer_tree = ttk.Treeview(
            right, columns=("name", "prog", "state"), show="headings", height=9)
        for c, w in (("name", 120), ("prog", 48), ("state", 82)):
            self.transfer_tree.heading(c, text={"name": "文件", "prog": "进度",
                                                "state": "状态"}[c])
            self.transfer_tree.column(c, width=w, anchor="w")
        self.transfer_tree.pack(fill="both", expand=True, pady=(2, 8))
        self.transfer_tree.bind("<Double-1>", self.open_transfer_item)
        make_button(right, "打开下载目录", self.open_download_dir).pack(fill="x", pady=2)
        make_button(right, "发送文件给指定成员", self.send_file_to_peer).pack(fill="x", pady=2)

    # ------------------------------------------------------------ 在线成员
    def _build_peers_tab(self):
        f = self._page("peers")
        card = make_card(f, "自动发现的同伴",
                         "启动即扫描本网段与已添加的网段；组播不通时用 register 定向探测。",
                         "search")
        card.pack(fill="x", pady=(0, 10))
        make_button(card.actions, "立即扫描", self.scan_now, True).pack(side="left")

        cols = ("name", "note", "local_note", "ip", "source", "status")
        self.peer_tree = ttk.Treeview(f, columns=cols, show="tree headings", height=14)
        self.peer_tree.heading("#0", text="头像")
        self.peer_tree.column("#0", width=64, stretch=False)
        heads = {"name": ("显示名称", 140), "note": ("对方备注", 160),
                 "local_note": ("我的备注", 160), "ip": ("IP 地址", 125),
                 "source": ("发现方式", 88), "status": ("状态", 66)}
        for c in cols:
            self.peer_tree.heading(c, text=heads[c][0])
            self.peer_tree.column(c, width=heads[c][1], anchor="w")
        self.peer_tree.pack(fill="both", expand=True, side="left")
        vs = ttk.Scrollbar(f, orient="vertical", command=self.peer_tree.yview)
        vs.pack(side="left", fill="y")
        self.peer_tree.configure(yscrollcommand=vs.set)

        side = ttk.Frame(f, padding=(10, 0, 0, 0))
        side.pack(side="left", fill="y")
        for text, cmd in [("给选中同伴写备注", self.edit_peer_note),
                          ("发送文件给选中成员", self.send_file_to_peer),
                          ("邀请进当前群", self.invite_members),
                          ("收藏选中 IP", self.favorite_selected),
                          ("刷新", self.refresh_peers)]:
            make_button(side, text, cmd).pack(fill="x", pady=3)
        ttk.Label(side, text="提示：\n· 对方备注 = 对方自己填\n· 我的备注 = 仅本机可见\n"
                            "· 双击传输记录可打开文件",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=12)

    # ------------------------------------------------------------ 跨网段
    def _build_network_tab(self):
        f = self._page("net")
        card = make_card(f, "添加可访问的网段",
                         "支持 192.168.2.0/24、单 IP 192.168.2.5、范围 192.168.2.10-192.168.2.30",
                         "navigation")
        card.pack(fill="x", pady=(0, 8))
        ttk.Entry(card.actions, textvariable=self.subnet_var,
                  width=28).pack(side="left", padx=(0, 6))
        make_button(card.actions, "添加", self.add_subnet, True).pack(side="left")

        row = ttk.Frame(f)
        row.pack(fill="both", expand=True)
        left = ttk.LabelFrame(row, text="  已添加网段（自动扫描）  ", padding=8)
        left.pack(side="left", fill="both", expand=True, padx=(0, 6))
        self.subnet_list = tk.Listbox(left, height=10, activestyle="none")
        self.subnet_list.pack(fill="both", expand=True)
        make_button(left, "删除选中网段", self.remove_subnet).pack(pady=6)

        right = ttk.LabelFrame(row, text="  收藏设备 / 固定 IP  ", padding=8)
        right.pack(side="left", fill="both", expand=True, padx=(6, 0))
        self.fav_list = tk.Listbox(right, height=10, activestyle="none")
        self.fav_list.pack(fill="both", expand=True)
        er = ttk.Frame(right)
        er.pack(fill="x", pady=6)
        ttk.Entry(er, textvariable=self.favorite_var, width=18).pack(
            side="left", fill="x", expand=True)
        make_button(er, "添加", self.add_favorite).pack(side="left", padx=4)
        make_button(er, "删除", self.remove_favorite).pack(side="left")
        self.refresh_lists()

    # ------------------------------------------------------------ 我的资料
    def _build_profile_tab(self):
        f = self._page("profile")
        card = make_card(f, "显示名称", "同伴的成员列表与群聊中显示的名字", "edit")
        card.pack(fill="x", pady=6)
        ttk.Entry(card.actions, textvariable=self.name_var, width=30).pack(side="left")
        card2 = make_card(f, "个人备注", "例如：财务部-小王 / 三楼会议室 / 负责 B 列", "text")
        card2.pack(fill="x", pady=6)
        ttk.Entry(card2.actions, textvariable=self.note_var, width=42).pack(side="left")

        av = ttk.LabelFrame(f, text="  我的头像（12 款卡通头像 / 自定义）  ", padding=12)
        av.pack(fill="x", pady=8)
        grid = ttk.Frame(av)
        grid.pack(side="left", padx=(0, 16))
        self._avatar_photos = []
        for i in range(CARTOON_COUNT):
            photo = make_photo(cartoon_b64(i), self.root)
            self._avatar_photos.append(photo)
            b = tk.Button(grid, image=photo, relief="flat", bd=1,
                          command=lambda n=i: self.choose_avatar("cartoon:%d" % n))
            b.grid(row=i // 6, column=i % 6, padx=4, pady=4)
        aside = ttk.Frame(av)
        aside.pack(side="left", fill="both", expand=True)
        ttk.Label(aside, text="当前头像", style="Muted.TLabel").pack(anchor="w")
        self.preview = ttk.Label(aside)
        self.preview.pack(anchor="w", pady=6)
        make_button(aside, "自定义头像…", self.choose_custom_avatar).pack(anchor="w", pady=4)
        ttk.Label(aside, text="支持 JPG / PNG / BMP / GIF，自动缩放为 64×64 并同步给同伴",
                  style="Muted.TLabel", wraplength=260, justify="left").pack(anchor="w", pady=4)
        self.update_preview()

        make_button(f, "保存并广播给所有同伴", self.save_profile, True).pack(anchor="w", pady=12)

        cfg = self.node.config_public()
        info = ttk.LabelFrame(f, text="  本机节点信息  ", padding=12)
        info.pack(fill="x", pady=8)
        lines = [
            "设备指纹（持久化）: " + cfg["fingerprint"],
            "HTTP / 发现端口: %s     节点互联 TCP: %s     文件传输 TCP: %s"
            % (self.node.http_port, self.node.tcp_port, self.node.file_port),
            "组播: 224.0.0.167:53317     广播: 5688",
            "文件下载目录: " + cfg["download_dir"],
            "配置文件: " + cfg["config_path"],
        ]
        for line in lines:
            ttk.Label(info, text=line, style="Muted.TLabel").pack(anchor="w", pady=2)

    # ------------------------------------------------------------ 头像
    def _photo(self, key, b64):
        if not b64:
            return None
        k = key + ":" + b64[:48]
        if k not in self._photo_cache:
            self._photo_cache[k] = make_photo(b64, self.root)
        return self._photo_cache[k]

    def update_nav_avatar(self):
        photo = self._photo("me", avatar_b64(self.node.avatar, self.node.avatar_data))
        if photo:
            self.nav_avatar.configure(image=photo)
            self.nav_avatar.image = photo
        self.nav_name.configure(text=self.node.name)

    def update_preview(self):
        if self._pending_avatar and self._pending_avatar.get("custom_path"):
            try:
                b64 = custom_avatar_b64(self._pending_avatar["custom_path"])
            except Exception as e:
                messagebox.showerror("头像解析失败", str(e))
                self._pending_avatar = None
                b64 = None
        elif self._pending_avatar and self._pending_avatar.get("avatar"):
            spec = self._pending_avatar["avatar"]
            b64 = cartoon_b64(int(spec.split(":", 1)[1]))
        else:
            b64 = avatar_b64(self.node.avatar, self.node.avatar_data)
        if b64:
            photo = self._photo("preview", b64)
            if photo:
                self.preview.configure(image=photo)
                self.preview.image = photo

    def choose_avatar(self, spec):
        self._pending_avatar = {"avatar": spec}
        self.update_preview()

    def choose_custom_avatar(self):
        path = filedialog.askopenfilename(
            title="选择自定义头像",
            filetypes=[("图片", "*.png;*.jpg;*.jpeg;*.bmp;*.gif"), ("所有文件", "*.*")])
        if not path:
            return
        self._pending_avatar = {"custom_path": path}
        self.update_preview()

    # ------------------------------------------------------------ 表格同步
    def _activate_sheet(self, styled):
        """有原始 xlsx 模板时切到保真渲染控件，否则用普通协作网格"""
        target = self.styled_sheet if styled else self.plain_sheet
        if self.sheet is target:
            return
        try:
            self.sheet.finish_edit(False)
        except Exception:
            pass
        for widget in (self.plain_sheet, self.styled_sheet):
            try:
                widget.pack_forget()
            except Exception:
                pass
        target.pack(fill="both", expand=True)
        self.sheet = target

    def _update_sheet_choices(self):
        self.sheet_cb["values"] = self._sheet_names
        cur = ""
        info = self.node.room_style(self.room) or {}
        if info.get("sheet") in self._sheet_names:
            cur = info["sheet"]
        self.sheet_var.set(cur)
        self._rebuild_sheet_tabs(cur)

    TAB_ACTIVE_BG = "#0067C0"
    TAB_ACTIVE_FG = "#FFFFFF"
    TAB_BG = "#E8EDF2"
    TAB_FG = "#1A1A1A"

    def _rebuild_sheet_tabs(self, cur=""):
        """底部标签栏：工作簿的每个工作表一个标签，当前表高亮"""
        for w in self.sheet_tabbar.winfo_children():
            try:
                w.destroy()
            except Exception:
                pass
        sheets = self._sheet_names
        if not sheets:
            self.sheet_tabbar.pack_forget()
            return
        self.sheet_tabbar.pack(side="bottom", fill="x", pady=(8, 0))
        padx = 14 if len(sheets) <= 8 else 6
        for name in sheets:
            is_cur = (name == cur)
            b = tk.Button(self.sheet_tabbar, text="  %s  " % name,
                          relief="flat", borderwidth=0, highlightthickness=0,
                          bg=self.TAB_ACTIVE_BG if is_cur else self.TAB_BG,
                          fg=self.TAB_ACTIVE_FG if is_cur else self.TAB_FG,
                          activebackground="#1975C5" if is_cur else "#D8E0E8",
                          activeforeground="#FFFFFF" if is_cur else "#1A1A1A",
                          padx=padx, pady=3, cursor="hand2",
                          command=lambda n=name: self._goto_sheet(n))
            b.pack(side="left", padx=(0, 4), pady=2)
        ttk.Label(self.sheet_tabbar, text="  ← 点击标签切换工作表",
                  style="Muted.TLabel").pack(side="left", pady=4)

    def _goto_sheet(self, name):
        self.sheet_var.set(name)
        self.switch_sheet()

    def _apply_room_style(self, info):
        style = info.get("style")
        if style:
            self.view_rows = max(SHEET_ROWS, int(style.get("rows", 1)) + 2)
            self.view_cols = max(SHEET_COLS,
                                 min(int(style.get("cols", 1)) + 2, 60))
            self._activate_sheet(True)
            self.styled_sheet.apply_style(style)
            self._sheet_names = list(info.get("sheets") or [])
            self._active_sheet = info.get("active") or ""
        else:
            self.view_rows, self.view_cols = SHEET_ROWS, SHEET_COLS
            self._activate_sheet(False)
            self._sheet_names = []
            self._active_sheet = ""
        matrix = [["" for _ in range(self.view_cols)]
                  for _ in range(self.view_rows)]
        for (r, c), val in self.grid_state.items():
            if 0 <= r < self.view_rows and 0 <= c < self.view_cols:
                matrix[r][c] = val
        self.grid_state = {(r, c): matrix[r][c]
                           for r in range(self.view_rows)
                           for c in range(self.view_cols)}
        self.sheet.set_data(matrix, rows=self.view_rows, columns=self.view_cols)
        self._update_sheet_choices()
        if style:
            self.info.set("已按原文件样式渲染（%s）· 房间: %s · 协作只同步值"
                          % (style_summary(style), self.room))
        else:
            self.info.set("当前房间: %s · 双击单元格编辑，Ctrl+V 可粘贴区域"
                          % self.room)

    def switch_sheet(self):
        name = self.sheet_var.get()
        info = self.node.room_style(self.room) or {}
        sheets = info.get("sheets") or []
        if not name or name not in sheets:
            return
        base = self.room.split("::", 1)[0]
        target = base if name == self._active_sheet else "%s::%s" % (base, name)
        if target == self.room:
            return
        self.node.get_room(target, create=True)
        self._rooms_sig = ""
        self.room_cb["values"] = sorted(self.node.rooms.keys())
        self.room_var.set(target)
        self.switch_room()

    def switch_room(self):
        name = self.room_var.get()
        if not name:
            return
        self.room = name
        self.since = 0
        self.grid_state = {}
        self._style_version = -1
        self.node.my_presence["room"] = name
        self._update_sheet_choices()

    def on_sheet_changed(self, event=None):
        if not self.room:
            return
        data = self.sheet.get_data()
        cells, ts = {}, now_ns()
        for r, row in enumerate(data):
            for c, val in enumerate(row):
                if val is None:
                    val = ""
                if self.grid_state.get((r, c), "") == val:
                    continue
                self.grid_state[(r, c)] = val
                cells["%d,%d" % (r + 1, c + 1)] = [to_value(val), ts, self.node.id]
        if cells:
            self.node.apply_edit(self.room, cells, local=True)
            self.info.set("已同步 %d 个单元格 · 房间: %s" % (len(cells), self.room))

    def on_sheet_select(self, event=None):
        if not self.room:
            return
        try:
            r, c = self.sheet.active
        except Exception:
            return
        cell = "%d,%d" % (r + 1, c + 1)
        self.node.my_presence = {"room": self.room, "cell": cell, "ts": time.time()}
        self.node.broadcast({"t": "presence", "id": self.node.id,
                             "name": self.node.name, "color": self.node.color,
                             "note": self.node.note, "room": self.room, "cell": cell})

    def poll_room(self):
        if not self.room:
            return
        info = self.node.room_style(self.room)
        if info and info.get("version", 0) != self._style_version:
            self._style_version = info.get("version", 0)
            self._apply_room_style(info)
        d = self.node.poll(self.since, self.room)
        self.since = d.get("seq", self.since)
        if d.get("full"):
            cells = d.get("cells", {})
            matrix = [["" for _ in range(self.view_cols)]
                      for _ in range(self.view_rows)]
            self.grid_state = {(r, c): "" for r in range(self.view_rows)
                               for c in range(self.view_cols)}
            for key, (v, ts, who) in cells.items():
                r, c = (int(x) - 1 for x in key.split(","))
                if 0 <= r < self.view_rows and 0 <= c < self.view_cols:
                    text = str(v)
                    matrix[r][c] = text
                    self.grid_state[(r, c)] = text
            self.sheet.set_data(matrix, rows=self.view_rows,
                                columns=self.view_cols)
        else:
            for e in d.get("edits", []):
                for key, (v, ts, who) in e["cells"].items():
                    r, c = (int(x) - 1 for x in key.split(","))
                    if 0 <= r < self.view_rows and 0 <= c < self.view_cols:
                        text = str(v)
                        if self.grid_state.get((r, c), "") != text:
                            self.grid_state[(r, c)] = text
                            self.sheet.set_cell(r, c, text)

    # ------------------------------------------------------------ 群聊逻辑
    def _avatar_for(self, pid, data=""):
        b64 = data or (avatar_b64(self.node.avatar, self.node.avatar_data)
                       if pid == self.node.id else self.node.peer_avatar_b64(pid))
        return self._photo(str(pid), b64)

    def _append_message(self, m):
        if m.get("gid") != self.current_gid:
            return
        mid = m.get("id", "")
        if mid and mid in self._rendered_mids:
            # 老消息状态变化（被撤回）：标记需要整体重绘，由 refresh_chat 统一处理
            self._need_rebuild = True
            return
        if mid:
            self._rendered_mids.add(mid)
        t = self.chat_text
        t.configure(state="normal")
        start = t.index("end-1c")
        photo = self._avatar_for(m.get("from", ""), m.get("avatar_data", ""))
        if photo:
            t.image_create("end", image=photo)
        stamp = time.strftime("%H:%M:%S", time.localtime(m.get("ts", time.time())))
        t.insert("end", "  %s  %s\n" % (m.get("from_name") or "同伴", stamp), "name")
        kind = m.get("kind")
        if m.get("recalled"):
            who = m.get("recalled_by_name") or m.get("from_name") or "同伴"
            if kind == "file":
                t.insert("end", "  [已撤回] %s 撤回了一个文件（%s）\n\n"
                         % (who, m.get("file", "")), "recalled")
            else:
                t.insert("end", "  [已撤回] %s 撤回了一条消息\n\n" % who, "recalled")
        elif kind == "text":
            if m.get("at_all"):
                t.insert("end", "  @所有人 ", ("body", "atall"))
            t.insert("end", "  %s\n\n" % m.get("text", ""), "body")
        elif kind == "system":
            t.insert("end", "  %s\n\n" % m.get("text", ""), "muted")
        elif kind == "sheet":
            t.insert("end", "  [协作表格·仅群成员可见] %s · %s 格\n"
                     % (m.get("room", ""), m.get("cells", 0)), "body")
            tag = "jump_%s" % m.get("seq")
            t.insert("end", "  打开这张协作表格\n\n", ("link", tag))
            t.tag_bind(tag, "<Button-1>",
                       lambda e, r=m.get("room", ""): self.jump_to_sheet(r))
        elif kind == "file":
            t.insert("end", "  [文件] %s · %s\n"
                     % (m.get("file", ""), human_size(m.get("size"))), "body")
            path = m.get("path", "")
            if path and os.path.exists(path):
                tag = "file_%s" % m.get("seq")
                t.insert("end", "  打开此文件\n\n", ("link", tag))
                t.tag_bind(tag, "<Button-1>",
                           lambda e, p=path: self.open_path(p))
            else:
                t.insert("end", "\n", "body")
        end = t.index("end-1c")
        tag = "msg_%s" % m.get("seq")
        t.tag_add(tag, start, end)
        t.tag_bind(tag, "<Button-3>", lambda e, mm=m: self._msg_menu(e, mm))
        t.configure(state="disabled")
        t.see("end")

    def _msg_menu(self, event, m):
        """消息右键：撤回（发送者本人或群管理员，不限时间）"""
        if m.get("kind") == "system":
            return
        menu = tk.Menu(self.root, tearoff=0)
        if m.get("recalled"):
            menu.add_command(label="该消息已撤回", state="disabled")
        elif self.node.can_recall(m):
            label = "撤回这个文件" if m.get("kind") == "file" else "撤回这条消息"
            menu.add_command(label=label, command=lambda: self._do_recall(m))
        else:
            menu.add_command(label="只有发送者或群管理员可撤回", state="disabled")
        if m.get("kind") == "file" and m.get("path") and os.path.exists(m["path"]):
            menu.add_command(label="打开文件", command=lambda: self.open_path(m["path"]))
        if m.get("kind") == "sheet" and m.get("room"):
            menu.add_command(label="打开协作表格",
                             command=lambda: self.jump_to_sheet(m["room"]))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _do_recall(self, m):
        tip = "确定撤回这条消息？\n撤回不限时间。"
        if m.get("kind") == "file":
            tip = ("确定撤回这个文件？\n"
                   "文件会同时从各成员已接收的下载目录中删除，\n"
                   "但已经被打开或另存到别处的副本无法收回。")
        if not messagebox.askyesno("撤回", tip, parent=self.root):
            return
        try:
            self.node.recall_message(m.get("id"))
        except Exception as e:
            messagebox.showerror("撤回失败", str(e), parent=self.root)
            return
        self.rebuild_chat()

    def rebuild_chat(self):
        self._need_rebuild = False
        self.chat_text.configure(state="normal")
        self.chat_text.delete("1.0", "end")
        self.chat_text.configure(state="disabled")
        self._rendered_mids = set()
        with self.node.chat_lock:
            hist = [dict(m) for m in self.node.chat_log
                    if m.get("gid") == self.current_gid]
        for m in hist:
            self._append_message(m)

    def refresh_chat(self):
        d = self.node.chat_since(self.chat_since)
        self.chat_since = d["seq"]
        mine = [g for g in d["groups"] if g["mine"]]
        self.group_data = mine
        sig = json.dumps(mine, ensure_ascii=False, sort_keys=True)
        if sig != self._groups_sig:
            self._groups_sig = sig
            self.group_ids = [g["id"] for g in mine]
            self.group_list.delete(0, "end")
            for g in mine:
                mark = ""
                if g["dissolved"]:
                    mark = " ·已解散"
                elif g["is_owner"]:
                    mark = " ·群主"
                elif g["is_admin"]:
                    mark = " ·管理"
                if g["can_manage"] and g["pending_count"]:
                    mark += " ·待审%d" % g["pending_count"]
                self.group_list.insert("end", " %s (%d)%s"
                                       % (g["name"], g["count"], mark))
            if self.current_gid in self.group_ids:
                self.group_list.selection_set(self.group_ids.index(self.current_gid))
            elif mine:
                self.current_gid = mine[0]["id"]
                self.group_list.selection_set(0)
                self.rebuild_chat()
            else:
                self.current_gid = ""
            self.refresh_members()
        for m in d["messages"]:
            self._append_message(m)
        if self._need_rebuild:
            self._need_rebuild = False
            self.rebuild_chat()
        tree = self.transfer_tree
        for item in tree.get_children():
            tree.delete(item)
        for t in d["transfers"][-40:]:
            pct = (t["sent"] * 100.0 / t["size"]) if t["size"] else 100.0
            arrow = "↑" if t["direction"] == "send" else "↓"
            tree.insert("", "end", iid=t["id"], values=(
                arrow + t["name"][:20], "%.0f%%" % pct, t["status"]))
        if mine:
            cur = next((g for g in mine if g["id"] == self.current_gid), None)
            if cur:
                role = "群主" if cur["is_owner"] else ("管理员" if cur["is_admin"] else "成员")
                state = " · 已解散" if cur["dissolved"] else ""
                self.chat_title.set("%s · %d 人 · 我是%s%s"
                                    % (cur["name"], cur["count"], role, state))
                if cur["can_manage"] and cur["pending_count"]:
                    self.chat_title.set(self.chat_title.get() +
                                        " · %d 人待审核" % cur["pending_count"])
        else:
            self.chat_title.set("还没有群聊，点击左侧「新建群聊」")

    def refresh_members(self):
        self.member_list.delete(0, "end")
        cur = next((g for g in self.group_data if g["id"] == self.current_gid), None)
        if not cur:
            return
        owner = cur["owner"]
        admins = set(cur["admins"])
        for pid in cur["members"]:
            role = " [群主]" if pid == owner else (" [管理员]" if pid in admins else "")
            if pid == self.node.id:
                self.member_list.insert("end", " %s%s (我)" % (self.node.name, role))
                continue
            with self.node.peers_lock:
                p = self.node.peers.get(pid, {})
            self.member_list.insert("end", " %s%s"
                                    % (p.get("name") or pid[:8], role))

    def on_group_select(self, event=None):
        sel = self.group_list.curselection()
        if not sel or sel[0] >= len(self.group_ids):
            return
        gid = self.group_ids[sel[0]]
        if gid != self.current_gid:
            self.current_gid = gid
            self.rebuild_chat()
            self.refresh_members()

    # ------------------------------------------------------------ 群设置 / 管理
    def _current_group(self):
        return next((g for g in self.group_data if g["id"] == self.current_gid), None)

    def show_group_settings(self):
        cur = self._current_group()
        if not cur:
            self._alert("请先选中或新建一个群聊")
            return
        top = tk.Toplevel(self.root)
        top.title("群设置 · " + cur["name"])
        top.geometry("640x640")
        top.transient(self.root)
        top.grab_set()

        role = "群主" if cur["is_owner"] else ("管理员" if cur["is_admin"] else "普通成员")
        head = ttk.Frame(top, padding=(16, 14, 16, 6))
        head.pack(fill="x")
        ttk.Label(head, text=cur["name"], font=(FONT, 13, "bold")).pack(anchor="w")
        ttk.Label(head, text="%d 人 · 我是%s · 进群方式：%s"
                            % (cur["count"], role,
                               "需管理员审核" if cur["join_mode"] == "approval" else "可直接加入"),
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 0))
        if cur["dissolved"]:
            ttk.Label(head, text="该群已解散：只能查看历史记录，协作表格已失效",
                      foreground="#C42B1C").pack(anchor="w", pady=(4, 0))

        body = ttk.Frame(top, padding=(16, 0))
        body.pack(fill="both", expand=True)
        left = ttk.LabelFrame(body, text="  群成员  ", padding=8)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8))
        member_box = tk.Listbox(left, height=15, activestyle="none")
        member_box.pack(fill="both", expand=True)
        owner = cur["owner"]
        admins = set(cur["admins"])
        member_ids = list(cur["members"])
        for pid in member_ids:
            role_tag = " [群主]" if pid == owner else (" [管理员]" if pid in admins else "")
            if pid == self.node.id:
                name = "%s (我)" % self.node.name
            else:
                with self.node.peers_lock:
                    p = self.node.peers.get(pid, {})
                name = p.get("name") or pid[:8]
            member_box.insert("end", " %s%s" % (name, role_tag))

        ops = ttk.Frame(left)
        ops.pack(fill="x", pady=(8, 0))
        can = cur["can_manage"]
        b_admin = make_button(ops, "设为管理员", lambda: self._gs_admin(
            top, member_box, member_ids, True))
        b_admin.pack(fill="x", pady=2)
        b_unadmin = make_button(ops, "取消管理员", lambda: self._gs_admin(
            top, member_box, member_ids, False))
        b_unadmin.pack(fill="x", pady=2)
        b_kick = make_button(ops, "移出群聊", lambda: self._gs_kick(
            top, member_box, member_ids))
        b_kick.pack(fill="x", pady=2)
        if not can:
            for b in (b_admin, b_unadmin, b_kick):
                b.configure(state="disabled")
        if not cur["is_owner"]:
            b_admin.configure(state="disabled")
            b_unadmin.configure(state="disabled")

        right = ttk.LabelFrame(body, text="  进群审核  ", padding=8)
        right.pack(side="right", fill="both", expand=True)
        approve_var = tk.BooleanVar(value=(cur["join_mode"] == "approval"))
        cb = ttk.Checkbutton(right, text="进群需要管理员审核",
                             variable=approve_var,
                             command=lambda: self._gs_joinmode(top, approve_var))
        cb.pack(anchor="w")
        if not can:
            cb.configure(state="disabled")
        ttk.Label(right, text="待审核申请", style="Muted.TLabel").pack(anchor="w", pady=(10, 2))
        pend_box = tk.Listbox(right, height=8, activestyle="none")
        pend_box.pack(fill="both", expand=True)
        pend_ids = [p["id"] for p in cur.get("pending") or []]
        for p in cur.get("pending") or []:
            name = p.get("name") or p["id"][:8]
            if p["id"] == self.node.id:
                name += " (我)"
            pend_box.insert("end", " " + name)
        if not pend_ids:
            pend_box.insert("end", " （暂无待审核申请）")
        arow = ttk.Frame(right)
        arow.pack(fill="x", pady=(6, 0))
        b_ok = make_button(arow, "同意", lambda: self._gs_approve(
            top, pend_box, pend_ids, True))
        b_ok.pack(side="left", fill="x", expand=True, padx=(0, 3))
        b_no = make_button(arow, "拒绝", lambda: self._gs_approve(
            top, pend_box, pend_ids, False))
        b_no.pack(side="left", fill="x", expand=True)
        if not can or not pend_ids:
            b_ok.configure(state="disabled")
            b_no.configure(state="disabled")

        # 邀请 / 加入
        mid_row = ttk.Frame(top, padding=(16, 8, 16, 0))
        mid_row.pack(fill="x")
        if not cur["dissolved"]:
            make_button(mid_row, "邀请成员进群",
                        lambda: (top.destroy(), self.invite_members())).pack(
                side="left", padx=(0, 6))

        foot = ttk.Frame(top, padding=(16, 10, 16, 14))
        foot.pack(fill="x", side="bottom")
        if cur["is_owner"] and not cur["dissolved"]:
            make_button(foot, "解散群聊", lambda: self._gs_dissolve(top),
                        False).pack(side="left")
        elif not cur["is_owner"] and not cur["dissolved"]:
            make_button(foot, "退出群聊", lambda: self._gs_leave(top)).pack(side="left")
        make_button(foot, "关闭", top.destroy, True).pack(side="right")

    def _gs_refresh(self, top, gid=None):
        top.destroy()
        self._groups_sig = ""
        self.refresh_chat()

    def _gs_admin(self, top, box, ids, on):
        sel = box.curselection()
        if not sel or sel[0] >= len(ids):
            return
        pid = ids[sel[0]]
        try:
            self.node.set_admin(self.current_gid, pid, on)
        except Exception as e:
            messagebox.showerror("操作失败", str(e), parent=top)
            return
        self._gs_refresh(top)

    def _gs_kick(self, top, box, ids):
        sel = box.curselection()
        if not sel or sel[0] >= len(ids):
            return
        pid = ids[sel[0]]
        if not messagebox.askyesno("移出群聊",
                                   "确定把该成员移出群聊？\n"
                                   "移出后他立即看不到群内消息和协作表格。",
                                   parent=top):
            return
        try:
            self.node.kick_member(self.current_gid, pid)
        except Exception as e:
            messagebox.showerror("操作失败", str(e), parent=top)
            return
        self._gs_refresh(top)

    def _gs_approve(self, top, box, ids, ok):
        sel = box.curselection()
        if not sel or sel[0] >= len(ids):
            return
        try:
            self.node.approve_join(self.current_gid, ids[sel[0]], ok)
        except Exception as e:
            messagebox.showerror("操作失败", str(e), parent=top)
            return
        self._gs_refresh(top)

    def _gs_joinmode(self, top, var):
        try:
            self.node.set_join_mode(self.current_gid,
                                    "approval" if var.get() else "free")
        except Exception as e:
            messagebox.showerror("操作失败", str(e), parent=top)
            return
        self._groups_sig = ""
        self.refresh_chat()
        top.destroy()

    def _gs_dissolve(self, top):
        if not messagebox.askyesno(
                "解散群聊",
                "确定解散该群聊？\n\n"
                "· 所有成员将看不到群内消息与协作表格\n"
                "· 群内共享的协作表格立即失效\n"
                "· 该操作不可撤销",
                parent=top):
            return
        try:
            self.node.dissolve_group(self.current_gid)
        except Exception as e:
            messagebox.showerror("解散失败", str(e), parent=top)
            return
        self._gs_refresh(top)

    def _gs_leave(self, top):
        if not messagebox.askyesno("退出群聊", "确定退出该群聊？", parent=top):
            return
        try:
            self.node.leave_group(self.current_gid)
        except Exception as e:
            messagebox.showerror("退出失败", str(e), parent=top)
            return
        self.current_gid = ""
        self._gs_refresh(top)

    def send_at_all(self):
        cur = self._current_group()
        if not cur:
            self._alert("请先选中或新建一个群聊")
            return
        if not cur["is_admin"]:
            self._alert("只有群主或管理员可以 @所有人")
            return
        text = self.chat_input.get().strip() or "请注意查看群消息"
        try:
            self.node.at_all(self.current_gid, text)
        except Exception as e:
            messagebox.showerror("发送失败", str(e))
            return
        self.chat_input.set("")

    def create_group(self):
        name = simpledialog.askstring("新建群聊", "群聊名称:",
                                      initialvalue="项目协作群", parent=self.root)
        if not name:
            return
        g = self.node.create_group(name)
        self.current_gid = g["id"]
        self._groups_sig = ""
        self.refresh_chat()
        self.invite_members()

    def invite_members(self):
        if not self.current_gid:
            self._alert("请先选中或新建一个群聊")
            return
        peers = [p for p in self.node.peers_detail() if p["online"]]
        if not peers:
            self._alert("当前没有在线成员。可以稍后再点「邀请成员进群」。")
            return
        top = tk.Toplevel(self.root)
        top.title("邀请成员进群")
        top.geometry("360x420")
        top.transient(self.root)
        ttk.Label(top, text="勾选要邀请的在线成员",
                  style="Muted.TLabel").pack(anchor="w", padx=14, pady=(12, 6))
        box = ttk.Frame(top)
        box.pack(fill="both", expand=True, padx=14)
        vars_ = {}
        for p in peers:
            v = tk.BooleanVar(value=False)
            vars_[p["id"]] = v
            ttk.Checkbutton(box, text="%s  (%s)" % (p["name"], p["ip"]),
                            variable=v).pack(anchor="w", pady=3)

        def confirm():
            ids = [pid for pid, v in vars_.items() if v.get()]
            if ids:
                self.node.invite_members(self.current_gid, ids)
            top.destroy()
            self._groups_sig = ""
            self.refresh_chat()

        make_button(top, "确认邀请", confirm, True).pack(fill="x", padx=14, pady=10)

    def _ask_choice(self, title, prompt, values, initial=""):
        if not values:
            self._alert(prompt + "\n（当前没有可选项）")
            return None
        top = tk.Toplevel(self.root)
        top.title(title)
        top.geometry("340x150")
        top.transient(self.root)
        ttk.Label(top, text=prompt, style="Muted.TLabel").pack(anchor="w", padx=14, pady=(14, 6))
        var = tk.StringVar(value=initial if initial in values else values[0])
        cb = ttk.Combobox(top, textvariable=var, values=values,
                          state="readonly", width=30)
        cb.pack(padx=14, fill="x")
        result = {"value": None}

        def ok():
            result["value"] = var.get()
            top.destroy()

        make_button(top, "确定", ok, True).pack(pady=12)
        top.grab_set()
        self.root.wait_window(top)
        return result["value"]

    def send_chat_text(self):
        cur = self._current_group()
        if not self.current_gid or not cur:
            self._alert("请先选中或新建一个群聊")
            return
        if cur["dissolved"]:
            self._alert("该群聊已解散，无法发送消息")
            return
        text = self.chat_input.get().strip()
        if not text:
            return
        self.node.send_text(self.current_gid, text)
        self.chat_input.set("")

    def send_sheet_card(self):
        if not self.current_gid:
            self._alert("请先选中或新建一个群聊")
            return
        room = self._ask_choice("发送协作表格", "选择要发到群里的协作表格",
                                sorted(self.node.rooms.keys()), self.room)
        if not room:
            return
        self.node.send_sheet(self.current_gid, room)
        self._alert("已把协作表格「%s」发到群里，成员点击卡片即可打开共同编辑" % room)

    def send_local_sheet(self):
        if not self.current_gid:
            self._alert("请先选中或新建一个群聊")
            return
        path = filedialog.askopenfilename(
            title="选择本地 Excel（将转为群内协作表格）",
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            info = self.node.share_local_xlsx(self.current_gid, path)
            self._rooms_sig = ""
            self._alert("已转为协作表格：%s\n房间：%s\n读取 %d 个单元格\n"
                        "群成员会自动加入并共同编辑"
                        % (os.path.basename(path), info["room"], len(info["cells"])))
        except Exception as e:
            messagebox.showerror("发送失败", str(e))

    def send_file_group(self):
        if not self.current_gid:
            self._alert("请先选中或新建一个群聊")
            return
        path = filedialog.askopenfilename(title="选择要发送到群聊的文件（不限大小）")
        if not path:
            return
        r = self.node.send_file(path, gid=self.current_gid)
        self._alert("已开始向 %d 位在线群成员发送 %s（%s）\n"
                    "右侧可查看实时进度；文件不限制大小"
                    % (len(r["targets"]), r["name"], human_size(r["size"])))

    def send_file_to_peer(self):
        sel = self.peer_tree.selection()
        if not sel:
            self._alert("请先在「在线成员」页选中一个成员")
            return
        pid = sel[0]
        path = filedialog.askopenfilename(title="选择要发送的文件（不限大小）")
        if not path:
            return
        try:
            r = self.node.send_file(path, to=pid)
            self._alert("已开始向该成员发送 %s（%s）"
                        % (r["name"], human_size(r["size"])))
        except Exception as e:
            messagebox.showerror("发送失败", str(e))

    def jump_to_sheet(self, room):
        if not room:
            return
        self.node.get_room(room, create=True)
        self._rooms_sig = ""
        self.room_cb["values"] = sorted(self.node.rooms.keys())
        self.room_var.set(room)
        self.switch_room()
        self.show_page("sheet")

    def open_path(self, path):
        try:
            os.startfile(path)                      # Windows（含 Win7）
        except Exception:
            webbrowser.open("file:///" + path.replace("\\", "/"))

    def open_download_dir(self):
        d = self.node.download_dir()
        try:
            os.makedirs(d, exist_ok=True)
            os.startfile(d)
        except Exception:
            self._alert("下载目录: " + d)

    def open_transfer_item(self, event=None):
        sel = self.transfer_tree.selection()
        if not sel:
            return
        for r in self.node.transfer_list():
            if r["id"] == sel[0] and r.get("path") and os.path.exists(r["path"]):
                self.open_path(r["path"])
                return
        self.open_download_dir()

    def _on_file_received(self, path, meta):
        try:
            self.root.after(0, lambda: self.status.set(
                "收到文件: %s → %s" % (meta.get("name", ""), path)))
        except Exception:
            pass

    # ------------------------------------------------------------ 周期刷新
    def refresh(self):
        try:
            self.poll_room()
            rooms = sorted(self.node.rooms.keys())
            sig = "|".join(rooms)
            if sig != self._rooms_sig:
                self._rooms_sig = sig
                cur = self.room_var.get()
                self.room_cb["values"] = rooms
                if cur and cur in rooms:
                    self.room_var.set(cur)
                elif rooms:
                    self.room_var.set(rooms[0])
                    self.switch_room()
                self._update_sheet_choices()
            self.refresh_peers()
            self.refresh_chat()
            sc = self.node.scan_count
            self.scan_info.set(("扫描中…" if self.node.scanning else
                                "已扫描 %d 次 · 发现 %d 个节点" % (sc, len(self.node.peers))))
            self.status.set(
                "%s (%s)  |  界面 http://127.0.0.1:%s  |  房间 %d  |  群聊 %d  |  "
                "在线成员 %d  |  文件端口 %s"
                % (self.node.name, self.node.id[:8], self.node.http_port,
                   len(rooms), len(self.group_data),
                   len([u for u in self.node.active_users(self.room)]) if self.room else 0,
                   self.node.file_port))
        except Exception:
            traceback.print_exc()
        self.root.after(800, self.refresh)

    def refresh_peers(self):
        peers = self.node.peers_detail()
        sig = json.dumps(peers, ensure_ascii=False, sort_keys=True)
        if sig == self._peers_sig:
            return
        self._peers_sig = sig
        tree = self.peer_tree
        selected = tree.selection()
        for item in tree.get_children():
            tree.delete(item)
        src = {"multicast": "组播", "broadcast": "广播",
               "register": "定向探测", "": "—"}
        for p in peers:
            photo = self._photo(p["id"], self.node.peer_avatar_b64(p["id"]))
            self._peer_photos[p["id"]] = photo
            tree.insert("", "end", iid=p["id"], image=photo, values=(
                p["name"], p["note"], p["local_note"], p["ip"],
                src.get(p["source"], p["source"]),
                "在线" if p["online"] else "离线"))
        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])

    def refresh_lists(self):
        cfg = self.node.config_public()
        self.subnet_list.delete(0, "end")
        for x in cfg["subnets"]:
            self.subnet_list.insert("end", x)
        self.fav_list.delete(0, "end")
        for x in cfg["favorites"]:
            self.fav_list.insert("end", x)

    # ------------------------------------------------------------ 其他操作
    def _alert(self, msg):
        messagebox.showinfo("LanExcel", msg)

    def new_room(self):
        name = simpledialog.askstring("新建房间", "房间名（同事用相同名字即可互相同步）:",
                                      initialvalue="共享表1", parent=self.root)
        if not name:
            return
        self.node.get_room(name, create=True)
        self._rooms_sig = ""
        self.room_var.set(name)
        self.switch_room()

    def open_xlsx(self):
        path = filedialog.askopenfilename(
            title="打开要共享的 Excel 文件",
            filetypes=[("Excel 文件", "*.xlsx"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            info = self.node.open_xlsx(path)
            self._rooms_sig = ""
            self.room_var.set(info["room"])
            self.switch_room()
            self._alert(
                "已共享: %s\n房间: %s · 读取 %d 个单元格\n"
                "工作表: %s\n原有样式: %s\n\n"
                "样式（字体/合并/边框/列宽行高/公式/其它工作表）以原文件为模板完整保留，"
                "协作只同步单元格的值。"
                % (os.path.basename(path), info["room"], len(info["cells"]),
                   " / ".join(info.get("sheets", [])),
                   info.get("style", "")))
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def save_xlsx(self, save_as):
        if not self.room:
            self._alert("请先选择或新建房间")
            return
        path = None
        if save_as:
            path = filedialog.asksaveasfilename(
                title="保存为", defaultextension=".xlsx",
                initialfile=self.room + ".xlsx",
                filetypes=[("Excel 文件", "*.xlsx")])
            if not path:
                return
        try:
            p = self.node.save_xlsx(self.room, path)
            self._alert("已保存到: " + p)
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    def open_browser(self):
        webbrowser.open("http://127.0.0.1:%s" % self.node.http_port)

    def scan_now(self):
        self.node.scan_now()
        self._alert("已启动全网段扫描：组播 + 收藏设备 + 本网段 + 已添加网段")

    def edit_peer_note(self):
        sel = self.peer_tree.selection()
        if not sel:
            self._alert("请先选中一个同伴")
            return
        pid = sel[0]
        old = self.node.cfg.get("peer_notes", {}).get(pid, "")
        text = simpledialog.askstring("本地备注", "给这位同伴的备注（仅本机可见）:",
                                      initialvalue=old, parent=self.root)
        if text is None:
            return
        notes = dict(self.node.cfg.get("peer_notes", {}))
        notes[pid] = text
        self.node.update_config({"peer_notes": notes})
        self._peers_sig = ""
        self.refresh_peers()

    def favorite_selected(self):
        sel = self.peer_tree.selection()
        if not sel:
            self._alert("请先选中一个同伴")
            return
        ip = self.peer_tree.item(sel[0], "values")[3]
        if ip:
            self.node.update_config({"add_favorite": ip})
            self.refresh_lists()
            self._alert("已收藏 %s，跨网段扫描会优先探测它" % ip)

    def add_subnet(self):
        v = self.subnet_var.get().strip()
        if not v:
            return
        self.node.update_config({"add_subnet": v})
        self.subnet_var.set("")
        self.refresh_lists()
        self.node.scan_now()

    def remove_subnet(self):
        sel = self.subnet_list.curselection()
        if sel:
            self.node.update_config({"remove_subnet": self.subnet_list.get(sel[0])})
            self.refresh_lists()

    def add_favorite(self):
        v = self.favorite_var.get().strip()
        if not v:
            return
        self.node.update_config({"add_favorite": v})
        self.favorite_var.set("")
        self.refresh_lists()
        self.node.scan_now()

    def remove_favorite(self):
        sel = self.fav_list.curselection()
        if sel:
            self.node.update_config({"remove_favorite": self.fav_list.get(sel[0])})
            self.refresh_lists()

    def save_profile(self):
        self.node.update_config({"name": self.name_var.get().strip() or self.node.name,
                                 "note": self.note_var.get()})
        if self._pending_avatar:
            try:
                self.node.set_avatar(self._pending_avatar)
                self._pending_avatar = None
            except Exception as e:
                messagebox.showerror("头像保存失败", str(e))
                return
        self.name_var.set(self.node.name)
        self.note_var.set(self.node.note)
        self.update_nav_avatar()
        self.update_preview()
        self._peers_sig = ""
        self._alert("资料与头像已保存，并已立即广播给所有在线同伴")

    def on_close(self):
        try:
            self.node.shutdown()
        except Exception:
            pass
        self.root.destroy()


def main():
    try:
        init_theme_before = True
        # enable_dpi_awareness 必须在 Tk() 之前，由 ui_compat 封装
        from ui_compat import ui
        if ui is not None:
            try:
                ui.enable_dpi_awareness()
            except Exception:
                pass
        root = tk.Tk()
        init_theme(root)
        node = Node()
        node.no_browser = True
        node.start_services()
        LanExcelApp(root, node)
        root.mainloop()
    except Exception:
        msg = traceback.format_exc()
        try:
            with open(os.path.join(os.path.dirname(CONFIG_PATH), "lanexcel_error.log"),
                      "w", encoding="utf-8") as f:
                f.write(msg)
        except Exception:
            pass
        try:
            print(msg)
        except Exception:
            pass
        raise


if __name__ == "__main__":
    main()
