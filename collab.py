# -*- coding: utf-8 -*-
"""
LanExcel v3 协作扩展（Mixin）
============================
* 局域网群聊：创建、邀请成员、文本消息、系统消息、历史持久化、新同伴上线自动补历史
* 协作表格消息：把已有协作房间 / 本地 xlsx 作为「协作表格卡片」发到群里，收件人自动加入房间
* 头像：12 款卡通头像 + 用户自定义头像，随设备信息 / hello / 聊天消息同步
* 文件传输：独立 TCP 通道 + 64KB 分块流式读写，写入磁盘不落内存 → 不限制文件大小
* 全去中心化：群资料与消息在节点间复制，无服务器；断线重连通过 group_sync 补齐
"""

import json
import os
import re
import socket
import threading
import time
import urllib.request
import uuid

from avatars import (CARTOON_COUNT, avatar_b64, cartoon_b64,
                     custom_avatar_b64, default_avatar)

FILE_PORT_BASE = 8776          # 文件传输 TCP 端口（占用时顺延）
FILE_CHUNK = 64 * 1024         # 流式分块大小
CHAT_LIMIT = 600               # 每台机器保留的聊天记录条数
MAX_HEADER = 65536


def _safe_name(name):
    name = os.path.basename(str(name or "received.bin"))
    name = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", name)
    return name[:180] or "received.bin"


def _unique_path(path):
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    for i in range(1, 10000):
        p = "%s (%d)%s" % (stem, i, ext)
        if not os.path.exists(p):
            return p
    return stem + "-" + uuid.uuid4().hex[:6] + ext


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % int(n)
        n /= 1024.0


class CollabMixin:
    # ------------------------------------------------------------ 初始化 / 持久化
    def init_collab(self, config_path):
        self._chat_path = os.path.join(os.path.dirname(config_path), "lanexcel_chat.json")
        self._chat_save_lock = threading.RLock()

        self.avatar = self.cfg.get("avatar") or default_avatar(self.id)
        self.avatar_data = self.cfg.get("avatar_data") or ""

        self.file_port = None
        self._file_srv = None

        self.groups = {}                       # gid -> {id,name,owner,members:set,created}
        self.groups_lock = threading.RLock()
        self.chat_log = []                     # 消息（含本地 seq）
        self.chat_seen = set()                 # 全局唯一消息 id，去重
        self.chat_seq = 0
        self.chat_lock = threading.RLock()
        self.transfers = []                    # 文件传输进度/记录
        self.transfers_lock = threading.RLock()
        self.peer_avatars = {}                 # pid -> {avatar, data}
        self.peer_avatars_lock = threading.RLock()
        self.on_file_received = None           # 可选回调：文件收完后通知桌面 UI
        self._load_chat()

    def _load_chat(self):
        try:
            with open(self._chat_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for g in data.get("groups", []):
                self.groups[g["id"]] = {
                    "id": g["id"], "name": g.get("name", "群聊"),
                    "owner": g.get("owner", ""),
                    "members": set(g.get("members", [])),
                    "created": g.get("created", 0)}
            for m in data.get("messages", []):
                mid = m.get("id") or uuid.uuid4().hex
                if mid in self.chat_seen:
                    continue
                self.chat_seen.add(mid)
                self.chat_seq += 1
                m["seq"] = self.chat_seq
                self.chat_log.append(m)
            self.chat_log = self.chat_log[-CHAT_LIMIT:]
        except Exception:
            pass

    def _save_chat(self):
        """聊天记录落盘（每次消息/群变更保存一次，量小无压力）"""
        with self._chat_save_lock:
            try:
                with self.groups_lock:
                    groups = [self.group_public(g) for g in self.groups.values()]
                with self.chat_lock:
                    msgs = []
                    for m in self.chat_log[-CHAT_LIMIT:]:
                        x = dict(m)
                        x.pop("seq", None)
                        x.pop("avatar_data", None)   # 自定义头像由 hello 重传，避免日志膨胀
                        msgs.append(x)
                os.makedirs(os.path.dirname(self._chat_path), exist_ok=True)
                tmp = self._chat_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"groups": groups, "messages": msgs},
                              f, ensure_ascii=False)
                os.replace(tmp, self._chat_path)
            except Exception:
                pass

    # ------------------------------------------------------------ 群 / 消息
    def group_public(self, g):
        with self.groups_lock:
            members = sorted(g["members"])
        return {"id": g["id"], "name": g["name"], "owner": g["owner"],
                "members": members, "created": g["created"],
                "mine": self.id in g["members"], "count": len(members)}

    def groups_public(self):
        with self.groups_lock:
            gs = list(self.groups.values())
        out = [self.group_public(g) for g in gs]
        out.sort(key=lambda x: x["created"])
        return out

    def _merge_group(self, info):
        if not isinstance(info, dict) or not info.get("id"):
            return
        gid = info["id"]
        members = set(info.get("members") or [])
        with self.groups_lock:
            g = self.groups.get(gid)
            if g is None:
                self.groups[gid] = {"id": gid, "name": info.get("name") or "群聊",
                                    "owner": info.get("owner", ""),
                                    "members": members,
                                    "created": info.get("created", time.time())}
            else:
                g["members"] |= members
                if info.get("name"):
                    g["name"] = info["name"]
        self._save_chat()

    def _merge_group_list(self, infos):
        for info in infos or []:
            self._merge_group(info)

    def create_group(self, name, members=None):
        gid = uuid.uuid4().hex[:12]
        mem = set([self.id] + [str(x) for x in (members or []) if x])
        g = {"id": gid, "name": (str(name or "新群聊").strip() or "新群聊")[:40],
             "owner": self.id, "members": mem, "created": time.time()}
        with self.groups_lock:
            self.groups[gid] = g
        pub = self.group_public(g)
        self.broadcast({"t": "group_update", "group": pub})
        self._add_message(gid, "system",
                          text="%s 创建了群聊「%s」，邀请了 %d 位成员"
                               % (self.name, g["name"], len(mem) - 1))
        self._save_chat()
        return pub

    def invite_members(self, gid, members):
        with self.groups_lock:
            g = self.groups.get(gid)
            if g is None:
                raise ValueError("群聊不存在: " + str(gid))
            new = [str(x) for x in (members or []) if x and x != self.id
                   and x not in g["members"]]
            g["members"].update(new)
            pub = self.group_public(g)
        if not new:
            return pub
        self.broadcast({"t": "group_update", "group": pub})
        names = []
        for pid in new:
            with self.peers_lock:
                p = self.peers.get(pid)
            names.append(p.get("name", pid[:8]) if p else pid[:8])
        self._add_message(gid, "system",
                          text="%s 邀请 %s 加入群聊" % (self.name, "、".join(names)))
        self._save_chat()
        return pub

    def _add_message(self, gid, kind, from_id=None, from_name=None,
                     avatar="", avatar_data="", broadcast=True, seen=None, **extra):
        """统一构造消息：mid 全局去重 / seq 本地排序 / 附带头像"""
        mid = extra.pop("id", None) or ("%s:%d" % (from_id or self.id, time.time_ns()))
        msg = {"id": mid, "gid": gid, "ts": time.time(),
               "from": from_id or self.id, "from_name": from_name or self.name,
               "avatar": avatar or self.avatar,
               "avatar_data": avatar_data or (self.avatar_data if self.avatar == "custom" else ""),
               "kind": kind}
        msg.update(extra)
        return self._store_message(msg, broadcast=broadcast, seen=seen)

    def _store_message(self, msg, broadcast=True, seen=None):
        mid = msg.get("id")
        with self.chat_lock:
            if mid and mid in self.chat_seen:
                return False
            if mid:
                self.chat_seen.add(mid)
            msg = dict(msg)
            msg.pop("seq", None)
            self.chat_seq += 1
            msg["seq"] = self.chat_seq
            self.chat_log.append(msg)
            if len(self.chat_log) > CHAT_LIMIT:
                self.chat_log = self.chat_log[-CHAT_LIMIT:]
        self._save_chat()
        if broadcast:
            payload = dict(msg)
            payload.pop("seq", None)
            g = None
            with self.groups_lock:
                if msg.get("gid") in self.groups:
                    g = self.group_public(self.groups[msg["gid"]])
            self.broadcast({"t": "chat", "msg": payload, "group": g,
                            "seen": sorted(set(seen or []) | {self.id})})
        return True

    def send_text(self, gid, text):
        text = str(text or "").strip()
        if not text:
            return None
        return self._add_message(gid, "text", text=text[:4000])

    def send_sheet(self, gid, room_name):
        r = self.get_room(room_name)
        if r is None:
            raise ValueError("协作表格不存在: " + str(room_name))
        # 让收件人立刻知道有新房间（rooms 广播会触发自动加入 + 全量拉取 + 模板同步）
        self.broadcast_rooms()
        self._add_message(gid, "sheet",
                          text="%s 发送了协作表格「%s」" % (self.name, room_name),
                          room=room_name, sheet=r.sheet_name, cells=len(r.cells))
        return room_name

    def share_local_xlsx(self, gid, path):
        """把本地 Excel 发到群里变成协作表格：打开成共享房间 + 发卡片"""
        info = self.open_xlsx(path)
        r = self.get_room(info["room"])
        self._add_message(gid, "sheet",
                          text="%s 发送了本地表格「%s」，已转为协作表格"
                               % (self.name, os.path.basename(path)),
                          room=info["room"], sheet=info["sheet"],
                          cells=len(info["cells"]), file=os.path.basename(path))
        return info

    def _request_sheet(self, msg, conn):
        room = msg.get("room")
        if not room:
            return
        self.get_room(room, create=True)
        try:
            conn.send({"t": "state_req", "room": room})
        except Exception:
            pass

    # ------------------------------------------------------------ 头像
    def avatar_info(self):
        return {"id": self.id, "avatar": self.avatar,
                "data": avatar_b64(self.avatar, self.avatar_data)}

    def set_avatar(self, spec):
        if isinstance(spec, dict):
            if spec.get("avatar"):
                self.avatar = str(spec["avatar"])[:40]
                self.avatar_data = ""
            elif spec.get("custom_path"):
                self.avatar_data = custom_avatar_b64(spec["custom_path"])
                self.avatar = "custom"
            elif spec.get("custom_data"):
                self.avatar_data = str(spec["custom_data"])
                self.avatar = "custom"
            if spec.get("download_dir"):
                self.cfg["download_dir"] = str(spec["download_dir"])
        else:
            self.avatar = str(spec)[:40]
            self.avatar_data = ""
        self.cfg["avatar"] = self.avatar
        self.cfg["avatar_data"] = self.avatar_data
        self.persist_config()
        self.announce_now()
        self.broadcast({"t": "avatar", **self.avatar_info()})
        return self.avatar_info()

    def _cache_avatar(self, pid, avatar=None, data=None):
        if not pid or pid == self.id:
            return
        with self.peer_avatars_lock:
            rec = self.peer_avatars.setdefault(pid, {"avatar": "", "data": ""})
            if avatar:
                rec["avatar"] = avatar
            if data:
                rec["data"] = data
        with self.peers_lock:
            p = self.peers.get(pid)
            if p is not None and avatar:
                p["avatar"] = avatar

    def peer_avatar_b64(self, pid):
        with self.peer_avatars_lock:
            rec = dict(self.peer_avatars.get(pid, {}))
        if rec.get("data"):
            return rec["data"]
        spec = rec.get("avatar") or ""
        if spec.startswith("cartoon:"):
            try:
                return cartoon_b64(int(spec.split(":", 1)[1]) % CARTOON_COUNT)
            except ValueError:
                pass
        return avatar_b64(default_avatar(pid))

    def fetch_peer_avatar(self, pid):
        """跨网段 / 未建立 TCP 连接时，用 HTTP 主动取同伴自定义头像"""
        with self.peers_lock:
            p = dict(self.peers.get(pid, {}))
        if not p.get("ip"):
            return ""
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            url = "http://%s:%s/api/lanexcel/v2/avatar" % (
                p["ip"], p.get("http") or 53317)
            with opener.open(url, timeout=2) as resp:
                info = json.load(resp)
            self._cache_avatar(pid, info.get("avatar"), info.get("data"))
            return info.get("data", "")
        except Exception:
            return ""

    # ------------------------------------------------------------ 文件传输（不限大小）
    def download_dir(self):
        d = self.cfg.get("download_dir")
        if d:
            return d
        return os.path.join(os.path.dirname(self._chat_path), "LanExcel下载")

    def _add_transfer(self, rec):
        with self.transfers_lock:
            self.transfers.append(rec)
            if len(self.transfers) > 120:
                self.transfers = self.transfers[-120:]

    def transfer_list(self):
        with self.transfers_lock:
            return [dict(x) for x in self.transfers]

    def _peer_online(self, pid):
        with self.peers_lock:
            p = self.peers.get(pid)
        return bool(p and time.time() - p.get("last_seen", 0) < 45)

    def send_file(self, path, to=None, gid=None):
        """发送文件：to=成员 id（单聊）或 gid=群 id（发给全体在线群成员）
        文件以 64KB 分块流式读取发送，接收端边收边写盘，内存占用恒定。
        """
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise ValueError("文件不存在: " + path)
        size = os.path.getsize(path)
        name = os.path.basename(path)
        if to:
            targets = [to]
        elif gid:
            with self.groups_lock:
                g = self.groups.get(gid)
                if g is None:
                    raise ValueError("群聊不存在")
                targets = [p for p in g["members"] if p != self.id and self._peer_online(p)]
        else:
            raise ValueError("请指定接收成员或群聊")
        tid = uuid.uuid4().hex[:10]
        if gid:
            self._add_message(gid, "file",
                              text="%s 发送了文件 %s（%s）"
                                   % (self.name, name, human_size(size)),
                              file=name, size=size, path=path,
                              transfer=tid, targets=len(targets))
        for pid in targets:
            threading.Thread(target=self._send_file_worker,
                             args=(path, pid, gid or "", tid, size, name),
                             daemon=True).start()
        return {"id": tid, "name": name, "size": size, "targets": targets}

    def _send_file_worker(self, path, pid, gid, tid, size, name):
        rec = {"id": tid + ":" + pid, "name": name, "size": size, "sent": 0,
               "status": "发送中", "direction": "send", "path": path,
               "peer": pid, "group": gid, "ts": time.time()}
        self._add_transfer(rec)
        sock = None
        try:
            with self.peers_lock:
                p = dict(self.peers.get(pid, {}))
            ip = p.get("ip")
            port = p.get("file_port")
            if not ip:
                raise RuntimeError("同伴不在线或缺少地址")
            if not port:
                # 绝不退回本机默认端口，否则同机多实例时会把文件发给自己
                raise RuntimeError("对方未提供文件传输端口，请确认对方也是 v3 客户端")
            meta = {"t": "file", "id": tid, "from": self.id,
                    "from_name": self.name, "avatar": self.avatar,
                    "avatar_data": avatar_b64(self.avatar, self.avatar_data),
                    "name": name, "size": size, "group": gid, "ts": time.time()}
            sock = socket.create_connection((ip, int(port)), timeout=10)
            sock.settimeout(120)
            sock.sendall((json.dumps(meta, ensure_ascii=False) + "\n").encode("utf-8"))
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(FILE_CHUNK)
                    if not chunk:
                        break
                    sock.sendall(chunk)
                    rec["sent"] += len(chunk)
            sock.shutdown(socket.SHUT_WR)
            rec["status"] = "已完成"
        except Exception as e:
            rec["status"] = "失败: %s" % e
        finally:
            try:
                if sock:
                    sock.close()
            except OSError:
                pass

    def start_file_server(self):
        while self.running:
            try:
                sock, addr = self._file_srv.accept()
            except OSError:
                break
            threading.Thread(target=self._recv_file_worker, args=(sock,),
                             daemon=True).start()

    def _recv_file_worker(self, sock):
        try:
            f = sock.makefile("rb")
            header = f.readline(MAX_HEADER)
            if not header:
                return
            meta = json.loads(header.decode("utf-8"))
            size = int(meta.get("size", 0))
            if size < 0:
                raise ValueError("文件大小异常")
            name = _safe_name(meta.get("name"))
            dldir = self.download_dir()
            os.makedirs(dldir, exist_ok=True)
            path = _unique_path(os.path.join(dldir, name))
            sender = meta.get("from", "")
            self._cache_avatar(sender, meta.get("avatar"), meta.get("avatar_data"))
            rec = {"id": meta.get("id", "?") + ":recv:" + sender, "name": name,
                   "size": size, "sent": 0, "status": "接收中",
                   "direction": "recv", "path": path, "peer": sender,
                   "group": meta.get("group", ""), "ts": time.time()}
            self._add_transfer(rec)
            with open(path, "wb") as out:
                left = size
                while left > 0:
                    chunk = f.read(min(FILE_CHUNK, left))
                    if not chunk:
                        raise IOError("连接中断，文件不完整")
                    out.write(chunk)
                    left -= len(chunk)
                    rec["sent"] += len(chunk)
            rec["status"] = "已完成"
            gid = meta.get("group")
            with self.groups_lock:
                g = self.groups.get(gid) if gid else None
                mine = bool(g and self.id in g["members"])
            if mine:
                # 收件端本地生成一张文件卡片（不广播，其他人各自收各自的副本）
                self._store_message({
                    "id": "file:" + str(meta.get("id")) + ":" + self.id,
                    "gid": gid, "ts": time.time(), "from": sender,
                    "from_name": meta.get("from_name", "同伴"),
                    "avatar": meta.get("avatar", ""),
                    "avatar_data": meta.get("avatar_data", ""),
                    "kind": "file",
                    "text": "%s 发来文件 %s（%s）"
                            % (meta.get("from_name", "同伴"), name, human_size(size)),
                    "file": name, "size": size, "path": path,
                    "transfer": meta.get("id", "")}, broadcast=False)
            if self.on_file_received:
                try:
                    self.on_file_received(path, meta)
                except Exception:
                    pass
        except Exception as e:
            try:
                with self.transfers_lock:
                    if self.transfers:
                        self.transfers[-1]["status"] = "失败: %s" % e
            except Exception:
                pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    # ------------------------------------------------------------ TCP 消息分发
    def on_peer_hello(self, conn, msg):
        """TCP 建连时交换头像，并把群聊/历史补发给刚上线的同伴"""
        self._cache_avatar(msg.get("id"), msg.get("avatar"), msg.get("data"))
        self.send_group_sync(conn)

    def send_group_sync(self, conn):
        with self.groups_lock:
            groups = [self.group_public(g) for g in self.groups.values()]
        with self.chat_lock:
            msgs = []
            for m in self.chat_log[-120:]:
                x = dict(m)
                x.pop("seq", None)
                msgs.append(x)
        if groups or msgs:
            conn.send({"t": "group_sync", "groups": groups, "messages": msgs})

    def handle_collab_msg(self, conn, msg):
        t = msg.get("t")
        if t == "group_update":
            self._merge_group(msg.get("group"))
            return True
        if t == "group_sync":
            self._merge_group_list(msg.get("groups"))
            for m in msg.get("messages") or []:
                gid = m.get("gid")
                with self.groups_lock:
                    g = self.groups.get(gid)
                    mine = bool(g and self.id in g["members"])
                if mine:
                    self._cache_avatar(m.get("from"), m.get("avatar"), m.get("avatar_data"))
                    self._store_message(m, broadcast=False)
            return True
        if t == "avatar":
            self._cache_avatar(msg.get("id"), msg.get("avatar"), msg.get("data"))
            return True
        if t == "chat":
            self._merge_group(msg.get("group"))
            m = msg.get("msg") or {}
            gid = m.get("gid")
            with self.groups_lock:
                g = self.groups.get(gid)
                mine = bool(g and self.id in g["members"])
            # 群资料即使自己不在群里也保留，方便以后被邀请
            stored = False
            if mine:
                self._cache_avatar(m.get("from"), m.get("avatar"), m.get("avatar_data"))
                stored = self._store_message(m, broadcast=False)
                if stored and m.get("kind") == "sheet":
                    self._request_sheet(m, conn)
            if stored:
                seen = set(msg.get("seen") or []) | {self.id}
                if conn.peer_id:
                    seen.add(conn.peer_id)
                out = {"t": "chat", "msg": m, "group": msg.get("group"),
                       "seen": sorted(seen)}
                for c in list(self.conns):
                    if c.peer_id and c.peer_id not in seen:
                        c.send(out)
            return True
        return False

    # ------------------------------------------------------------ UI 轮询接口
    def chat_since(self, seq=0):
        with self.chat_lock:
            msgs = [dict(m) for m in self.chat_log if m.get("seq", 0) > seq]
        return {"seq": self.chat_seq, "messages": msgs,
                "groups": self.groups_public(),
                "transfers": self.transfer_list()}
