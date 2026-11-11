# -*- coding: utf-8 -*-
"""exe 实测：启动耗时 / 表情资源打包情况 / 截图"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request

EXE = sys.argv[1] if len(sys.argv) > 1 else "dist/LanExcel.exe"
CFG = os.path.abspath("_exe_test")
os.makedirs(CFG, exist_ok=True)
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))

env = dict(os.environ)
env["LANEXCEL_CONFIG_DIR"] = CFG
env.pop("LANEXCEL_WIN7_MODE", None)

before = set(os.listdir(os.environ.get("TEMP", ".")))
t0 = time.time()
proc = subprocess.Popen([os.path.abspath(EXE)], env=env, cwd=os.path.dirname(os.path.abspath(EXE)))
print("已启动 %s (pid %d)" % (os.path.basename(EXE), proc.pid))

port, ready = None, None
while time.time() - t0 < 40:
    if proc.poll() is not None:
        print("进程提前退出，返回码", proc.returncode)
        break
    for pt in range(53317, 53340):
        try:
            s = socket.create_connection(("127.0.0.1", pt), 0.15)
            s.close()
            d = json.load(op.open("http://127.0.0.1:%d/api/lanexcel/v2/info" % pt, timeout=1))
            if d.get("alias"):
                port, ready = pt, time.time() - t0
                break
        except Exception:
            pass
    if port:
        break
    time.sleep(0.1)

print("节点服务就绪: %.2f 秒 (端口 %s)" % (ready or -1, port))

# 找 _MEI 解压目录，数一下表情资源
time.sleep(1.0)
after = set(os.listdir(os.environ.get("TEMP", ".")))
meis = [d for d in after if d.startswith("_MEI")]
cnt = 0
for d in meis:
    p = os.path.join(os.environ["TEMP"], d, "assets", "emoji")
    if os.path.isdir(p):
        cnt = len([f for f in os.listdir(p) if f.endswith(".png")])
        print("表情资源: %s → %d 个 PNG" % (d, cnt))
        break
if not cnt:
    print("⚠ 未在解压目录找到表情资源")

time.sleep(2.5)
try:
    from PIL import ImageGrab
    ImageGrab.grab().save("shot_exe.png")
    print("桌面截图已保存 shot_exe.png")
except Exception as e:
    print("截图失败:", e)

proc.terminate()
try:
    proc.wait(timeout=6)
except Exception:
    proc.kill()
print("已结束进程")
