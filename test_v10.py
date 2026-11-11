# -*- coding: utf-8 -*-
"""LanExcel v10 群聊删除 E2E 测试（子进程三节点）

甲 = 群主 / 乙 = 普通成员 / 丙 = 普通成员
验证：成员删除 = 退出并删除（本机列表移除、聊天记录清空、其他人不受影响）；
      群主删除 = 解散并删除（其他成员看到「已解散」）；
      已解散的群可一键从列表移除；
      被重新邀请时群聊自动恢复显示；
      删除群聊后，群内协作表格在本机也不再显示。
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

ROOT = os.path.join(BASE, "_test_v10")
shutil.rmtree(ROOT, ignore_errors=True)
DIR = {k: os.path.join(ROOT, k) for k in ("A", "B", "C")}
for d in DIR.values():
    os.makedirs(d, exist_ok=True)

ENV = dict(os.environ)
PY = sys.executable
procs = []


def start_node(name, cfgdir):
    env = dict(ENV)
    env["LANEXCEL_CONFIG_DIR"] = cfgdir
    p = subprocess.Popen([PY, os.path.join(BASE, "node.py"), "--name", name,
                          "--no-browser"], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    procs.append(p)
    return p


opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def api(port, path, body=None, timeout=60):
    url = "http://127.0.0.1:%d%s" % (port, path)
    try:
        if body is None:
            with opener.open(url, timeout=timeout) as r:
                return json.load(r)
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        try:
            with opener.open(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode("utf-8") or "{}")
    except Exception as e:
        return {"ok": False, "error": "HTTP %s" % e}


PASS = []


def ok(name, cond, extra=""):
    PASS.append((name, bool(cond)))
    tail = ("  | " + str(extra)) if (extra and not cond) else ""
    print(("  PASS  " if cond else "  FAIL  ") + name + tail)


def wait_for(fn, timeout=15.0, step=0.4):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def groups(port):
    return api(port, "/api/groups").get("groups") or []


def group_of(port, gid):
    return next((g for g in groups(port) if g["id"] == gid), None)


def has_group(port, gid):
    return group_of(port, gid) is not None


def chat(port):
    return api(port, "/api/chat?since=0")


def msgs_of(port, gid):
    return [m for m in (chat(port).get("messages") or []) if m.get("gid") == gid]


def has_room(port, name):
    return any(r["name"] == name for r in api(port, "/api/state").get("rooms", []))


def main():
    start_node("甲", DIR["A"])
    start_node("乙", DIR["B"])
    start_node("丙", DIR["C"])
    time.sleep(4)

    ports = []
    for p in range(53317, 53340):
        s = socket.socket()
        try:
            s.connect(("127.0.0.1", p))
            info = api(p, "/api/lanexcel/v2/info")
            if info.get("alias"):
                ports.append((info["alias"], p))
        except OSError:
            pass
        finally:
            s.close()
    P = {name: port for name, port in ports}
    ok("三节点启动", len(P) >= 3, ports)
    if len(P) < 3:
        return
    api(P["甲"], "/api/scan", {})
    time.sleep(3)

    ids = {n: api(P[n], "/api/lanexcel/v2/info").get("id") for n in P}
    ok("拿到三节点 id", all(ids.values()), ids)

    # --------------------------------------------- 1. 建群并同步
    r = api(P["甲"], "/api/group/create",
            {"name": "项目群", "members": [ids["乙"], ids["丙"]]})
    gid = (r.get("group") or {}).get("id") or ""
    ok("建群成功", r.get("ok") and bool(gid), r)
    ok("乙 看到群聊", wait_for(lambda: has_group(P["乙"], gid)), groups(P["乙"]))
    ok("丙 看到群聊", wait_for(lambda: has_group(P["丙"], gid)))

    api(P["甲"], "/api/chat/send", {"gid": gid, "kind": "text", "text": "群公告：明天上线"})
    ok("乙 收到群消息", wait_for(lambda: len(msgs_of(P["乙"], gid)) >= 1),
       msgs_of(P["乙"], gid))

    # 群内协作表格（用于验证删除群后本机也不再显示群内表格）
    api(P["乙"], "/api/new", {"room": "群内进度表"})
    api(P["乙"], "/api/set", {"room": "群内进度表",
                              "cells": [{"r": 1, "c": 1, "v": "任务A"}]})
    r = api(P["乙"], "/api/chat/send", {"gid": gid, "kind": "sheet",
                                       "room": "群内进度表"})
    ok("群内共享表格", r.get("ok"), r)
    ok("甲 看到群内表格", wait_for(lambda: has_room(P["甲"], "群内进度表")))

    # --------------------------------------------- 2. 成员删除 = 退出并删除
    r = api(P["乙"], "/api/group/delete", {"gid": gid})
    res = r.get("result") or {}
    ok("成员删除 → 退出", r.get("ok") and res.get("action") == "leave", r)
    ok("乙 列表里该群消失", not has_group(P["乙"], gid), groups(P["乙"]))
    ok("乙 的群消息被清空", not msgs_of(P["乙"], gid), msgs_of(P["乙"], gid))
    ok("乙 本机不再显示群内表格", not has_room(P["乙"], "群内进度表"),
       [x["name"] for x in api(P["乙"], "/api/state").get("rooms", [])])
    ok("甲 的群还在，且乙已不在成员里", wait_for(
        lambda: (group_of(P["甲"], gid) or {}).get("count") == 2
        and ids["乙"] not in ((group_of(P["甲"], gid) or {}).get("members") or [])),
       group_of(P["甲"], gid))
    ok("丙 的群还在", has_group(P["丙"], gid))

    # --------------------------------------------- 3. 群主删除 = 解散并删除
    r = api(P["甲"], "/api/group/delete", {"gid": gid})
    res = r.get("result") or {}
    ok("群主删除 → 解散", r.get("ok") and res.get("action") == "dissolve", r)
    ok("甲 列表里该群消失", not has_group(P["甲"], gid), groups(P["甲"]))
    ok("丙 看到群已解散（保留一条待清理记录）", wait_for(
        lambda: bool((group_of(P["丙"], gid) or {}).get("dissolved"))),
       group_of(P["丙"], gid))
    ok("解散后群内表格在丙本机不可见", wait_for(
        lambda: not has_room(P["丙"], "群内进度表")))

    # --------------------------------------------- 4. 已解散的群一键移除
    r = api(P["丙"], "/api/group/delete", {"gid": gid})
    res = r.get("result") or {}
    ok("已解散的群仅从本机移除", r.get("ok") and res.get("action") == "hide", r)
    ok("丙 列表里该群消失", not has_group(P["丙"], gid), groups(P["丙"]))
    ok("丙 的群消息被清空", not msgs_of(P["丙"], gid))

    # --------------------------------------------- 5. 被重新邀请自动恢复
    r = api(P["甲"], "/api/group/create", {"name": "临时群", "members": [ids["乙"]]})
    gid2 = (r.get("group") or {}).get("id") or ""
    ok("建第二个群", r.get("ok") and bool(gid2), r)
    ok("乙 看到第二个群", wait_for(lambda: has_group(P["乙"], gid2)))
    r = api(P["乙"], "/api/group/delete", {"gid": gid2})
    ok("乙 删除第二个群", r.get("ok") and not has_group(P["乙"], gid2), r)
    time.sleep(2)
    ok("删除后不会被旧广播复活", not has_group(P["乙"], gid2), groups(P["乙"]))

    api(P["甲"], "/api/group/invite", {"gid": gid2, "members": [ids["乙"]]})
    ok("重新邀请后自动恢复显示", wait_for(lambda: has_group(P["乙"], gid2)),
       groups(P["乙"]))

    # --------------------------------------------- 6. 防越权
    r = api(P["乙"], "/api/group/delete", {"gid": "不存在的群"})
    ok("删除不存在的群报错", r.get("ok") is False, r)
    r = api(P["乙"], "/api/group/delete", {"gid": gid2, "mode": "dissolve"})
    ok("非群主不能解散", r.get("ok") is False, r)


try:
    main()
finally:
    for pr in procs:
        try:
            pr.terminate()
        except Exception:
            pass
    time.sleep(1)
    for pr in procs:
        if pr.poll() is None:
            try:
                pr.kill()
            except Exception:
                pass

total = len(PASS)
good = sum(1 for _, c in PASS if c)
print("\n%d/%d 通过" % (good, total))
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if good == total else 1)
