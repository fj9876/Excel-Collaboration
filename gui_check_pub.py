# -*- coding: utf-8 -*-
"""GUI 实测（v9）：打开表格先选公开/隐私 + 发布条 + 发布动作"""
import os
import shutil
import sys
import time

CFG = os.path.join(os.getcwd(), "_gui_pub")
shutil.rmtree(CFG, ignore_errors=True)
os.makedirs(CFG, exist_ok=True)
os.environ["LANEXCEL_CONFIG_DIR"] = CFG
sys.path.insert(0, os.getcwd())

import tkinter as tk

import openpyxl
from PIL import ImageGrab

import app as appmod
from node import Node
from ui_compat import init_theme

XLSX = os.path.join(CFG, "报价单.xlsx")
wb = openpyxl.Workbook()
ws = wb.active
ws.title = "报价单"
ws.append(["产品", "数量", "单价", "合计"])
ws.append(["A 型", 10, 25.5, None])
ws.append(["B 型", 5, 88, None])
ws.append(["C 型", 2, 199, None])
ws2 = wb.create_sheet("汇总")
ws2.append(["合计", None])
wb.save(XLSX)

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


def find_buttons(w):
    out = []
    for ch in w.winfo_children():
        if hasattr(ch, "invoke") and hasattr(ch, "cget"):
            out.append(ch)
        out.extend(find_buttons(ch))
    return out


# ---------------------------------------------------------------- 1. 选择类型弹窗
def grab_kind_dialog():
    for w in root.winfo_children():
        if isinstance(w, tk.Toplevel) and "协作表格" in w.title():
            x, y = w.winfo_rootx(), w.winfo_rooty()
            ImageGrab.grab((x - 6, y - 30, x + w.winfo_width() + 6,
                            y + w.winfo_height() + 6)).save("shot_kind.png")
            print("  截图: shot_kind.png")
            for btn in find_buttons(w):
                try:
                    if btn.cget("text") == "确定":
                        btn.invoke()
                        return
                except Exception:
                    pass
            w.destroy()


root.after(900, grab_kind_dialog)
kind = gui.ask_table_kind("报价单.xlsx")
ok("类型弹窗可返回（默认公开）", kind in ("public", "private"), kind)

# ---------------------------------------------------------------- 2. 打开 = 草稿
info = node.open_xlsx(XLSX)          # 默认 publish=False → 草稿
gui._rooms_sig = ""
gui.room_var.set(info["room"])
gui.switch_room()
gui._pub_key = None
gui._update_pub_bar()
shot("shot_pub_draft.png")
ok("草稿状态：本机房间存在", info["room"] in node.rooms)
ok("草稿 state.published=False",
   node.room_privacy(info["room"]).get("published") is False)
ok("草稿发布条已显示", gui.pub_bar.winfo_ismapped())
ok("草稿提示含「未发布」", "未发布" in gui.pub_label.cget("text"),
   gui.pub_label.cget("text"))
ok("第二工作表也是草稿",
   node.room_privacy(info["room"] + "::汇总").get("published") is False)

# ---------------------------------------------------------------- 3. 设为隐私再发布
r = node.publish_room(info["room"], True, "", "C")   # 隐私列 C
ok("隐私发布成功", r.get("published") is True, r)
ok("同一工作簿一起发布", len(r.get("rooms") or []) >= 2, r.get("rooms"))
ok("隐私规则生效", (node.room_privacy(info["room"]).get("priv") or {}).get("on") == 1)
gui._pub_key = None
gui._update_pub_bar()
root.update()
ok("发布后发布条自动隐藏", not gui.pub_bar.winfo_ismapped())
shot("shot_pub_done.png")

# ---------------------------------------------------------------- 4. 公开草稿 → 点发布
info2 = node.open_xlsx(XLSX, room_name="公开草稿")
gui._rooms_sig = ""
gui.room_var.set(info2["room"])
gui.switch_room()
gui._pub_key = None
gui._update_pub_bar()
root.update()
ok("公开草稿发布条显示", gui.pub_bar.winfo_ismapped())
ok("发布条按钮可用", gui.pub_go.cget("text") == "立即发布")

appmod.messagebox.askyesno = lambda *a, **k: True
appmod.messagebox.showinfo = lambda *a, **k: None
appmod.messagebox.showwarning = lambda *a, **k: None
ok("点发布返回成功", gui.publish_current() is True)
ok("发布后状态为已发布",
   node.room_privacy(info2["room"]).get("published") is True)
ok("发布后发布条隐藏", not gui.pub_bar.winfo_ismapped())

# ---------------------------------------------------------------- 5. 放弃草稿
info3 = node.open_xlsx(XLSX, room_name="要放弃的草稿")
gui.room_var.set(info3["room"])
gui.switch_room()
ok("放弃前存在", info3["room"] in node.rooms)
gui.discard_current()
ok("放弃后本机已移除", info3["room"] not in node.rooms)

gui.on_close()
print("\n结果:", "全部通过" if not FAIL else ("失败 " + ", ".join(FAIL)))
sys.exit(0 if not FAIL else 1)
