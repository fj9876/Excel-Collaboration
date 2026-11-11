# -*- coding: utf-8 -*-
"""
隐私表格规则
============
发布者可以把某些「行」或「列」标记为隐私区域。隐私单元格的规则：
  · 谁填的谁看得见；发布者看得见全部；
  · 其它填写者只看到 *** （星号遮掩），既读不到内容，也不能覆盖。

本模块只放纯规则与文本解析，不依赖 tkinter / openpyxl，node 与桌面端共用。
隐私描述结构（与网络上传输的一致）：
    {"on": 1, "rows": [3, 4], "cols": [5], "rev": 7}
    on   : 1 表示这是隐私表格
    rows : 1-based 行号列表
    cols : 1-based 列号列表
    rev  : 版本号，每次修改 +1，用于多端合并时以新盖旧
"""

MASK_TEXT = "***"


def col_name(c):
    """1 → A, 27 → AA"""
    s = ""
    c = int(c)
    while c > 0:
        s = chr(65 + (c - 1) % 26) + s
        c = (c - 1) // 26
    return s


def col_index(name):
    """A → 1, AA → 27；非法返回 0"""
    name = str(name).strip().upper()
    if not name or not name.isalpha():
        return 0
    n = 0
    for ch in name:
        n = n * 26 + (ord(ch) - 64)
    return n


def _split(text):
    return [x for x in str(text or "").replace("，", ",").replace("、", ",")
            .replace("；", ",").replace(";", ",").split(",") if x.strip()]


def parse_rows(text):
    """'3,5-8' → [3,5,6,7,8]（1-based，自动去重排序）"""
    out = set()
    for part in _split(text):
        p = part.strip().replace(" ", "")
        if not p:
            continue
        if "-" in p:
            a, _, b = p.partition("-")
            try:
                a, b = int(a), int(b)
            except ValueError:
                continue
            if a > b:
                a, b = b, a
            for x in range(max(1, a), min(b, 100000) + 1):
                out.add(x)
        else:
            try:
                out.add(int(p))
            except ValueError:
                continue
    return sorted(x for x in out if x > 0)


def parse_cols(text):
    """'A,C-D,5' → [1,3,4,5]（字母与数字都认，1-based）"""
    out = set()
    for part in _split(text):
        p = part.strip().replace(" ", "")
        if not p:
            continue
        if "-" in p:
            a, _, b = p.partition("-")
            a = col_index(a) or _int(a)
            b = col_index(b) or _int(b)
            if a and b:
                if a > b:
                    a, b = b, a
                for x in range(a, min(b, 1000) + 1):
                    out.add(x)
        else:
            v = col_index(p) or _int(p)
            if v:
                out.add(v)
    return sorted(x for x in out if x > 0)


def _int(text):
    try:
        return int(str(text).strip())
    except (TypeError, ValueError):
        return 0


def compress(nums):
    """[3,4,5,9] → '3-5,9'"""
    nums = sorted(set(int(n) for n in nums))
    if not nums:
        return ""
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else "%d-%d" % (start, prev))
        start = prev = n
    parts.append(str(start) if start == prev else "%d-%d" % (start, prev))
    return ",".join(parts)


def compress_cols(nums):
    """[3,4,5,9] → 'C-E,I'（用表头字母表示，和用户的直觉一致）"""
    nums = sorted(set(int(n) for n in nums))
    if not nums:
        return ""
    parts, start, prev = [], nums[0], nums[0]

    def fmt(a, b):
        return col_name(a) if a == b else "%s-%s" % (col_name(a), col_name(b))

    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        parts.append(fmt(start, prev))
        start = prev = n
    parts.append(fmt(start, prev))
    return ",".join(parts)


def is_on(spec):
    return bool(spec) and bool(spec.get("on"))


def norm(spec):
    """把任意来源的描述整理成统一结构（保留 rev，便于多端合并）"""
    if not spec:
        return None
    rows = sorted(set(int(x) for x in (spec.get("rows") or []) if _int(x) > 0))
    cols = sorted(set(int(x) for x in (spec.get("cols") or []) if _int(x) > 0))
    on = 1 if (spec.get("on") and (rows or cols)) else 0
    return {"on": on, "rows": rows, "cols": cols,
            "rev": int(spec.get("rev") or 0)}


def is_private(spec, r, c):
    """坐标是否落在隐私区域（1-based）"""
    if not is_on(spec):
        return False
    if int(r) in (spec.get("rows") or ()):
        return True
    return int(c) in (spec.get("cols") or ())


def describe(spec):
    """给界面看的一句话描述"""
    if not is_on(spec):
        return "公开表格（所有人可见可编辑）"
    bits = []
    rows = spec.get("rows") or []
    cols = spec.get("cols") or []
    if not rows and not cols:
        return "公开表格（所有人可见可编辑）"
    if cols:
        names = [col_name(c) for c in cols]
        bits.append("%s 列" % ("、".join(names) if len(names) <= 6
                               else "、".join(names[:6]) + " 等 %d 列" % len(names)))
    if rows:
        bits.append("%s 行" % compress(rows))
    return "隐私表格：仅填写者本人与发布者可见（%s）" % (" / ".join(bits) or "全部")
