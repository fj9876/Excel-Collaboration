# -*- coding: utf-8 -*-
"""GUI 实测：彩色表情 / 多标签分组 / 截图验证"""
import os, sys, time, shutil

CFG = os.path.join(os.getcwd(), "_gui")
shutil.rmtree(CFG, ignore_errors=True)
os.makedirs(CFG, exist_ok=True)
os.environ["LANEXCEL_CONFIG_DIR"] = CFG
sys.path.insert(0, os.getcwd())

import tkinter as tk
import app as appmod
from node import Node
from ui_compat import init_theme
from PIL import ImageGrab

root = tk.Tk()
init_theme(root)
node = Node("生产部-小王")
node.no_browser = True
node.start_services()
app = appmod.LanExcelApp(root, node)
root.geometry("1280x800+40+40")
root.attributes("-topmost", True)
root.update()
while app._deferred:
    root.update()
    time.sleep(0.01)

# 造联系人（含多标签，验证重复 iid 不再崩）
names = ["张工", "李经理", "王会计", "赵主管"]
with node.peers_lock:
    for i, n in enumerate(names):
        pid = "peer%04d" % i
        node.peers[pid] = {"id": pid, "name": n, "note": "备注" + n,
                           "ip": "192.168.1.%d" % (10 + i), "tcp": 53318,
                           "http": 53317, "file_port": 53319, "avatar": "",
                           "avatar_data": "", "source": "multicast",
                           "last_seen": time.time(), "ts": time.time()}
node.cfg["peer_tags"] = {"peer0000": ["重要", "同事"], "peer0001": ["重要"],
                         "peer0002": ["财务"]}
node.cfg["pinned_peers"] = ["peer0000"]
app.refresh_peers(force=True)
root.update()
print("多标签分组渲染 OK，行数:", len(app.peer_tree.get_children()))

# 群 + 消息
g = node.create_group("生产协作群")
gid = g["id"]
node.invite_members(gid, ["peer0000", "peer0001", "peer0002"])
for i, t in enumerate([
        "大家好 😀 今天的产品试作单已经发到群里了，请查收",
        "收到 👍 我看一下表格", "😍🎉 这个方案不错，辛苦了",
        "❤️💪 那我们下午三点对一下进度",
        "明白了 🙏 有问题随时说"]):
    node._add_message(gid, "text", from_id="peer%04d" % (i % 3),
                      from_name=names[i % 3], text=t)

app.current_gid = gid
app.show_page("chat")
app.root.update()
time.sleep(0.4)
root.update()
x, y = root.winfo_rootx(), root.winfo_rooty()
w, h = root.winfo_width(), root.winfo_height()
ImageGrab.grab((x, y, x + w, y + h)).save("shot_chat.png")
print("聊天截图已保存")

app.show_emoji_picker()
for _ in range(6):
    root.update()
    time.sleep(0.05)
ImageGrab.grab().save("shot_emoji.png")
print("表情面板截图已保存")

app.show_page("peers")
root.update()
time.sleep(0.3)
root.update()
ImageGrab.grab((x, y, x + w, y + h)).save("shot_peers.png")
print("成员页截图已保存")

app.on_close()
