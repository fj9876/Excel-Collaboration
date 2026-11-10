"""Reusable integrated title bar; callers provide content, not drawing or motion."""
import tkinter as tk
from tkinter import ttk
from .core import apply_theme
from .fluent import configure_fluent, FluentWindowChrome


class FluentTitleBar(ttk.Frame):
    def __init__(self,root,*,title=None,sidebar_width=0,sidebar=None,on_close=None,height=48,window_flicker_workaround=None):
        apply_theme(root)
        configure_fluent(root)
        px=lambda n:round(n*root.winfo_fpixels('1i')/96)
        super().__init__(root,style='Workspace.TFrame',height=px(height))
        self.pack_propagate(False)
        self.root=root
        self.on_close=on_close
        self._height_dip=height
        self._sidebar_width_dip=sidebar_width
        brand=ttk.Frame(self,style='Sidebar.TFrame' if sidebar_width else 'Workspace.TFrame')
        self.brand=brand
        brand.pack(side='left',fill='y')
        if sidebar_width:
            brand.configure(width=px(sidebar_width));brand.pack_propagate(False)
        self.title_variable=tk.StringVar(root,value=root.title() if title is None else title)
        self.label=ttk.Label(brand,textvariable=self.title_variable,style='Sidebar.TLabel' if sidebar_width else 'Workspace.TLabel')
        self.label.pack(side='left',padx=px(24))
        self.chrome=FluentWindowChrome(root,self,drag_widgets=(self,brand,self.label),title_widgets=(self.label,),sidebar=sidebar,on_close=self.request_close,window_flicker_workaround=window_flicker_workaround)
        root.protocol('WM_DELETE_WINDOW',self.request_close)

    def _apply_window_dpi(self,dpi):
        from tkinter import font
        px=lambda n:round(n*dpi/96)
        self.configure(height=px(self._height_dip))
        if self._sidebar_width_dip:
            sidebar=self.chrome.sidebar
            width=max(sidebar.winfo_width(),sidebar.winfo_reqwidth()) if sidebar is not None else px(self._sidebar_width_dip)
            self.brand.configure(width=width)
        self.label.pack_configure(padx=px(24))
        if not hasattr(self,'_dpi_font'):
            self._dpi_font=font.Font(self,font=ttk.Style(self).lookup(self.label.cget('style'),'font') or 'TkDefaultFont')
            self._font_points=abs(self._dpi_font.cget('size'))
        self._dpi_font.configure(size=-round(self._font_points*dpi/72))
        self.label.configure(font=self._dpi_font)
        self.chrome.controls.configure(width=px(138))
        for index,key in enumerate(('minimize','maximize','close')):
            self.chrome.buttons[key].place_configure(x=px(46*index),width=px(46))

    def set_title(self,text):
        self.title_variable.set(text)
        self.root.title(text)

    def request_close(self):
        if self.on_close is not None and self.on_close() is False:return False
        try:alive=self.root.winfo_exists()
        except tk.TclError:alive=False
        if alive:
            self.chrome.detach()
            self.root.destroy()
        return True
