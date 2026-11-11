# -*- coding: utf-8 -*-
"""LanExcel v9 发布机制 E2E 测试（子进程三节点）

甲 = 发布者 / 乙 = 同事 / 丙 = 另一位同事
验证：打开本地表格先成草稿（同事看不到、改不了）；
      公开表格点发布后才可见可编辑；
      隐私表格设置完再发布，发布后隐私规则同时生效；
      放弃草稿后本机也不再保留；同一工作簿多工作表一起发布。
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

ROOT = os.path.join(BASE, "_test_v9")
shutil.rmtree(ROOT, ignore_errors=True)
DIR = {k: os.path.join(ROOT, k) for k in ("A", "B", "C")}
for d in DIR.values():
    os.makedirs(d, exist_ok=True)

import openpyxl

# 两张表的工作簿：验证「一起发布」
XLSX = os.path.join(ROOT, "考勤表.xlsx")
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "一月"
ws.append(["姓名", "工号", "工资", "备注"])
ws.append(["张三", "A001", None, ""])
ws.append(["李四", "A002", None, ""])
ws2 = wb.create_sheet("二月")
ws2.append(["姓名", "工号", "工资", "备注"])
ws2.append(["张三", "A001", None, ""])
wb.save(XLSX)

# 公开表格用的工作簿
XLSX2 = os.path.join(ROOT, "公开表.xlsx")
wb2 = openpyxl.Workbook()
w2 = wb2.active
w2.title = "Sheet1"
w2.append(["项目", "负责人"])
w2.append(["内网部署", ""])
wb2.save(XLSX2)

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


def poll(port, room):
    return api(port, "/api/poll?since=0&room=" + urllib.parse.quote(room))


def cell_of(port, room, key):
    return (poll(port, room).get("cells") or {}).get(key)


def value(port, room, key):
    c = cell_of(port, room, key)
    return None if c is None else c[0]


def hidden(port, room, key):
    c = cell_of(port, room, key)
    return bool(c and len(c) > 3 and c[3])


def peer_has_room(port, room):
    return any(r["name"] == room for r in api(port, "/api/state").get("rooms", []))


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
ok("三节点启动", len(P) >= 3, ports)
if len(P) < 3:
    print("节点不足，退出")
    for pr in procs:
        pr.terminate()
    sys.exit(1)

api(P["甲"], "/api/scan", {})
time.sleep(3)

# ------------------------------------------------- 1. 打开表格 = 草稿（未发布）
info = api(P["甲"], "/api/open", {"path": XLSX, "publish": False})
ok("打开表格成功（草稿）", info.get("ok") and info.get("published") is False, info)
ROOM = info.get("room") or "考勤表"
ROOM2 = ROOM + "::二月"
time.sleep(3)
ok("草稿未出现在同伴房间列表（乙）", not peer_has_room(P["乙"], ROOM),
   [r["name"] for r in api(P["乙"], "/api/state").get("rooms", [])])
ok("草稿未出现在同伴房间列表（丙）", not peer_has_room(P["丙"], ROOM))
ok("草稿对同伴不可读（乙 poll 为空）",
   not (poll(P["乙"], ROOM).get("cells") or {}), poll(P["乙"], ROOM).get("cells"))

r = api(P["乙"], "/api/set", {"room": ROOM,
                              "cells": [{"r": 2, "c": 1, "v": "偷偷改"}]})
ok("草稿拒绝同伴写入", r.get("ok") is False, r)

# 发布者本机可以照常编辑草稿
r = api(P["甲"], "/api/set", {"room": ROOM,
                              "cells": [{"r": 1, "c": 4, "v": "发布前本机修改"}]})
ok("发布者可在草稿上编辑", r.get("ok"), r)
time.sleep(1)
ok("草稿内容留在本机", value(P["甲"], ROOM, "1,4") == "发布前本机修改")

# ------------------------------------------------- 2. 隐私表格：设置完再发布
r = api(P["甲"], "/api/room/publish",
        {"room": ROOM, "on": True, "cols": "C", "rows": ""})
ok("发布隐私表格（含隐私设置）", r.get("ok") and r.get("published"), r)
ok("同一工作簿多表一起发布", len(r.get("rooms") or []) >= 2, r.get("rooms"))
ok("隐私描述正确", "隐私表格" in (r.get("desc") or ""), r.get("desc"))

ok("乙 看到发布后的房间", wait_for(lambda: peer_has_room(P["乙"], ROOM), 15))
ok("丙 看到发布后的房间", wait_for(lambda: peer_has_room(P["丙"], ROOM), 15))
ok("第二个工作表也一起可见", wait_for(lambda: peer_has_room(P["乙"], ROOM2), 15))
ok("乙 收到隐私规则", wait_for(
    lambda: (api(P["乙"], "/api/room/privacy?room=" + urllib.parse.quote(ROOM))
             .get("priv") or {}).get("rev", 0) > 0, 15))
ok("发布前的本机修改同步给乙", wait_for(
    lambda: value(P["乙"], ROOM, "1,4") == "发布前本机修改", 15),
   cell_of(P["乙"], ROOM, "1,4"))

# 乙 填写隐私格 + 公开格
r = api(P["乙"], "/api/set", {"room": ROOM,
                              "cells": [{"r": 2, "c": 3, "v": "工资8000"},
                                        {"r": 2, "c": 4, "v": "乙的备注"}]})
ok("发布后乙可写入", r.get("ok"), r)
time.sleep(2.5)
ok("甲（发布者）看到乙填的", value(P["甲"], ROOM, "2,3") == "工资8000")
ok("丙 看到 ***（隐私生效）", hidden(P["丙"], ROOM, "2,3"),
   cell_of(P["丙"], ROOM, "2,3"))
ok("公开列丙能看到", value(P["丙"], ROOM, "2,4") == "乙的备注")

# ------------------------------------------------- 3. 公开表格：点发布才共享
info2 = api(P["甲"], "/api/open", {"path": XLSX2, "publish": False})
ROOM_PUB = info2.get("room") or "公开表"
time.sleep(2.5)
ok("公开表草稿对乙不可见", not peer_has_room(P["乙"], ROOM_PUB))
r = api(P["甲"], "/api/room/publish", {"room": ROOM_PUB})
ok("公开表发布成功", r.get("ok") and r.get("published"), r)
ok("公开表发布后乙可见", wait_for(lambda: peer_has_room(P["乙"], ROOM_PUB), 15))
time.sleep(1.5)
r = api(P["乙"], "/api/set", {"room": ROOM_PUB,
                              "cells": [{"r": 2, "c": 2, "v": "乙负责"}]})
ok("公开表发布后乙可编辑", r.get("ok"), r)
time.sleep(2)
ok("甲 看到乙填的公开内容", value(P["甲"], ROOM_PUB, "2,2") == "乙负责",
   cell_of(P["甲"], ROOM_PUB, "2,2"))
ok("公开表没有被遮罩", not hidden(P["甲"], ROOM_PUB, "2,2"))
ok("公开表没有隐私规则",
   not (api(P["甲"], "/api/room/privacy?room=" + urllib.parse.quote(ROOM_PUB))
        .get("priv") or {}).get("on"))

# ------------------------------------------------- 4. 放弃草稿
info3 = api(P["甲"], "/api/open", {"path": XLSX2, "publish": False,
                                   "room": "丢弃测试"})
ROOM_D = info3.get("room") or "丢弃测试"
time.sleep(1.5)
ok("草稿存在于本机", peer_has_room(P["甲"], ROOM_D))
r = api(P["甲"], "/api/room/discard", {"room": ROOM_D})
ok("放弃草稿成功", r.get("ok"), r)
time.sleep(1)
ok("放弃后本机也不再保留", not peer_has_room(P["甲"], ROOM_D))
ok("放弃草稿不影响同伴（本就没有）", not peer_has_room(P["乙"], ROOM_D))

# ------------------------------------------------- 5. 已发布的表格仍是发布状态
st = api(P["甲"], "/api/room/privacy?room=" + urllib.parse.quote(ROOM))
ok("已发布表格状态正确", st.get("published") is True, st)
r = api(P["乙"], "/api/room/publish", {"room": ROOM})
ok("非发布者不能发布", r.get("ok") is False, r)

# ------------------------------------------------- 6. 公开表格回归：默认立即发布
api(P["甲"], "/api/new", {"room": "老式房间"})
time.sleep(1)
api(P["甲"], "/api/set", {"room": "老式房间",
                          "cells": [{"r": 1, "c": 1, "v": "老逻辑"}]})
ok("新建房间仍立即共享（原有逻辑不变）", wait_for(
    lambda: (cell_of(P["乙"], "老式房间", "1,1") or [None])[0] == "老逻辑", 20),
   cell_of(P["乙"], "老式房间", "1,1"))

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
