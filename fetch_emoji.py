# -*- coding: utf-8 -*-
"""下载 Twemoji 彩色表情 PNG（CC-BY 4.0）到 assets/emoji/，并生成 manifest.json"""
import json
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from emojis import EMOJI_GROUPS

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "emoji")
os.makedirs(OUT, exist_ok=True)
CDN = "https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/%s.png"
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def codes(ch):
    return [ord(c) for c in ch]


def variants(ch):
    """候选文件名：先去 FE0F，再保留原样，再只取首码点"""
    cs = codes(ch)
    out = []
    no_vs = [c for c in cs if c != 0xFE0F]
    if no_vs:
        out.append("-".join("%x" % c for c in no_vs))
    raw = "-".join("%x" % c for c in cs)
    if raw not in out:
        out.append(raw)
    if len(no_vs) > 1:
        out.append("%x" % no_vs[0])
    return out


def canon(ch):
    """规范文件名（去掉 FE0F）"""
    cs = [c for c in codes(ch) if c != 0xFE0F]
    return "-".join("%x" % c for c in cs)


seen, chars = set(), []
for _, group in EMOJI_GROUPS:
    for ch in group:
        if ch not in seen:
            seen.add(ch)
            chars.append(ch)

print("待下载表情数：%d" % len(chars))


def fetch(ch):
    name = canon(ch)
    path = os.path.join(OUT, name + ".png")
    if os.path.isfile(path) and os.path.getsize(path) > 200:
        return ch, name, True
    for v in variants(ch):
        try:
            r = op.open(CDN % v, timeout=12)
            data = r.read()
            if len(data) > 200:
                with open(path, "wb") as f:
                    f.write(data)
                return ch, name, True
        except Exception:
            continue
    return ch, name, False


ok, bad = {}, []
with ThreadPoolExecutor(max_workers=12) as ex:
    for ch, name, good in ex.map(fetch, chars):
        if good:
            ok[ch] = name
        else:
            bad.append(ch)

with open(os.path.join(OUT, "manifest.json"), "w", encoding="utf-8") as f:
    json.dump(ok, f, ensure_ascii=False, indent=0, sort_keys=True)

total = sum(os.path.getsize(os.path.join(OUT, n + ".png"))
            for n in set(ok.values()))
print("成功 %d / %d，失败：%s" % (len(ok), len(chars), " ".join(bad) or "无"))
print("资源总大小：%.0f KB" % (total / 1024.0))
