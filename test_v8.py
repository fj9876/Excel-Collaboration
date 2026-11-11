# -*- coding: utf-8 -*-
"""LanExcel v8 隐私表格 E2E 测试（子进程三节点）

甲 = 发布者 / 乙 = 填写者 / 丙 = 另一位填写者（第三方）
验证：隐私列只有本人与发布者可见；他人填写处显示 *** 且改不动；
      公开列照常全员可见；公开表格（未设隐私）逻辑完全不变。
"""
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

ROOT = os.path.join(BASE, "_test_v8")
shutil.rmtree(ROOT, ignore_errors=True)
DIR = {k: os.path.join(ROOT, k) for k in ("A", "B", "C")}
for d in DIR.values():
    os.makedirs(d, exist_ok=True)

import openpyxl

XLSX = os.path.join(ROOT, "工资表.xlsx")
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "工资表"
ws.append(["姓名", "工号", "工资", "备注"])
ws.append(["张三", "A001", None, ""])
ws.append(["李四", "A002", None, ""])
ws.append(["王五", "A003", None, ""])
wb.save(XLSX)

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


def wait_for(fn, timeout=12.0, step=0.4):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:
            pass
        time.sleep(step)
    return False


def cell_of(port, room, key):
    d = api(port, "/api/poll?since=0&room=" + urllib.parse.quote(room))
    return (d.get("cells") or {}).get(key)


def hidden(port, room, key):
    c = cell_of(port, room, key)
    return bool(c and len(c) > 3 and c[3])


def value(port, room, key):
    c = cell_of(port, room, key)
    return None if c is None else c[0]


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
            ports.append((info["alias"], p, info["fingerprint"]))
    except OSError:
        pass
    finally:
        s.close()

P = {name: port for name, port, _ in ports}
ID = {name: fid for name, _, fid in ports}
ok("三节点启动", len(P) >= 3, ports)
if len(P) < 3:
    print("节点不足，退出")
    for pr in procs:
        pr.terminate()
    sys.exit(1)

api(P["甲"], "/api/scan", {})
time.sleep(3)

# ---------------------------------------------------------------- 发布隐私表格
info = api(P["甲"], "/api/open", {"path": XLSX})
ok("发布表格成功", info.get("ok"), info)
ROOM = info.get("room") or "工资表"
time.sleep(3)


def peer_has_room(name, room):
    st = api(P[name], "/api/state")
    return any(r["name"] == room for r in st.get("rooms", []))


ok("乙 自动加入房间", wait_for(lambda: peer_has_room("乙", ROOM), 15))
ok("丙 自动加入房间", wait_for(lambda: peer_has_room("丙", ROOM), 15))

r = api(P["甲"], "/api/room/privacy",
        {"room": ROOM, "on": True, "cols": "C", "rows": ""})
ok("设置隐私列 C", r.get("ok") and r.get("priv", {}).get("cols") == [3], r)
ok("隐私描述正确", "隐私表格" in (r.get("desc") or ""), r.get("desc"))

ok("乙 收到隐私设置", wait_for(
    lambda: (api(P["乙"], "/api/room/privacy?room=" + urllib.parse.quote(ROOM))
             .get("priv") or {}).get("rev", 0) > 0, 15))
ok("丙 收到隐私设置", wait_for(
    lambda: (api(P["丙"], "/api/room/privacy?room=" + urllib.parse.quote(ROOM))
             .get("priv") or {}).get("rev", 0) > 0, 15))

# ---------------------------------------------------------------- 乙 填写
r = api(P["乙"], "/api/set", {"room": ROOM,
                              "cells": [{"r": 2, "c": 3, "v": "工资8000"},
                                        {"r": 2, "c": 4, "v": "乙的备注"}]})
ok("乙 写入自己的隐私格 + 公开格", r.get("ok"), r)
time.sleep(2.5)

ok("甲（发布者）看到乙填的工资", value(P["甲"], ROOM, "2,3") == "工资8000",
   cell_of(P["甲"], ROOM, "2,3"))
ok("乙 自己看得到", value(P["乙"], ROOM, "2,3") == "工资8000")
ok("丙 看到 ***（被遮掩）", hidden(P["丙"], ROOM, "2,3"),
   cell_of(P["丙"], ROOM, "2,3"))
ok("丙 拿不到真实值", value(P["丙"], ROOM, "2,3") in ("", None),
   cell_of(P["丙"], ROOM, "2,3"))
ok("公开列丙能看到", value(P["丙"], ROOM, "2,4") == "乙的备注",
   cell_of(P["丙"], ROOM, "2,4"))

# ---------------------------------------------------------------- 丙 越权
r = api(P["丙"], "/api/set", {"room": ROOM,
                              "cells": [{"r": 2, "c": 3, "v": "篡改"}]})
ok("丙 覆盖他人隐私格被拒绝", r.get("ok") is False and "隐私" in (r.get("error") or ""), r)
time.sleep(1.5)
ok("原值未被改动", value(P["甲"], ROOM, "2,3") == "工资8000",
   cell_of(P["甲"], ROOM, "2,3"))
ok("丙 端仍是遮罩", hidden(P["丙"], ROOM, "2,3"))

# 丙 写自己的空格 → 允许
r = api(P["丙"], "/api/set", {"room": ROOM,
                              "cells": [{"r": 3, "c": 3, "v": "工资9000"}]})
ok("丙 填写自己的隐私格", r.get("ok"), r)
time.sleep(2.5)
ok("甲 看到丙填的", value(P["甲"], ROOM, "3,3") == "工资9000")
ok("乙 看不到丙的（***）", hidden(P["乙"], ROOM, "3,3"),
   cell_of(P["乙"], ROOM, "3,3"))
ok("丙 自己看得到", value(P["丙"], ROOM, "3,3") == "工资9000")

# 甲（发布者）可写任意隐私格
r = api(P["甲"], "/api/set", {"room": ROOM,
                              "cells": [{"r": 4, "c": 3, "v": "待定"}]})
time.sleep(2)
ok("发布者写隐私格", r.get("ok") and value(P["甲"], ROOM, "4,3") == "待定", r)
ok("乙 看不到发布者写的（***）", hidden(P["乙"], ROOM, "4,3"),
   cell_of(P["乙"], ROOM, "4,3"))
ok("但发布者自己看得到", value(P["甲"], ROOM, "4,3") == "待定")

# 非发布者不能改隐私设置
r = api(P["乙"], "/api/room/privacy", {"room": ROOM, "on": True, "cols": "D"})
ok("非发布者不能改隐私设置", r.get("ok") is False, r)

# ---------------------------------------------------------------- 关掉隐私
r = api(P["甲"], "/api/room/privacy", {"room": ROOM, "on": False})
ok("关闭隐私设置", r.get("ok"), r)
ok("丙 恢复可见真实值", wait_for(
    lambda: value(P["丙"], ROOM, "2,3") == "工资8000", 15),
   cell_of(P["丙"], ROOM, "2,3"))
ok("丙 不再遮罩", not hidden(P["丙"], ROOM, "2,3"))

# ---------------------------------------------------------------- 公开表格不受影响
api(P["甲"], "/api/new", {"room": "公开表"})
time.sleep(2)
api(P["甲"], "/api/set", {"room": "公开表",
                          "cells": [{"r": 1, "c": 1, "v": "公开内容"}]})
ok("公开表格照常全员可见", wait_for(
    lambda: (cell_of(P["乙"], "公开表", "1,1") or [None])[0] == "公开内容", 20),
   cell_of(P["乙"], "公开表", "1,1"))
c = cell_of(P["乙"], "公开表", "1,1")
ok("公开表格没有被遮罩", not (c and len(c) > 3 and c[3]), c)
ok("公开表格没有隐私设置",
   not (api(P["乙"], "/api/room/privacy?room=" + urllib.parse.quote("公开表"))
        .get("priv") or {}).get("on"))

# ---------------------------------------------------------------- 群内隐私表格
XLSX2 = os.path.join(ROOT, "工资表群.xlsx")
wb2 = openpyxl.Workbook()
ws2 = wb2.active
ws2.title = "工资表"
ws2.append(["姓名", "工号", "工资", "备注"])
ws2.append(["张三", "A001", None, ""])
ws2.append(["李四", "A002", None, ""])
wb2.save(XLSX2)
g = api(P["甲"], "/api/group/create", {"name": "工资群", "members": [ID["乙"]]})
gid = (g.get("group") or {}).get("id")
time.sleep(2)
info2 = api(P["甲"], "/api/chat/send",
            {"gid": gid, "kind": "local-xlsx", "path": XLSX2})
ok("群内发表格", info2.get("ok"), info2)
room2 = info2.get("room") or ""
r = api(P["甲"], "/api/room/privacy", {"room": room2, "on": True, "cols": "C"})
ok("群内表格可设隐私", r.get("ok"), r)
time.sleep(2.5)
r = api(P["乙"], "/api/set", {"room": room2,
                              "cells": [{"r": 2, "c": 3, "v": "群内工资"}]})
ok("群成员写入群内隐私格", r.get("ok"), r)
time.sleep(2.5)
ok("群内隐私：甲可见", value(P["甲"], room2, "2,3") == "群内工资")
d = api(P["丙"], "/api/poll?since=0&room=" + urllib.parse.quote(room2))
ok("群内表格对非群成员不可见", d.get("seq") == 0 or not (d.get("cells") or {}), 
   list((d.get("cells") or {}).keys())[:3])

# ---------------------------------------------------------------- 汇总
total = len(PASS)
good = sum(1 for _, c in PASS if c)
print("\n%d/%d 通过" % (good, total))
if good != total:
    print("失败项：" + ", ".join(n for n, c in PASS if not c))

for pr in procs:
    try:
        pr.send_signal(signal.SIGTERM)
    except Exception:
        pass
time.sleep(1)
for pr in procs:
    try:
        pr.kill()
    except Exception:
        pass
shutil.rmtree(ROOT, ignore_errors=True)
sys.exit(0 if good == total else 1)
