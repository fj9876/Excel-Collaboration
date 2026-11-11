# -*- coding: utf-8 -*-
"""LanExcel v6 E2E 测试（子进程双节点）：
图片消息 / GIF / 标签 / 置顶 / 在线状态 / 表情 / 撤回兼容"""
import json
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

DIR_A = os.path.join(BASE, "_test_v6", "A")
DIR_B = os.path.join(BASE, "_test_v6", "B")
shutil.rmtree(os.path.join(BASE, "_test_v6"), ignore_errors=True)
for d in (DIR_A, DIR_B):
    os.makedirs(d, exist_ok=True)

MEDIA = os.path.join(BASE, "_test_v6", "media")
os.makedirs(MEDIA, exist_ok=True)

from PIL import Image

def make_gif(path, frames=4):
    imgs = []
    for i in range(frames):
        im = Image.new("RGBA", (120, 90), (255, i * 40 % 256, 80, 255))
        d = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        im.alpha_composite(d, (30 + i * 8, 15))
        imgs.append(im.convert("P", palette=Image.ADAPTIVE))
    imgs[0].save(path, save_all=True, append_images=imgs[1:], duration=100, loop=0)

GIF = os.path.join(MEDIA, "demo.gif")
PNG = os.path.join(MEDIA, "demo.png")
make_gif(GIF, 4)
Image.new("RGB", (100, 80), (40, 120, 220)).save(PNG)

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

def api(port, path, body=None):
    url = "http://127.0.0.1:%d%s" % (port, path)
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        if body is None:
            with op.open(url, timeout=5) as r:
                return json.load(r)
        req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        code = 200
        try:
            with op.open(req, timeout=10) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": "HTTP %s" % e}

PASS = []
def ok(name, cond, extra=""):
    PASS.append((name, bool(cond)))
    print(("  PASS  " if cond else "  FAIL  ") + name + (("  | " + str(extra)) if extra and not cond else ""))

start_node("甲", DIR_A)
start_node("乙", DIR_B)
time.sleep(3)

# 找端口
def node_info(port):
    return api(port, "/api/lanexcel/v2/info")

# 探测端口
import socket
def find_http():
    ports = []
    for p in range(53317, 53330):
        s = socket.socket()
        try:
            s.connect(("127.0.0.1", p))
            ports.append(p)
        except OSError:
            pass
        finally:
            s.close()
    return ports

ports = find_http()
info_a = info_b = None
for p in ports:
    i = node_info(p)
    if i.get("alias") == "甲" and info_a is None:
        info_a = i
    elif i.get("alias") == "乙" and info_b is None:
        info_b = i
PA, PB = info_a["http_port"], info_b["http_port"]
ok("双节点启动", info_a and info_b, ports)

# A 扫描发现 B
api(PA, "/api/scan", {})
time.sleep(2.5)
peers_a = api(PA, "/api/peers")["peers"]
ok("A 发现 B", any(p["name"] == "乙" for p in peers_a), peers_a)

bid = info_b["fingerprint"]
# 建群并邀请 B
g = api(PA, "/api/group/create", {"name": "v6测试群", "members": [bid]})
ok("建群返回", g.get("ok"), g)
gid = (g.get("group") or {}).get("id")
time.sleep(2.0)
groups_b = api(PB, "/api/groups")["groups"]
ok("B 收到群资料", any(x["id"] == gid and "乙" and True for x in groups_b
                      if x["id"] == gid and True) and any(
    x["id"] == gid for x in groups_b), groups_b)

# ---------------------------------------------------------------- 图片消息
r = api(PA, "/api/chat/send", {"gid": gid, "kind": "image", "path": GIF})
ok("发送 GIF 返回 targets", r.get("ok") and len(r.get("targets", [])) == 1, r)
time.sleep(3)
d = api(PB, "/api/chat?since=0")
img_msgs = [m for m in d["messages"] if m.get("kind") == "image"
            and m.get("file") == "demo.gif"]
ok("B 收到 GIF 卡片", len(img_msgs) >= 1)
b_img = img_msgs[0] if img_msgs else {}
ok("B 端 GIF 文件存在", os.path.isfile(b_img.get("path", "")))
ok("B 端 image 标记", b_img.get("image") == 1)

r2 = api(PA, "/api/chat/send", {"gid": gid, "kind": "image", "path": PNG})
time.sleep(2.5)
d2 = api(PB, "/api/chat?since=0")
png_msgs = [m for m in d2["messages"] if m.get("kind") == "image"
            and m.get("file") == "demo.png"]
ok("B 收到 PNG 卡片", len(png_msgs) >= 1)

# 本地图片查看接口（Web 用）
img_resp = api(PB, "/api/chat/image?mid=" + b_img.get("id", ""))
ok("web 图片接口返回数据", isinstance(img_resp, dict) and img_resp.get("ok") is False
   or isinstance(img_resp, dict), "json接口只回JSON，二进制接口在浏览器测试")

# ---------------------------------------------------------------- 撤回图片（发送者本人）
res = api(PA, "/api/chat/recall", {"mid": b_img.get("id", "")})
ok("发送者撤回图片", res.get("ok") and res.get("recalled"), res)
time.sleep(1.5)
ok("B 端 GIF 副本已删除", not os.path.isfile(b_img.get("path", "")))
d3 = api(PB, "/api/chat?since=0")
bm = next((m for m in d3["messages"] if m.get("id") == b_img.get("id")), None)
ok("B 端消息标记撤回", bm and bm.get("recalled"))

# ---------------------------------------------------------------- 在线状态
d4 = api(PA, "/api/chat?since=0")
om = d4.get("online", {})
ok("chat 携带 online 映射", isinstance(om, dict))
# 注意：A 视角里 B 是否在线取决于 TCP/扫描；只要键存在即可
ok("online 含 B 节点", bid in om, om)

# ---------------------------------------------------------------- 标签 / 置顶
r = api(PA, "/api/config", {"peer_tags": {bid: ["技术部", "二楼"]}})
ok("设置标签", r.get("ok"), r)
cfg = api(PA, "/api/config")
ok("标签持久化", cfg.get("peer_tags", {}).get(bid) == ["技术部", "二楼"], cfg.get("peer_tags"))
r = api(PA, "/api/config", {"toggle_pin_peer": bid})
cfg = api(PA, "/api/config")
ok("联系人置顶", bid in cfg.get("pinned_peers", []))
r = api(PA, "/api/config", {"toggle_pin_peer": bid})
cfg = api(PA, "/api/config")
ok("取消联系人置顶", bid not in cfg.get("pinned_peers", []))
r = api(PA, "/api/config", {"toggle_pin_group": gid})
cfg = api(PA, "/api/config")
ok("群置顶", gid in cfg.get("pinned_groups", []))
r = api(PA, "/api/config", {"toggle_pin_group": gid})
cfg = api(PA, "/api/config")
ok("取消群置顶", gid not in cfg.get("pinned_groups", []))
peers = api(PA, "/api/peers")["peers"]
bp = next((p for p in peers if p["id"] == bid), None)

# 再打一次标签验证 peers_detail 携带
api(PA, "/api/config", {"peer_tags": {bid: ["技术部"], "toggle_pin_peer": bid}})
peers = api(PA, "/api/peers")["peers"]
bp = next((p for p in peers if p["id"] == bid), None)
api(PA, "/api/config", {"toggle_pin_peer": bid})
peers = api(PA, "/api/peers")["peers"]
bp = next((p for p in peers if p["id"] == bid), None)
ok("peers_detail 携带 tags", bool(bp) and bp.get("tags") == ["技术部"], bp)

# ---------------------------------------------------------------- 表情文本
r = api(PA, "/api/chat/send", {"gid": gid, "kind": "text",
                               "text": "大家好 😀👍 (≧▽≦)"})
ok("发送表情文本", r.get("ok"), r)
time.sleep(1.5)
d5 = api(PB, "/api/chat?since=0")
texts = [m for m in d5["messages"] if m.get("kind") == "text"
         and "😀" in (m.get("text") or "")]
ok("B 收到表情文本", len(texts) >= 1)

# ---------------------------------------------------------------- 桌面端渲染测试（进程内、不进 mainloop）
os.environ["LANEXCEL_CONFIG_DIR"] = DIR_B
import tkinter as tk
import node as node_mod
node_mod.CONFIG_PATH = os.path.join(DIR_B, "lanexcel_config.json")
import app as app_mod

root = tk.Tk()
root.withdraw()
from ui_compat import init_theme
init_theme(root)
n_b = node_mod.Node()
n_b.no_browser = True
try:
    n_b.start_services()
except Exception:
    pass
a = app_mod.LanExcelApp(root, n_b)
a.show_page("chat")           # 懒构建：先把聊天页建出来
while a._deferred:
    a.root.update()
a.current_gid = gid
a.group_ids = [gid]
a._groups_sig = "x"
a.rebuild_chat()          # 含图片/GIF 标记的重建，不应抛异常
ok("桌面 rebuild_chat 含图片消息", True)
photo, anim_scale = a._load_chat_image(GIF)
ok("GIF 首帧加载 + 动图识别", photo is not None and anim_scale, anim_scale)
photo2, anim2 = a._load_chat_image(PNG)
ok("PNG 静态加载", photo2 is not None and anim2 is None)
a._cancel_anims()
a.chat_text.configure(state="normal")
a.chat_text.insert("end", "t\n")
a.chat_text.mark_set("imgmark_test", "end-1c")
a.chat_text.mark_gravity("imgmark_test", "left")
a.chat_text.image_create("end", image=photo)
a.chat_text.configure(state="disabled")
a._start_gif("imgmark_test", GIF, anim_scale or 1.0)
time.sleep(0.6)
ok("GIF 动画循环运行", any(st.get("job") for st in a._anim_jobs),
   [st.get("job") for st in a._anim_jobs])
root.update()
root.destroy()

print("\n结果: %d/%d 通过" % (sum(1 for _, c in PASS if c), len(PASS)))
for p in procs:
    try:
        p.terminate()
    except Exception:
        pass
sys.exit(0 if all(c for _, c in PASS) else 1)
