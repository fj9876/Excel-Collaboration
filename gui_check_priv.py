# -*- coding: utf-8 -*-
"""GUI 实测 v8：隐私表格的遮罩渲染 / 只读 / 状态条"""
import os
import shutil
import sys
import time

CFG = os.path.join(os.getcwd(), "_gui8")
shutil.rmtree(CFG, ignore_errors=True)
os.makedirs(CFG, exist_ok=True)
os.environ["LANEXCEL_CONFIG_DIR"] = CFG
sys.path.insert(0, os.getcwd())

import tkinter as tk
import openpyxl

import app as appmod
import privacy
from node import Node
from ui_compat import init_theme
from PIL import ImageGrab

ROOT = os.path.join(CFG, "files")
os.makedirs(ROOT, exist_ok=True)
XLSX = os.path.join(ROOT, "工资表.xlsx")
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "工资表"
ws.append(["姓名", "工号", "工资", "备注"])
for i, (n, no) in enumerate([("张三", "A001"), ("李四", "A002"),
                             ("王五", "A003"), ("赵六", "A004")]):
    ws.append([n, no, None, ""])
wb.save(XLSX)

root = tk.Tk()
init_theme(root)
node = Node("发布者-甲")
node.no_browser = True
node.start_services()
node.id = "AAAA0000"
app = appmod.LanExcelApp(root, node)
root.geometry("1240x780+40+30")
root.attributes("-topmost", True)
root.update()
while app._deferred:
    root.update()
    time.sleep(0.01)

# 打开表格 → 设为隐私列 C
info = node.open_xlsx(XLSX)
room = info["room"]
app.room_var.set(room)
app.switch_room()
app.poll_room()
root.update()

# 发布者设好隐私列后，切成「另一位填写者」的视角来看遮罩效果
node.set_privacy(room, rows=[], cols=[3])
room_obj = node.get_room(room)
room_obj.owner_id = "ZZZZ9999"        # 假装表格是别人发布的
for r, who, val in ((2, "BBBB1111", "工资8000"),
                    (3, "CCCC2222", "工资9100"),
                    (4, "DDDD3333", "工资7500")):
    node.apply_edit(room, {"%d,3" % r: [val, 1000 + r, who],
                           "%d,4" % r: ["已填", 1000 + r, who]},
                    local=False, from_peer=who)
    # 把真值换成网络传输时的占位（其它节点只拿到这个）
    node.apply_edit(room, {"%d,3" % r: ["", 1000 + r, who, 1]},
                    local=False, from_peer=who)

app._priv_rev = -1
app.since = 0
app.poll_room()
root.update()
time.sleep(0.5)
root.update()

print("隐私描述:", privacy.describe(node.get_room(room).privacy))
print("锁定格数:", len(app._locked))
print("状态条:", app.priv_label.cget("text"))
print("C2 显示:", app.grid_state.get((1, 2)), "| B2 显示:", app.grid_state.get((1, 1)))

# 截图：整窗
root.update()
x, y = root.winfo_rootx(), root.winfo_rooty()
w, h = root.winfo_width(), root.winfo_height()
ImageGrab.grab(bbox=(x, y, x + w, y + h)).save("shot_priv.png")

# 点一下遮罩格，确认只读提示
app.sheet.select(1, 2)
root.update()
app.sheet.start_edit()
root.update()
print("遮罩格编辑框:", app.sheet.editor)
print("提示:", app.info.get())

# 发布者视角：自己是 owner，应看到全部
print("甲能看到 C2 真值:", node.get_room(room).cells.get((2, 3)))

# 隐私设置对话框截图
dlg = app.show_privacy_dialog()
root.update()
time.sleep(0.4)
root.update()
dx, dy = dlg.winfo_rootx(), dlg.winfo_rooty()
dw, dh = dlg.winfo_width(), dlg.winfo_height()
ImageGrab.grab(bbox=(dx, dy, dx + dw, dy + dh)).save("shot_priv_dlg.png")
print("对话框大小:", dw, dh)

root.destroy()
print("GUI 检查完成")
