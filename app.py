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
from emojis import EMOJI_GROUPS, TEXT_FACES          # noqa: E402
from emoji_assets import EmojiImages, split_text, has_emoji   # noqa: E402
import privacy                                       # noqa: E402
from sheetstyle import describe as style_summary     # noqa: E402
from styled_sheet import StyledSheet                 # noqa: E402

import tkinter as tk                                  # noqa: E402
from tkinter import ttk, messagebox, filedialog, simpledialog   # noqa: E402
from ui_compat import (init_theme, make_button, make_card,      # noqa: E402
                       make_sheet, make_photo, FONT, LEGACY)

try:
    from PIL import Image, ImageTk                    # noqa: E402
except ImportError:                                   # Pillow 缺失时表情面板仍可用
    Image = ImageTk = None

SHEET_ROWS, SHEET_COLS = 60, 26
NAV_ITEMS = [("sheet", "协作表格"), ("chat", "局域网群聊"),
             ("peers", "在线成员"), ("net", "跨网段联机"),
             ("profile", "我的资料")]

ONLINE_FG = "#1A7A3C"     # 在线：深绿
OFFLINE_FG = "#9A9A9A"    # 离线：灰
CHAT_IMG_MAX = 240        # 聊天图片最长边（像素）
EMOJI_PANEL_PX = 24       # 表情面板图标尺寸
EMOJI_CHAT_PX = 20        # 聊天正文里的表情尺寸
GIF_MAX_FRAMES = 48       # 每张动图最多预缩放多少帧
GIF_MAX_SIDE = 240        # 动图最长边
GIF_MAX_ANIM = 6          # 同时播放的动图上限（超出只显示首帧）
PEER_AVATAR_PX = 26       # 成员列表头像尺寸（小图重绘快得多）
CHAT_RENDER_LIMIT = 300   # 聊天窗口最多渲染最近多少条消息


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
        self.emoji = EmojiImages(root)     # 彩色表情图片工厂（Twemoji PNG）
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
        self._deferred = []
        self._last_chat = 0.0
        self._last_peers = 0.0
        self._last_status = ""
        self._last_scan = ""
        self._transfer_sig = None
        self._member_list_sig = ()
        self._priv_rev = -1          # 隐私设置版本，变化时强制全量重拉
        self._priv_spec = {}
        self._pub_key = None         # v9 发布状态签名（避免每 tick 重绘发布条）
        self._locked = set()

        self.pages = {}
        self.nav_buttons = {}

        self.chat_since = 0
        self.current_gid = ""
        self.group_ids = []
        self.group_data = []
        self._rendered_mids = set()
        self._need_rebuild = False
        self._chat_images = []        # PhotoImage 引用，防止被垃圾回收
        self._anim_jobs = []          # GIF 动图动画任务
        self.tag_filter = tk.StringVar(value="全部")

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

        # 只先建协作表格页（首屏），其余页面分帧补齐，启动从 3.7s 降到 1s 内
        self._build_sheet_tab()
        self._build_footer()
        self.show_page("sheet")
        self._deferred = [self._build_chat_tab, self._build_peers_tab,
                          self._build_network_tab, self._build_profile_tab]
        root.after(1, self._build_next_page)

        self.node.on_file_received = self._on_file_received
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(300, self.refresh)
        self._warm_emoji()

    # ------------------------------------------------------------ 布局
    PAGE_BUILDERS = {"sheet": "_build_sheet_tab", "chat": "_build_chat_tab",
                     "peers": "_build_peers_tab", "net": "_build_network_tab",
                     "profile": "_build_profile_tab"}

    def _page(self, key):
        f = ttk.Frame(self.content, padding=10)
        f.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.pages[key] = f
        return f

    def _ensure_page(self, key):
        """页面懒构建：切到哪个页面才建哪个，启动只建首屏"""
        if key in self.pages:
            return True
        fn = getattr(self, self.PAGE_BUILDERS.get(key, ""), None)
        if fn is None:
            return False
        try:
            fn()
        except Exception:
            traceback.print_exc()
            return key in self.pages
        self.pages[key].lift()
        return key in self.pages

    def _build_next_page(self):
        """分帧补齐其余页面，每帧只建一页，界面不会假死"""
        if not self._deferred:
            return
        fn = self._deferred.pop(0)
        try:
            fn()
        except Exception:
            traceback.print_exc()
        cur = self.pages.get(self.page)
        if cur is not None:
            cur.lift()                       # 新建的页面会叠在最上层，纠正回来
        if self._deferred:
            self.root.after(20, self._build_next_page)

    def show_page(self, key):
        if key not in self.pages and not self._ensure_page(key):
            return
        self.page = key
        self.pages[key].lift()
        # 刷新延到下一帧做，先让页面切换立刻响应，避免点击后停顿
        if key == "chat":
            self._resume_anims()
            self.root.after(1, self._safe_chat_refresh)
        elif key == "peers":
            if self._anim_jobs:
                self._pause_anims()
            self.root.after(1, self._safe_peers_refresh)
        else:
            if self._anim_jobs:
                self._pause_anims()
            if key == "sheet" and self.room:
                self.root.after(1, self._safe_poll)
        if getattr(self, "_nav_active", None) != key:
            for k, b in self.nav_buttons.items():
                try:
                    b.configure(style="Primary.TButton" if k == key else "TButton")
                except Exception:
                    pass
            self._nav_active = key

    def _safe_chat_refresh(self):
        try:
            self.refresh_chat()
        except Exception:
            traceback.print_exc()

    def _safe_peers_refresh(self):
        try:
            self.refresh_peers()
        except Exception:
            traceback.print_exc()

    def _safe_poll(self):
        try:
            self.poll_room()
        except Exception:
            traceback.print_exc()

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
                                   ("另存为", lambda: self.save_xlsx(True), False),
                                   ("隐私设置", self.show_privacy_dialog, False)]:
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
        # 保真渲染控件（StyledSheet）只在打开带样式的 xlsx 时才创建，
        # 省掉首屏四分之一秒的构建时间
        self.styled_sheet = None
        self.sheet = self.plain_sheet
        self.plain_sheet.bind("<<SheetChanged>>", self.on_sheet_changed)
        self.plain_sheet.bind("<<SheetSelect>>", self.on_sheet_select)
        self.plain_sheet.bind("<<CellLocked>>", self._on_cell_locked)
        self.priv_bar = tk.Frame(f, bg="#FFF8E1")
        self.priv_label = tk.Label(self.priv_bar, text="", anchor="w",
                                   bg="#FFF8E1", fg="#8A6100",
                                   font=(FONT, 9), padx=10, pady=5,
                                   justify="left")
        self.priv_label.pack(side="left", fill="x", expand=True)
        self.priv_btn = tk.Label(self.priv_bar, text="设置隐私区域",
                                 bg="#FFF8E1", fg="#0067C0", font=(FONT, 9),
                                 cursor="hand2", padx=10)
        self.priv_btn.pack(side="right")
        self.priv_btn.bind("<Button-1>", lambda e: self.show_privacy_dialog())
        # v9 发布条：本地表格打开后先是草稿，点「发布」才对同伴可见
        self.pub_bar = tk.Frame(f, bg="#E8F1FF")
        self.pub_label = tk.Label(self.pub_bar, text="", anchor="w",
                                  bg="#E8F1FF", fg="#0B4C8C",
                                  font=(FONT, 9), padx=10, pady=5,
                                  justify="left")
        self.pub_label.pack(side="left", fill="x", expand=True)
        self.pub_kind = tk.Label(self.pub_bar, text="", bg="#E8F1FF", fg="#0067C0",
                                 font=(FONT, 9, "bold"), cursor="hand2", padx=10)
        self.pub_kind.pack(side="right")
        self.pub_kind.bind("<Button-1>", lambda e: self.show_privacy_dialog())
        self.pub_go = tk.Label(self.pub_bar, text="立即发布", bg="#0067C0",
                               fg="#FFFFFF", font=(FONT, 9, "bold"),
                               cursor="hand2", padx=12, pady=3)
        self.pub_go.pack(side="right", padx=(0, 10))
        self.pub_go.bind("<Button-1>", lambda e: self.publish_current())
        self.pub_drop = tk.Label(self.pub_bar, text="放弃", bg="#E8F1FF",
                                 fg="#8A8F98", font=(FONT, 9),
                                 cursor="hand2", padx=8)
        self.pub_drop.pack(side="right")
        self.pub_drop.bind("<Button-1>", lambda e: self.discard_current())
        ttk.Label(f, textvariable=self.info, style="Muted.TLabel").pack(
            fill="x", pady=(6, 0))
        self.pub_bar.pack(fill="x", before=self.sheet_host, pady=(0, 6))
        self.pub_bar.pack_forget()
        self.priv_bar.pack(fill="x", pady=(4, 0))
        self.priv_bar.pack_forget()
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
        self.group_list.bind("<Button-3>", self._group_menu)
        make_button(left, "新建群聊", self.create_group, True).pack(fill="x", pady=2)
        make_button(left, "邀请成员进群", self.invite_members).pack(fill="x", pady=2)
        make_button(left, "群设置 / 管理", self.show_group_settings).pack(fill="x", pady=2)
        self.pin_btn = make_button(left, "置顶该群", self.toggle_pin_group)
        self.pin_btn.pack(fill="x", pady=2)
        make_button(left, "刷新", self.refresh_chat).pack(fill="x", pady=2)
        ttk.Label(left, text="右键群名可置顶 / 设置\n提示：群内发的协作表格\n只有群成员能看到和编辑",
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
        smile = self.emoji.get("😀", 18)
        if smile is not None:
            emb = ttk.Button(entry, text=" 表情", image=smile, compound="left",
                             command=self.show_emoji_picker)
            emb.image = smile
        else:
            emb = make_button(entry, "表情", self.show_emoji_picker)
        emb.pack(side="left", padx=(6, 0))
        make_button(entry, "发送", self.send_chat_text, True).pack(side="left", padx=6)
        self.atall_btn = make_button(entry, "@所有人", self.send_at_all)
        self.atall_btn.pack(side="left", padx=(0, 6))

        tools = ttk.Frame(center)
        tools.pack(fill="x")
        for text, cmd in [("发送协作表格", self.send_sheet_card),
                          ("本地表格转协作表", self.send_local_sheet),
                          ("发送文件到群", self.send_file_group),
                          ("发送图片(GIF动图)", self.send_image_group)]:
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
                         "启动即扫描本网段与已添加的网段；组播不通时用 register 定向探测。"
                         "支持给联系人打标签分组、置顶，在线/离线一目了然。",
                         "search")
        card.pack(fill="x", pady=(0, 10))
        make_button(card.actions, "立即扫描", self.scan_now, True).pack(side="left")
        ttk.Label(card.actions, text="显示分组:", style="Muted.TLabel").pack(
            side="left", padx=(14, 0))
        self.tag_cb = ttk.Combobox(card.actions, textvariable=self.tag_filter,
                                   state="readonly", width=14)
        self.tag_cb.pack(side="left", padx=4)
        self.tag_cb.bind("<<ComboboxSelected>>", lambda e: self.refresh_peers(True))

        cols = ("name", "note", "local_note", "ip", "source", "status")
        self.peer_tree = ttk.Treeview(f, columns=cols, show="tree headings", height=14)
        self.peer_tree.heading("#0", text="头像")
        self.peer_tree.column("#0", width=64, stretch=False)
        heads = {"name": ("显示名称", 140), "note": ("对方备注", 160),
                 "local_note": ("我的备注", 160), "ip": ("IP 地址", 125),
                 "source": ("发现方式", 88), "status": ("状态", 90)}
        for c in cols:
            self.peer_tree.heading(c, text=heads[c][0])
            self.peer_tree.column(c, width=heads[c][1], anchor="w")
        self.peer_tree.pack(fill="both", expand=True, side="left")
        vs = ttk.Scrollbar(f, orient="vertical", command=self.peer_tree.yview)
        vs.pack(side="left", fill="y")
        self.peer_tree.configure(yscrollcommand=vs.set)
        # 在线绿色 / 离线灰色
        self.peer_tree.tag_configure("online", foreground=ONLINE_FG)
        self.peer_tree.tag_configure("offline", foreground=OFFLINE_FG)

        side = ttk.Frame(f, padding=(10, 0, 0, 0))
        side.pack(side="left", fill="y")
        for text, cmd in [("给选中同伴写备注", self.edit_peer_note),
                          ("设置标签 / 置顶", self.edit_peer_tags),
                          ("发送文件给选中成员", self.send_file_to_peer),
                          ("邀请进当前群", self.invite_members),
                          ("收藏选中 IP", self.favorite_selected),
                          ("刷新", self.refresh_peers)]:
            make_button(side, text, cmd).pack(fill="x", pady=3)
        ttk.Label(side, text="提示：\n· ● 在线 / ○ 离线\n· 对方备注 = 对方自己填\n"
                            "· 我的备注与标签仅本机可见\n· 右键群聊列表可置顶群聊\n"
                            "· 双击传输记录可打开文件",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=12)

    def _selected_peer(self):
        """选中的必须是联系人（而不是分组节点）时返回其 id

        同一个联系人可能同时出现在「置顶」「多个标签」下，所以树行 iid 是
        ``pid@序号`` 形式，这里剥掉后缀还原成真正的成员 id。
        """
        sel = self.peer_tree.selection()
        if not sel:
            return ""
        iid = sel[0]
        if iid.startswith("grp:"):
            return ""
        pid = iid.rsplit("@", 1)[0]
        with self.node.peers_lock:
            return pid if pid in self.node.peers else ""

    def edit_peer_tags(self):
        """给联系人设置标签分组（逗号分隔）与置顶"""
        pid = self._selected_peer()
        if not pid:
            self._alert("请先在列表中选中一位联系人（不能选分组标题行）")
            return
        tags_map = dict(self.node.cfg.get("peer_tags", {}))
        pins = list(self.node.cfg.get("pinned_peers", []))
        with self.node.peers_lock:
            name = (self.node.peers.get(pid) or {}).get("name") or pid[:8]
        all_tags = sorted({t for tl in tags_map.values() for t in tl})
        top = tk.Toplevel(self.root)
        top.title("联系人标签 / 置顶 · " + name)
        top.geometry("380x260")
        top.transient(self.root)
        top.grab_set()
        ttk.Label(top, text="联系人: %s" % name,
                  font=(FONT, 11, "bold")).pack(anchor="w", padx=16, pady=(14, 2))
        ttk.Label(top, text="已有标签: " + ("、".join(all_tags) if all_tags else "（暂无）"),
                  style="Muted.TLabel").pack(anchor="w", padx=16)
        ttk.Label(top, text="标签（多个用逗号分隔，留空清除全部标签）:",
                  style="Muted.TLabel").pack(anchor="w", padx=16, pady=(10, 2))
        old = "、".join(tags_map.get(pid, []))
        var = tk.StringVar(value=old.replace("、", ","))
        ttk.Entry(top, textvariable=var, width=36).pack(padx=16, fill="x")
        pin_var = tk.BooleanVar(value=pid in pins)
        ttk.Checkbutton(top, text="置顶该联系人（排到列表最前）",
                        variable=pin_var).pack(anchor="w", padx=16, pady=(12, 0))

        def save():
            tags = [t.strip() for t in var.get().replace("，", ",").split(",")]
            tags = [t for t in tags if t]
            if tags:
                tags_map[pid] = tags
            else:
                tags_map.pop(pid, None)
            if pin_var.get() and pid not in pins:
                pins.append(pid)
            elif not pin_var.get() and pid in pins:
                pins.remove(pid)
            self.node.update_config({"peer_tags": tags_map, "pinned_peers": pins})
            top.destroy()
            self.refresh_peers(True)

        make_button(top, "保存", save, True).pack(pady=12)

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
    def _photo(self, key, b64, px=0):
        """头像 PhotoImage（带缓存）；px 给定时按整数倍缩小，列表里更省重绘"""
        if not b64:
            return None
        k = "%s:%d:%s" % (key, px, b64[:48])
        if k not in self._photo_cache:
            ph = make_photo(b64, self.root)
            if ph is not None and px and ph.width() > px:
                try:
                    ph = ph.subsample(max(1, int(round(ph.width() / float(px)))))
                except Exception:
                    pass
            self._photo_cache[k] = ph
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

    def _ensure_styled_sheet(self):
        """惰性创建保真表格控件（打开带样式的 xlsx 才需要）"""
        if self.styled_sheet is not None:
            return self.styled_sheet
        headers = [col_name(c) for c in range(1, self.view_cols + 1)]
        self.styled_sheet = StyledSheet(self.sheet_host, self.view_rows,
                                        self.view_cols, headers,
                                        column_width=104, row_height=27)
        self.styled_sheet.bind("<<SheetChanged>>", self.on_sheet_changed)
        self.styled_sheet.bind("<<SheetSelect>>", self.on_sheet_select)
        self.styled_sheet.bind("<<CellLocked>>", self._on_cell_locked)
        return self.styled_sheet

    # ------------------------------------------------------------ 表格同步
    def _activate_sheet(self, styled):
        """有原始 xlsx 模板时切到保真渲染控件，否则用普通协作网格"""
        if styled:
            self._ensure_styled_sheet()
        target = self.styled_sheet if (styled and self.styled_sheet is not None) \
            else self.plain_sheet
        if self.sheet is target:
            return
        try:
            self.sheet.finish_edit(False)
        except Exception:
            pass
        for widget in (self.plain_sheet, self.styled_sheet):
            if widget is None:
                continue
            try:
                widget.pack_forget()
            except Exception:
                pass
        target.pack(fill="both", expand=True)
        self.sheet = target
        setter = getattr(target, "set_locked", None)
        if setter and getattr(self, "_locked", None):
            try:
                setter(set(self._locked))
            except Exception:
                pass

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
        self._priv_rev = -1
        self._priv_spec = {}
        self._locked = set()
        self._pub_key = None
        self.node.my_presence["room"] = name
        self._update_pub_bar()
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
            try:
                self.node.apply_edit(self.room, cells, local=True)
                self.info.set("已同步 %d 个单元格 · 房间: %s"
                              % (len(cells), self.room))
            except ValueError as e:
                # 隐私格被他人占用：放弃这次写入，恢复成服务端的数据
                self.info.set("⚠ " + str(e))
                self.since = 0
                self.poll_room()

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
        self._update_pub_bar()
        info = self.node.room_style(self.room)
        if info and info.get("version", 0) != self._style_version:
            self._style_version = info.get("version", 0)
            self._apply_room_style(info)
        d = self.node.poll(self.since, self.room)
        # 隐私设置变化：本机视角整体变了，必须全量重拉一次
        p = d.get("priv") or {}
        rev = int(p.get("rev", 0))
        if rev != self._priv_rev:
            self._priv_rev = rev
            self._priv_spec = p
            self.since = 0
            self._update_priv_bar(p, d.get("owner", ""))
            if rev:
                d = self.node.poll(0, self.room)
        self.since = d.get("seq", self.since)
        locked = set()
        if d.get("full"):
            cells = d.get("cells", {})
            matrix = [["" for _ in range(self.view_cols)]
                      for _ in range(self.view_rows)]
            self.grid_state = {(r, c): "" for r in range(self.view_rows)
                               for c in range(self.view_cols)}
            for key, cell in cells.items():
                r, c = (int(x) - 1 for x in key.split(","))
                if not (0 <= r < self.view_rows and 0 <= c < self.view_cols):
                    continue
                text = self._cell_text(cell)
                if len(cell) > 3 and cell[3]:
                    locked.add((r, c))
                matrix[r][c] = text
                self.grid_state[(r, c)] = text
            self.sheet.set_data(matrix, rows=self.view_rows,
                                columns=self.view_cols)
            self._set_locked(locked)
        else:
            changed = False
            for e in d.get("edits", []):
                for key, cell in e["cells"].items():
                    r, c = (int(x) - 1 for x in key.split(","))
                    if not (0 <= r < self.view_rows and 0 <= c < self.view_cols):
                        continue
                    text = self._cell_text(cell)
                    hidden = len(cell) > 3 and bool(cell[3])
                    if hidden != ((r, c) in self._locked):
                        if hidden:
                            locked.add((r, c))
                        else:
                            self._locked.discard((r, c))
                        changed = True
                    if self.grid_state.get((r, c), "") != text:
                        self.grid_state[(r, c)] = text
                        self.sheet.set_cell(r, c, text)
            if changed:
                self._set_locked(locked or {x for x in self._locked})

    @staticmethod
    def _cell_text(cell):
        v = cell[0] if cell else ""
        if len(cell) > 3 and cell[3]:
            return "***"
        return "" if v is None else str(v)

    def _set_locked(self, cells):
        cells = set(cells)
        self._locked = cells
        setter = getattr(self.sheet, "set_locked", None)
        if setter:
            try:
                setter(cells)
            except Exception:
                pass

    def _update_priv_bar(self, spec, owner=""):
        """表格页顶部的隐私状态条"""
        if bool(spec) and bool(spec.get("on")):
            is_owner = (owner or self.node.id) == self.node.id
            text = privacy.describe(spec)
            if not is_owner:
                text += " · 你只能看到自己填写的内容，他人填写处以 *** 显示"
            self.priv_label.configure(text="🔒  " + text)
            if is_owner:
                self.priv_btn.configure(text="修改隐私区域")
            else:
                with self.node.peers_lock:
                    pname = (self.node.peers.get(owner) or {}).get("name") or ""
                pname = pname or (owner[:8] if owner else "同伴")
                self.priv_btn.configure(text="发布者：%s" % pname)
            if not self.priv_bar.winfo_ismapped():
                self.priv_bar.pack(fill="x", pady=(4, 0))
        else:
            self.priv_btn.configure(text="设为隐私表格")
            self.priv_bar.pack_forget()

    def _on_cell_locked(self, event=None):
        self.info.set("🔒 这个单元格是他人填写的隐私内容，已用 *** 遮掩，不能查看或修改")

    # ------------------------------------------------------------ 发布（v9）
    def _update_pub_bar(self):
        """草稿状态条：没发布前提醒「同事还看不到」，并给出发布入口"""
        if not getattr(self, "pub_bar", None):
            return
        if not self.room:
            self._pub_key = None
            self.pub_bar.pack_forget()
            return
        info = self.node.room_privacy(self.room) or {}
        pub = bool(info.get("published", True))
        is_owner = bool(info.get("is_owner", True))
        spec = info.get("priv") or {}
        key = (self.room, pub, is_owner, bool(spec.get("on")),
               len(spec.get("rows") or []), len(spec.get("cols") or []))
        if key == getattr(self, "_pub_key", None):
            return
        self._pub_key = key
        if pub or not is_owner:
            self.pub_bar.pack_forget()
            return
        if spec.get("on"):
            kind = "隐私表格 · " + privacy.describe(spec)
            self.pub_kind.configure(text="设置隐私区域")
        else:
            kind = "公开表格（所有人可见可编辑）"
            self.pub_kind.configure(text="改为隐私表格")
        self.pub_label.configure(
            text="📝 草稿（未发布）：%s   —   同事现在还看不到这张表格，也不能编辑。" % kind)
        if not self.pub_bar.winfo_ismapped():
            self.pub_bar.pack(fill="x", before=self.sheet_host, pady=(0, 6))

    def publish_current(self):
        """点「发布」：草稿才真正共享给同事；发布后立刻可见可编辑"""
        if not self.room:
            self._alert("请先打开或新建一个协作表格")
            return False
        info = self.node.room_privacy(self.room) or {}
        if not info.get("is_owner", True):
            self._alert("这张表格由他人发布，只有发布者可以发布它")
            return False
        if info.get("published"):
            self._alert("这张表格已经发布过了，同事可以看到并填写")
            return False
        spec = info.get("priv") or {}
        if spec.get("on") and not (spec.get("rows") or spec.get("cols")):
            messagebox.showwarning(
                "发布表格",
                "这是隐私表格，但还没有指定隐私行 / 隐私列。\n"
                "请先点「设置隐私区域」填好，再点发布。", parent=self.root)
            return False
        kind = "隐私表格" if spec.get("on") else "公开表格"
        detail = privacy.describe(spec)
        if not messagebox.askyesno(
                "发布%s" % kind,
                "把「%s」发布为%s？\n\n%s\n\n发布后，同一内网的同事就能在各自界面里看到"
                "这张表格，并按上面的规则查看 / 填写。"
                % (self.room, kind, detail), parent=self.root):
            return False
        try:
            self.node.publish_room(self.room, None)
        except Exception as e:
            messagebox.showerror("发布失败", str(e), parent=self.root)
            return False
        self._pub_key = None
        self._rooms_sig = ""
        self._update_pub_bar()
        self.info.set("已发布：%s · 同事现在可以看到并填写" % self.room)
        self._alert("已发布「%s」（%s）\n\n同事的界面里马上就会出现这张表格。"
                    % (self.room, detail))
        return True

    def discard_current(self):
        """放弃未发布的草稿：本机也不再保留（原始 Excel 文件不受影响）"""
        if not self.room:
            return
        info = self.node.room_privacy(self.room) or {}
        if info.get("published"):
            self._alert("这张表格已经发布，不能直接放弃；如需收回请用「收回发布」")
            return
        if not messagebox.askyesno(
                "放弃草稿",
                "放弃「%s」？\n\n这张草稿还没发布，放弃后本机也不再保留它；"
                "原始 Excel 文件不受任何影响。" % self.room, parent=self.root):
            return
        name = self.room
        try:
            self.node.discard_draft(name)
        except Exception as e:
            messagebox.showerror("放弃失败", str(e), parent=self.root)
            return
        self._pub_key = None
        self._rooms_sig = ""
        rooms = sorted(self.node.rooms.keys())
        self.room_cb["values"] = rooms
        self.room_var.set(rooms[0] if rooms else "")
        self.room = ""
        self.pub_bar.pack_forget()
        if rooms:
            self.switch_room()
        else:
            self.pub_bar.pack_forget()
        self.info.set("已放弃草稿：%s" % name)

    def ask_table_kind(self, fname):
        """打开本地表格时先问一句：公开表格还是隐私表格

        返回 "public" / "private" / None（取消）
        """
        top = tk.Toplevel(self.root)
        top.title("打开为协作表格")
        top.transient(self.root)
        top.resizable(False, False)
        top.configure(bg="#FFFFFF")
        wrap = ttk.Frame(top, padding=18)
        wrap.pack(fill="both", expand=True)
        ttk.Label(wrap, text="「%s」" % fname,
                  font=(FONT, 10, "bold")).pack(anchor="w")
        ttk.Label(wrap, text="要把这张表格发布成哪种协作表格？\n"
                            "选好之后点「发布」，同事才可以看到和填写。",
                  style="Muted.TLabel", justify="left").pack(anchor="w", pady=(4, 10))
        kind = tk.StringVar(value="public")
        box = ttk.Frame(wrap)
        box.pack(fill="x")

        def card(value, title, desc):
            fr = ttk.Frame(box, padding=(10, 8))
            fr.pack(fill="x", pady=2)
            ttk.Radiobutton(fr, text=title, value=value,
                            variable=kind).pack(anchor="w")
            ttk.Label(fr, text=desc, style="Muted.TLabel",
                      justify="left").pack(anchor="w", padx=(22, 0))
        card("public", "公开表格（原有协作方式）",
             "所有收到这张表格的同事都能看到全部内容并自由填写。")
        card("private", "隐私表格",
             "指定某些行 / 列只有「填写者本人」和「你（发布者）」可见，\n"
             "其他人看到 *** 且不能修改。设置完再点发布。")

        result = {"v": None}
        btns = ttk.Frame(wrap)
        btns.pack(fill="x", pady=(14, 0))

        def ok():
            result["v"] = kind.get()
            top.destroy()
        make_button(btns, "确定", ok, True).pack(side="right")
        make_button(btns, "取消", top.destroy).pack(side="right", padx=6)
        top.grab_set()
        self.root.wait_window(top)
        return result["v"]

    # ------------------------------------------------------------ 隐私表格
    def show_privacy_dialog(self, initial=None, publish_after=False):
        """发布者设置哪些行 / 列属于隐私区域

        publish_after=True 时用于「打开表格 → 选隐私表格」流程：
        设置好隐私区域后直接发布，一步到位。
        """
        if not self.room:
            self._alert("请先打开或新建一个协作表格")
            return
        info = self.node.room_privacy(self.room) or {}
        cur = initial or info.get("priv") or {}
        if info and not info.get("is_owner", True):
            with self.node.peers_lock:
                pname = (self.node.peers.get(info.get("owner", "")) or {}).get("name")
            messagebox.showinfo(
                "隐私设置",
                "这张表格由 %s 发布，只有发布者可以设置隐私区域。\n\n%s"
                % (pname or (info.get("owner") or "")[:8], privacy.describe(cur)),
                parent=self.root)
            return

        draft = not bool(info.get("published", True))
        top = tk.Toplevel(self.root)
        top.title("设置隐私区域并发布" if publish_after else "隐私表格设置")
        top.transient(self.root)
        top.resizable(False, False)
        top.configure(bg="#FFFFFF")
        wrap = ttk.Frame(top, padding=16)
        wrap.pack(fill="both", expand=True)

        if publish_after and draft:
            ttk.Label(wrap, text="第 2 步 / 共 2 步：指定隐私行 / 列，然后点「保存并发布」",
                      font=(FONT, 9, "bold"), foreground="#0B4C8C").pack(
                anchor="w", pady=(0, 6))
        ttk.Label(wrap, text="隐私表格：指定的行 / 列只有「填写者本人」和「发布者」可见，\n"
                            "其他人看到的是 *** ，也改不了别人的内容。",
                  justify="left").pack(anchor="w")
        ttk.Label(wrap, text="公开表格（原有协作方式，所有人可见可编辑）",
                  style="Muted.TLabel").pack(anchor="w", pady=(8, 0))

        on_var = tk.BooleanVar(value=bool(cur.get("on")))
        row_on = ttk.Frame(wrap)
        row_on.pack(fill="x", pady=(6, 2))
        ttk.Radiobutton(row_on, text="公开表格", value=False,
                        variable=on_var).pack(side="left")
        ttk.Radiobutton(row_on, text="隐私表格", value=True,
                        variable=on_var).pack(side="left", padx=(14, 0))

        ttk.Label(wrap, text="隐私列（表头字母或列号，如 D,E 或 4-6）").pack(
            anchor="w", pady=(10, 2))
        cols_var = tk.StringVar(value=privacy.compress_cols(cur.get("cols")))
        ttk.Entry(wrap, textvariable=cols_var, width=46).pack(anchor="w")

        ttk.Label(wrap, text="隐私行（行号，如 3,5-8）").pack(anchor="w", pady=(10, 2))
        rows_var = tk.StringVar(value=privacy.compress(cur.get("rows") or []))
        ttk.Entry(wrap, textvariable=rows_var, width=46).pack(anchor="w")

        bar = ttk.Frame(wrap)
        bar.pack(fill="x", pady=(6, 0))

        def add_current(which):
            try:
                r, c = self.sheet.active
            except Exception:
                return
            if which == "col":
                nums = privacy.parse_cols(cols_var.get()) + [c + 1]
                cols_var.set(privacy.compress_cols(nums))
            else:
                nums = privacy.parse_rows(rows_var.get()) + [r + 1]
                rows_var.set(privacy.compress(nums))
        make_button(bar, "＋ 当前选中列", lambda: add_current("col")).pack(side="left")
        make_button(bar, "＋ 当前选中行", lambda: add_current("row")).pack(
            side="left", padx=6)

        prev = ttk.Label(wrap, text="", style="Muted.TLabel", justify="left")
        prev.pack(anchor="w", pady=(10, 0))

        def refresh_preview(*_):
            if not on_var.get():
                prev.configure(text="当前：公开表格，行为与原协作表格完全一致")
                return
            spec = {"on": 1, "rows": privacy.parse_rows(rows_var.get()),
                    "cols": privacy.parse_cols(cols_var.get())}
            if not spec["rows"] and not spec["cols"]:
                prev.configure(text="⚠ 请至少填写一组隐私行或隐私列")
            else:
                prev.configure(text="当前：" + privacy.describe(spec))
        for var in (cols_var, rows_var):
            var.trace_add("write", refresh_preview)
        on_var.trace_add("write", refresh_preview)
        refresh_preview()

        tip = ttk.Label(wrap, text=("提示：点「保存并发布」后，同事才会看到这张表格；\n"
                                    "发布前它只是本机的草稿。") if publish_after else
                        ("提示：建议在发布表格时就设置好隐私区域；\n"
                         "已经公开同步出去的内容无法收回。"),
                        style="Muted.TLabel", justify="left")
        tip.pack(anchor="w", pady=(8, 0))

        btns = ttk.Frame(wrap)
        btns.pack(fill="x", pady=(12, 0))

        def apply():
            rows = privacy.parse_rows(rows_var.get())
            cols = privacy.parse_cols(cols_var.get())
            if on_var.get() and not rows and not cols:
                messagebox.showwarning("隐私设置", "请至少填写一组隐私行或隐私列",
                                       parent=top)
                return
            try:
                if publish_after:
                    # 设置 + 发布一步完成：发布后同事立刻可见
                    self.node.publish_room(self.room, on_var.get(), rows, cols)
                else:
                    self.node.set_privacy(self.room, rows=rows, cols=cols,
                                          on=on_var.get())
            except Exception as e:
                messagebox.showerror("设置失败", str(e), parent=top)
                return
            top.destroy()
            self._priv_rev = -1
            self._pub_key = None
            self.since = 0
            self.poll_room()
            self._update_pub_bar()
            desc = privacy.describe(
                {"on": 1, "rows": rows, "cols": cols} if on_var.get() else None)
            if publish_after:
                self.info.set("已发布：%s · %s" % (self.room, desc))
                self._alert("已发布「%s」\n%s\n\n同事现在可以看到这张表格了。"
                            % (self.room, desc))
            else:
                self._alert("已保存隐私设置：\n" + desc)

        make_button(btns, "保存并发布" if publish_after else "保存", apply,
                    True).pack(side="right")
        make_button(btns, "取消", top.destroy).pack(side="right", padx=6)
        top.grab_set()
        return top

    # ------------------------------------------------------------ 群聊逻辑
    def _avatar_for(self, pid, data=""):
        b64 = data or (avatar_b64(self.node.avatar, self.node.avatar_data)
                       if pid == self.node.id else self.node.peer_avatar_b64(pid))
        return self._photo(str(pid), b64)

    def _append_message(self, m, scroll=True):
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
            elif kind == "image":
                t.insert("end", "  [已撤回] %s 撤回了一张图片\n\n" % who, "recalled")
            else:
                t.insert("end", "  [已撤回] %s 撤回了一条消息\n\n" % who, "recalled")
        elif kind == "text":
            if m.get("at_all"):
                t.insert("end", "  @所有人 ", ("body", "atall"))
            t.insert("end", "  ", "body")
            self._insert_rich(t, m.get("text", ""), "body")
            t.insert("end", "\n\n", "body")
        elif kind == "system":
            t.insert("end", "  %s\n\n" % m.get("text", ""), "muted")
        elif kind == "sheet":
            t.insert("end", "  [协作表格·仅群成员可见] %s · %s 格\n"
                     % (m.get("room", ""), m.get("cells", 0)), "body")
            tag = "jump_%s" % m.get("seq")
            t.insert("end", "  打开这张协作表格\n\n", ("link", tag))
            t.tag_bind(tag, "<Button-1>",
                       lambda e, r=m.get("room", ""): self.jump_to_sheet(r))
        elif kind == "image":
            t.insert("end", "  [图片] %s · %s\n"
                     % (m.get("file", ""), human_size(m.get("size"))), "body")
            path = m.get("path", "")
            photo, anim_scale = self._load_chat_image(path)
            if photo is not None:
                t.insert("end", "  ")
                mark = "imgmark_%s" % m.get("seq")
                t.mark_set(mark, "end-1c")
                t.mark_gravity(mark, "left")
                t.image_create("end", image=photo)
                if anim_scale:
                    self._start_gif(mark, path, anim_scale)
                t.insert("end", "\n")
                if os.path.exists(path):
                    tag = "img_%s" % m.get("seq")
                    t.insert("end", "  查看原图\n\n", ("link", tag))
                    t.tag_bind(tag, "<Button-1>",
                               lambda e, p=path: self.open_path(p))
                else:
                    t.insert("end", "  （等待接收完成）\n\n", "muted")
            elif path and os.path.exists(path):
                t.insert("end", "  （图片格式暂不支持预览）\n\n", "muted")
                tag = "img_%s" % m.get("seq")
                t.insert("end", "  打开此图片\n\n", ("link", tag))
                t.tag_bind(tag, "<Button-1>",
                           lambda e, p=path: self.open_path(p))
            else:
                t.insert("end", "  （图片未接收或已被清理）\n\n", "muted")
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
        if scroll:
            t.see("end")

    # ------------------------------------------------ 富文本（彩色表情）
    def _insert_rich(self, text_widget, text, tag="body", px=None):
        """把带表情的文字插入 Text：表情用彩色 PNG，其余照常排版

        Tk 文本控件用 GDI 画字，彩色 Emoji 字体（COLR）会被渲染成黑白轮廓，
        所以这里把 Emoji 拆出来用图片贴进文本流，效果与微信一致。
        """
        px = px or EMOJI_CHAT_PX
        for chunk, is_emoji in split_text(text):
            if is_emoji:
                ph = self.emoji.get(chunk, px)
                if ph is not None:
                    text_widget.image_create("end", image=ph)
                    self._chat_images.append(ph)
                    continue
            text_widget.insert("end", chunk, tag)

    # ------------------------------------------------ 聊天图片 / GIF 动图
    def _load_chat_image(self, path):
        """加载聊天图片：返回 (PhotoImage, GIF动图缩放比 或 None)"""
        if Image is None or not path or not os.path.isfile(path):
            return None, None
        try:
            img = Image.open(path)
        except Exception:
            return None, None
        try:
            frames = int(getattr(img, "n_frames", 1) or 1)
        except Exception:
            frames = 1
        animated = (img.format or "").upper() == "GIF" and frames > 1
        w, h = img.size
        scale = min(1.0, float(CHAT_IMG_MAX) / max(w, h)) if max(w, h) > CHAT_IMG_MAX else 1.0
        if animated:
            try:
                return self._gif_frame(img, 0, scale), scale
            except Exception:
                return None, None
        try:
            size = (max(1, int(w * scale)), max(1, int(h * scale)))
            ph = ImageTk.PhotoImage(img.convert("RGBA").resize(size), master=self.root)
            self._chat_images.append(ph)
            if len(self._chat_images) > 400:
                del self._chat_images[:200]
            return ph, None
        except Exception:
            return None, None

    def _gif_frame(self, img, idx, scale):
        img.seek(idx)
        frame = img.convert("RGBA")
        size = (max(1, int(frame.size[0] * scale)),
                max(1, int(frame.size[1] * scale)))
        ph = ImageTk.PhotoImage(frame.resize(size), master=self.root)
        self._chat_images.append(ph)
        if len(self._chat_images) > 400:
            del self._chat_images[:200]
        return ph

    def _prepare_gif(self, img, scale):
        """一次性把所有帧预缩放成 PhotoImage

        旧实现每播一帧都要 seek+convert+resize，多张动图时主线程被打满，
        整个界面跟着卡。预缩放后播放只剩 image_configure，开销接近于零。
        """
        frames, delays = [], []
        try:
            n = int(getattr(img, "n_frames", 1) or 1)
        except Exception:
            n = 1
        step = 1
        if n > GIF_MAX_FRAMES:
            step = int(n / float(GIF_MAX_FRAMES) + 0.999)
        for i in range(0, n, step):
            try:
                img.seek(i)
                f = img.convert("RGBA")
                w, h = f.size
                s = scale
                if max(w, h) * s > GIF_MAX_SIDE:
                    s = GIF_MAX_SIDE / float(max(w, h))
                size = (max(1, int(w * s)), max(1, int(h * s)))
                if size != f.size:
                    f = f.resize(size, Image.BILINEAR)
                frames.append(ImageTk.PhotoImage(f, master=self.root))
                delays.append(int(img.info.get("duration", 100) or 100))
            except Exception:
                continue
        if not delays:
            delays = [100]
        return frames, delays

    def _start_gif(self, mark, path, scale=1.0):
        """在聊天窗口里循环播放 GIF 动图（预缩放帧 + 并发上限）"""
        if Image is None or not path or len(self._anim_jobs) >= GIF_MAX_ANIM:
            return
        try:
            img = Image.open(path)
            frames, delays = self._prepare_gif(img, scale)
            try:
                img.close()
            except Exception:
                pass
        except Exception:
            return
        if not frames:
            return
        st = {"mark": mark, "frames": frames, "delays": delays, "idx": 0,
              "job": None, "paused": False}
        self._anim_jobs.append(st)
        if self.page == "chat":
            self._gif_tick(st)

    def _gif_tick(self, st):
        if st.get("paused") or self.page != "chat":
            st["job"] = None
            return
        try:
            self.chat_text.image_configure(st["mark"],
                                           image=st["frames"][st["idx"]])
        except Exception:
            st["job"] = None
            return
        delay = st["delays"][st["idx"]] if st["idx"] < len(st["delays"]) else 100
        st["idx"] = (st["idx"] + 1) % len(st["frames"])
        st["job"] = self.root.after(max(delay, 60),
                                    lambda: self._gif_tick(st))

    def _pause_anims(self):
        """切走聊天页时停掉所有动图，别在后台白烧 CPU"""
        for st in self._anim_jobs:
            st["paused"] = True
            job = st.get("job")
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
            st["job"] = None

    def _resume_anims(self):
        for st in self._anim_jobs:
            if st.get("paused"):
                st["paused"] = False
                if not st.get("job"):
                    self._gif_tick(st)

    def _cancel_anims(self):
        for st in self._anim_jobs:
            job = st.get("job")
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self._anim_jobs = []

    # ------------------------------------------------ 表情面板（Canvas 绘制）
    EMOJI_CELL = 34
    EMOJI_COLS = 12

    def show_emoji_picker(self):
        old = getattr(self, "_emoji_top", None)
        if old is not None:
            try:
                old.deiconify()
                old.lift()
                old.focus_set()
                return
            except Exception:
                self._emoji_top = None

        C = self.EMOJI_CELL
        top = tk.Toplevel(self.root)
        self._emoji_top = top
        top.title("表情")
        top.geometry("%dx%d" % (self.EMOJI_COLS * C + 40, 420))
        top.transient(self.root)
        top.configure(bg="#FFFFFF")
        top.resizable(False, False)

        def on_close():
            self._emoji_top = None
            try:
                top.destroy()
            except Exception:
                pass
        top.protocol("WM_DELETE_WINDOW", on_close)

        cat_row = tk.Frame(top, bg="#FFFFFF")
        cat_row.pack(fill="x", padx=10, pady=(10, 4))
        cv = tk.Canvas(top, bg="#FFFFFF", highlightthickness=0, bd=0)
        cv.pack(fill="both", expand=True, padx=8)
        cat_btns = []
        state = {"grid": {}, "refs": []}

        def show_cat(i):
            for j, b in enumerate(cat_btns):
                try:
                    b.configure(style="Primary.TButton" if j == i else "TButton")
                except Exception:
                    pass
            cv.delete("all")
            state["grid"].clear()
            state["refs"] = []
            chars = list(EMOJI_GROUPS[i][1])
            # Canvas 一次绘制全部图标：96 个按钮换成 96 个 image item，
            # 打开面板从 1.2 秒降到几十毫秒
            for idx, ch in enumerate(chars):
                ph = self.emoji.get(ch, EMOJI_PANEL_PX)
                if ph is None:
                    continue
                r, c = divmod(idx, self.EMOJI_COLS)
                state["refs"].append(ph)
                state["grid"][(r, c)] = ch
                cv.create_image(8 + c * C + C // 2, 6 + r * C + C // 2, image=ph)
            rows = (len(chars) + self.EMOJI_COLS - 1) // self.EMOJI_COLS
            cv.configure(scrollregion=(0, 0, self.EMOJI_COLS * C + 16,
                                       rows * C + 12))

        def pick(event):
            c = (event.x - 8) // C
            r = (event.y - 6) // C
            ch = state["grid"].get((r, c))
            if ch:
                self._insert_emoji(ch)

        cv.bind("<Button-1>", pick)

        for i, (name, _) in enumerate(EMOJI_GROUPS):
            b = make_button(cat_row, name, lambda i=i: show_cat(i))
            b.pack(side="left", padx=2)
            cat_btns.append(b)

        hint = tk.Frame(top, bg="#FFFFFF")
        hint.pack(fill="x", padx=10, pady=(2, 8))
        tk.Label(hint, text="颜文字：", bg="#FFFFFF", fg="#6B6B6B",
                 font=(FONT, 9)).pack(side="left")
        for face in TEXT_FACES[:5]:
            f = tk.Label(hint, text=face, bg="#F2F5F9", fg="#1A1A1A",
                         font=(FONT, 9), padx=4, pady=1, cursor="hand2")
            f.pack(side="left", padx=2)
            f.bind("<Button-1>", lambda e, t=face: self._insert_emoji(t))
        show_cat(0)
        top.focus_set()

    def _warm_emoji(self, budget=8, delay=60):
        """后台分帧预热表情图片，避免首次打开面板时集中解码卡顿"""
        chars = list(dict.fromkeys(ch for _, g in EMOJI_GROUPS for ch in g))
        total = len(chars)
        pos = [0]

        def step():
            for ch in chars[pos[0]:pos[0] + budget]:
                self.emoji.get(ch, EMOJI_PANEL_PX)
                self.emoji.get(ch, EMOJI_CHAT_PX)
            pos[0] += budget
            if pos[0] < total:
                try:
                    self.root.after(delay, step)
                except Exception:
                    pass
        try:
            self.root.after(1200, step)      # 启动稳定后再预热，不抢首屏
        except Exception:
            pass

    def _insert_emoji(self, ch):
        try:
            pos = self.chat_entry.index("insert")
        except Exception:
            pos = len(self.chat_input.get())
        cur = self.chat_input.get()
        self.chat_input.set(cur[:pos] + ch + cur[pos:])
        try:
            self.chat_entry.icursor(pos + len(ch))
            self.chat_entry.focus_set()
        except Exception:
            pass

    def send_image_group(self):
        if not self.current_gid:
            self._alert("请先选中或新建一个群聊")
            return
        path = filedialog.askopenfilename(
            title="选择要发送的图片（GIF 动图 / PNG / JPG / BMP）",
            filetypes=[("图片", "*.gif;*.png;*.jpg;*.jpeg;*.bmp"),
                       ("所有文件", "*.*")])
        if not path:
            return
        try:
            r = self.node.send_file(path, gid=self.current_gid, image=True)
            self._alert("已向 %d 位在线群成员发送图片 %s（%s）"
                        % (len(r["targets"]), r["name"], human_size(r["size"])))
        except Exception as e:
            messagebox.showerror("发送失败", str(e))

    # ------------------------------------------------ 群置顶
    def _selected_gid(self):
        sel = self.group_list.curselection()
        if not sel or sel[0] >= len(self.group_ids):
            return ""
        return self.group_ids[sel[0]]

    def toggle_pin_group(self, gid=None):
        gid = gid or self._selected_gid() or self.current_gid
        if not gid:
            self._alert("请先选中一个群聊")
            return
        self.node.update_config({"toggle_pin_group": gid})
        self._groups_sig = ""
        self.refresh_chat()

    def _group_menu(self, event):
        idx = self.group_list.nearest(event.y)
        if idx < len(self.group_ids):
            self.group_list.selection_clear(0, "end")
            self.group_list.selection_set(idx)
            gid = self.group_ids[idx]
            g = next((x for x in self.group_data if x["id"] == gid), None)
            pinned = gid in self.node.cfg.get("pinned_groups", [])
            menu = tk.Menu(self.root, tearoff=0)
            menu.add_command(label="取消置顶" if pinned else "置顶该群",
                             command=lambda: self.toggle_pin_group(gid))
            if g and not g["dissolved"]:
                menu.add_command(label="群设置 / 管理",
                                 command=self.show_group_settings)
                menu.add_command(label="邀请成员进群",
                                 command=self.invite_members)
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()

    def _msg_menu(self, event, m):
        """消息右键：撤回（发送者本人或群管理员，不限时间）"""
        if m.get("kind") == "system":
            return
        menu = tk.Menu(self.root, tearoff=0)
        if m.get("recalled"):
            menu.add_command(label="该消息已撤回", state="disabled")
        elif self.node.can_recall(m):
            label = {"file": "撤回这个文件",
                     "image": "撤回这张图片"}.get(m.get("kind"), "撤回这条消息")
            menu.add_command(label=label, command=lambda: self._do_recall(m))
        else:
            menu.add_command(label="只有发送者或群管理员可撤回", state="disabled")
        if m.get("kind") in ("file", "image") and m.get("path") \
                and os.path.exists(m["path"]):
            menu.add_command(label="打开文件" if m.get("kind") == "file" else "打开图片",
                             command=lambda: self.open_path(m["path"]))
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
        elif m.get("kind") == "image":
            tip = ("确定撤回这张图片？\n"
                   "图片会同时从各成员已接收的下载目录中删除。")
        if not messagebox.askyesno("撤回", tip, parent=self.root):
            return
        try:
            self.node.recall_message(m.get("id"))
        except Exception as e:
            messagebox.showerror("撤回失败", str(e), parent=self.root)
            return
        self.rebuild_chat()

    def rebuild_chat(self, scroll=True):
        self._need_rebuild = False
        self._cancel_anims()
        t = self.chat_text
        t.configure(state="normal")
        t.delete("1.0", "end")
        self._rendered_mids = set()
        self._chat_images = []          # 旧图已从文本里删除，引用可以释放
        with self.node.chat_lock:
            hist = [dict(m) for m in self.node.chat_log
                    if m.get("gid") == self.current_gid]
        # 只渲染最近的消息，历史太长时整窗重建会明显变慢
        if len(hist) > CHAT_RENDER_LIMIT:
            cut = len(hist) - CHAT_RENDER_LIMIT
            hist = hist[cut:]
            t.insert("end", "（更早的 %d 条消息已折叠，可在聊天记录文件里查看）\n\n"
                     % cut, "muted")
        for m in hist:
            self._append_message(m, scroll=False)
        t.configure(state="disabled")
        if scroll:
            t.see("end")

    def refresh_chat(self):
        d = self.node.chat_since(self.chat_since)
        self.chat_since = d["seq"]
        mine = [g for g in d["groups"] if g["mine"]]
        # 置顶的群排到最前面
        pins = self.node.cfg.get("pinned_groups", [])
        mine = sorted(mine, key=lambda g: (0 if g["id"] in pins else 1,))
        self.group_data = mine
        # 只比较影响显示的字段，避免每轮把整个群对象（含成员表）序列化一遍
        sig = tuple((g["id"], g["name"], g["count"], g["pending_count"],
                     g["dissolved"], g["is_owner"], g["is_admin"],
                     g["can_manage"], g.get("join_mode"), g["id"] in pins)
                    for g in mine)
        if sig != self._groups_sig:
            self._groups_sig = sig
            self.group_ids = [g["id"] for g in mine]
            self.group_list.delete(0, "end")
            for g in mine:
                mark = ""
                if g["id"] in pins:
                    mark = " 📌"
                if g["dissolved"]:
                    mark += " ·已解散"
                elif g["is_owner"]:
                    mark += " ·群主"
                elif g["is_admin"]:
                    mark += " ·管理"
                if g["can_manage"] and g["pending_count"]:
                    mark += " ·待审%d" % g["pending_count"]
                self.group_list.insert("end", " %s%s (%d)"
                                       % ("📌 " if g["id"] in pins else "",
                                          g["name"], g["count"]) + mark)
            if self.current_gid in self.group_ids:
                self.group_list.selection_set(self.group_ids.index(self.current_gid))
            elif mine:
                self.current_gid = mine[0]["id"]
                self.group_list.selection_set(0)
                self.rebuild_chat()
            else:
                self.current_gid = ""
            self.refresh_members()
            # 置顶按钮文字跟随状态
            if self.current_gid in pins:
                try:
                    self.pin_btn.configure(text="取消置顶")
                except Exception:
                    pass
            else:
                try:
                    self.pin_btn.configure(text="置顶该群")
                except Exception:
                    pass
        for m in d["messages"]:
            self._append_message(m)
        if self._need_rebuild:
            self._need_rebuild = False
            self.rebuild_chat()
        # 传输列表：只有进度真的变了才重建，别每秒整表 delete+insert
        tsig = tuple((t["id"], t["sent"], t["size"], t["status"])
                     for t in d["transfers"][-40:])
        if tsig != self._transfer_sig:
            self._transfer_sig = tsig
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

    def refresh_members(self, force=False):
        cur = next((g for g in self.group_data if g["id"] == self.current_gid), None)
        if not cur:
            if self._member_list_sig:
                self._member_list_sig = ()
                self.member_list.delete(0, "end")
            return
        owner = cur["owner"]
        admins = set(cur["admins"])

        def display_name(pid):
            if pid == self.node.id:
                return "%s (我)" % self.node.name
            with self.node.peers_lock:
                p = self.node.peers.get(pid, {})
            return p.get("name") or pid[:8]

        rows = []
        for pid in cur["members"]:
            online = pid == self.node.id or self.node._peer_online(pid)
            role = " [群主]" if pid == owner else (" [管理员]" if pid in admins else "")
            rows.append((not online, pid != owner and pid not in admins,
                         display_name(pid), pid, online, role))
        rows.sort(key=lambda x: (x[0], x[1], x[2]))
        sig = tuple((name, online, role) for _, _, name, _, online, role in rows)
        if not force and sig == self._member_list_sig:
            return
        self._member_list_sig = sig
        self.member_list.delete(0, "end")
        for _, _, name, pid, online, role in rows:
            dot = "●" if online else "○"
            self.member_list.insert("end", " %s %s%s" % (dot, name, role))
            self.member_list.itemconfigure(
                "end", foreground=ONLINE_FG if online else OFFLINE_FG)

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
            online = pid == self.node.id or self.node._peer_online(pid)
            member_box.insert("end", " %s %s%s" % ("●" if online else "○",
                                                   name, role_tag))
            member_box.itemconfigure(
                "end", foreground=ONLINE_FG if online else OFFLINE_FG)

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
            if messagebox.askyesno(
                    "隐私表格",
                    "要把这张群内表格设为隐私表格吗？\n\n"
                    "指定行/列后，群成员互相看不到对方填写的内容，"
                    "只有你（发布者）能看到全部。",
                    parent=self.root):
                self._priv_rev = -1
                self.show_privacy_dialog()
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
        pid = self._selected_peer()
        if not pid:
            self._alert("请先在「在线成员」页选中一位联系人")
            return
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
        """主循环：250ms 轻量 tick，按当前页面分频做重活，避免整轮刷新卡住输入

        * 协作表格页：每 tick 轮询单元格（高频才不卡顿手感）
        * 在线成员页：2 秒一次（头像/状态变化不快）
        * 群聊页：1 秒一次（消息与传输进度）
        不在视野内的页面完全不刷新，切回来时立即补一次全量。
        """
        try:
            now = time.time()
            page = self.page
            rooms = sorted(self.node.rooms.keys())

            if page == "sheet":
                self.poll_room()

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
                elif page == "sheet":
                    self._update_sheet_choices()

            if "peers" in self.pages and now - self._last_peers >= 2.0:
                self._last_peers = now
                self.refresh_peers()

            if "chat" in self.pages and now - self._last_chat >= 1.0:
                self._last_chat = now
                self.refresh_chat()

            sc = self.node.scan_count
            scan = ("扫描中…" if self.node.scanning else
                    "已扫描 %d 次 · 发现 %d 个节点" % (sc, len(self.node.peers)))
            if scan != self._last_scan:
                self._last_scan = scan
                self.scan_info.set(scan)

            status = ("%s (%s)  |  界面 http://127.0.0.1:%s  |  房间 %d  |  群聊 %d  |  "
                      "在线成员 %d  |  文件端口 %s"
                      % (self.node.name, self.node.id[:8], self.node.http_port,
                         len(rooms), len(self.group_data),
                         len(self.node.active_users(self.room)) if self.room else 0,
                         self.node.file_port))
            if status != self._last_status:
                self._last_status = status
                self.status.set(status)
        except Exception:
            traceback.print_exc()
        self.root.after(250, self.refresh)

    PEER_SRC = {"multicast": "组播", "broadcast": "广播",
                "register": "定向探测", "": "—"}

    def _peer_rows(self, peers, tags_map, pins, filt):
        """按「置顶 / 各标签 / 未分组」组装成扁平行，供树增量刷新使用

        返回 [(iid, parent_iid, pid, values, tags), ...]，顺序即显示顺序。
        同一联系人挂多个标签时用 ``pid@序号`` 保证 iid 唯一（此前直接拿 pid
        当 iid，重复 insert 会抛 TclError，把整轮刷新打断）。
        """
        sections = []
        if filt in ("全部", "", "📌 置顶"):
            pinned = [p for p in peers if p["id"] in pins]
            if pinned:
                sections.append(("📌 置顶", pinned))
        if filt not in ("📌 置顶", "未分组"):
            tag_groups = {}
            for p in peers:
                for t in tags_map.get(p["id"], []):
                    tag_groups.setdefault(t, []).append(p)
            for t in sorted(tag_groups):
                if filt in ("全部", "", t):
                    sections.append(("🏷 " + t, tag_groups[t]))
        if filt in ("全部", "", "未分组"):
            untagged = [p for p in peers
                        if not tags_map.get(p["id"]) and p["id"] not in pins]
            if untagged:
                sections.append(("未分组", untagged))

        rows, seen = [], {}
        for gname, members in sections:
            giid = "grp:" + gname
            rows.append((giid, "", None,
                         ("%d 人" % len(members), "", "", "", "", ""),
                         ("group",)))
            for p in sorted(members, key=lambda x: (not x["online"], x["name"])):
                n = seen.get(p["id"], 0)
                seen[p["id"]] = n + 1
                online = p["online"]
                rows.append((
                    "%s@%d" % (p["id"], n), giid, p["id"],
                    (p["name"], p["note"], p["local_note"], p["ip"],
                     self.PEER_SRC.get(p["source"], p["source"]),
                     "● 在线" if online else "○ 离线"),
                    ("online" if online else "offline",)))
        return rows

    def refresh_peers(self, force=False):
        peers = self.node.peers_detail()
        tags_map = self.node.cfg.get("peer_tags", {})
        pins = set(self.node.cfg.get("pinned_peers", []))
        filt = self.tag_filter.get()
        sig = (tuple((p["id"], p["online"], p["name"], p["note"],
                      p["local_note"], p["ip"], p["source"]) for p in peers),
               filt, tuple(sorted(pins)),
               tuple(sorted((k, tuple(sorted(v))) for k, v in tags_map.items())))
        if not force and sig == self._peers_sig:
            return
        self._peers_sig = sig

        tree = self.peer_tree
        rows = self._peer_rows(peers, tags_map, pins, filt)
        tree_rows = [r[0] for r in rows if not r[0].startswith("grp:")]
        selected = [iid for iid in tree.selection() if not iid.startswith("grp:")]

        # 结构没变就只更新变化的行，避免每 2 秒整棵树 delete+insert 造成闪烁
        if rows:
            same_shape = (len(tree.get_children()) == len([r for r in rows if not r[1]])
                          and tree.exists(rows[0][0]))
        else:
            same_shape = not tree.get_children()
        if same_shape:
            for iid, parent, pid, values, tags in rows:
                if not tree.exists(iid):
                    same_shape = False
                    break
                cur = tree.item(iid, "values")
                if tuple(str(x) for x in cur) != tuple(str(x) for x in values):
                    tree.item(iid, values=values, tags=tags)
                elif pid:
                    tree.item(iid, tags=tags)

        if not same_shape:
            board = tree.yview()
            for item in tree.get_children():
                tree.delete(item)
            for iid, parent, pid, values, tags in rows:
                kw = {"text": "", "values": values, "tags": tags}
                if not parent:
                    kw["text"] = iid[4:]
                    kw["open"] = True
                else:
                    photo = self._photo(pid, self.node.peer_avatar_b64(pid),
                                        PEER_AVATAR_PX)
                    self._peer_photos[pid] = photo
                    if photo is not None:
                        kw["image"] = photo
                try:
                    tree.insert(parent, "end", iid=iid, **kw)
                except Exception:
                    pass
            try:
                tree.yview_moveto(board[0])
            except Exception:
                pass

        # 更新筛选下拉选项
        all_tags = sorted({t for tl in tags_map.values() for t in tl})
        values = ["全部", "📌 置顶"] + all_tags + ["未分组"]
        if list(self.tag_cb["values"]) != values:
            self.tag_cb["values"] = values
        if filt not in values:
            self.tag_filter.set("全部")

        if selected:
            keep = [i for i in selected if tree.exists(i)]
            if keep:
                tree.selection_set(keep)

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
        # v9：先确认这张表格要发布成公开表格还是隐私表格
        kind = self.ask_table_kind(os.path.basename(path))
        if not kind:
            return
        try:
            info = self.node.open_xlsx(path)      # 先建成草稿，未发布
            self._rooms_sig = ""
            self.room_var.set(info["room"])
            self.switch_room()
            self._pub_key = None
            self._update_pub_bar()
            if kind == "private":
                # 隐私表格：先把行列设置好，再在对话框里直接发布
                self._priv_rev = -1
                self.show_privacy_dialog(publish_after=True)
            else:
                self._alert(
                    "已作为公开表格打开: %s\n房间: %s · 读取 %d 个单元格\n"
                    "工作表: %s\n原有样式: %s\n\n"
                    "样式（字体/合并/边框/列宽行高/公式/其它工作表）以原文件为模板完整保留，"
                    "协作只同步单元格的值。\n\n"
                    "现在还是草稿：点「立即发布」后同事才看得到、才能填。"
                    % (os.path.basename(path), info["room"], len(info["cells"]),
                       " / ".join(info.get("sheets", [])),
                       info.get("style", "")))
                self.publish_current()
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
        pid = self._selected_peer()
        if not pid:
            self._alert("请先在列表中选中一位联系人")
            return
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
        pid = self._selected_peer()
        if not pid:
            self._alert("请先在列表中选中一位联系人")
            return
        with self.node.peers_lock:
            ip = (self.node.peers.get(pid) or {}).get("ip", "")
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
