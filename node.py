# -*- coding: utf-8 -*-
"""
LanExcel v2 —— 内网去中心化 Excel 协作节点
=====================================================
参考 飞秋(自动发现) + LocalSend(组播发现 / register 双向握手 / 分阶段扫描 / 收藏设备)。

* 每台电脑运行一个节点，无中心服务器
* 发现三件套：
    1) UDP 组播 224.0.0.167:53317（LocalSend 风格，可跨支持组播的路由）
    2) UDP 广播 5688（飞秋风格，兼容旧版节点）
    3) HTTP POST /api/lanexcel/v2/register 定向探测（组播被禁/跨网段时有效）
* 首次启动自动扫描：组播宣告 → 收藏设备 → 本网段 + 用户添加的网段全量探测
* 节点间 TCP 直连，单元格级 LWW(最后写入优先) 合并，防熵收敛
* 显示名称 / 个人备注 / 给同伴的本地备注 / 用户自定义网段 全部持久化

用法:
    python node.py --name 张三 [--no-browser]
    app.py 直接以进程内方式调用 Node.start_services()
依赖已内置在 lib/ 目录，绿色免安装。
"""

import argparse
import hashlib
import ipaddress
import json
import os
import socket
import struct
import sys
import threading
import time
import uuid
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote

APP_DIR = os.path.dirname(os.path.abspath(__file__))
# 依赖全部放在工程目录内：先按 Python 版本选 ABI 相关的目录（lib38 / lib312），
# 再退回纯 Python 的 lib/。这样 Python 3.8(Win7) 与 3.12(Win10/11) 都能直接运行。
_VER_LIB = os.path.join(APP_DIR, "lib%d%d" % sys.version_info[:2])
_LIB = os.path.join(APP_DIR, "lib")
# 逆序 insert(0)：最终优先级 lib38/lib312 > lib > APP_DIR
for _d in (APP_DIR, _LIB, _VER_LIB):
    if os.path.isdir(_d) and _d not in sys.path:
        sys.path.insert(0, _d)

from lazyimp import LazyModule

try:
    from importlib.util import find_spec
    if find_spec("openpyxl") is None:      # 只检查是否存在，不真正加载
        raise ImportError
    openpyxl = LazyModule("openpyxl")      # 惰性导入，启动省下 0.6 秒以上
except ImportError:
    print("缺少依赖 openpyxl，请先执行: pip install openpyxl")
    sys.exit(1)

import privacy as priv                                  # noqa: E402
from collab import CollabMixin, FILE_PORT_BASE, human_size  # noqa: E402
from avatars import avatar_b64, default_avatar               # noqa: E402
import sheetstyle                                          # noqa: E402

UDP_PORT = 5688            # 飞秋式广播发现端口（兼容旧版）
MC_GROUP = "224.0.0.167"   # LocalSend 默认组播组
MC_PORT = 53317            # LocalSend 默认端口
TCP_PORT_BASE = 8766       # 节点互联 TCP 端口
HTTP_PORT_BASE = MC_PORT   # 本地 Web / 发现服务端口（固定，便于跨网段定向探测）
DISCOVERY_PORTS = (53317, 53318, 53319, 53320)  # 同机多实例时依次尝试
PROTOCOL_VERSION = "2.0"
ANNOUNCE_INTERVAL = 3.0
SCAN_INTERVAL = 30.0
PEER_TIMEOUT = 45.0
ANTIENTROPY_INTERVAL = 5.0
AUTOSAVE_INTERVAL = 10.0
PRESENCE_TIMEOUT = 20.0
REGION_WIDTH = 5

PALETTE = ["#e74c3c", "#2980b9", "#27ae60", "#8e44ad", "#e67e22",
           "#16a085", "#d35400", "#2c3e50", "#c0392b", "#7f8c8d"]


def color_of(nid: str) -> str:
    return PALETTE[int(hashlib.md5(nid.encode()).hexdigest(), 16) % len(PALETTE)]


def now_ns() -> int:
    return time.time_ns()


def data_dir() -> str:
    """配置存放目录：exe 旁优先，不可写时退回 %APPDATA%/LanExcel"""
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else APP_DIR
    try:
        probe = os.path.join(base, ".lanexcel_write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        return base
    except Exception:
        alt = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "LanExcel")
        os.makedirs(alt, exist_ok=True)
        return alt


CONFIG_PATH = os.path.join(
    os.environ.get("LANEXCEL_CONFIG_DIR") or data_dir(),
    "lanexcel_config.json")
# 样式模板目录：保存原始 xlsx 的字节副本，协作只改值、样式永远以它为基底
TEMPLATE_DIR = os.path.join(os.path.dirname(CONFIG_PATH), "lanexcel_templates")
DEFAULT_CONFIG = {"name": "", "note": "", "fingerprint": "",
                  "subnets": [], "favorites": [], "peer_notes": {},
                  "avatar": "", "avatar_data": "", "download_dir": "",
                  "peer_tags": {}, "pinned_peers": [], "pinned_groups": []}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    if not cfg.get("fingerprint"):
        cfg["fingerprint"] = uuid.uuid4().hex[:16]   # 持久设备指纹（LocalSend 风格）
    if not cfg.get("name"):
        cfg["name"] = socket.gethostname()
    for k in ("subnets", "favorites"):
        if not isinstance(cfg.get(k), list):
            cfg[k] = []
    if not isinstance(cfg.get("peer_notes"), dict):
        cfg["peer_notes"] = {}
    if not isinstance(cfg.get("peer_tags"), dict):
        cfg["peer_tags"] = {}
    for k in ("pinned_peers", "pinned_groups"):
        if not isinstance(cfg.get(k), list):
            cfg[k] = []
    # 标签规范化：{pid: [标签...]}，标签为去空格的非空字符串
    tags = {}
    for pid, tl in cfg["peer_tags"].items():
        if isinstance(tl, list):
            ts = [str(t).strip() for t in tl if str(t).strip()]
            if ts:
                tags[str(pid)] = ts
    cfg["peer_tags"] = tags
    for k in ("avatar", "avatar_data", "download_dir"):
        if not isinstance(cfg.get(k), str):
            cfg[k] = ""
    save_config(cfg)
    return cfg


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def local_ip():
    """获取本机在内网中的主 IP（不需要真的联网）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"
    finally:
        s.close()


def subnet_targets(cidr, limit=512):
    """把用户输入的网段/单 IP/范围解析成待探测 IP 列表"""
    cidr = (cidr or "").strip()
    if not cidr:
        return []
    if "-" in cidr:  # 支持 192.168.2.10-192.168.2.30
        try:
            a, b = cidr.split("-", 1)
            start = int(ipaddress.IPv4Address(a.strip()))
            end = int(ipaddress.IPv4Address(b.strip()))
            return [str(ipaddress.IPv4Address(i)) for i in range(start, min(end, start + limit) + 1)]
        except ValueError:
            return []
    try:
        net = ipaddress.ip_network(cidr, strict=False)
        if net.num_addresses <= 2:
            return [str(net.network_address)]
        return [str(ip) for _, ip in zip(range(limit), net.hosts())]
    except ValueError:
        return []


# ---------------------------------------------------------------- 房间(共享表格)
class Room:
    def __init__(self, name: str):
        self.name = name
        self.sheet_name = "Sheet1"
        self.rows = 50
        self.cols = 20
        self.cells = {}          # {(r,c): [value, ts_ns, who]}
        self.file_path = None
        self.wb = None
        self.dirty = False
        self.lock = threading.RLock()
        self.seq = 0
        self.edit_log = []
        # ---- v4 样式保真：模板 + 样式元数据 + 被协作改过的坐标 ----
        self.template_path = None       # 原始 xlsx 的只读副本
        self.template_hash = ""         # 模板内容指纹，跨机同步用
        self.source_path = None         # 用户的原始文件路径（保存目标）
        self.sheets = []                # 工作簿内全部工作表名
        self.style = None               # 本房间对应工作表的样式元数据
        self.style_version = 0          # 样式变更计数，UI 据此重绘
        self.edited = set()             # {(r, c)} 1-based，仅这些格需要写回
        self.styled = False
        # ---- v5 群内表格隔离：绑定群后只有群成员可见可编辑 ----
        self.group_id = None            # 所属群；None = 公开房间（原有协作方式）
        self.group_members = set()      # 成员快照，群资料未到时也能判定
        self.owner_id = None            # 发布者（共享这个表格的人）
        # ---- v8 隐私表格：指定行/列的内容只有填写者本人与发布者可见 ----
        self.privacy = {"on": 0, "rows": [], "cols": [], "rev": 0}
        # on=0 表示公开表格（原有逻辑完全不变）；rev 用于多端合并
        self.masked = set()             # 本机只拿到占位值的坐标（真值不在本机）

    # ------------------------------------------------------------ 隐私规则
    def privacy_on(self):
        return priv.is_on(self.privacy)

    def cell_private(self, r, c):
        return priv.is_private(self.privacy, r, c)

    def cell_hidden(self, r, c, viewer):
        """该单元格对 viewer 是否显示为星号（读不到真实内容）"""
        if viewer == self.owner_id:
            return False                      # 发布者看全部
        if not self.cell_private(r, c):
            return False                      # 非隐私区域，原有逻辑
        cell = self.cells.get((r, c))
        if cell is None:
            return False                      # 空格子没有可遮掩的内容
        if cell[2] == viewer:
            return False                      # 自己填的，自己看得见
        return bool(cell[0]) or (r, c) in self.masked

    def cell_editable(self, r, c, pid):
        """谁可以写这个单元格：隐私区只有本人（或发布者）能写，防止覆盖他人"""
        if not self.cell_private(r, c):
            return True
        if pid and pid == self.owner_id:
            return True
        cell = self.cells.get((r, c))
        return cell is None or cell[2] == pid

    def digest(self):
        with self.lock:
            if not self.cells:
                return (0, "")
            if self.privacy_on():
                # 隐私表格的摘要不能包含值：各端对同一格持有的内容不同
                #（有的只有占位），只比 坐标+时间戳+填写者 才能一致收敛
                raw = "\n".join(
                    f"{r},{c}:{ts}:{who}"
                    for (r, c), (v, ts, who) in sorted(self.cells.items())
                )
                raw += "|p%d" % int((self.privacy or {}).get("rev", 0))
            else:
                raw = "\n".join(
                    f"{r},{c}:{repr(v)}:{ts}:{who}"
                    for (r, c), (v, ts, who) in sorted(self.cells.items())
                )
            return (len(self.cells), hashlib.md5(raw.encode("utf-8")).hexdigest())

    def rooms_map(self):
        count, h = self.digest()
        return {"name": self.name, "sheet": self.sheet_name,
                "sheets": list(self.sheets),
                "rows": self.rows, "cols": self.cols,
                "count": count, "hash": h,
                "tmpl": self.template_hash, "styled": self.styled,
                "group": self.group_id or "",
                "owner": self.owner_id or "",
                "priv": dict(self.privacy or {})}

    def snapshot(self, with_style=False, viewer=None, mask_only=False):
        """viewer 非空时按他的身份裁剪隐私单元格（返回占位，不含真值）"""
        with self.lock:
            out = {"name": self.name, "sheet": self.sheet_name,
                   "sheets": list(self.sheets),
                   "rows": self.rows, "cols": self.cols,
                   "cells": self.cells_view(viewer, mask_only=mask_only),
                   "tmpl": self.template_hash,
                   "style_version": self.style_version,
                   "styled": self.styled,
                   "group": self.group_id or "",
                   "owner": self.owner_id or "",
                   "priv": dict(self.privacy or {})}
            if with_style:
                out["style"] = self.style
            return out

    def cells_view(self, viewer, keys=None, mask_only=False):
        """把单元格字典转成查看者视角。

        mask_only=True 时只返回「需要打码」的 key 列表（用于增量编辑）。
        隐私单元格统一换成 ["", ts, who, 1]，第 4 位是遮掩标记。
        """
        out = {}
        with self.lock:
            items = self.cells.items() if keys is None else \
                [(self._key_rc(k), self.cells.get(self._key_rc(k)))
                 for k in keys]
            for (r, c), cell in items:
                if cell is None:
                    continue
                k = "%d,%d" % (r, c)
                if self.cell_hidden(r, c, viewer):
                    if not mask_only:
                        out[k] = ["", cell[1], cell[2], 1]
                elif not mask_only:
                    out[k] = [cell[0], cell[1], cell[2]]
        return out

    @staticmethod
    def _key_rc(key):
        if isinstance(key, tuple):
            return key
        try:
            r, c = str(key).split(",")
            return int(r), int(c)
        except ValueError:
            return (0, 0)


class PeerConn:
    def __init__(self, sock, addr, node):
        self.sock = sock
        self.addr = addr
        self.node = node
        self.peer_id = None
        self.peer_name = "?"
        self.send_lock = threading.Lock()
        self.alive = True

    def send(self, obj):
        try:
            data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
            with self.send_lock:
                self.sock.sendall(data)
        except OSError:
            self.close()

    def close(self):
        if self.alive:
            self.alive = False
            try:
                self.sock.close()
            except OSError:
                pass
            self.node.drop_conn(self)


class Node(CollabMixin):
    def __init__(self, name=None, manual_peers=None):
        self.cfg = load_config()
        if name:
            self.cfg["name"] = name
            save_config(self.cfg)
        self.id = self.cfg["fingerprint"]
        self.name = self.cfg["name"]
        self.note = self.cfg.get("note", "")
        self.color = color_of(self.id)
        self.manual_peers = manual_peers or []

        self.rooms = {}
        self.rooms_lock = threading.RLock()
        self._tmpl_lock = threading.Lock()
        self._tmpl_fetching = set()      # 正在后台拉取的 (房间, 模板hash)
        self.peers = {}          # id -> {id,name,note,ip,tcp,http,rooms,last_seen,source}
        self.peers_lock = threading.RLock()
        self.conns = []
        self.conns_lock = threading.RLock()
        self.presence = {}
        self.presence_lock = threading.RLock()
        self.my_presence = {"room": None, "cell": None, "ts": 0}

        self.tcp_port = None
        self.http_port = None
        self.running = True
        self.scanning = False
        self.scan_count = 0
        self.last_scan = 0
        self.scan_lock = threading.Lock()
        self.httpd = None
        self.no_browser = False
        self.init_collab(CONFIG_PATH)      # v3：群聊 / 头像 / 文件传输

    def persist_config(self):
        save_config(self.cfg)

    # ---------------------------------------------------------- 设备信息 / 配置
    def device_info(self, announce=True):
        """LocalSend 风格设备信息（含扩展字段 note / http_port / avatar / file_port）"""
        return {"alias": self.name, "note": self.note,
                "version": PROTOCOL_VERSION, "deviceModel": "Windows",
                "deviceType": "desktop", "fingerprint": self.id,
                "port": self.tcp_port, "http_port": self.http_port,
                "file_port": self.file_port, "avatar": self.avatar,
                "protocol": "http", "download": True, "announce": announce,
                # 兼容 v1 广播字段
                "id": self.id, "name": self.name, "tcp": self.tcp_port, "t": "ann"}

    def config_public(self):
        return {"name": self.name, "note": self.note,
                "fingerprint": self.id, "avatar": self.avatar,
                "avatar_data": self.avatar_data,
                "download_dir": self.download_dir(),
                "subnets": list(self.cfg.get("subnets", [])),
                "favorites": list(self.cfg.get("favorites", [])),
                "peer_notes": dict(self.cfg.get("peer_notes", {})),
                "peer_tags": dict(self.cfg.get("peer_tags", {})),
                "pinned_peers": list(self.cfg.get("pinned_peers", [])),
                "pinned_groups": list(self.cfg.get("pinned_groups", [])),
                "config_path": CONFIG_PATH}

    def update_config(self, body):
        """修改显示名称 / 个人备注 / 网段 / 收藏设备 / 给同伴的本地备注
        / 联系人标签 / 置顶（联系人 / 群聊）"""
        if "name" in body and str(body["name"]).strip():
            self.name = str(body["name"]).strip()
            self.cfg["name"] = self.name
        if "note" in body:
            self.note = str(body["note"])
            self.cfg["note"] = self.note
        for key in ("subnets", "favorites"):
            if key in body and isinstance(body[key], list):
                self.cfg[key] = [str(x).strip() for x in body[key] if str(x).strip()]
        if "peer_notes" in body and isinstance(body["peer_notes"], dict):
            self.cfg["peer_notes"] = {str(k): str(v) for k, v in body["peer_notes"].items()}
        if "peer_tags" in body and isinstance(body["peer_tags"], dict):
            tags = {}
            for pid, tl in body["peer_tags"].items():
                if isinstance(tl, list):
                    ts = [str(t).strip() for t in tl if str(t).strip()]
                    if ts:
                        tags[str(pid)] = ts
            self.cfg["peer_tags"] = tags
        if "toggle_pin_peer" in body:
            pid = str(body["toggle_pin_peer"])
            pins = list(self.cfg.get("pinned_peers", []))
            if pid in pins:
                pins.remove(pid)
            elif pid:
                pins.append(pid)
            self.cfg["pinned_peers"] = pins
        if "toggle_pin_group" in body:
            gid = str(body["toggle_pin_group"])
            pins = list(self.cfg.get("pinned_groups", []))
            if gid in pins:
                pins.remove(gid)
            elif gid:
                pins.append(gid)
            self.cfg["pinned_groups"] = pins
        if isinstance(body.get("pinned_peers"), list):
            self.cfg["pinned_peers"] = [str(x) for x in body["pinned_peers"] if x]
        if isinstance(body.get("pinned_groups"), list):
            self.cfg["pinned_groups"] = [str(x) for x in body["pinned_groups"] if x]
        if "add_subnet" in body:
            v = str(body["add_subnet"]).strip()
            if v and v not in self.cfg["subnets"]:
                self.cfg["subnets"].append(v)
        if "remove_subnet" in body:
            v = str(body["remove_subnet"]).strip()
            self.cfg["subnets"] = [x for x in self.cfg["subnets"] if x != v]
        if "add_favorite" in body:
            v = str(body["add_favorite"]).strip()
            if v and v not in self.cfg["favorites"]:
                self.cfg["favorites"].append(v)
        if "remove_favorite" in body:
            v = str(body["remove_favorite"]).strip()
            self.cfg["favorites"] = [x for x in self.cfg["favorites"] if x != v]
        if "download_dir" in body:
            self.cfg["download_dir"] = str(body["download_dir"])
        save_config(self.cfg)
        self.announce_now()            # 立即让同伴看到新名称/备注
        return self.config_public()

    def peers_detail(self):
        now = time.time()
        tags_map = self.cfg.get("peer_tags", {})
        pins = set(self.cfg.get("pinned_peers", []))
        with self.peers_lock:
            items = []
            for pid, p in self.peers.items():
                items.append({"id": pid, "name": p.get("name", "?"),
                              "note": p.get("note", ""),
                              "local_note": self.cfg.get("peer_notes", {}).get(pid, ""),
                              "ip": p.get("ip", ""),
                              "tcp": p.get("tcp"), "http": p.get("http"),
                              "file_port": p.get("file_port"),
                              "avatar": p.get("avatar", ""),
                              "source": p.get("source", ""),
                              "online": now - p.get("last_seen", 0) < PEER_TIMEOUT,
                              "tags": list(tags_map.get(pid, [])),
                              "pinned": pid in pins,
                              "color": color_of(pid)})
            return sorted(items, key=lambda x: (not x["pinned"], x["name"]))

    def register_peer(self, info, ip):
        """LocalSend 式双向发现：收到任何设备信息都登记为同伴"""
        pid = info.get("fingerprint") or info.get("id")
        if not pid or pid == self.id:
            return None
        with self.peers_lock:
            p = self.peers.get(pid)
            if p is None:
                p = {"id": pid, "rooms": set(), "source": info.get("_source", "")}
                self.peers[pid] = p
            p.update({"name": info.get("alias") or info.get("name") or "?",
                      "note": info.get("note", ""),
                      "ip": ip, "tcp": info.get("port") or info.get("tcp"),
                      "http": info.get("http_port") or info.get("http"),
                      "avatar": info.get("avatar") or p.get("avatar", ""),
                      "last_seen": time.time(),
                      "source": info.get("_source", p.get("source", ""))})
            # 老版本广播报文可能没有这些字段，不能用 None 覆盖已发现的有效端口
            if info.get("file_port"):
                p["file_port"] = info["file_port"]
        if info.get("avatar"):
            self._cache_avatar(pid, info.get("avatar"))
        return p

    # ---------------------------------------------------------- 房间操作
    def get_room(self, name, create=False):
        with self.rooms_lock:
            if name in self.rooms:
                return self.rooms[name]
            if create:
                r = Room(name)
                self.rooms[name] = r
                return r
            return None

    @staticmethod
    def _cell_parts(cell):
        """兼容 3 元 / 4 元单元格：[值, 时间戳, 填写者, 遮掩标记?]"""
        try:
            v, ts, who = cell[0], cell[1], cell[2]
        except (TypeError, IndexError):
            return "", 0, "", False
        hidden = len(cell) > 3 and bool(cell[3])
        return v, ts, who, hidden

    def apply_edit(self, room_name, cells, seen=None, local=False, from_peer=None):
        room = self.get_room(room_name)
        if room is None:
            if local:
                raise ValueError("协作表格不存在或你没有访问权限: " + str(room_name))
            return
        # 群内房间：非成员一律拒绝写入（含本机越权调用）
        if local:
            if not self.room_visible_here(room):
                raise ValueError("该协作表格仅群成员可编辑，你不在该群聊中")
        elif from_peer and not self.room_visible_to(room, from_peer):
            return
        writer = self.id if local else from_peer
        changed = {}
        blocked = []
        with room.lock:
            for key, cell in cells.items():
                v, ts, who, hidden = self._cell_parts(cell)
                try:
                    r, c = key.split(",")
                    r, c = int(r), int(c)
                except (AttributeError, ValueError):
                    continue
                # 隐私区：别人的格子不许覆盖（前端已锁，这里兜住越权请求）
                if not room.cell_editable(r, c, who if not local else self.id):
                    blocked.append(key)
                    continue
                cur = room.cells.get((r, c))
                placeholder = (r, c) in room.masked and not hidden
                if cur is None or placeholder or (ts, who) > (cur[1], cur[2]):
                    if hidden:
                        room.cells[(r, c)] = ["", ts, who]
                        room.masked.add((r, c))
                    else:
                        room.cells[(r, c)] = [v, ts, who]
                        room.masked.discard((r, c))
                    room.edited.add((r, c))
                    room.rows = max(room.rows, r + 1)
                    room.cols = max(room.cols, c + 1)
                    changed[key] = room.cells[(r, c)]
        if changed:
            room.dirty = True
            with room.lock:
                room.seq += 1
                room.edit_log.append({"seq": room.seq, "cells": changed})
                if len(room.edit_log) > 3000:
                    room.edit_log = room.edit_log[-3000:]
            if local:
                self.broadcast_edit(room_name, changed)
            else:
                self.forward(room_name, changed, seen or [])
        if blocked and local:
            raise ValueError("有 %d 个隐私单元格已被他人填写，不能覆盖" % len(blocked))
        return blocked

    def merge_room(self, meta, cells, mask=()):
        room = self.get_room(meta["name"], create=True)
        mask = set(mask or ())
        changed = {}
        force_real = False          # 隐私刚好关闭：占位格必须接收真值
        with room.lock:
            if not room.cells and not room.file_path:
                room.sheet_name = meta.get("sheet", room.sheet_name)
                room.rows = max(room.rows, meta.get("rows", 0))
                room.cols = max(room.cols, meta.get("cols", 0))
            if meta.get("sheets"):
                room.sheets = list(meta["sheets"])
            if meta.get("group"):
                room.group_id = meta["group"]
            if meta.get("owner") and not room.owner_id:
                room.owner_id = meta["owner"]
            # 隐私设置：以 rev 大的为准，避免旧广播把新设置顶回去
            p = priv.norm(meta.get("priv"))
            if p is not None:
                cur = room.privacy or {}
                if p["rev"] >= int(cur.get("rev", 0)):
                    was_on = bool(cur.get("on"))
                    room.privacy = p
                    if was_on and not p["on"]:
                        # 隐私关闭：本地占位要全部换成同伴发来的真值
                        for _rc in list(room.masked):
                            room.masked.discard(_rc)
                        force_real = True
            if meta.get("tmpl"):
                room.template_hash = meta["tmpl"]
            if meta.get("style"):
                room.style = meta["style"]
                room.styled = True
                room.style_version += 1
                room.rows = max(room.rows, meta["style"].get("rows", 0) + 2)
                room.cols = max(room.cols, meta["style"].get("cols", 0) + 2)
            for key, cell in cells.items():
                v, ts, who, hidden = self._cell_parts(cell)
                try:
                    r, c = key.split(",")
                    r, c = int(r), int(c)
                except (AttributeError, ValueError):
                    continue
                hidden = hidden or key in mask
                cur = room.cells.get((r, c))
                # 本地只是占位（真值在别人机器上）时，任何一份真值都该收下
                placeholder = ((r, c) in room.masked or force_real) and not hidden
                if cur is None or placeholder or (ts, who) > (cur[1], cur[2]):
                    if hidden:
                        room.cells[(r, c)] = ["", ts, who]
                        room.masked.add((r, c))
                    else:
                        room.cells[(r, c)] = [v, ts, who]
                        room.masked.discard((r, c))
                    room.edited.add((r, c))
                    room.rows = max(room.rows, r + 1)
                    room.cols = max(room.cols, c + 1)
                    changed[key] = room.cells[(r, c)]
            # 隐私刚生效时，把本机不该看见的真值清成占位：数据只在本人与发布者机器上
            changed.update(self._scrub_private(room))
            if changed:
                room.dirty = True
        if changed:
            with room.lock:
                room.seq += 1
                room.edit_log.append({"seq": room.seq, "cells": changed})

    def _scrub_private(self, room):
        """把本机不该看见的隐私真值清成占位（隐私开启时调用）"""
        changed = {}
        if not room.privacy_on():
            return changed
        with room.lock:
            for (r, c), cell in list(room.cells.items()):
                if (r, c) in room.masked or not cell[0]:
                    continue
                if room.cell_hidden(r, c, self.id):
                    room.cells[(r, c)] = ["", cell[1], cell[2]]
                    room.masked.add((r, c))
                    changed["%d,%d" % (r, c)] = room.cells[(r, c)]
        return changed

    def set_privacy(self, room_name, rows=None, cols=None, on=True):
        """发布者设置隐私区域；rows / cols 是 1-based 的行号 / 列号列表"""
        room = self.get_room(room_name)
        if room is None:
            raise ValueError("协作表格不存在: " + str(room_name))
        if not self.room_visible_here(room):
            raise ValueError("你没有这张表格的访问权限")
        if room.owner_id and room.owner_id != self.id:
            raise ValueError("只有发布者才能设置隐私区域")
        with room.lock:
            if not room.owner_id:
                room.owner_id = self.id
            rev = int((room.privacy or {}).get("rev", 0)) + 1
            p = priv.norm({"on": 1 if on else 0, "rows": rows or [],
                           "cols": cols or [], "rev": rev})
            room.privacy = p
            if not p["on"]:
                room.masked.clear()
        changed = self._scrub_private(room)
        if changed:
            with room.lock:
                room.seq += 1
                room.edit_log.append({"seq": room.seq, "cells": changed})
        self.broadcast_rooms()
        return {"room": room_name, "priv": dict(room.privacy or {}),
                "owner": room.owner_id or "", "desc": priv.describe(room.privacy)}

    def room_privacy(self, room_name):
        room = self.get_room(room_name)
        if room is None:
            return {}
        with room.lock:
            return {"room": room_name,
                    "priv": dict(room.privacy or {}),
                    "owner": room.owner_id or "",
                    "is_owner": (room.owner_id or self.id) == self.id,
                    "desc": priv.describe(room.privacy)}

    # ---------------------------------------------------------- TCP 消息
    def _rooms_msg_for(self, pid):
        """按收件人裁剪房间清单：群内房间只发给群成员"""
        with self.rooms_lock:
            rooms = [r.rooms_map() for r in self.rooms.values()
                     if self.room_visible_to(r, pid)]
        return {"t": "rooms", "rooms": rooms,
                "name": self.name, "id": self.id, "http": self.http_port}

    def _rooms_msg(self):
        """本机全量房间清单（仅用于本地展示）"""
        with self.rooms_lock:
            rooms = [r.rooms_map() for r in self.rooms.values()]
        return {"t": "rooms", "rooms": rooms,
                "name": self.name, "id": self.id, "http": self.http_port}

    def broadcast_rooms(self):
        for conn in list(self.conns):
            if conn.peer_id:
                conn.send(self._rooms_msg_for(conn.peer_id))

    def broadcast(self, obj, exclude_ids=()):
        for conn in list(self.conns):
            if conn.peer_id and conn.peer_id not in exclude_ids:
                conn.send(obj)

    def broadcast_edit(self, room_name, cells):
        """把本地编辑发给同伴：隐私表格按收件人身份逐条裁剪"""
        room = self.get_room(room_name)
        for conn in list(self.conns):
            if not conn.peer_id:
                continue
            if room is not None and not self.room_visible_to(room, conn.peer_id):
                continue
            msg = {"t": "edit", "room": room_name, "cells": cells,
                   "seen": [self.id]}
            if room is not None and room.privacy_on():
                msg["cells"] = room.cells_view(conn.peer_id, keys=list(cells))
            conn.send(msg)

    def forward(self, room, cells, seen):
        seen = set(seen) | {self.id}
        r = self.get_room(room)
        for conn in list(self.conns):
            if conn.peer_id and conn.peer_id not in seen:
                if r is not None and not self.room_visible_to(r, conn.peer_id):
                    continue          # 群内房间不转发给非成员
                payload = cells
                if r is not None and r.privacy_on():
                    payload = r.cells_view(conn.peer_id, keys=list(cells))
                conn.send({"t": "edit", "room": room, "cells": payload,
                           "seen": sorted(seen)})

    def handle_msg(self, conn, msg):
        # 任意 TCP 消息都刷新同伴存活时间，跨网段同伴不会因扫描间隔被误判离线
        if conn.peer_id:
            with self.peers_lock:
                p = self.peers.get(conn.peer_id)
                if p:
                    p["last_seen"] = time.time()
        t = msg.get("t")
        if t in ("group_update", "group_sync", "chat", "avatar", "chat_recall"):
            self.handle_collab_msg(conn, msg)
            return
        if t == "hello":
            # 先认人再握手：补发群聊历史与房间清单都要按对方身份裁剪
            conn.peer_id = msg.get("id")
            conn.peer_name = msg.get("name", "?")
            self.on_peer_hello(conn, msg)     # 交换头像 + 补发群聊历史
            conn.send(self._rooms_msg_for(conn.peer_id))
        elif t == "rooms":
            peer_ip = conn.addr[0] if getattr(conn, "addr", None) else ""
            http_port = msg.get("http")
            if not http_port:
                with self.peers_lock:
                    p0 = self.peers.get(conn.peer_id)
                    http_port = (p0 or {}).get("http")
            for rm in msg.get("rooms", []):
                gid = rm.get("group") or ""
                if gid:
                    with self.groups_lock:
                        g = self.groups.get(gid)
                    if not (g and self.id in g["members"] and not g["dissolved"]):
                        continue      # 非群成员：不加入、不拉数据、不可见
                mine = self.get_room(rm["name"])
                if mine is not None:
                    cnt, h = mine.digest()
                    if (cnt, h) != (rm["count"], rm["hash"]):
                        conn.send({"t": "state_req", "room": rm["name"]})
                elif rm.get("count", 0) > 0:
                    self.merge_room(rm, {})   # 自动加入同伴正在共享的房间
                    conn.send({"t": "state_req", "room": rm["name"]})
                if rm.get("tmpl"):
                    self._ensure_template(rm["name"], rm["tmpl"], peer_ip,
                                          http_port, rm.get("sheet"),
                                          rm.get("sheets"))
            with self.peers_lock:
                p = self.peers.get(conn.peer_id)
                if p:
                    p["rooms"] = {r["name"] for r in msg.get("rooms", [])
                                  if not r.get("group")
                                  or self.room_visible_to(
                                      self.get_room(r["name"]), self.id)}
        elif t == "state_req":
            room = self.get_room(msg.get("room", ""))
            if room is None:
                return
            if not self.room_visible_to(room, conn.peer_id):
                conn.send({"t": "state_denied", "room": room.name})
                return
            snap = room.snapshot(with_style=False, viewer=conn.peer_id)
            snap["t"] = "state"
            snap["http"] = self.http_port
            conn.send(snap)
        elif t == "state_denied":
            # 对方拒绝：本地若有该房间的残留，直接丢弃，避免越权缓存
            name = msg.get("room", "")
            room = self.get_room(name)
            if room is not None and not self.room_visible_here(room):
                with self.rooms_lock:
                    self.rooms.pop(name, None)
        elif t == "state":
            name = msg.get("name", "")
            exist = self.get_room(name)
            if exist is not None and not self.room_visible_to(exist, conn.peer_id):
                return                    # 拒绝非成员推来的数据
            gid = msg.get("group") or ""
            if gid and not exist:
                with self.groups_lock:
                    g = self.groups.get(gid)
                if not (g and self.id in g["members"]):
                    return
            self.merge_room(msg, msg.get("cells", {}))
            if msg.get("tmpl"):
                peer_ip = conn.addr[0] if getattr(conn, "addr", None) else ""
                http_port = msg.get("http")
                if not http_port:
                    with self.peers_lock:
                        p0 = self.peers.get(conn.peer_id)
                        http_port = (p0 or {}).get("http")
                self._ensure_template(msg.get("name", ""), msg["tmpl"],
                                      peer_ip, http_port, msg.get("sheet"),
                                      msg.get("sheets"))
        elif t == "edit":
            self.apply_edit(msg["room"], msg.get("cells", {}),
                            seen=msg.get("seen", []), from_peer=conn.peer_id)
        elif t == "presence":
            pid = msg.get("id")
            with self.presence_lock:
                self.presence[pid] = {
                    "name": msg.get("name"), "color": msg.get("color"),
                    "room": msg.get("room"), "cell": msg.get("cell"),
                    "note": msg.get("note", ""), "ts": time.time()}

    def conn_reader(self, conn):
        f = conn.sock.makefile("r", encoding="utf-8")
        try:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                self.handle_msg(conn, msg)
        except OSError:
            pass
        finally:
            conn.close()

    def drop_conn(self, conn):
        with self.conns_lock:
            if conn in self.conns:
                self.conns.remove(conn)

    def send_hello(self, conn):
        conn.send({"t": "hello", "id": self.id, "name": self.name,
                   "note": self.note, "avatar": self.avatar,
                   "data": avatar_b64(self.avatar, self.avatar_data)})

    # ---------------------------------------------------------- 发现：组播 / 广播 / register
    def announce_now(self):
        info = self.device_info(True)
        raw = json.dumps(info, ensure_ascii=False).encode("utf-8")
        legacy = json.dumps({"t": "ann", "id": self.id, "name": self.name,
                             "note": self.note, "tcp": self.tcp_port,
                             "http": self.http_port, "file_port": self.file_port,
                             "avatar": self.avatar},
                            ensure_ascii=False).encode("utf-8")
        # 1) LocalSend 式组播
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            s.sendto(raw, (MC_GROUP, MC_PORT))
            s.close()
        except OSError:
            pass
        # 2) 飞秋式广播
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(legacy, ("<broadcast>", UDP_PORT))
            s.close()
        except OSError:
            pass

    def udp_announcer(self):
        while self.running:
            self.announce_now()
            time.sleep(ANNOUNCE_INTERVAL)
        # 退出时 goodbye
        bye = json.dumps({"t": "bye", "id": self.id}).encode("utf-8")
        for addr, sock_type in ((UDP_PORT, "b"), (MC_PORT, "m")):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                if sock_type == "b":
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                    s.sendto(bye, ("<broadcast>", addr))
                else:
                    s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                    s.sendto(bye, (MC_GROUP, addr))
                s.close()
            except OSError:
                pass

    def _udp_receive(self, port, multicast=False):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        s.bind(("0.0.0.0", port))
        if multicast:
            try:
                mreq = socket.inet_aton(MC_GROUP) + socket.inet_aton("0.0.0.0")
                s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            except OSError:
                pass
        while self.running:
            try:
                data, addr = s.recvfrom(8192)
            except OSError:
                break
            try:
                msg = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            if msg.get("t") == "bye" and msg.get("id") != self.id:
                with self.peers_lock:
                    self.peers.pop(msg.get("id"), None)
                continue
            pid = msg.get("fingerprint") or msg.get("id")
            if not pid or pid == self.id:
                continue
            info = dict(msg)
            info["_source"] = "multicast" if multicast else "broadcast"
            self.register_peer(info, addr[0])
            # 组播 announce=true 时，按 LocalSend 协议用 HTTP 向发起者回注册（双向发现）
            if multicast and msg.get("announce"):
                self.probe_ip(addr[0], ports=(msg.get("http_port") or MC_PORT,))

    def udp_listener(self):
        self._udp_receive(UDP_PORT, multicast=False)

    def multicast_listener(self):
        self._udp_receive(MC_PORT, multicast=True)

    def probe_ip(self, ip, ports=None):
        """LocalSend legacy 模式：向候选 IP POST /register，双向交换设备信息

        使用禁用代理的 opener —— 某些机器配置了全局 HTTP 代理，
        会让发往 192.168.x.x / 10.x.x.x 的探测被代理劫持而失败。
        """
        body = json.dumps(self.device_info(False), ensure_ascii=False).encode("utf-8")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for port in ports or DISCOVERY_PORTS:
            try:
                url = f"http://{ip}:{port}/api/lanexcel/v2/register"
                req = urllib.request.Request(
                    url, data=body, headers={"Content-Type": "application/json"})
                with opener.open(req, timeout=0.8) as resp:
                    info = json.load(resp)
                info["_source"] = "register"
                self.register_peer(info, ip)
                return True
            except Exception:
                continue
        return False

    def scan_once(self, include_subnets=True):
        """分阶段扫描：收藏设备优先，随后本网段 + 用户添加的网段"""
        with self.scan_lock:
            if self.scanning:
                return 0
            self.scanning = True
        targets = set()
        for fav in self.cfg.get("favorites", []):
            targets.update(subnet_targets(fav))
        if include_subnets:
            lip = local_ip()
            if lip and not lip.startswith("127."):
                try:
                    targets.update(subnet_targets(ipaddress.ip_network(lip + "/24", strict=False).with_prefixlen))
                except ValueError:
                    pass
            for cidr in self.cfg.get("subnets", []):
                targets.update(subnet_targets(cidr))
        self.announce_now()
        found = 0
        try:
            if targets:
                with ThreadPoolExecutor(max_workers=50) as ex:
                    for ok in ex.map(self.probe_ip, sorted(targets)):
                        if ok:
                            found += 1
        finally:
            self.scan_count += 1
            self.last_scan = time.time()
            self.scanning = False
        return found

    def discovery_loop(self):
        # 阶段1（立即）：组播宣告 + 收藏设备定向探测
        self.scan_once(include_subnets=False)
        time.sleep(1.2)
        # 阶段2（首次启动必做）：全网段扫描，自动找到已运行的同伴
        self.scan_once(include_subnets=True)
        while self.running:
            time.sleep(SCAN_INTERVAL)
            self.scan_once(include_subnets=True)

    def scan_now(self):
        threading.Thread(target=self.scan_once, kwargs={"include_subnets": True},
                         daemon=True).start()
        return True

    # ---------------------------------------------------------- TCP 直连
    def tcp_server(self):
        while self.running:
            try:
                sock, addr = self._tcp_srv.accept()
            except OSError:
                break
            conn = PeerConn(sock, addr, self)
            with self.conns_lock:
                self.conns.append(conn)
            self.send_hello(conn)
            threading.Thread(target=self.conn_reader, args=(conn,),
                             daemon=True).start()

    def has_conn(self, peer_id):
        with self.conns_lock:
            return any(c.peer_id == peer_id for c in self.conns if c.alive)

    def connect_loop(self):
        while self.running:
            with self.peers_lock:
                targets = [(pid, p.get("ip"), p.get("tcp")) for pid, p in self.peers.items()
                           if time.time() - p.get("last_seen", 0) < PEER_TIMEOUT
                           and p.get("ip") not in ("127.0.0.1", None) and p.get("tcp")]
            for pid, ip, port in targets:
                if self.has_conn(pid):
                    continue
                try:
                    sock = socket.create_connection((ip, int(port)), timeout=4)
                    conn = PeerConn(sock, (ip, int(port)), self)
                    conn.peer_id = pid
                    with self.conns_lock:
                        self.conns.append(conn)
                    self.send_hello(conn)
                    threading.Thread(target=self.conn_reader, args=(conn,),
                                     daemon=True).start()
                except OSError:
                    pass
            for host_port in self.manual_peers:   # 兼容旧版 --peer 参数
                try:
                    ip, port = host_port.rsplit(":", 1)
                    port = int(port)
                except ValueError:
                    continue
                with self.conns_lock:
                    connected = any(c.addr[0] == ip and c.addr[1] == port
                                    for c in self.conns if c.alive)
                if not connected:
                    try:
                        sock = socket.create_connection((ip, port), timeout=4)
                        conn = PeerConn(sock, (ip, port), self)
                        with self.conns_lock:
                            self.conns.append(conn)
                        self.send_hello(conn)
                        threading.Thread(target=self.conn_reader,
                                         args=(conn,), daemon=True).start()
                    except OSError:
                        pass
            time.sleep(3)

    def antientropy_loop(self):
        while self.running:
            with self.rooms_lock:
                has_rooms = bool(self.rooms)
            if has_rooms:
                self.broadcast_rooms()
            self.broadcast({"t": "presence", "id": self.id,
                            "name": self.name, "color": self.color,
                            "note": self.note,
                            "room": self.my_presence.get("room"),
                            "cell": self.my_presence.get("cell")})
            # 群资料定期广播：即使 TCP 建连时错过 group_sync 也能自动补齐
            for g in self.groups_public():
                self.broadcast({"t": "group_update",
                                "group": {k: g[k] for k in
                                          ("id", "name", "owner", "members", "created")}})
            time.sleep(ANTIENTROPY_INTERVAL)

    # ---------------------------------------------------------- Excel
    @staticmethod
    def _norm(v):
        if v is None:
            return ""
        if isinstance(v, (int, float, str, bool)):
            return v
        try:
            return v.isoformat(sep=" ")[:19]
        except AttributeError:
            return str(v)

    # ---------------------------------------------------------- 样式模板同步
    @staticmethod
    def _template_file(h):
        return os.path.join(TEMPLATE_DIR, h + ".xlsx")

    def room_style(self, room_name):
        """给桌面 UI 用的样式信息（进程内直接取，不走网络）"""
        room = self.get_room(room_name)
        if room is None:
            return None
        with room.lock:
            return {"style": room.style, "version": room.style_version,
                    "sheets": list(room.sheets), "sheet": room.sheet_name,
                    "active": (room.style or {}).get("active", ""),
                    "rows": room.rows, "cols": room.cols,
                    "template": room.template_hash,
                    "file": room.file_path or "", "styled": bool(room.style)}

    def _ensure_template(self, room_name, tmpl_hash, peer_ip, http_port,
                         sheet_name=None, sheets=None):
        """同伴共享了带样式的表格：本机补齐模板，保证显示与保存都保真"""
        if not room_name or not tmpl_hash:
            return
        room = self.get_room(room_name)
        if (room and room.template_hash == tmpl_hash and room.template_path
                and os.path.isfile(room.template_path)):
            return
        local = self._template_file(tmpl_hash)
        if os.path.isfile(local):
            # 清掉可能残留的下载临时文件
            try:
                for f in os.listdir(TEMPLATE_DIR):
                    if f.startswith(tmpl_hash + ".xlsx.part"):
                        os.remove(os.path.join(TEMPLATE_DIR, f))
            except Exception:
                pass
            self.install_template(room_name, local, tmpl_hash, sheet_name, sheets)
            return
        if not peer_ip or not http_port:
            return
        # 同一份模板只下载一次（一个工作簿可能对应多个工作表房间）
        with self._tmpl_lock:
            if tmpl_hash in self._tmpl_fetching:
                return
            self._tmpl_fetching.add(tmpl_hash)
        threading.Thread(target=self._fetch_template,
                         args=(room_name, tmpl_hash, peer_ip, http_port,
                               sheet_name, sheets), daemon=True).start()

    def _fetch_template(self, room_name, tmpl_hash, peer_ip, http_port,
                        sheet_name, sheets):
        tmp = None
        try:
            url = "http://%s:%s/api/room/template?room=%s&hash=%s&peer=%s" % (
                peer_ip, int(http_port), quote(room_name), tmpl_hash,
                quote(self.id))
            # 强制直连，避免系统代理把内网请求转走
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=10) as resp:
                data = resp.read()
            if not data or hashlib.md5(data).hexdigest()[:16] != tmpl_hash:
                return
            os.makedirs(TEMPLATE_DIR, exist_ok=True)
            path = self._template_file(tmpl_hash)
            tmp = path + ".part.%d" % threading.get_ident()
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
            tmp = None
            # 一次安装到该模板下的所有工作表房间
            with self.rooms_lock:
                targets = [(r.name, r.sheet_name, list(r.sheets))
                           for r in self.rooms.values()
                           if r.template_hash == tmpl_hash]
            for rn, sn, sns in targets:
                self.install_template(rn, path, tmpl_hash, sn, sns)
        except Exception as e:
            print("[样式模板同步失败] %s: %s" % (room_name, e))
        finally:
            if tmp and os.path.isfile(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass
            with self._tmpl_lock:
                self._tmpl_fetching.discard(tmpl_hash)

    def install_template(self, room_name, path, tmpl_hash,
                         sheet_name=None, sheets=None):
        try:
            style = sheetstyle.extract_style(path, sheet_name)
        except Exception as e:
            print("[样式模板解析失败] %s: %s" % (room_name, e))
            return None
        room = self.get_room(room_name, create=True)
        with room.lock:
            room.template_path = path
            room.template_hash = tmpl_hash
            room.sheet_name = style["sheet"]
            room.sheets = list(sheets or style["sheets"])
            room.style = style
            room.style_version += 1
            room.styled = True
            room.rows = max(room.rows, style["rows"] + 2)
            room.cols = max(room.cols, style["cols"] + 2)
            if not room.file_path:
                # 远端没有原文件：保存到本机导出目录，绝不覆盖别人的文件
                exports = os.path.join(os.path.dirname(TEMPLATE_DIR),
                                       "lanexcel_exports")
                safe = "".join(ch for ch in room_name
                               if ch not in '\\/:*?"<>|')[:80] or "共享表"
                room.file_path = os.path.join(exports, safe + ".xlsx")
                room.source_path = room.file_path
        return style

    def open_xlsx(self, path, room_name=None, gid=None):
        """打开 Excel：原文件复制为只读样式模板，每个工作表各建一个协作房间。

        房间之间只同步「值」；样式、合并、公式、其它工作表、页面设置始终以
        模板为基底，任何一次协作编辑都不会改写它们。
        gid 非空时把整套房间绑定到该群聊：仅群成员可见可编辑。
        """
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise ValueError("文件不存在: " + path)
        g = None
        if gid:
            g = self._need_group(gid)
            if g["dissolved"]:
                raise ValueError("群聊已解散，无法共享表格")
            if self.id not in g["members"]:
                raise ValueError("你不在该群聊中，不能共享表格")
        with open(path, "rb") as f:
            raw = f.read()
        tmpl_hash = hashlib.md5(raw).hexdigest()[:16]
        tpl = self._template_file(tmpl_hash)
        if not os.path.isfile(tpl):
            os.makedirs(TEMPLATE_DIR, exist_ok=True)
            tmp = tpl + ".part"
            with open(tmp, "wb") as f:
                f.write(raw)
            os.replace(tmp, tpl)

        wb = openpyxl.load_workbook(tpl, data_only=True)
        sheet_names = list(wb.sheetnames)
        active_name = wb.active.title
        stem = os.path.splitext(os.path.basename(path))[0]
        base = room_name or stem
        ts = now_ns()
        primary = None
        for sn in sheet_names:
            rn = base if sn == active_name else "%s::%s" % (base, sn)
            style = sheetstyle.extract_style(tpl, sn)
            vals = sheetstyle.read_values(tpl, sn)
            room = self.get_room(rn, create=True)
            cells = {}
            with room.lock:
                room.file_path = path
                room.source_path = path
                room.template_path = tpl
                room.template_hash = tmpl_hash
                room.sheet_name = sn
                room.sheets = sheet_names
                room.style = style
                room.style_version += 1
                room.styled = True
                room.cells = {}
                room.edited = set()
                room.rows = max(vals["rows"], style["rows"] + 2, 1)
                room.cols = max(vals["cols"], style["cols"] + 2, 1)
                for (r, c), v in vals["values"].items():
                    v = Node._norm(v)
                    if v != "":
                        room.cells[(r, c)] = [v, ts, self.id]
                        cells["%d,%d" % (r, c)] = [v, ts, self.id]
                room.dirty = False
                room.seq += 1
                room.edit_log.append({"seq": room.seq, "cells": cells})
                room.owner_id = self.id
                if g is not None:
                    room.group_id = gid
                    room.group_members = set(g["members"])
            if sn == active_name:
                primary = {"room": rn, "cells": cells, "sheet": sn,
                           "rows": room.rows, "cols": room.cols}
        if primary is None:
            raise ValueError("工作簿没有可读取的工作表")
        self.broadcast_rooms()
        primary.update({"sheets": sheet_names, "active": active_name,
                        "styled": True, "template": tmpl_hash,
                        "style": sheetstyle.describe(
                            self.get_room(primary["room"]).style)})
        return primary

    def save_xlsx(self, room_name, path=None):
        """以原始模板为基底保存：只写回被协作改过的单元格，其余全部原样保留"""
        room = self.get_room(room_name)
        if room is None:
            raise ValueError("房间不存在: " + room_name)
        # 群内表格：非成员不能保存（解散后成员仍可把手上数据存到本地）
        if room.group_id:
            with self.groups_lock:
                g = self.groups.get(room.group_id)
            allowed = bool(g and self.id in g["members"]) or \
                self.id in (room.group_members or set())
            if not allowed:
                raise ValueError("该协作表格仅群成员可操作")
        with room.lock:
            out = os.path.abspath(path) if path else (room.file_path or room.source_path)
            tpl = room.template_path
        if not out:
            raise ValueError("请指定保存路径")
        if tpl and os.path.isfile(tpl):
            # 同一个工作簿的所有工作表房间一起写回，避免多次保存互相覆盖
            with self.rooms_lock:
                group = [r for r in self.rooms.values() if r.template_path == tpl]
            sheet_values = {}
            for r in group:
                with r.lock:
                    vals = {}
                    for (rr, cc) in r.edited:
                        cur = r.cells.get((rr, cc))
                        vals[(rr, cc)] = cur[0] if cur else ""
                    sheet_values[r.sheet_name] = vals
            sheetstyle.write_values(tpl, out, sheet_values)
            for r in group:
                with r.lock:
                    r.file_path = out
                    r.dirty = False
            return out
        # 纯新建的房间（没有原始模板）：退化为普通工作簿
        with room.lock:
            if room.wb is None:
                room.wb = openpyxl.Workbook()
            ws = room.wb.active
            ws.title = room.sheet_name or "Sheet1"
            for row in ws.iter_rows():
                for cell in row:
                    cell.value = None
            for (r, c), (v, ts, who) in room.cells.items():
                if v == "":
                    continue
                try:
                    ws.cell(row=r, column=c).value = v
                except (AttributeError, ValueError):
                    continue
            room.wb.save(out)
            room.file_path = out
            room.dirty = False
        return out

    def autosave_loop(self):
        while self.running:
            time.sleep(AUTOSAVE_INTERVAL)
            done = set()
            for room in list(self.rooms.values()):
                with room.lock:
                    key = room.template_path or room.name
                    dirty = room.dirty
                    has_path = bool(room.file_path)
                if not dirty or not has_path or key in done:
                    continue
                done.add(key)
                try:
                    self.save_xlsx(room.name)
                except Exception as e:
                    print("[自动保存失败] %s: %s" % (room.name, e))

    # ---------------------------------------------------------- 状态
    def active_users(self, room_name):
        now = time.time()
        users = [{"id": self.id, "name": self.name + " (我)",
                  "color": self.color, "cell": self.my_presence.get("cell"),
                  "note": self.note, "local_note": ""}]
        with self.presence_lock:
            for pid, p in self.presence.items():
                if p["room"] == room_name and now - p["ts"] < PRESENCE_TIMEOUT:
                    users.append({"id": pid, "name": p["name"],
                                  "color": p["color"], "cell": p["cell"],
                                  "note": p.get("note", ""),
                                  "local_note": self.cfg.get("peer_notes", {}).get(pid, "")})
        users.sort(key=lambda u: u["id"])
        for i, u in enumerate(users):
            u["index"] = i
            u["region"] = {"start": 1 + i * REGION_WIDTH,
                           "end": i * REGION_WIDTH + REGION_WIDTH}
        return users

    def _visible_room_names(self):
        with self.rooms_lock:
            return sorted(n for n, r in self.rooms.items()
                          if self.room_visible_here(r))

    def public_state(self):
        with self.rooms_lock:
            rooms = [r.snapshot(viewer=self.id) for r in self.rooms.values()
                     if self.room_visible_here(r)]
        return {"me": {"id": self.id, "name": self.name, "note": self.note,
                       "color": self.color, "avatar": self.avatar,
                       "avatar_data": avatar_b64(self.avatar, self.avatar_data)},
                "rooms": rooms,
                "groups": self.groups_public(),
                "chat_seq": self.chat_seq,
                "peers": self.peers_detail(),
                "config": self.config_public(),
                "scan": {"scanning": self.scanning, "count": self.scan_count,
                         "last": self.last_scan, "http_port": self.http_port,
                         "tcp_port": self.tcp_port},
                "users": self.active_users(
                    self.my_presence.get("room") or
                    (rooms[0]["name"] if rooms else ""))}

    def poll(self, since_seq, room_name):
        room = self.get_room(room_name)
        if room is not None and not self.room_visible_here(room):
            room = None                      # 无权限：当作不存在，不泄露任何数据
        if room is None:
            return {"seq": 0, "edits": [], "full": True, "cells": {},
                    "users": [], "peers": self.peers_detail(),
                    "rooms": self._visible_room_names()}
        with room.lock:
            edits = [e for e in room.edit_log if e["seq"] > since_seq]
            if since_seq == 0 or (room.edit_log and since_seq > 0 and
                                  room.edit_log[0]["seq"] > since_seq + 1):
                full, edits = True, []
            else:
                full = False
            out = {"seq": room.seq, "edits": edits, "full": full,
                   "users": self.active_users(room.name),
                   "peers": self.peers_detail(),
                   "rooms": self._visible_room_names(),
                   "dirty": room.dirty,
                   "style_version": room.style_version,
                   "styled": bool(room.style),
                   "sheets": list(room.sheets),
                   "owner": room.owner_id or "",
                   "priv": dict(room.privacy or {}),
                   "scan": {"scanning": self.scanning, "count": self.scan_count}}
            if room.privacy_on():
                out["edits"] = [{"seq": e["seq"],
                                 "cells": self._view_payload(room, e["cells"],
                                                             self.id)}
                                for e in edits]
            if full:
                out["cells"] = room.cells_view(self.id)
                out["meta"] = {"sheet": room.sheet_name, "rows": room.rows,
                               "cols": room.cols,
                               "file": room.file_path or "",
                               "sheets": list(room.sheets),
                               "style_version": room.style_version,
                               "template": room.template_hash,
                               "styled": bool(room.style),
                               "owner": room.owner_id or "",
                               "priv": dict(room.privacy or {})}
                out["style"] = room.style
        return out

    def _view_payload(self, room, cells, viewer):
        """把一份编辑负载转成 viewer 视角（隐私格换成占位）"""
        out = {}
        for key, cell in cells.items():
            v, ts, who, hidden = self._cell_parts(cell)
            r, c = room._key_rc(key)
            if not hidden and viewer != room.owner_id and room.cell_private(r, c) \
                    and who != viewer and (bool(v) or (r, c) in room.masked):
                hidden = True
            out[key] = ["", ts, who, 1] if hidden else [v, ts, who]
        return out

    # ---------------------------------------------------------- 启动 / 关闭
    def start_services(self):
        """启动全部后台服务（不阻塞），供 node.py 与 app.py 共用"""
        self.tcp_port = free_port(TCP_PORT_BASE)
        self._tcp_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._tcp_srv.bind(("0.0.0.0", self.tcp_port))
        self._tcp_srv.listen(20)

        # 文件传输独立 TCP 端口：流式分块，不占用同步通道，也不受 HTTP 请求体限制
        self.file_port = free_port(FILE_PORT_BASE)
        self._file_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._file_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._file_srv.bind(("0.0.0.0", self.file_port))
        self._file_srv.listen(20)

        self.http_port = free_port(HTTP_PORT_BASE)
        self.httpd = QuietHTTPServer(("0.0.0.0", self.http_port), Handler)
        self.httpd.node = self

        jobs = [self.udp_announcer, self.udp_listener, self.multicast_listener,
                self.tcp_server, self.start_file_server,
                self.connect_loop, self.antientropy_loop,
                self.autosave_loop, self.discovery_loop,
                self.httpd.serve_forever]
        for fn in jobs:
            threading.Thread(target=fn, daemon=True).start()
        return self.httpd

    def start(self):
        self.start_services()
        url = f"http://127.0.0.1:{self.http_port}"
        print("=" * 60)
        print(f"  LanExcel v2 节点已启动   昵称: {self.name}  备注: {self.note}")
        print(f"  设备指纹: {self.id}   TCP: {self.tcp_port}   HTTP: {self.http_port}")
        print(f"  界面: {url}")
        print("=" * 60)
        if not self.no_browser:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        try:
            while self.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        self.shutdown()

    def shutdown(self):
        self.running = False
        try:
            if self.httpd:
                self.httpd.shutdown()
        except Exception:
            pass
        for srv in (getattr(self, "_file_srv", None),
                    getattr(self, "_tcp_srv", None)):
            try:
                if srv:
                    srv.close()
            except OSError:
                pass


def free_port(start):
    for p in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", p))
                return p
            except OSError:
                continue
    return start


# ---------------------------------------------------------------- HTTP API
class QuietHTTPServer(ThreadingHTTPServer):
    """局域网定向探测会主动断开连接，屏蔽这类无害的连接中断日志"""

    def handle_error(self, request, client_address):
        _, val, _ = sys.exc_info()
        if isinstance(val, (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    def _binary(self, data, ctype):
        try:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            pass

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n == 0:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    def _query(self):
        if "?" not in self.path:
            return {}
        return {k: v[0] for k, v in parse_qs(self.path.split("?", 1)[1]).items()}

    def do_GET(self):
        node = self.server.node
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            with open(os.path.join(APP_DIR, "ui.html"), "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif path == "/api/room/template":
            q = self._query()
            room = node.get_room(q.get("room", ""))
            src = None
            if room is not None:
                # 群内表格的模板同样只给群成员下载
                if not node.room_visible_to(room, q.get("peer", "")):
                    self.send_error(403)
                    return
                with room.lock:
                    src = room.template_path or room.file_path
                    expected = room.template_hash
            if src and os.path.isfile(src):
                with open(src, "rb") as f:
                    raw = f.read()
                if q.get("hash") and expected and \
                        hashlib.md5(raw).hexdigest()[:16] != q["hash"]:
                    self.send_error(404)
                    return
                self._binary(
                    raw, "application/vnd.openxmlformats-officedocument"
                         ".spreadsheetml.sheet")
            else:
                self.send_error(404)
        elif path == "/api/room/style":
            self._json(node.room_style(self._query().get("room", "")) or {})
        elif path == "/api/room/privacy":
            self._json(node.room_privacy(self._query().get("room", "")))
        elif path == "/api/state":
            self._json(node.public_state())
        elif path == "/api/poll":
            q = self._query()
            self._json(node.poll(int(q.get("since", "0")), q.get("room", "")))
        elif path == "/api/config":
            self._json(node.config_public())
        elif path == "/api/peers":
            self._json({"peers": node.peers_detail()})
        elif path == "/api/lanexcel/v2/info":
            self._json(node.device_info(False))
        elif path == "/api/lanexcel/v2/avatar":
            self._json(node.avatar_info())
        elif path == "/api/groups":
            self._json({"groups": node.groups_public()})
        elif path == "/api/chat":
            q = self._query()
            self._json(node.chat_since(int(q.get("since", "0"))))
        elif path == "/api/chat/image":
            # 本地 Web 界面查看聊天图片（仅 127.0.0.1，局域网成员走文件通道）
            if self.client_address[0] not in ("127.0.0.1", "::1"):
                self.send_error(403)
                return
            q = self._query()
            with node.chat_lock:
                msg = node.chat_index.get(q.get("mid", ""))
            p = (msg or {}).get("path") or ""
            if (msg or {}).get("kind") != "image" or not os.path.isfile(p):
                self.send_error(404)
                return
            ext = os.path.splitext(p)[1].lower()
            ctype = {".gif": "image/gif", ".png": "image/png",
                     ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                     ".bmp": "image/bmp", ".webp": "image/webp"}.get(ext)
            if not ctype:
                self.send_error(415)
                return
            with open(p, "rb") as f:
                self._binary(f.read(), ctype)
        elif path == "/api/files":
            self._json({"transfers": node.transfer_list(),
                        "download_dir": node.download_dir()})
        elif path == "/api/avatar/peer":
            q = self._query()
            data = node.fetch_peer_avatar(q.get("pid", ""))
            self._json({"data": data})
        elif path == "/api/scan/status":
            self._json({"scanning": node.scanning, "count": node.scan_count,
                        "last": node.last_scan, "peers": len(node.peers)})
        else:
            self.send_error(404)

    def do_POST(self):
        node = self.server.node
        path = self.path.split("?", 1)[0]
        body = self._body()
        try:
            if path == "/api/lanexcel/v2/register":
                # LocalSend 风格双向注册：登记对方 + 返回我方设备信息
                body["_source"] = "register"
                node.register_peer(body, self.client_address[0])
                self._json(node.device_info(False))
            elif path == "/api/set":
                cells, ts = {}, now_ns()
                for c in body.get("cells", []):
                    cells[f"{int(c['r'])},{int(c['c'])}"] = [c.get("v", ""), ts, node.id]
                node.apply_edit(body.get("room", ""), cells, local=True)
                self._json({"ok": True})
            elif path == "/api/presence":
                node.my_presence = {"room": body.get("room"),
                                    "cell": body.get("cell"), "ts": time.time()}
                node.broadcast({"t": "presence", "id": node.id,
                                "name": node.name, "color": node.color,
                                "note": node.note, "room": body.get("room"),
                                "cell": body.get("cell")})
                self._json({"ok": True})
            elif path == "/api/open":
                self._json({"ok": True, **node.open_xlsx(
                    body["path"], body.get("room"), gid=body.get("gid") or None)})
            elif path == "/api/save":
                self._json({"ok": True, "path": node.save_xlsx(body.get("room"), body.get("path"))})
            elif path == "/api/new":
                room = node.get_room(body.get("room", ""), create=True)
                if not room.owner_id:
                    room.owner_id = node.id       # 本机建的房间，我就是发布者
                self._json({"ok": True, "room": room.name})
            elif path == "/api/config":
                self._json({"ok": True, "config": node.update_config(body)})
            elif path == "/api/scan":
                self._json({"ok": True, "started": node.scan_now()})
            elif path == "/api/avatar":
                self._json({"ok": True, **node.set_avatar(body)})
            elif path == "/api/group/create":
                self._json({"ok": True, "group": node.create_group(
                    body.get("name", "新群聊"), body.get("members", []))})
            elif path == "/api/group/invite":
                self._json({"ok": True, "group": node.invite_members(
                    body.get("gid", ""), body.get("members", []))})
            elif path == "/api/group/kick":
                self._json({"ok": True, "group": node.kick_member(
                    body.get("gid", ""), body.get("pid", ""))})
            elif path == "/api/group/admin":
                self._json({"ok": True, "group": node.set_admin(
                    body.get("gid", ""), body.get("pid", ""),
                    bool(body.get("on", True)))})
            elif path == "/api/group/joinmode":
                self._json({"ok": True, "group": node.set_join_mode(
                    body.get("gid", ""), body.get("mode", ""))})
            elif path == "/api/group/dissolve":
                self._json({"ok": True, "group": node.dissolve_group(
                    body.get("gid", ""))})
            elif path == "/api/group/leave":
                self._json({"ok": True, "group": node.leave_group(
                    body.get("gid", ""))})
            elif path == "/api/group/apply":
                self._json({"ok": True, "group": node.request_join(
                    body.get("gid", ""))})
            elif path == "/api/group/approve":
                self._json({"ok": True, "group": node.approve_join(
                    body.get("gid", ""), body.get("pid", ""),
                    bool(body.get("ok", True)))})
            elif path == "/api/chat/atall":
                node.at_all(body.get("gid", ""), body.get("text", ""))
                self._json({"ok": True})
            elif path == "/api/chat/recall":
                self._json(node.recall_message(body.get("mid", "")))
            elif path == "/api/chat/send":
                kind = body.get("kind", "text")
                gid = body.get("gid", "")
                if kind == "text":
                    node.send_text(gid, body.get("text", ""))
                elif kind == "atall":
                    node.at_all(gid, body.get("text", ""))
                elif kind == "sheet":
                    node.send_sheet(gid, body.get("room", ""))
                elif kind == "local-xlsx":
                    info = node.share_local_xlsx(gid, body.get("path", ""))
                    self._json({"ok": True, "room": info["room"],
                                "cells": len(info["cells"])})
                    return
                elif kind == "file":
                    r = node.send_file(body.get("path", ""),
                                       to=body.get("to"), gid=gid or None)
                    self._json({"ok": True, **r})
                    return
                elif kind == "image":
                    r = node.send_file(body.get("path", ""),
                                       to=body.get("to"), gid=gid or None,
                                       image=True)
                    self._json({"ok": True, **r})
                    return
                else:
                    raise ValueError("未知的消息类型: " + str(kind))
                self._json({"ok": True})
            elif path == "/api/room/privacy":
                self._json({"ok": True, **node.set_privacy(
                    body.get("room", ""),
                    rows=priv.parse_rows(body.get("rows", ""))
                    if isinstance(body.get("rows"), str) else body.get("rows") or [],
                    cols=priv.parse_cols(body.get("cols", ""))
                    if isinstance(body.get("cols"), str) else body.get("cols") or [],
                    on=bool(body.get("on", True)))})
            elif path == "/api/file/send":
                self._json({"ok": True, **node.send_file(
                    body.get("path", ""), to=body.get("to"),
                    gid=body.get("gid") or None)})
            else:
                self.send_error(404)
        except Exception as e:
            self._json({"ok": False, "error": str(e)}, code=400)


def main():
    ap = argparse.ArgumentParser(description="LanExcel v2 内网协作节点")
    ap.add_argument("--name", default=os.environ.get("LANEXCEL_NAME", ""), help="显示名称")
    ap.add_argument("--note", default="", help="个人备注")
    ap.add_argument("--peer", action="append", default=[], help="手动同伴 ip:tcp端口")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    node = Node(args.name, args.peer)
    if args.note:
        node.update_config({"note": args.note})
    node.no_browser = args.no_browser
    node.start()


if __name__ == "__main__":
    main()
