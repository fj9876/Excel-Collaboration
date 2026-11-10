"""Non-activating Fluent tooltips shared by the original Tooltip API."""
import tkinter as tk
from tkinter import ttk
from .tcl_bridge import unbind


_USER32=None


def _user32():
    # A private library object: prototypes set here never leak into ctypes.windll.
    global _USER32
    if _USER32 is None:
        import ctypes
        _USER32=ctypes.WinDLL('user32')
    return _USER32


def _install_host_hooks(host):
    """One shared set of window bindings for all tooltips of a toplevel.

    Per-tooltip host bindings ran every tooltip's handler for every Configure
    event of every widget in the window, and their cleanup removed the
    window's other <Configure> handlers on Python < 3.13.
    """
    if getattr(host,'_tkwin11_tooltip_hooks',False):return
    host._tkwin11_tooltip_hooks=True
    def hide_active(event=None):
        tip=getattr(host,'_tkwin11_active_tooltip',None)
        if tip is not None:tip.hide()
    host.bind('<Configure>',lambda event:hide_active() if event.widget is host else None,add='+')
    for sequence in ('<KeyPress-Escape>','<KeyPress-Control_L>','<KeyPress-Control_R>'):
        host.bind(sequence,hide_active,add='+')


class Tooltip:
    def __init__(self,widget,text,delay=450):
        self.widget,self.text,self.delay=widget,text,delay
        self.host=widget.winfo_toplevel();self.job=None;self.window=None;self.timeout=None
        self.bindings=[]
        for event,callback in [('<Enter>',self.schedule),('<FocusIn>',self.schedule),('<Leave>',self.hide),
                               ('<FocusOut>',self.hide),('<ButtonPress>',self.hide),('<Unmap>',self.hide),('<Destroy>',self.destroy)]:
            self.bindings.append((widget,event,widget.bind(event,callback,add='+')))
        _install_host_hooks(self.host)

    def schedule(self,event=None):
        self.hide()
        active=getattr(self.host,'_tkwin11_active_tooltip',None)
        if active is not None and active is not self:active.hide()
        self.host._tkwin11_active_tooltip=self
        self.job=self.widget.after(self.delay,self.show)

    def host_changed(self,event):
        if event.widget is self.host:self.hide()

    def show(self):
        self.job=None
        if self.window is not None and self.window.winfo_exists():return
        if not self.widget.winfo_exists() or not self.widget.winfo_viewable():return
        grab=self.widget.grab_current()
        if grab is not None and grab.winfo_toplevel() is not self.host:return
        content=self.text() if callable(self.text) else self.text
        if not content:return
        self.hide()
        from .core import Card,Modal,PALETTE
        from .extras import MenuPopup
        from .motion import prepare_popup,reveal_popup
        resources=getattr(self.widget._root(),'_dpi_resources',None)
        dpi=resources.window_dpi(self.host) if resources else self.widget.winfo_fpixels('1i')
        scale=dpi/96;px=lambda n:max(1,round(n*scale))
        win=self.window=tk.Toplevel(self.host);win.withdraw();win.overrideredirect(True);win.transient(self.host)
        self.host._tkwin11_active_tooltip=self
        prepare_popup(win);win.attributes('-alpha',0.0)
        win.scale=scale;win.corner_radius=4;win._region_size=None;win._closed=False
        shell=Card(win,padding=(px(8),px(5)),border_color=PALETTE['border']);shell.pack(fill='both',expand=True)
        ttk.Label(shell,text=content,wraplength=px(320),justify='left').pack()
        if resources:resources.update(win,dpi)
        win.update_idletasks();width,height=win.winfo_reqwidth(),win.winfo_reqheight()
        x=self.widget.winfo_rootx();y=self.widget.winfo_rooty()+self.widget.winfo_height()+px(6)
        left,top,right,bottom=MenuPopup.work_area(x,self.widget.winfo_rooty())
        if y+height>bottom-px(8):y=self.widget.winfo_rooty()-height-px(6)
        x=max(left+px(8),min(x,right-width-px(8)));y=max(top+px(8),min(y,bottom-height-px(8)))
        win.geometry(f'{width}x{height}+{x}+{y}')
        # Prevent native activation as well as avoiding Tk focus/grab calls.
        import sys
        if sys.platform=='win32':
            import ctypes as c
            from ctypes import wintypes as w
            user=_user32()
            user.GetParent.argtypes=[w.HWND];user.GetParent.restype=w.HWND
            user.GetWindowLongPtrW.argtypes=[w.HWND,c.c_int];user.GetWindowLongPtrW.restype=c.c_ssize_t
            user.SetWindowLongPtrW.argtypes=[w.HWND,c.c_int,c.c_ssize_t];user.SetWindowLongPtrW.restype=c.c_ssize_t
            hwnd=user.GetParent(win.winfo_id()) or win.winfo_id()
            user.SetWindowLongPtrW(hwnd,-20,user.GetWindowLongPtrW(hwnd,-20)|0x08000000|0x80)
        win.deiconify();win.update_idletasks()
        Modal.round_corners(win);win.lift();reveal_popup(win)
        # A tooltip never requests focus or a grab, including for disabled owners.
        self.timeout=self.widget.after(5000,self.hide)

    def hide(self,event=None):
        for name in ('job','timeout'):
            job=getattr(self,name)
            if job is not None:
                self.widget.after_cancel(job);setattr(self,name,None)
        if self.window is not None:
            window=self.window;self.window=None
            if window.winfo_exists():window.destroy()
        if getattr(self.host,'_tkwin11_active_tooltip',None) is self:self.host._tkwin11_active_tooltip=None

    def destroy(self,event=None):
        if event is not None and event.widget is not self.widget:return
        self.hide()
        for widget,sequence,binding in self.bindings:
            if widget.winfo_exists():unbind(widget,sequence,binding)
        self.bindings=[]

