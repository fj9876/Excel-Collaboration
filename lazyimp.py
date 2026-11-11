# -*- coding: utf-8 -*-
"""轻量惰性导入工具

openpyxl 首次导入在部分机器上要 0.6~1.7 秒（磁盘/杀毒扫描），而它只在打开或
保存 xlsx 时才需要。用这个代理把导入推迟到真正使用的那一刻，程序启动因此快很多。
"""


class LazyModule:
    """像模块一样使用，第一次访问属性时才真正 import"""

    def __init__(self, name):
        self.__dict__["_name"] = name
        self.__dict__["_mod"] = None

    def _load(self):
        mod = self.__dict__["_mod"]
        if mod is None:
            import importlib
            mod = importlib.import_module(self.__dict__["_name"])
            self.__dict__["_mod"] = mod
        return mod

    def __getattr__(self, item):
        return getattr(self._load(), item)

    def __repr__(self):
        return "<lazy module %r>" % self.__dict__["_name"]
