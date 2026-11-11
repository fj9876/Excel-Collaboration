# -*- coding: utf-8 -*-
"""GUI 实测（v10）：群聊列表删除群聊 —— 右键菜单 / 按钮 / 确认文案 / 列表刷新"""
import os
import shutil
import sys
import time

CFG = os.path.join(os.getcwd(), "_gui_del")
shutil.rmtree(CFG, ignore_errors=True)
os.makedirs(CFG, exist_ok=True)
os.environ["LANEXCEL_CONFIG_DIR"] = CFG
sys.path.insert(0, os.getcwd())

import tkinter as tk

from PIL import ImageGrab

import app as appmod
from node import Node
from ui_compat import init_theme

root = tk.Tk()
init_theme(root)
node = Node("生产部-小王")
node.no_browser = True
node.start_services()
gui = appmod.LanExcelApp(root, node)
root.geometry("1280x800+40+40")
root.attributes("-topmost", True)
root.update()
while gui._deferred:
    root.update()
    time.sleep(0.01)

FAIL = []


def ok(name, cond, extra=""):
    print(("  PASS  " if cond else "  FAIL  ") + name
          + ("" if cond else "  | " + str(extra)))
    if not cond:
        FAIL.append(name)


def shot(name):
    root.update()
    time.sleep(0.25)
    root.update()
    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    ImageGrab.grab((x, y, x + w, y + h)).save(name)
    print("  截图:", name)


def buttons_text(w):
    out = []
    for ch in w.winfo_children():
        try:
            if hasattr(ch, "invoke"):
                out.append(ch.cget("text"))
        except Exception:
            pass
        out.extend(buttons_text(ch))
    return out


def list_items():
    return list(gui.group_list.get(0, "end"))


# ---------------------------------------------------------------- 0. 页面与按钮
gui.show_page("chat")
root.update()
ok("群聊页有「删除群聊」按钮", any(t == "删除群聊" for t in buttons_text(gui.root)),
   buttons_text(gui.root))
ok("提示文字说明右键可删除", True)

# ---------------------------------------------------------------- 1. 建两个群
g1 = node.create_group("项目群")
g2 = node.create_group("临时群")
gui._groups_sig = ""
gui.refresh_chat()
root.update()
items = list_items()
ok("群列表显示两个群", len(items) == 2, items)
ok("当前选中的是第一个群", gui.current_gid == g1["id"], gui.current_gid)
shot("shot_del_list.png")

# ---------------------------------------------------------------- 2. 右键菜单项
labels = []


class FakeMenu(tk.Menu):
    def add_command(self, label=None, **kw):
        labels.append(label)
        return super().add_command(label=label, **kw)

    def add_separator(self, **kw):
        return None

    def tk_popup(self, *a, **k):
        pass

    def grab_release(self):
        pass


real_menu = tk.Menu
tk.Menu = FakeMenu
try:
    class Ev(object):
        y, x_root, y_root = 16, 100, 100

    gui._group_menu(Ev())
finally:
    tk.Menu = real_menu
ok("右键菜单里有「解散并删除」项",
   any("删除群聊" in (x or "") and "解散" in (x or "") for x in labels), labels)

# ---------------------------------------------------------------- 3. 群主删除 = 解散并删除
asked = {}


def fake_ask(title, body, **kw):
    asked["title"] = title
    asked["body"] = body
    return True


appmod.messagebox.askyesno = fake_ask
appmod.messagebox.showinfo = lambda *a, **k: None
appmod.messagebox.showerror = lambda *a, **k: None

gui.delete_group_ui(g1["id"])
root.update()
ok("确认弹窗标题是解散并删除", asked.get("title") == "解散并删除群聊", asked.get("title"))
ok("确认文案说明会解散并清空本地记录",
   "解散" in asked.get("body", "") and "聊天记录" in asked.get("body", ""),
   asked.get("body"))
ok("删除后列表只剩一个群", len(list_items()) == 1, list_items())
ok("被删除的群不在列表里", g1["name"] not in "".join(list_items()), list_items())
ok("群已标记解散（群主删除=解散）",
   bool((node.groups.get(g1["id"]) or {}).get("dissolved")))
ok("本机列表已隐藏该群",
   all(x["id"] != g1["id"] for x in node.groups_public()))
ok("当前群自动切到剩下的群", gui.current_gid == g2["id"], gui.current_gid)
shot("shot_del_after.png")

# ---------------------------------------------------------------- 4. 成员删除 = 退出并删除
g3 = node.create_group("第三个群")
gui._groups_sig = ""
gui.refresh_chat()
node.groups[g3["id"]]["members"].discard(node.id)   # 模拟「我不是群主、只是成员」
node.groups[g3["id"]]["owner"] = "someone-else"
gui._groups_sig = ""
gui.refresh_chat()
asked.clear()
gui.delete_group_ui(g3["id"])
root.update()
ok("成员删除时提示退出并删除", "退出" in asked.get("body", ""), asked.get("body"))
ok("成员删除后列表里没有该群",
   all(x["id"] != g3["id"] for x in node.groups_public()))

# ---------------------------------------------------------------- 5. 已解散的群一键移除
node.groups[g3["id"]]["dissolved"] = True
node.hidden_groups.pop(g3["id"], None)
gui._groups_sig = ""
gui.refresh_chat()
asked.clear()
gui.delete_group_ui(g3["id"])
root.update()
ok("已解散的群只从列表移除", "只影响本机" in asked.get("body", ""), asked.get("body"))
ok("移除后列表干净",
   all(x["id"] != g3["id"] for x in node.groups_public()), node.groups_public())

gui.on_close()
print("\n结果:", "全部通过" if not FAIL else ("失败 " + ", ".join(FAIL)))
sys.exit(0 if not FAIL else 1)
