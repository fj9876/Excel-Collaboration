# -*- coding: utf-8 -*-
"""
LanExcel v5 协作扩展（Mixin）
============================
* 局域网群聊：创建、邀请、申请/审核进群、解散、@所有人
* 群权限：群主可解散；群主与管理员均可踢人、审核进群、设置进群方式、
  撤回任意消息、设置管理员（仅群主）；管理员除解散外与群主权限一致
* 群内协作表格隔离：房间绑定群，只有群成员能看到、能拿到数据、能编辑
* 撤回：消息与文件都可撤回且不限时间；撤回文件同时删除各端下载副本
* 文件传输：独立 TCP 通道 + 64KB 分块流式读写 → 不限制文件大小
* 全去中心化：群资料与消息在节点间复制，无服务器
  群变更带 rev 版本号 + removed 墓碑，保证邀请/踢人/解散在多次转发后仍收敛
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

JOIN_FREE = "free"             # 任何人被邀请即可进群
JOIN_APPROVAL = "approval"     # 进群需要群主/管理员审核


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

        # gid -> {id,name,owner,members:set,admins:set,removed:{pid:ts},
        #         join_mode,pending:{pid:{id,name,ts}},dissolved,rev,updated,editor,created}
        self.groups = {}
        self.groups_lock = threading.RLock()
        self.chat_log = []                     # 消息（含本地 seq）
        self.chat_seen = set()                 # 全局唯一消息 id，去重
        self.chat_index = {}                   # mid -> 消息对象（原地修改用于撤回）
        self.chat_seq = 0
        self.chat_lock = threading.RLock()
        self.transfers = []                    # 文件传输进度/记录
        self.transfers_lock = threading.RLock()
        self.peer_avatars = {}                 # pid -> {avatar, data}
        self.peer_avatars_lock = threading.RLock()
        self.on_file_received = None           # 可选回调：文件收完后通知桌面 UI
        self.on_group_event = None             # 可选回调：群资料变化（UI 刷新）
        self._load_chat()

    # ------------------------------------------------------------ 群结构工具
    @staticmethod
    def _group_norm(info):
        """把网络/磁盘上的群资料标准化为内部结构（set 化、补默认值）"""
        info = dict(info or {})
        gid = str(info.get("id") or "")
        members = set(str(x) for x in (info.get("members") or []) if x)
        admins = set(str(x) for x in (info.get("admins") or []) if x)
        owner = str(info.get("owner") or "")
        if owner:
            members.add(owner)
            admins.add(owner)
        removed = {}
        for k, v in (info.get("removed") or {}).items():
            if k:
                try:
                    removed[str(k)] = float(v)
                except (TypeError, ValueError):
                    removed[str(k)] = 0.0
        members -= set(removed)
        admins -= set(removed)
        pending = {}
        for k, v in (info.get("pending") or {}).items():
            if not k:
                continue
            rec = dict(v) if isinstance(v, dict) else {"id": str(k)}
            rec["id"] = str(k)
            rec.setdefault("name", str(k)[:8])
            rec.setdefault("ts", 0.0)
            pending[str(k)] = rec
        pending = {k: v for k, v in pending.items() if k not in members}
        join_mode = info.get("join_mode")
        if join_mode not in (JOIN_FREE, JOIN_APPROVAL):
            join_mode = JOIN_FREE
        return {
            "id": gid,
            "name": str(info.get("name") or "群聊")[:40],
            "owner": owner,
            "members": members,
            "admins": admins,
            "removed": removed,
            "pending": pending,
            "join_mode": join_mode,
            "dissolved": bool(info.get("dissolved")),
            "created": float(info.get("created") or time.time()),
            "rev": int(info.get("rev") or 0),
            "updated": float(info.get("updated") or 0),
            "editor": str(info.get("editor") or ""),
        }

    @staticmethod
    def _gkey(g):
        """群资料合并优先级：rev > updated > editor（保证多跳转发后收敛）"""
        return (int(g.get("rev") or 0), float(g.get("updated") or 0),
                str(g.get("editor") or ""))

    def _group_full(self, g):
        """完整结构（用于持久化与节点间同步）"""
        with self.groups_lock:
            return {
                "id": g["id"], "name": g["name"], "owner": g["owner"],
                "members": sorted(g["members"]),
                "admins": sorted(g["admins"]),
                "removed": dict(g["removed"]),
                "pending": {k: dict(v) for k, v in g["pending"].items()},
                "join_mode": g["join_mode"],
                "dissolved": g["dissolved"],
                "created": g["created"], "rev": g["rev"],
                "updated": g["updated"], "editor": g["editor"],
            }

    def group_public(self, g):
        """给 UI/API 的视图：pending 名单只给管理者看"""
        with self.groups_lock:
            me = self.id
            is_owner = bool(g["owner"] == me)
            is_admin = bool(is_owner or me in g["admins"])
            members = sorted(g["members"])
            admins = sorted(g["admins"])
            pending = [dict(v) for v in g["pending"].values()]
            dissolved = bool(g["dissolved"])
        return {
            "id": g["id"], "name": g["name"], "owner": g["owner"],
            "members": members, "admins": admins,
            "join_mode": g["join_mode"], "dissolved": dissolved,
            "created": g["created"], "rev": g["rev"],
            "count": len(members),
            "mine": me in g["members"],
            "is_owner": is_owner, "is_admin": is_admin,
            "can_manage": bool(is_admin and not dissolved),
            "pending": pending if is_admin else [],
            "pending_count": len(pending),
        }

    def groups_public(self):
        with self.groups_lock:
            gs = list(self.groups.values())
        out = [self.group_public(g) for g in gs]
        out.sort(key=lambda x: (x["dissolved"], x["created"]))
        return out

    def _need_group(self, gid):
        with self.groups_lock:
            g = self.groups.get(gid)
        if g is None:
            raise ValueError("群聊不存在")
        return g

    def _is_owner(self, g, pid=None):
        return bool(g and g["owner"] == (pid or self.id))

    def _is_admin(self, g, pid=None):
        pid = pid or self.id
        return bool(g and (g["owner"] == pid or pid in g["admins"]))

    def _bump(self, g, editor=None):
        """记录一次群变更：rev+1，供各节点比较新旧"""
        g["rev"] = int(g.get("rev") or 0) + 1
        g["updated"] = time.time()
        g["editor"] = editor or self.id

    def _publish_group(self, g):
        pub = self.group_public(g)
        self.broadcast({"t": "group_update", "group": self._group_full(g)})
        self._save_chat()
        # 成员变化后同步房间 ACL 并重新广播：新成员立即可见，被踢者立即失效
        try:
            self._drop_room_access(g["id"])
        except Exception:
            pass
        if self.on_group_event:
            try:
                self.on_group_event(g["id"])
            except Exception:
                pass
        return pub

    def _merge_group(self, info):
        if not isinstance(info, dict) or not info.get("id"):
            return
        remote = self._group_norm(info)
        gid = remote["id"]
        with self.groups_lock:
            g = self.groups.get(gid)
            if g is None:
                self.groups[gid] = remote
            elif self._gkey(remote) > self._gkey(g):
                # 新版本：整体接管，但成员取并集（并发邀请不丢），
                # 再减去双方墓碑（踢掉的人不会被旧广播加回来）
                members = set(g["members"]) | set(remote["members"])
                removed = dict(g["removed"])
                removed.update(remote["removed"])
                admins = set(remote["admins"])
                members -= set(removed)
                admins -= set(removed)
                g.update(remote)
                g["members"] = members
                g["admins"] = admins
                g["removed"] = removed
            else:
                # 同版本或旧版本：只做无损合并，绝不回滚踢人/解散
                members = set(g["members"]) | set(remote["members"])
                removed = dict(g["removed"])
                removed.update(remote["removed"])
                g["members"] = members - set(removed)
                if remote.get("dissolved"):
                    g["dissolved"] = True
        self._save_chat()

    def _merge_group_list(self, infos):
        for info in infos or []:
            self._merge_group(info)

    # ------------------------------------------------------------ 加载 / 保存
    def _load_chat(self):
        try:
            with open(self._chat_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            for g in data.get("groups", []):
                if not g.get("id"):
                    continue
                self.groups[g["id"]] = self._group_norm(g)
            for m in data.get("messages", []):
                mid = m.get("id") or uuid.uuid4().hex
                if mid in self.chat_seen:
                    continue
                self.chat_seen.add(mid)
                self.chat_seq += 1
                m["seq"] = self.chat_seq
                self.chat_log.append(m)
                self.chat_index[mid] = m
            self.chat_log = self.chat_log[-CHAT_LIMIT:]
            self._reindex()
        except Exception:
            pass

    def _reindex(self):
        seen, index = set(), {}
        for m in self.chat_log:
            mid = m.get("id")
            if not mid or mid in seen:
                continue
            seen.add(mid)
            index[mid] = m
        self.chat_seen = seen
        self.chat_index = index

    def _save_chat(self):
        """聊天记录落盘（每次消息/群变更保存一次，量小无压力）"""
        with self._chat_save_lock:
            try:
                with self.groups_lock:
                    groups = [self._group_full(g) for g in self.groups.values()]
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

    # ------------------------------------------------------------ 群：创建 / 邀请 / 申请
    def create_group(self, name, members=None):
        gid = uuid.uuid4().hex[:12]
        mem = set([self.id] + [str(x) for x in (members or []) if x])
        g = self._group_norm({
            "id": gid, "name": (str(name or "新群聊").strip() or "新群聊")[:40],
            "owner": self.id, "members": sorted(mem), "admins": [self.id],
            "created": time.time(), "rev": 1, "updated": time.time(),
            "editor": self.id, "join_mode": JOIN_FREE})
        with self.groups_lock:
            self.groups[gid] = g
        self.broadcast({"t": "group_update", "group": self._group_full(g)})
        self._add_message(gid, "system",
                          text="%s 创建了群聊「%s」，邀请了 %d 位成员"
                               % (self.name, g["name"], len(mem) - 1))
        self._save_chat()
        return self.group_public(g)

    def invite_members(self, gid, members):
        """邀请成员：群主/管理员直接拉入；普通成员在开启审核时只能提交申请"""
        g = self._need_group(gid)
        if g["dissolved"]:
            raise ValueError("群聊已解散，无法邀请")
        if self.id not in g["members"]:
            raise ValueError("你不在该群聊中")
        is_admin = self._is_admin(g)
        cand = [str(x) for x in (members or [])
                if x and x != self.id and x not in g["members"]]
        if not cand:
            return self.group_public(g)
        with self.groups_lock:
            if g["join_mode"] == JOIN_APPROVAL and not is_admin:
                for pid in cand:
                    if pid in g["removed"]:
                        continue          # 被踢过的人需管理者重新邀请
                    g["pending"].setdefault(pid, {"id": pid, "name": self._peer_name(pid),
                                                  "ts": time.time()})
                self._bump(g)
                names = [self._peer_name(p) for p in cand]
                self._add_message(gid, "system",
                                  text="%s 申请邀请 %s 加入群聊，等待管理员审核"
                                       % (self.name, "、".join(names)))
                return self._publish_group(g)
            for pid in cand:
                g["members"].add(pid)
                g["removed"].pop(pid, None)
                g["pending"].pop(pid, None)
            self._bump(g)
            names = [self._peer_name(p) for p in cand]
        self._add_message(gid, "system",
                          text="%s 邀请 %s 加入群聊" % (self.name, "、".join(names)))
        return self._publish_group(g)

    def request_join(self, gid):
        """申请进群：free 模式直接进；approval 模式进入待审"""
        g = self._need_group(gid)
        if g["dissolved"]:
            raise ValueError("群聊已解散")
        if self.id in g["members"]:
            return self.group_public(g)
        with self.groups_lock:
            if g["join_mode"] == JOIN_FREE:
                g["members"].add(self.id)
                g["removed"].pop(self.id, None)
                g["pending"].pop(self.id, None)
                self._bump(g)
                joined = True
            else:
                g["pending"][self.id] = {"id": self.id, "name": self.name,
                                         "ts": time.time()}
                self._bump(g)
                joined = False
        if joined:
            self._add_message(gid, "system", text="%s 加入了群聊" % self.name)
        else:
            self._add_message(gid, "system",
                              text="%s 申请加入群聊，等待管理员审核" % self.name)
        return self._publish_group(g)

    def approve_join(self, gid, pid, ok=True):
        g = self._need_group(gid)
        if not self._is_admin(g):
            raise ValueError("只有群主或管理员可以审核进群")
        pid = str(pid or "")
        with self.groups_lock:
            rec = g["pending"].pop(pid, None)
            if ok:
                if pid in g["removed"]:
                    raise ValueError("该成员已被移出群聊，请使用「邀请成员」")
                g["members"].add(pid)
                g["removed"].pop(pid, None)
            self._bump(g)
        nm = (rec or {}).get("name") or self._peer_name(pid)
        self._add_message(gid, "system",
                          text="%s %s %s 加入群聊" % (self.name, "同意" if ok else "拒绝", nm))
        return self._publish_group(g)

    def kick_member(self, gid, pid):
        g = self._need_group(gid)
        pid = str(pid or "")
        if not self._is_admin(g):
            raise ValueError("只有群主或管理员可以移出成员")
        if pid == g["owner"]:
            raise ValueError("不能移出群主")
        if pid == self.id:
            raise ValueError("不能移出自己，如需离开请使用「退出群聊」")
        if pid in g["admins"] and not self._is_owner(g):
            raise ValueError("管理员无法移出其他管理员")
        with self.groups_lock:
            if pid not in g["members"] and pid not in g["pending"]:
                raise ValueError("该成员不在此群聊中")
            g["members"].discard(pid)
            g["admins"].discard(pid)
            g["pending"].pop(pid, None)
            g["removed"][pid] = time.time()    # 墓碑：防止旧广播把人加回来
            self._bump(g)
        nm = self._peer_name(pid)
        self._add_message(gid, "system", text="%s 将 %s 移出了群聊" % (self.name, nm))
        return self._publish_group(g)

    def set_admin(self, gid, pid, on=True):
        g = self._need_group(gid)
        pid = str(pid or "")
        if not self._is_owner(g):
            raise ValueError("只有群主可以设置管理员")
        if pid == g["owner"]:
            raise ValueError("群主无需设为管理员")
        if pid not in g["members"]:
            raise ValueError("该成员不在此群聊中")
        with self.groups_lock:
            if on:
                g["admins"].add(pid)
            else:
                g["admins"].discard(pid)
            self._bump(g)
        nm = self._peer_name(pid)
        self._add_message(gid, "system",
                          text="%s 将 %s %s管理员" % (self.name, nm,
                                                    "设为" if on else "取消了"))
        return self._publish_group(g)

    def set_join_mode(self, gid, mode):
        g = self._need_group(gid)
        if not self._is_admin(g):
            raise ValueError("只有群主或管理员可以修改进群方式")
        mode = JOIN_APPROVAL if mode == JOIN_APPROVAL else JOIN_FREE
        with self.groups_lock:
            g["join_mode"] = mode
            self._bump(g)
        self._add_message(gid, "system",
                          text="%s 将进群方式改为「%s」"
                               % (self.name, "需要管理员审核" if mode == JOIN_APPROVAL
                                  else "可直接加入"))
        return self._publish_group(g)

    def dissolve_group(self, gid):
        g = self._need_group(gid)
        if not self._is_owner(g):
            raise ValueError("只有群主可以解散群聊")
        with self.groups_lock:
            g["dissolved"] = True
            g["pending"].clear()
            members = list(g["members"])
            self._bump(g)
        self._add_message(gid, "system", text="%s 解散了该群聊" % self.name)
        return self._publish_group(g)

    def leave_group(self, gid):
        g = self._need_group(gid)
        if self.id == g["owner"]:
            raise ValueError("群主不能退出群聊，只能解散")
        with self.groups_lock:
            g["members"].discard(self.id)
            g["admins"].discard(self.id)
            self._bump(g)
        self._add_message(gid, "system", text="%s 退出了群聊" % self.name)
        return self._publish_group(g)

    def at_all(self, gid, text=""):
        """@所有人：仅群主/管理员可用"""
        g = self._need_group(gid)
        if not self._is_admin(g):
            raise ValueError("只有群主或管理员可以 @所有人")
        if g["dissolved"]:
            raise ValueError("群聊已解散")
        body = (str(text or "").strip() or "请注意查看群消息")[:4000]
        return self._add_message(gid, "text", text=body, at_all=True)

    def _peer_name(self, pid):
        with self.peers_lock:
            p = self.peers.get(pid)
        return (p or {}).get("name") or str(pid)[:8]

    # ------------------------------------------------------------ 群内协作表格隔离
    def bind_room_group(self, room_name, gid):
        """把协作房间绑定到群：此后只有群成员可见、可编辑"""
        g = self._need_group(gid)
        if g["dissolved"]:
            raise ValueError("群聊已解散，无法共享表格")
        if self.id not in g["members"]:
            raise ValueError("你不在该群聊中，不能共享表格")
        rooms = self._rooms_with_children(room_name)
        with self.rooms_lock:
            for r in rooms:
                if r.group_id and r.group_id != gid:
                    raise ValueError("该协作表格已属于另一个群聊")
            for r in rooms:
                r.group_id = gid
                r.owner_id = r.owner_id or self.id
                r.group_members = set(g["members"])
        self.broadcast_rooms()
        return room_name

    def _rooms_with_children(self, room_name):
        """主房间 + 它的所有 ::工作表 子房间"""
        out = []
        with self.rooms_lock:
            for name, r in self.rooms.items():
                if name == room_name or name.startswith(room_name + "::"):
                    out.append(r)
        r0 = self.get_room(room_name)
        if r0 is not None and r0 not in out:
            out.append(r0)
        return out

    def _sync_room_members(self, gid, members):
        with self.rooms_lock:
            for r in self.rooms.values():
                if r.group_id == gid:
                    r.group_members = set(members)

    def _drop_room_access(self, gid, pids=None):
        """成员被踢/群解散：刷新房间成员快照并重新广播（被踢者立即看不到）"""
        with self.groups_lock:
            g = self.groups.get(gid)
            members = set(g["members"]) if g else set()
        self._sync_room_members(gid, members)
        self.broadcast_rooms()

    def room_visible_to(self, room, pid):
        """房间对某个节点 id 是否可见/可编辑"""
        if room is None:
            return False
        gid = getattr(room, "group_id", None)
        if not gid:
            return True                      # 公开房间，保持原有协作方式
        with self.groups_lock:
            g = self.groups.get(gid)
        if g is not None:
            if g["dissolved"]:
                return False
            return bool(pid) and pid in g["members"]
        # 本地还没有群资料（例如模板先到）：退回房间携带的成员快照
        return bool(pid) and pid in (getattr(room, "group_members", None) or set())

    def room_visible_here(self, room):
        return self.room_visible_to(room, self.id)

    # ------------------------------------------------------------ 消息
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
            # 文件/图片：同一次传输（transfer）只保留一张卡片，避免
            # 「发送者广播卡片 + 本地接收卡片」重复显示。路径合并由
            # _recv_file_worker 收完文件后显式写入（本地路径最权威）。
            tr = msg.get("transfer")
            if tr and msg.get("kind") in ("file", "image"):
                for m in self.chat_log:
                    if (m.get("transfer") == tr
                            and m.get("kind") == msg.get("kind")
                            and m.get("gid") == msg.get("gid")):
                        if msg.get("path") and not (m.get("path")
                                                    and os.path.isfile(m["path"])):
                            m["path"] = msg["path"]
                            self.chat_seq += 1
                            m["rev"] = self.chat_seq      # 让各端 UI 重绘拿到本地路径
                        return False
            if mid:
                self.chat_seen.add(mid)
            msg = dict(msg)
            msg.pop("seq", None)
            self.chat_seq += 1
            msg["seq"] = self.chat_seq
            self.chat_log.append(msg)
            if mid:
                self.chat_index[mid] = msg
            if len(self.chat_log) > CHAT_LIMIT:
                self.chat_log = self.chat_log[-CHAT_LIMIT:]
                self._reindex()
        self._save_chat()
        if broadcast:
            payload = dict(msg)
            payload.pop("seq", None)
            g = None
            with self.groups_lock:
                if msg.get("gid") in self.groups:
                    g = self._group_full(self.groups[msg["gid"]])
            out = {"t": "chat", "msg": payload, "group": g,
                   "seen": sorted(set(seen or []) | {self.id})}
            # 只发给群成员：非成员连消息包都收不到
            allowed = set(g["members"]) if g else None
            for conn in list(self.conns):
                if not conn.peer_id:
                    continue
                if allowed is None or conn.peer_id in allowed:
                    conn.send(out)
        return True

    def send_text(self, gid, text, at_all=False):
        text = str(text or "").strip()
        if not text:
            return None
        with self.groups_lock:
            g = self.groups.get(gid)
        if g is not None and g["dissolved"]:
            raise ValueError("群聊已解散，无法发送消息")
        if at_all:
            return self.at_all(gid, text)
        return self._add_message(gid, "text", text=text[:4000])

    # ------------------------------------------------------------ 撤回（不限时间）
    def can_recall(self, msg):
        if not msg or msg.get("kind") == "system" or msg.get("recalled"):
            return False
        if msg.get("from") == self.id:
            return True
        gid = msg.get("gid")
        with self.groups_lock:
            g = self.groups.get(gid) if gid else None
        return bool(g and self._is_admin(g) and not g["dissolved"])

    def recall_message(self, mid):
        """撤回消息/文件：不限时间；文件会同步删除各端下载副本"""
        mid = str(mid or "")
        with self.chat_lock:
            msg = self.chat_index.get(mid)
            if msg is None:
                for m in reversed(self.chat_log):
                    if mid and m.get("transfer") == mid:
                        msg = m
                        break
        if msg is None:
            raise ValueError("消息不存在或已被清理")
        if msg.get("recalled"):
            return {"ok": True, "already": True, "mid": msg.get("id")}
        if not self.can_recall(msg):
            raise ValueError("只有发送者本人或群管理员可以撤回")
        return self._apply_recall(msg["id"], msg.get("transfer", ""),
                                  msg.get("gid", ""), self.id, self.name,
                                  relay=True)

    def _apply_recall(self, mid, transfer="", gid="", by="", by_name="", relay=True):
        target = None
        with self.chat_lock:
            m = self.chat_index.get(mid)
            if m is None and transfer:
                for x in reversed(self.chat_log):
                    if x.get("transfer") == transfer and \
                            (not gid or x.get("gid") == gid):
                        m = x
                        break
            if m is not None and not m.get("recalled"):
                m["recalled"] = True
                m["recalled_by"] = by
                m["recalled_by_name"] = by_name or by
                m["recalled_ts"] = time.time()
                m["text"] = ""
                # 提升 seq，让各端轮询能立刻感知这条老消息被撤回
                self.chat_seq += 1
                m["rev"] = self.chat_seq
                target = m
        if target is not None:
            self._delete_local_copy(target)
            self._save_chat()
            if relay:
                out = {"t": "chat_recall", "mid": target.get("id"),
                       "transfer": target.get("transfer", ""), "gid": gid,
                       "by": by, "by_name": by_name}
                with self.groups_lock:
                    g = self.groups.get(gid) if gid else None
                    allowed = set(g["members"]) if g else None
                for conn in list(self.conns):
                    if not conn.peer_id or conn.peer_id == by:
                        continue
                    if allowed is None or conn.peer_id in allowed:
                        conn.send(out)
        return {"ok": True, "mid": mid, "recalled": target is not None}

    def _delete_local_copy(self, msg):
        """撤回文件/图片时删除本机下载目录里的副本（只在自己的下载目录内动手）"""
        if msg.get("kind") not in ("file", "image"):
            return
        path = msg.get("path") or ""
        if not path:
            return
        try:
            dldir = os.path.abspath(self.download_dir())
            p = os.path.abspath(path)
            if p.startswith(dldir + os.sep) and os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass

    # ------------------------------------------------------------ 协作表格消息
    def send_sheet(self, gid, room_name):
        r = self.get_room(room_name)
        if r is None:
            raise ValueError("协作表格不存在: " + str(room_name))
        self.bind_room_group(room_name, gid)      # 绑定后仅群成员可见
        self.broadcast_rooms()
        self._add_message(gid, "sheet",
                          text="%s 发送了协作表格「%s」" % (self.name, room_name),
                          room=room_name, sheet=r.sheet_name, cells=len(r.cells))
        return room_name

    def share_local_xlsx(self, gid, path):
        """把本地 Excel 发到群里变成协作表格：打开成群内房间 + 发卡片"""
        info = self.open_xlsx(path, gid=gid)
        self.bind_room_group(info["room"], gid)
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
        gid = msg.get("gid")
        r = self.get_room(room, create=True)
        if gid and r is not None and not r.group_id:
            try:
                self.bind_room_group(room, gid)
            except Exception:
                pass
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

    def send_file(self, path, to=None, gid=None, image=False):
        """发送文件：to=成员 id（单聊）或 gid=群 id（发给全体在线群成员）
        文件以 64KB 分块流式读取发送，接收端边收边写盘，内存占用恒定。
        image=True 时以「图片卡片」形式发到群里，GIF 动图会动起来。
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
                if g["dissolved"]:
                    raise ValueError("群聊已解散")
                if self.id not in g["members"]:
                    raise ValueError("你不在该群聊中")
                targets = [p for p in g["members"] if p != self.id and self._peer_online(p)]
        else:
            raise ValueError("请指定接收成员或群聊")
        tid = uuid.uuid4().hex[:10]
        kind = "image" if image else "file"
        if gid:
            self._add_message(gid, kind,
                              text=("%s 发送了图片 %s（%s）" if image else
                                    "%s 发送了文件 %s（%s）")
                                   % (self.name, name, human_size(size)),
                              file=name, size=size, path=path,
                              transfer=tid, targets=len(targets), image=1 if image else 0)
        for pid in targets:
            threading.Thread(target=self._send_file_worker,
                             args=(path, pid, gid or "", tid, size, name, image),
                             daemon=True).start()
        return {"id": tid, "name": name, "size": size, "targets": targets}

    def _send_file_worker(self, path, pid, gid, tid, size, name, image=False):
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
                    "name": name, "size": size, "group": gid, "ts": time.time(),
                    "image": 1 if image else 0}
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
                mine = bool(g and self.id in g["members"] and not g["dissolved"])
            if mine:
                # 收件端本地生成/合并一张消息卡片（不广播，其他人各自收各自的副本）
                is_img = bool(meta.get("image"))
                tid = str(meta.get("id") or "")
                kind = "image" if is_img else "file"
                text = ("%s 发来图片 %s（%s）" if is_img else
                        "%s 发来文件 %s（%s）") % (
                    meta.get("from_name", "同伴"), name, human_size(size))
                with self.chat_lock:
                    dup = None
                    for m in reversed(self.chat_log):
                        if (m.get("kind") in ("file", "image")
                                and m.get("transfer") == tid
                                and m.get("gid") == gid):
                            dup = m
                            break
                if dup is not None:
                    # 广播卡片先到：把本地真实路径合并进去，撤回时才能删到本机副本
                    with self.chat_lock:
                        dup["path"] = path
                        dup["file"] = name
                        dup["size"] = size
                        self.chat_seq += 1
                        dup["rev"] = self.chat_seq
                else:
                    self._store_message({
                        "id": "file:" + tid + ":" + self.id,
                        "gid": gid, "ts": time.time(), "from": sender,
                        "from_name": meta.get("from_name", "同伴"),
                        "avatar": meta.get("avatar", ""),
                        "avatar_data": meta.get("avatar_data", ""),
                        "kind": kind,
                        "text": text,
                        "file": name, "size": size, "path": path,
                        "image": 1 if is_img else 0,
                        "transfer": tid}, broadcast=False)
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

    def _visible_groups_for(self, pid):
        """只有本人是成员的群才同步给该节点（未进群的群资料不外泄）"""
        with self.groups_lock:
            return [self._group_full(g) for g in self.groups.values()
                    if pid and pid in g["members"]]

    def _visible_messages_for(self, pid, limit=120):
        with self.groups_lock:
            allowed = set(g["id"] for g in self.groups.values()
                          if pid and pid in g["members"])
        with self.chat_lock:
            out = []
            for m in self.chat_log[-limit:]:
                if m.get("gid") in allowed:
                    x = dict(m)
                    x.pop("seq", None)
                    out.append(x)
        return out

    def send_group_sync(self, conn):
        pid = conn.peer_id
        groups = self._visible_groups_for(pid)
        msgs = self._visible_messages_for(pid)
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
        if t == "chat_recall":
            self._apply_recall(msg.get("mid", ""), msg.get("transfer", ""),
                               msg.get("gid", ""), msg.get("by", ""),
                               msg.get("by_name", ""), relay=True)
            return True
        if t == "chat":
            self._merge_group(msg.get("group"))
            m = msg.get("msg") or {}
            gid = m.get("gid")
            with self.groups_lock:
                g = self.groups.get(gid)
                mine = bool(g and self.id in g["members"])
                dissolved = bool(g and g["dissolved"])
            stored = False
            if mine and not dissolved:
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
                # 只转发给同群成员，避免群内容扩散到非成员节点
                with self.groups_lock:
                    member_ids = set(g["members"]) if g else set()
                for c in list(self.conns):
                    if c.peer_id and c.peer_id not in seen and c.peer_id in member_ids:
                        c.send(out)
            return True
        return False

    # ------------------------------------------------------------ UI 轮询接口
    def online_map(self):
        """{peer_id: 是否在线}，给 UI 区分在线/离线显示"""
        now = time.time()
        with self.peers_lock:
            return {pid: bool(now - p.get("last_seen", 0) < 45)
                    for pid, p in self.peers.items()}

    def chat_since(self, seq=0):
        with self.groups_lock:
            mine_ids = set(g["id"] for g in self.groups.values()
                           if self.id in g["members"])
        with self.chat_lock:
            msgs = [dict(m) for m in self.chat_log
                    if (m.get("seq", 0) > seq or m.get("rev", 0) > seq)
                    and m.get("gid") in mine_ids]
        return {"seq": self.chat_seq, "messages": msgs,
                "groups": self.groups_public(),
                "online": self.online_map(),
                "transfers": self.transfer_list()}
