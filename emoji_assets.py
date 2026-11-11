# -*- coding: utf-8 -*-
"""
LanExcel 彩色表情资源
=====================
Tk 的文本渲染走 GDI，不支持彩色字体（COLR），所以 Emoji 字符会显示成
黑白轮廓。这里把 Emoji 换成 Twemoji 彩色 PNG（CC-BY 4.0），按需要缩放成
PhotoImage 并在多个控件间复用。

* 资源目录：``assets/emoji/<码点>.png`` + ``manifest.json``（字符 → 文件名）
* 打包后从 ``sys._MEIPASS`` 读取，源码运行时从项目目录读取
* 没有 Pillow 时退回 ``PhotoImage.subsample`` 整数缩小
"""

import json
import os
import sys

_manifest = None
_image_cache = {}          # (ch, px) -> PhotoImage
_text_cache = {}           # 已解析的文本片段（供聊天渲染复用）


def asset_dir():
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "assets", "emoji")


def manifest():
    """字符 → 文件名（不含扩展名）；资源缺失时返回空字典，自动退回文字渲染"""
    global _manifest
    if _manifest is None:
        try:
            with open(os.path.join(asset_dir(), "manifest.json"),
                      "r", encoding="utf-8") as f:
                _manifest = json.load(f)
        except Exception:
            _manifest = {}
    return _manifest


def has_emoji(ch):
    return ch in manifest()


def png_path(ch):
    name = manifest().get(ch)
    if not name:
        return ""
    p = os.path.join(asset_dir(), name + ".png")
    return p if os.path.isfile(p) else ""


class EmojiImages:
    """按像素尺寸缓存的彩色表情图片工厂（必须传入 Tk 组件作为 master）"""

    def __init__(self, master):
        self.master = master
        self._by_size = {}       # px -> {ch: PhotoImage}
        self._pil = None
        try:
            from PIL import Image, ImageTk       # noqa: F401
            self._pil = True
        except Exception:
            self._pil = False

    def _load_png(self, path, px):
        """读取并缩放到 px，返回 PhotoImage；失败返回 None"""
        if self._pil:
            try:
                from PIL import Image, ImageTk
                with Image.open(path) as im:
                    im = im.convert("RGBA")
                    side = max(im.size)
                    if side != px:
                        im = im.resize((px, px), Image.LANCZOS)
                    return ImageTk.PhotoImage(im, master=self.master)
            except Exception:
                pass
        try:
            import tkinter as tk
            img = tk.PhotoImage(master=self.master, file=path)
            if img.width() > px:
                img = img.subsample(max(1, round(img.width() / float(px))))
            return img
        except Exception:
            return None

    def get(self, ch, px=24):
        """取某个表情的彩色图片（带缓存，无资源返回 None）"""
        bucket = self._by_size.setdefault(px, {})
        ph = bucket.get(ch)
        if ph is not None:
            return ph
        path = png_path(ch)
        if not path:
            return None
        ph = self._load_png(path, px)
        if ph is not None:
            bucket[ch] = ph
        return ph

    def warm(self, chars, px=24):
        """预热一批表情（打开面板前调用，避免逐个卡顿）"""
        for ch in chars:
            self.get(ch, px)


def split_text(text, px_cache=None):
    """把字符串切成 [(片段, 是不是表情), ...]，用于聊天窗口混排文字与图片

    只识别 manifest 里确实有图的表情；没图的原样当文字，避免出现空白。
    """
    text = str(text or "")
    if not text:
        return []
    man = manifest()
    out, buf, i, n = [], [], 0, len(text)
    while i < n:
        ch = text[i]
        key = ch
        if i + 1 < n and text[i + 1] == "\ufe0f" and (ch + "\ufe0f") in man:
            key = ch + "\ufe0f"
        elif i + 2 < n and text[i + 1] == "\ufe0f" and text[i + 2] == "\u20e3":
            key = text[i:i + 3]
        if key in man:
            if buf:
                out.append(("".join(buf), False))
                buf = []
            out.append((key, True))
            i += len(key)
        else:
            buf.append(ch)
            i += 1
    if buf:
        out.append(("".join(buf), False))
    return out
