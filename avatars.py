# -*- coding: utf-8 -*-
"""
LanExcel 头像模块
=================
* 12 款卡通头像由纯标准库逐像素绘制并编码为 PNG（不需要任何外部图片资源）
* 自定义头像（JPG/PNG/BMP/GIF）统一转成 64x64 PNG，方便在 P2P 网络里同步
* 只使用 zlib / struct / base64，兼容 Python 3.8 + Windows 7
"""

import base64
import hashlib
import io
import os
import struct
import zlib
from functools import lru_cache

SIZE = 64
CARTOON_COUNT = 12

_BG = [(226, 240, 255), (255, 232, 224), (226, 248, 232), (241, 232, 255),
       (255, 246, 219), (224, 245, 248), (255, 228, 238), (232, 238, 255)]
_SKIN = [(255, 220, 188), (246, 199, 160), (225, 170, 126), (196, 136, 96),
         (255, 234, 210), (172, 114, 80)]
_HAIR = [(62, 48, 42), (36, 36, 44), (124, 74, 38), (168, 112, 48),
         (92, 62, 118), (38, 74, 96)]
_SHIRT = [(70, 130, 200), (220, 96, 88), (80, 168, 118), (148, 108, 196),
          (236, 160, 64), (70, 160, 168)]
_DARK = (48, 44, 48)
_WHITE = (255, 255, 255)


class _Canvas:
    """极简 RGB 位图画布"""

    def __init__(self, size=SIZE):
        self.s = size
        self.px = bytearray(size * size * 3)

    def fill(self, rgb):
        for i in range(0, len(self.px), 3):
            self.px[i], self.px[i + 1], self.px[i + 2] = rgb[0], rgb[1], rgb[2]

    def point(self, x, y, rgb):
        x, y = int(x), int(y)
        if 0 <= x < self.s and 0 <= y < self.s:
            i = (y * self.s + x) * 3
            self.px[i], self.px[i + 1], self.px[i + 2] = rgb[0], rgb[1], rgb[2]

    def disc(self, cx, cy, r, rgb):
        r2 = float(r) * r
        for y in range(int(cy - r) - 1, int(cy + r) + 2):
            for x in range(int(cx - r) - 1, int(cx + r) + 2):
                dx, dy = x - cx, y - cy
                if dx * dx + dy * dy <= r2:
                    self.point(x, y, rgb)

    def ring(self, cx, cy, r, width, rgb):
        outer, inner = float(r) * r, float(r - width) * (r - width)
        for y in range(int(cy - r) - 1, int(cy + r) + 2):
            for x in range(int(cx - r) - 1, int(cx + r) + 2):
                dx, dy = x - cx, y - cy
                d = dx * dx + dy * dy
                if inner <= d <= outer:
                    self.point(x, y, rgb)

    def rect(self, x0, y0, x1, y1, rgb):
        for y in range(int(y0), int(y1) + 1):
            for x in range(int(x0), int(x1) + 1):
                self.point(x, y, rgb)

    def png_bytes(self):
        s = self.s
        raw = bytearray()
        for y in range(s):
            raw.append(0)                       # PNG 每行滤镜类型 0
            raw.extend(self.px[y * s * 3:(y + 1) * s * 3])

        def chunk(tag, data):
            return (struct.pack(">I", len(data)) + tag + data +
                    struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff))

        ihdr = struct.pack(">IIBBBBB", s, s, 8, 2, 0, 0, 0)   # 8bit RGB
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) +
                chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def _draw_cartoon(index):
    c = _Canvas()
    bg = _BG[index % len(_BG)]
    skin = _SKIN[(index // 3) % len(_SKIN)]
    hair = _HAIR[(index // 2) % len(_HAIR)]
    shirt = _SHIRT[(index // 2) % len(_SHIRT)]
    c.fill(bg)

    # 肩膀 / 衣服
    c.disc(32, 70, 26, shirt)
    c.rect(20, 55, 44, 63, shirt)
    # 脖子
    c.rect(27, 47, 37, 56, skin)
    # 耳朵 + 头发底 + 脸
    c.disc(11, 34, 6, skin)
    c.disc(53, 34, 6, skin)
    c.disc(32, 17, 21, hair)
    c.disc(32, 33, 19, skin)

    # 发型 3 种
    style = index % 3
    if style == 0:                              # 齐刘海
        c.rect(14, 15, 50, 21, hair)
        c.disc(14, 21, 7, hair)
        c.disc(50, 21, 7, hair)
    elif style == 1:                            # 斜刘海
        for x in range(13, 52):
            limit = 19 + int((x - 13) * 0.28)
            for y in range(13, limit + 1):
                c.point(x, y, hair)
    else:                                       # 呆毛 / 翘发
        c.rect(16, 13, 48, 18, hair)
        c.disc(30, 8, 6, hair)
        c.disc(40, 10, 4, hair)

    # 眼睛 3 种
    eye = index % 3
    if eye == 0:                                # 大眼
        for ex in (24, 40):
            c.disc(ex, 33, 5, _WHITE)
            c.disc(ex, 33, 2.6, _DARK)
            c.point(ex - 1, 31, _WHITE)
    elif eye == 1:                              # 点眼 + 眼镜
        for ex in (24, 40):
            c.disc(ex, 33, 2.6, _DARK)
            c.ring(ex, 33, 7, 1.4, (90, 100, 120))
        c.rect(31, 32, 33, 34, (90, 100, 120))
    else:                                       # 眯眯眼（弯线）
        for ex in (24, 40):
            for dx in range(-5, 6):
                y = 33 - int(2.2 * (1 - (dx / 5.0) ** 2))
                c.point(ex + dx, y, _DARK)

    # 腮红
    if index % 4 == 0:
        c.disc(17, 40, 3.4, (250, 160, 160))
        c.disc(47, 40, 3.4, (250, 160, 160))

    # 嘴巴 3 种
    mouth = (index // 4) % 3
    if mouth == 0:                              # 微笑
        for dx in range(-6, 7):
            y = 41 + int(0.13 * dx * dx)
            c.point(32 + dx, y, (170, 82, 82))
    elif mouth == 1:                            # 小圆嘴
        c.disc(32, 43, 3, (176, 86, 86))
    else:                                       # 露齿笑
        c.disc(32, 43, 5, (168, 76, 76))
        c.rect(28, 41, 36, 42, _WHITE)

    # 帽子 / 蝴蝶结等小配件
    if index in (3, 7, 11):
        c.rect(14, 8, 50, 14, (220, 88, 96))
        c.rect(10, 14, 54, 16, (188, 66, 76))
        c.rect(26, 4, 38, 9, (220, 88, 96))
    elif index in (5, 9):
        c.disc(48, 14, 4, (250, 196, 72))
        c.disc(48, 14, 1.6, (220, 150, 40))
    return c.png_bytes()


@lru_cache(maxsize=CARTOON_COUNT)
def cartoon_b64(index):
    """返回第 index 款卡通头像的 PNG base64 字符串"""
    return base64.b64encode(_draw_cartoon(index % CARTOON_COUNT)).decode("ascii")


def default_avatar(fingerprint):
    """按设备指纹自动分配一个默认卡通头像，保证不同成员默认就不同"""
    n = int(hashlib.md5(str(fingerprint).encode("utf-8")).hexdigest(), 16)
    return "cartoon:%d" % (n % CARTOON_COUNT)


def avatar_b64(spec, custom_data=""):
    """把头像描述（cartoon:N / custom）转成可直接给 Tk 使用的 PNG base64"""
    spec = str(spec or "")
    if spec.startswith("cartoon:"):
        try:
            idx = int(spec.split(":", 1)[1])
        except ValueError:
            idx = 0
        return cartoon_b64(idx)
    if spec == "custom" and custom_data:
        return custom_data
    n = int(hashlib.md5(spec.encode("utf-8")).hexdigest(), 16)
    return cartoon_b64(n % CARTOON_COUNT)


def custom_avatar_b64(path, size=SIZE):
    """自定义头像：任意常见图片 → 64x64 PNG base64（Pillow 可用时支持 JPG/BMP）"""
    try:
        from PIL import Image
    except ImportError:
        Image = None
    if Image is not None:
        im = Image.open(path)
        im = im.convert("RGBA")
        im.thumbnail((size, size))
        canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
        canvas.alpha_composite(im, ((size - im.width) // 2, (size - im.height) // 2))
        out = io.BytesIO()
        canvas.convert("RGB").save(out, "PNG")
        return base64.b64encode(out.getvalue()).decode("ascii")
    ext = os.path.splitext(path)[1].lower()
    if ext in (".png", ".gif", ".ppm", ".pgm"):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    raise RuntimeError("解析该图片需要 Pillow；也可以直接选择 PNG / GIF 图片作为头像")
