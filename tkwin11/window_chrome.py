"""Windows client-side title region, using ctypes only.

Keeps a real taskbar window, system menu, resize borders and Windows maximize.
No overrideredirect(), fake minimize window, WebView, or third-party package.
On non-Windows systems the ordinary OS title bar is retained.
"""
from __future__ import annotations
import sys
import re
import tkinter as tk
from tkinter import ttk
from .core import icon, Tooltip, PALETTE
from .tcl_bridge import unbind


class WindowChrome:
    def __init__(self,root,toolbar,drag_widgets=(),on_close=None,sidebar=None,*,window_flicker_workaround=None):
        self.root=root
        self.window_flicker_workaround=(bool(getattr(root._root(),'_tkwin11_window_flicker_workaround',False))
                                       if window_flicker_workaround is None else bool(window_flicker_workaround))
        root._window_chrome=self
        self.close_action=on_close or getattr(root,'close',root.destroy)
        self.attached=False
        self._old_proc=None
        self._cache_job=None
        self._cache_pending=False
        self._drag_rects=[]
        self._excluded=[]
        self._resize_axes=(True,True)
        self._fixed_size=None;self._fixed_reference=None;self._size_job=None
        self._geometry_originals={}
        self._max_rect=None
        self._snap_hooks={}
        self._erase_hooks={}
        self._erase_guard_active=False
        self._erase_timer_id=0x544B
        self._native_hover=False
        self._native_pressed=False
        self._native_visual=None
        self._dpi_pending=None
        self._bridge_job=None
        self.extra_exclusions=[]
        self._drag_anchor=None
        self._drag_job=None
        self._drag_target=None
        self._drag_widget=None
        self._batch_depth=0
        self._native_moving=False
        self._system_move_pending=False
        self.system_move_count=0
        self.resize_handles={}
        self.toolbar=toolbar
        self.sidebar=sidebar
        self.drag_widgets=list(drag_widgets)
        self._bindings=[]
        if sys.platform!='win32':return
        import ctypes
        from ctypes import wintypes as w
        self.c,self.w=ctypes,w
        self.u=ctypes.WinDLL('user32',use_last_error=True)
        self.dwm=ctypes.WinDLL('dwmapi',use_last_error=True)
        self.g=ctypes.WinDLL('gdi32',use_last_error=True)
        self._restore_animation=None
        if self.window_flicker_workaround:
            from .restore_animation import RestoreAnimation
            self._restore_animation=RestoreAnimation(root)
        self._restore_pending=False
        self._restore_prepare_requested=False
        self._restore_zoomed=False
        self._restore_cancel_requested=False
        self._minimize_requested=False
        self.PROC=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,w.HWND,w.UINT,w.WPARAM,w.LPARAM)
        self.ENUMPROC=ctypes.WINFUNCTYPE(w.BOOL,w.HWND,w.LPARAM)
        self._erase_callback=self.PROC(self.child_window_proc)
        self._erase_proc_address=ctypes.cast(self._erase_callback,ctypes.c_void_p).value
        self.u.EnumChildWindows.argtypes=[w.HWND,self.ENUMPROC,w.LPARAM];self.u.EnumChildWindows.restype=w.BOOL
        self.u.SetTimer.argtypes=[w.HWND,ctypes.c_size_t,w.UINT,ctypes.c_void_p];self.u.SetTimer.restype=ctypes.c_size_t
        self.u.KillTimer.argtypes=[w.HWND,ctypes.c_size_t];self.u.KillTimer.restype=w.BOOL
        self.g.CreateSolidBrush.argtypes=[w.COLORREF];self.g.CreateSolidBrush.restype=w.HBRUSH
        self.g.DeleteObject.argtypes=[w.HGDIOBJ];self.g.DeleteObject.restype=w.BOOL
        self.u.GetClientRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)];self.u.GetClientRect.restype=w.BOOL
        self.u.FillRect.argtypes=[w.HDC,ctypes.POINTER(w.RECT),w.HBRUSH];self.u.FillRect.restype=ctypes.c_int
        self.get_long=self.u.GetWindowLongPtrW
        self.get_long.argtypes=[w.HWND,ctypes.c_int];self.get_long.restype=ctypes.c_ssize_t
        self.set_long=self.u.SetWindowLongPtrW
        self.set_long.argtypes=[w.HWND,ctypes.c_int,ctypes.c_void_p];self.set_long.restype=ctypes.c_void_p
        self.u.CallWindowProcW.argtypes=[ctypes.c_void_p,w.HWND,w.UINT,w.WPARAM,w.LPARAM]
        self.u.CallWindowProcW.restype=ctypes.c_ssize_t
        self.u.GetParent.argtypes=[w.HWND];self.u.GetParent.restype=w.HWND
        self.u.SetWindowPos.argtypes=[w.HWND,w.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,w.UINT]
        self.u.RedrawWindow.argtypes=[w.HWND,ctypes.c_void_p,w.HANDLE,w.UINT]
        self.u.GetWindowRect.argtypes=[w.HWND,ctypes.POINTER(w.RECT)]
        self.u.IsZoomed.argtypes=[w.HWND]
        self.u.IsIconic.argtypes=[w.HWND];self.u.IsIconic.restype=w.BOOL
        self.u.ShowWindow.argtypes=[w.HWND,ctypes.c_int];self.u.ShowWindow.restype=w.BOOL
        self.u.AdjustWindowRectExForDpi.argtypes=[ctypes.POINTER(w.RECT),w.DWORD,w.BOOL,w.DWORD,w.UINT]
        self.u.GetMenu.argtypes=[w.HWND];self.u.GetMenu.restype=w.HMENU
        self.u.IsWindow.argtypes=[w.HWND];self.u.IsWindow.restype=w.BOOL
        self.u.IsWindowVisible.argtypes=[w.HWND];self.u.IsWindowVisible.restype=w.BOOL
        self.u.SendMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];self.u.SendMessageW.restype=ctypes.c_ssize_t
        self.u.PostMessageW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];self.u.PostMessageW.restype=w.BOOL
        self.u.DefWindowProcW.argtypes=[w.HWND,w.UINT,w.WPARAM,w.LPARAM];self.u.DefWindowProcW.restype=ctypes.c_ssize_t
        self.u.MonitorFromWindow.argtypes=[w.HWND,w.DWORD];self.u.MonitorFromWindow.restype=w.HANDLE
        self.u.GetMonitorInfoW.argtypes=[w.HANDLE,ctypes.c_void_p]
        self.u.GetDpiForWindow.argtypes=[w.HWND];self.u.GetDpiForWindow.restype=w.UINT
        class TrackMouseEvent(ctypes.Structure):
            _fields_=[('cbSize',w.DWORD),('dwFlags',w.DWORD),('hwndTrack',w.HWND),('dwHoverTime',w.DWORD)]
        self.TrackMouseEvent=TrackMouseEvent
        self.u.TrackMouseEvent.argtypes=[ctypes.POINTER(TrackMouseEvent)]
        self.dwm.DwmSetWindowAttribute.argtypes=[w.HWND,w.DWORD,ctypes.c_void_p,w.DWORD]
        self.dwm.DwmExtendFrameIntoClientArea.argtypes=[w.HWND,ctypes.c_void_p]
        class MonitorInfo(ctypes.Structure):
            _fields_=[('cbSize',w.DWORD),('rcMonitor',w.RECT),('rcWork',w.RECT),('dwFlags',w.DWORD)]
        class MinMaxInfo(ctypes.Structure):
            _fields_=[('ptReserved',w.POINT),('ptMaxSize',w.POINT),('ptMaxPosition',w.POINT),('ptMinTrackSize',w.POINT),('ptMaxTrackSize',w.POINT)]
        class WindowPos(ctypes.Structure):
            _fields_=[('hwnd',w.HWND),('hwndInsertAfter',w.HWND),('x',ctypes.c_int),('y',ctypes.c_int),('cx',ctypes.c_int),('cy',ctypes.c_int),('flags',w.UINT)]
        self.MonitorInfo,self.MinMaxInfo,self.WindowPos=MonitorInfo,MinMaxInfo,WindowPos
        self.controls=ttk.Frame(toolbar)
        self.controls.pack(side='right',padx=(18,0))
        self.pinned=False
        self.buttons={}
        self._drawn_controls={}
        self.icon_size=round(root.winfo_fpixels('1i')/96*14)
        for key,command,label in [('pin',self.toggle_pin,'窗口置顶'),('minimize',self.minimize,'最小化'),
                                  ('maximize',self.toggle_maximize,'最大化 / 还原'),('close',self.close,'关闭')]:
            button=ttk.Button(self.controls,image=icon(root,key,self.icon_size),
                              style='Close.TButton' if key=='close' else 'Quiet.TButton',
                              command=command,takefocus=False,padding=(12,9))
            button.pack(side='left',padx=1)
            self.buttons[key]=button
            button.bind('<Enter>',lambda event,k=key:self.paint_control(k,True),add='+')
            button.bind('<Leave>',lambda event,k=key:self.paint_control(k,False),add='+')
            Tooltip(button,(lambda:'取消置顶' if self.pinned else '窗口置顶') if key=='pin' else label)
        self.max_button=self.buttons['maximize']
        for widget in self.drag_widgets:
            for sequence,callback in [('<ButtonPress-1>',self.drag),('<B1-Motion>',self.drag_motion),('<ButtonRelease-1>',self.end_drag),('<Double-Button-1>',self.double_click)]:
                self._bindings.append((widget,sequence,widget.bind(sequence,callback,add='+')))
        for sequence,callback in [('<Escape>',self.cancel_drag),('<Configure>',self.schedule_cache),('<Map>',self.schedule_cache),('<Destroy>',self._window_destroyed)]:
            self._bindings.append((root,sequence,root.bind(sequence,callback,add='+')))
        self._attach_job=root.after_idle(self.attach)
        self._bridge_job=root.after(30,self.poll_native)

    def attach(self):
        if self.attached:return
        self._attach_job=None
        self.root.update_idletasks()
        # Tk creates its native wrapper after the first map. Subclassing the
        # client HWND before then would leave the real OS caption untouched.
        wrapper=self.u.GetParent(self.root.winfo_id())
        if not wrapper:
            self._attach_job=self.root.after(20,self.attach)
            return
        self.hwnd=wrapper
        requested=(self.root.winfo_width(),self.root.winfo_height())
        self.dpi=self.u.GetDpiForWindow(wrapper) or 96
        self._install_geometry_bridge()
        self.icon_size=round(self.dpi/96*14)
        self._drawn_controls.clear()
        dpi_callback=getattr(self.toolbar,'_apply_window_dpi',None)
        if dpi_callback is not None:dpi_callback(self.dpi)
        self.original_style=self.get_long(self.hwnd,-16)
        self.original_exstyle=self.get_long(self.hwnd,-20)
        # Tk computes its outer rectangle using interpreter DPI, even when
        # Windows maps it straight onto a different-DPI monitor. Recover the
        # client size from that outer rectangle; winfo_width already contains
        # the border-DPI mismatch at this point. On a re-attach (Tk replaced
        # its wrapper) this is the window's current size, not a stale request.
        outer=self.w.RECT();self.u.GetWindowRect(self.hwnd,self.c.byref(outer))
        frame=self.w.RECT(0,0,0,0)
        resources=getattr(self.root._root(),'_dpi_resources',None)
        tk_dpi=resources.base_dpi if resources else round(self.root.winfo_fpixels('1i'))
        if self.u.AdjustWindowRectExForDpi(self.c.byref(frame),self.original_style,bool(self.u.GetMenu(self.hwnd)),self.original_exstyle,tk_dpi):
            requested=(max(1,outer.right-outer.left-(frame.right-frame.left)),max(1,outer.bottom-outer.top-(frame.bottom-frame.top)))
        axes=tuple(bool(v) for v in self.root.resizable())
        if self._fixed_size is None:
            self._fixed_size=requested;self._fixed_reference=(requested,self.dpi)
        else:
            # Re-attach: only an axis that was already fixed keeps the cached
            # lock; one that was free until now (resizable(False, ...) after
            # the user resized) keeps the size the window has now.
            size=tuple(self._fixed_size[i] if not self._resize_axes[i] and not axes[i] else requested[i] for i in (0,1))
            if size!=self._fixed_size:self._fixed_size=size;self._fixed_reference=(size,self.dpi)

        self.sync_erase_brush()
        self._callback=self.PROC(self.window_proc)  # Must stay alive for the window lifetime.
        self._old_proc=self.set_long(self.hwnd,-4,self.c.cast(self._callback,self.c.c_void_p))
        if not self._old_proc:return
        self.attached=True
        # Keep standard window capabilities for native Snap/animations. The
        # caption is hidden by WM_NCCALCSIZE, not by stripping capability flags.
        style=self.original_style|0x00C00000
        self.set_long(self.hwnd,-16,self.c.c_void_p(style))
        # Preserve Tk ownership/toolwindow/taskbar semantics for Toplevels.
        self._resize_axes=tuple(bool(v) for v in self.root.resizable())
        self.sync_capabilities()
        margin=0 if self.window_flicker_workaround else 1
        margins=(self.c.c_int*4)(margin,margin,margin,margin)
        self.dwm.DwmExtendFrameIntoClientArea(self.hwnd,self.c.byref(margins))
        corner=self.c.c_int(2)
        self.dwm.DwmSetWindowAttribute(self.hwnd,33,self.c.byref(corner),self.c.sizeof(corner))
        self.u.SetWindowPos(self.hwnd,None,0,0,0,0,0x0001|0x0002|0x0004|0x0010|0x0020)
        # WM_NCCALCSIZE turned Tk's whole wrapper (client + frame) into client
        # area, so every window would open a frame larger than requested
        # (700x450 as 722x506). Shrink it back: fixed axes to the stable size,
        # free axes to the size Tk had for the client.
        if not self.u.IsZoomed(self.hwnd) and not self.u.IsIconic(self.hwnd):
            width=requested[0] if self._resize_axes[0] else self._fixed_size[0]
            height=requested[1] if self._resize_axes[1] else self._fixed_size[1]
            self.u.SetWindowPos(self.hwnd,None,0,0,width,height,0x0002|0x0004|0x0010)
        if not self.resize_handles:self.install_resize_handles()
        self._handle_layout=None
        self.schedule_cache()

    def _install_geometry_bridge(self):
        if self._geometry_originals:return
        original=self.root.wm_geometry
        for name in ('geometry','wm_geometry'):
            self._geometry_originals[name]=(name in self.root.__dict__,getattr(self.root,name))
        def geometry(value=None):
            match=re.match(r'^(\d+)x(\d+)',str(value)) if value is not None else None
            if match and self.attached:
                requested=tuple(map(int,match.groups()))
                lower=self.root.minsize();upper=self.root.maxsize()
                size=tuple(max(lower[i],min(upper[i],requested[i])) for i in (0,1))
                self._fixed_size=size;self._fixed_reference=(size,self.dpi)
            result=original(value)
            if match and self.attached:
                # Tk re-adds its frame to the size; restore the exact request
                # on resizable windows too (geometry('760x480') gave 782x480).
                if self._size_job is not None:self.root.after_cancel(self._size_job)
                self._size_job=self.root.after_idle(lambda:self._apply_fixed_size(all_axes=True))
            return result
        self._geometry_bridge=geometry
        self.root.geometry=self.root.wm_geometry=geometry

    def _apply_fixed_size(self,all_axes=False):
        self._size_job=None
        if not self.attached or self._fixed_size is None or self.u.IsIconic(self.hwnd) or self.u.IsZoomed(self.hwnd):return
        rect=self.w.RECT();self.u.GetWindowRect(self.hwnd,self.c.byref(rect))
        width=rect.right-rect.left if self._resize_axes[0] and not all_axes else self._fixed_size[0]
        height=rect.bottom-rect.top if self._resize_axes[1] and not all_axes else self._fixed_size[1]
        self.u.SetWindowPos(self.hwnd,None,0,0,width,height,0x0002|0x0004|0x0010)

    def install_snap_hooks(self):
        # Tk HWNDs above the caption must defer this region to the wrapper.
        widget=self.max_button
        if not widget.winfo_exists():return
        while widget is not None:
            hwnd=widget.winfo_id()
            if hwnd!=self.hwnd and hwnd not in self._snap_hooks:
                erase_previous=self._erase_hooks.pop(hwnd,None)
                if erase_previous is not None and self.get_long(hwnd,-4)==self._erase_proc_address:
                    self.set_long(hwnd,-4,self.c.c_void_p(erase_previous))
                previous=self.get_long(hwnd,-4)
                def proc(h,msg,wp,lp,previous=previous):
                    if msg==0x14 and self.window_flicker_workaround:
                        self.erase_background(h,wp)
                        return 1
                    if msg==0x84 and self._max_rect is not None and all(self._resize_axes):
                        x=self.c.c_short(lp&0xffff).value;y=self.c.c_short((lp>>16)&0xffff).value
                        a,b,c,d=self._max_rect
                        if a<=x<c and b<=y<d:return -1  # HTTRANSPARENT
                    return self.u.CallWindowProcW(previous,h,msg,wp,lp)
                callback=self.PROC(proc)
                self.set_long(hwnd,-4,self.c.cast(callback,self.c.c_void_p))
                self._snap_hooks[hwnd]=(previous,callback)
            if widget is self.root:break
            widget=widget.master

    def poll_native(self):
        self._bridge_job=None
        if self.attached:
            if self._restore_animation is not None and (self._restore_cancel_requested or
                    ((self._restore_pending or self._restore_animation.busy) and self.root.state()=='withdrawn')):
                self.cancel_restore()
            if self._restore_prepare_requested:
                self._restore_prepare_requested=False
                self.prepare_restore()
            if self._restore_pending and self.u.IsIconic(self.hwnd):
                self._restore_animation.present()
            if self._restore_pending and not self.u.IsIconic(self.hwnd) and self.u.IsWindowVisible(self.hwnd):
                self._restore_pending=False
                self._restore_animation.present()
                self._restore_animation.start()
            if self._cache_pending and not self._native_moving:
                self._cache_pending=False
                self.refresh_cache()
            if self._dpi_pending is not None:
                dpi,rect=self._dpi_pending;self._dpi_pending=None
                self.apply_window_dpi(dpi,rect)
            visual=(self._native_hover,self._native_pressed)
            if visual!=self._native_visual:
                self._native_visual=visual
                if self.max_button.winfo_exists():
                    self.max_button.state(['pressed'] if visual[1] else ['!pressed'])
                self.paint_control('maximize',visual[0])
        if self.root.winfo_exists():self._bridge_job=self.root.after(30,self.poll_native)

    def apply_window_dpi(self,dpi,rect):
        if self._fixed_reference is not None:
            size,source_dpi=self._fixed_reference
            self._fixed_size=tuple(round(v*dpi/source_dpi) for v in size)
        self.dpi=dpi
        self.resize_margin=max(5,round(dpi/96*5))
        self._handle_layout=None
        self.icon_size=round(dpi/96*14)
        self._drawn_controls.clear()
        callback=getattr(self.toolbar,'_apply_window_dpi',None)
        if callback is not None:callback(dpi)
        resources=getattr(self.root._root(),'_dpi_resources',None)
        if resources is not None:resources.update(self.root,dpi)
        self.u.SetWindowPos(self.hwnd,None,rect[0],rect[1],rect[2]-rect[0] if self._resize_axes[0] else self._fixed_size[0],rect[3]-rect[1] if self._resize_axes[1] else self._fixed_size[1],0x0004|0x0010)
        self.schedule_cache()

    def install_resize_handles(self):
        # Tk child HWNDs cover the client edges after WM_NCCALCSIZE removes
        # the frame. Wrapper-only WM_NCHITTEST therefore misses real mouse
        # input there. Narrow client hit targets forward to Windows SC_SIZE.
        for name,direction,cursor in [('left',1,'size_we'),('right',2,'size_we'),
                ('top',3,'size_ns'),('topleft',4,'size_nw_se'),('topright',5,'size_ne_sw'),
                ('bottom',6,'size_ns'),('bottomleft',7,'size_ne_sw'),('bottomright',8,'size_nw_se')]:
            # Only sidebar layouts have a grey left edge. Plain windows must
            # blend all resize targets, including both left corners, into bg.
            color=PALETTE['sidebar'] if self.sidebar is not None and 'left' in name else PALETTE['bg']
            handle=tk.Frame(self.root,bg=color,borderwidth=0,highlightthickness=0,cursor=cursor,takefocus=False)
            handle.bind('<ButtonPress-1>',lambda event,d=direction:self.start_resize(d,event))
            if name in ('top','bottom'):
                handle.sidebar_fill=tk.Frame(handle,bg=PALETTE['sidebar'],borderwidth=0,highlightthickness=0,cursor=cursor)
                handle.sidebar_fill.bind('<ButtonPress-1>',lambda event,d=direction:self.start_resize(d,event))
            self.resize_handles[name]=handle
        self.layout_resize_handles()

    def layout_resize_handles(self):
        if not self.resize_handles:return
        state=(self.root.winfo_width(),self.root.winfo_height(),bool(self.u.IsZoomed(self.hwnd)),
               self.sidebar.winfo_width() if self.sidebar is not None else 0,self._resize_axes)
        if state==getattr(self,'_handle_layout',None):return
        self._handle_layout=state
        if self.u.IsZoomed(self.hwnd):
            for handle in self.resize_handles.values():handle.place_forget()
            return
        edge=getattr(self,'resize_margin',7);corner=edge*2
        positions={
            'left':dict(x=0,y=corner,width=edge,relheight=1,height=-2*corner),
            'right':dict(relx=1,x=-edge,y=corner,width=edge,relheight=1,height=-2*corner),
            'top':dict(x=corner,y=0,relwidth=1,width=-2*corner,height=edge),
            'bottom':dict(x=corner,rely=1,y=-edge,relwidth=1,width=-2*corner,height=edge),
            'topleft':dict(x=0,y=0,width=corner,height=corner),
            'topright':dict(relx=1,x=-corner,y=0,width=corner,height=corner),
            'bottomleft':dict(x=0,rely=1,y=-corner,width=corner,height=corner),
            'bottomright':dict(relx=1,x=-corner,rely=1,y=-corner,width=corner,height=corner)}
        for name,handle in self.resize_handles.items():
            horizontal='left' in name or 'right' in name
            vertical='top' in name or 'bottom' in name
            if (horizontal and not self._resize_axes[0]) or (vertical and not self._resize_axes[1]):
                handle.place_forget()
                continue
            handle.place(**positions[name])
            if hasattr(handle,'sidebar_fill'):
                sidebar=self.sidebar
                width=max(0,sidebar.winfo_width()-corner) if sidebar is not None else 0
                if width:handle.sidebar_fill.place(x=0,y=0,width=width,relheight=1)
                else:handle.sidebar_fill.place_forget()

    def start_resize(self,direction,event=None):
        if not self.attached or self.u.IsZoomed(self.hwnd):return 'break'
        self.sync_capabilities()
        if direction in (1,2,4,5,7,8) and not self._resize_axes[0]:return 'break'
        if direction in (3,4,5,6,7,8) and not self._resize_axes[1]:return 'break'
        if self.root.grab_current() is not None:return 'break'
        self.u.ReleaseCapture()
        self._system_move_pending=True
        packed=0 if event is None else (event.x_root&0xffff)|((event.y_root&0xffff)<<16)
        self.u.PostMessageW(self.hwnd,0x0112,0xf000|direction,packed)
        return 'break'

    def work_area(self):
        info=self.MonitorInfo();info.cbSize=self.c.sizeof(info)
        monitor=self.u.MonitorFromWindow(self.hwnd,2)
        return info if self.u.GetMonitorInfoW(monitor,self.c.byref(info)) else None

    def sync_erase_brush(self):
        if not self.window_flicker_workaround:return
        color=self.root.cget('background')
        if color==getattr(self,'_erase_color',None):return
        brush=getattr(self,'_erase_brush',None)
        if brush is not None:self.g.DeleteObject(brush)
        rgb=tuple(value>>8 for value in self.root.winfo_rgb(color))
        self._erase_brush=self.g.CreateSolidBrush(rgb[0]|rgb[1]<<8|rgb[2]<<16)
        self._erase_color=color
        if self.sidebar is not None:
            if getattr(self,'_sidebar_erase_brush',None) is None:
                rgb=tuple(value>>8 for value in self.root.winfo_rgb(PALETTE['sidebar']))
                self._sidebar_erase_brush=self.g.CreateSolidBrush(rgb[0]|rgb[1]<<8|rgb[2]<<16)
            self._sidebar_hwnds=set()
            def collect(widget):
                self._sidebar_hwnds.add(widget.winfo_id())
                for child in widget.winfo_children():collect(child)
            collect(self.sidebar)

    def erase_background(self,hwnd,hdc):
        rect=self.w.RECT()
        self.u.GetClientRect(hwnd,self.c.byref(rect))
        brush=self._sidebar_erase_brush if getattr(self,'_erase_scope',None)=='sidebar' else self._erase_brush
        self.u.FillRect(hdc,self.c.byref(rect),brush)

    def child_window_proc(self,hwnd,message,wparam,lparam):
        previous=self._erase_hooks.get(hwnd)
        try:
            if message==0x0014:
                self.erase_background(hwnd,wparam)
                return 1
        except Exception:
            pass
        if previous is not None:result=self.u.CallWindowProcW(previous,hwnd,message,wparam,lparam)
        else:result=self.u.DefWindowProcW(hwnd,message,wparam,lparam)
        return result

    def begin_erase_guard(self,sidebar_only=False,duration=80):
        if not self.attached or not self.window_flicker_workaround:return
        scope='sidebar' if sidebar_only and self.sidebar is not None else 'all'
        if self._erase_guard_active and getattr(self,'_erase_scope',None)!=scope:self.end_erase_guard()
        if not self._erase_guard_active:
            self._erase_guard_active=True
            self._erase_scope=scope
            allowed=getattr(self,'_sidebar_hwnds',set())
            def hook(hwnd,_):
                if hwnd in self._snap_hooks or (scope=='sidebar' and hwnd not in allowed):return True
                previous=self.get_long(hwnd,-4)
                if previous and previous!=self._erase_proc_address and self.set_long(hwnd,-4,self.c.c_void_p(self._erase_proc_address)):
                    self._erase_hooks[hwnd]=previous
                return True
            callback=self.ENUMPROC(hook)
            self.u.EnumChildWindows(self.hwnd,callback,0)
        self.u.SetTimer(self.hwnd,self._erase_timer_id,duration,None)

    def end_erase_guard(self):
        if not self._erase_guard_active:return
        for hwnd,previous in self._erase_hooks.items():
            if self.u.IsWindow(hwnd) and self.get_long(hwnd,-4)==self._erase_proc_address:
                self.set_long(hwnd,-4,self.c.c_void_p(previous))
        self._erase_hooks.clear()
        self._erase_guard_active=False
        self._erase_scope=None

    def window_proc(self,hwnd,message,wparam,lparam):
        try:
            if self.window_flicker_workaround and message==0x0046 and lparam:  # WM_WINDOWPOSCHANGING
                position=self.c.cast(lparam,self.c.POINTER(self.WindowPos)).contents
                if position.flags&(0x0040|0x0080):  # SWP_SHOWWINDOW / SWP_HIDEWINDOW
                    if position.flags&0x0080:
                        # Hiding is not minimizing. Never capture on SWP_HIDEWINDOW.
                        # Defer after_cancel (Tcl) to poll_native, outside WndProc.
                        if not self._minimize_requested and not self.u.IsIconic(hwnd):
                            self.cancel_restore(native=True)
                    if position.flags&0x0040 and not position.flags&0x0080 and not self.u.IsIconic(hwnd) and self._restore_pending:
                        self._restore_animation.present()
                    if position.flags&0x0040 and self._restore_pending:
                        self.begin_erase_guard(sidebar_only=True,duration=80)
            elif self.window_flicker_workaround and message==0x0113 and wparam==self._erase_timer_id:  # WM_TIMER
                self.u.KillTimer(hwnd,self._erase_timer_id)
                self.end_erase_guard()
                return 0
            if self.window_flicker_workaround and message==0x0112 and wparam&0xfff0==0xf120 and self.u.IsIconic(hwnd) and self._restore_pending:  # SC_RESTORE
                self._restore_prepare_requested=True
                return 0
            if self.window_flicker_workaround and message==0x0112 and wparam&0xfff0==0xf030:  # SC_MAXIMIZE
                self.begin_erase_guard(duration=80)
            elif self.window_flicker_workaround and message==0x0112 and wparam&0xfff0==0xf020:  # SC_MINIMIZE
                self.remember_restore(hwnd)
                if self._restore_animation.busy:
                    self.cancel_restore(native=True)
                elif not self._restore_cancel_requested:
                    self._restore_pending=self._restore_animation.capture(hwnd)
                self._minimize_requested=True
            if message==0x0005:  # WM_SIZE
                self._minimize_requested=False
            if self.window_flicker_workaround and message==0x0014:  # WM_ERASEBKGND
                self.erase_background(hwnd,wparam)
                return 1
            if message==0x02E0:  # WM_DPICHANGED: copy transient data; Tk work later.
                rect=self.c.cast(lparam,self.c.POINTER(self.w.RECT)).contents
                self._dpi_pending=(wparam&0xffff,(rect.left,rect.top,rect.right,rect.bottom))
                return 0
            if message==0x00A0:  # WM_NCMOUSEMOVE
                self._native_hover=wparam==9
                track=self.TrackMouseEvent(self.c.sizeof(self.TrackMouseEvent),0x12,hwnd,0)
                self.u.TrackMouseEvent(self.c.byref(track))
            elif message==0x02A2:  # WM_NCMOUSELEAVE
                self._native_hover=False;self._native_pressed=False
            elif message==0x00A1 and wparam==9:
                self._native_pressed=True
                return 0
            elif message==0x00A2 and self._native_pressed:
                self._native_pressed=False
                if wparam==9 and all(self._resize_axes):
                    self.u.PostMessageW(hwnd,0x0112,0xF120 if self.u.IsZoomed(hwnd) else 0xF030,0)
                return 0
            if message==0x0231:  # WM_ENTERSIZEMOVE
                self._native_moving=True;self._system_move_pending=False
                self.system_move_count+=1
            elif message==0x0232:  # WM_EXITSIZEMOVE
                self._native_moving=False;self._system_move_pending=False
                if self.window_flicker_workaround:self._cache_pending=True
            if (message in (0x00A1,0x00A3) and (wparam==2 or 10<=wparam<=17)) or (message==0x0112 and wparam&0xfff0 in (0xf000,0xf010)):
                # Use Windows' caption/move handler, not Tk's translated mouse
                # bindings. This enables native Snap without synchronous Tcl
                # callback -> SendMessage -> Tcl callback recursion.
                result=self.u.DefWindowProcW(hwnd,message,wparam,lparam)
                self._system_move_pending=False
                return result
            if message==0x0085 and not self.window_flicker_workaround:  # WM_NCPAINT
                return 0
            if message==0x0086:  # WM_NCACTIVATE: retain client chrome when focus leaves the window.
                return 1
            if message==0x0083 and wparam:  # WM_NCCALCSIZE: the toolbar owns the top client area.
                if self.u.IsZoomed(hwnd):
                    info=self.work_area()
                    if info:
                        rect=self.c.cast(lparam,self.c.POINTER(self.w.RECT)).contents
                        rect.left,rect.top,rect.right,rect.bottom=info.rcWork.left,info.rcWork.top,info.rcWork.right,info.rcWork.bottom
                if self.window_flicker_workaround and self._native_moving:
                    rect=self.c.cast(lparam,self.c.POINTER(self.w.RECT)).contents
                    # One-pixel inset avoids a DWM native-child resize artifact:
                    # https://stackoverflow.com/q/6500336
                    rect.bottom-=1
                return 0
            if message==0x0112 and wparam&0xfff0==0xf030 and not all(self._resize_axes):return 0
            if message==0x0024:
                result=self.u.CallWindowProcW(self._old_proc,hwnd,message,wparam,lparam)
                limits=self.c.cast(lparam,self.c.POINTER(self.MinMaxInfo)).contents
                if self._fixed_size is not None:
                    if not self._resize_axes[0]:
                        limits.ptMinTrackSize.x=limits.ptMaxTrackSize.x=self._fixed_size[0]
                        limits.ptMaxSize.x=self._fixed_size[0]
                    if not self._resize_axes[1]:
                        limits.ptMinTrackSize.y=limits.ptMaxTrackSize.y=self._fixed_size[1]
                        limits.ptMaxSize.y=self._fixed_size[1]
                info=self.work_area()
                if info and all(self._resize_axes):
                    limits.ptMaxPosition.x=info.rcWork.left-info.rcMonitor.left
                    limits.ptMaxPosition.y=info.rcWork.top-info.rcMonitor.top
                    limits.ptMaxSize.x=info.rcWork.right-info.rcWork.left
                    limits.ptMaxSize.y=info.rcWork.bottom-info.rcWork.top
                return result
            if message==0x0084:  # WM_NCHITTEST, all pixel calculations; no Tk re-entry.
                x=self.c.c_short(lparam&0xffff).value;y=self.c.c_short((lparam>>16)&0xffff).value
                rect=self.w.RECT();self.u.GetWindowRect(hwnd,self.c.byref(rect))
                edge=getattr(self,'resize_margin',7)
                left=x<rect.left+edge;right=x>=rect.right-edge
                top=y<rect.top+edge;bottom=y>=rect.bottom-edge
                left=left and self._resize_axes[0];right=right and self._resize_axes[0]
                top=top and self._resize_axes[1];bottom=bottom and self._resize_axes[1]
                if not self.u.IsZoomed(hwnd):
                    if top and left:return 13
                    if top and right:return 14
                    if bottom and left:return 16
                    if bottom and right:return 17
                    if left:return 10
                    if right:return 11
                    if top:return 12
                    if bottom:return 15
                contains=lambda r:r[0]<=x<r[2] and r[1]<=y<r[3]
                if self._max_rect is not None and all(self._resize_axes) and contains(self._max_rect):return 9
                if any(contains(r) for r in self._excluded):return 1
                if any(contains(r) for r in self._drag_rects):return 2
        except Exception:
            # A platform-specific edge case must not crash the window procedure.
            pass
        return self.u.CallWindowProcW(self._old_proc,hwnd,message,wparam,lparam)

    def schedule_cache(self,event=None):
        if event is not None and event.widget is not self.root:return
        if self.window_flicker_workaround and self._native_moving:
            self._cache_pending=True
            return
        if self._cache_job is None:self._cache_job=self.root.after(16,self.refresh_cache)

    def refresh_cache(self):
        self._cache_job=None
        if not self.attached:return
        # Tk can replace its wrapper HWND after wm resizable/transient changes.
        wrapper=self.u.GetParent(self.root.winfo_id())
        if wrapper and wrapper!=self.hwnd:
            self.cancel_restore()
            self.u.KillTimer(self.hwnd,self._erase_timer_id)
            self.end_erase_guard()
            if self.u.IsWindow(self.hwnd):
                self.set_long(self.hwnd,-4,self.c.c_void_p(self._old_proc))
            self.attached=False
            self.attach()
            return
        self.sync_capabilities()
        self.sync_erase_brush()
        self.resize_margin=max(5,round(self.dpi/96*5))
        self.layout_resize_handles()
        def rect(widget):
            x,y=widget.winfo_rootx(),widget.winfo_rooty()
            return x,y,x+widget.winfo_width(),y+widget.winfo_height()
        self._drag_rects=[rect(widget) for widget in self.drag_widgets if widget.winfo_exists()]
        # Applications may replace the built-in controls with their own.
        # Clear the old hit target while continuing to refresh drag/exclusions.
        self._max_rect=None
        if self.max_button.winfo_exists() and self.max_button.winfo_ismapped():
            self._max_rect=rect(self.max_button)
            self.install_snap_hooks()
        self._excluded=[rect(widget) for widget in self.toolbar.winfo_children()
                        if widget not in self.drag_widgets and widget.winfo_ismapped()]
        self._excluded.extend(rect(widget) for widget in self.extra_exclusions if widget.winfo_exists() and widget.winfo_ismapped())
        self.paint_control('maximize')

    def sync_capabilities(self):
        """Read standard Tk window policy; consumers need no custom event logic."""
        if not self.attached:return
        previous=self._resize_axes
        self._resize_axes=tuple(bool(value) for value in self.root.resizable())
        if previous!=self._resize_axes and self._fixed_size is not None and not self.u.IsIconic(self.hwnd):
            size=list(self._fixed_size)
            for i in (0,1):
                if previous[i] and not self._resize_axes[i]:size[i]=self.root.winfo_width() if i==0 else self.root.winfo_height()
            self._fixed_size=tuple(size);self._fixed_reference=(self._fixed_size,self.dpi)
        style=self.get_long(self.hwnd,-16)
        updated=style|0x00040000 if any(self._resize_axes) else style&~0x00040000
        updated=updated|0x00010000 if all(self._resize_axes) else updated&~0x00010000
        if updated!=style:
            self.set_long(self.hwnd,-16,self.c.c_void_p(updated))
        for key,allowed in [('maximize',all(self._resize_axes) or bool(self.u.IsZoomed(self.hwnd))),
                            ('minimize',bool(updated&0x00020000))]:
            button=self.buttons.get(key)
            if button is None or not button.winfo_exists():continue
            button.state(['!disabled'] if allowed else ['disabled'])
            self.paint_control(key)

    def paint_control(self,key,hover=False):
        button=self.buttons.get(key)
        if button is None or not button.winfo_exists():return
        pinned=key=='pin' and self.pinned
        background=('#FFE8E8' if key=='close' else PALETTE['hover']) if hover else PALETTE['selected'] if pinned else PALETTE['bg']
        color='#DD3333' if key=='close' and hover else PALETTE['blue'] if pinned else PALETTE['muted']
        symbol='restore' if key=='maximize' and self.attached and self.u.IsZoomed(self.hwnd) else key
        signature=(symbol,color,background,pinned)
        if self._drawn_controls.get(key)==signature:return
        self._drawn_controls[key]=signature
        self.buttons[key].configure(image=icon(self.root,symbol,self.icon_size,color=color,background=background),
                                   style='Close.TButton' if key=='close' else 'CardIcon.TButton' if pinned else 'Quiet.TButton')

    def toggle_pin(self):
        self.pinned=not self.pinned
        self.root.attributes('-topmost',self.pinned)
        self.paint_control('pin')

    def drag(self,event):
        if not self.attached:return
        if self._native_moving or self._system_move_pending:return 'break'
        self._system_move_pending=True
        self.u.ReleaseCapture()
        point=((int(event.y_root)&0xffff)<<16)|(int(event.x_root)&0xffff)
        # Child HWND fallback: post a system command and return to Tk first.
        # Never synchronously send WM_NCLBUTTONDOWN from a Tk mouse callback.
        if not self.u.PostMessageW(self.hwnd,0x0112,0xF012,point):self._system_move_pending=False
        return 'break'

    def drag_motion(self,event):
        if self._drag_anchor is None:return
        x,y,left,top=self._drag_anchor
        self._drag_target=(left+event.x_root-x,top+event.y_root-y)
        if self._drag_job is None:self._drag_job=self.root.after(12,self.flush_drag)
        return 'break'

    def flush_drag(self):
        self._drag_job=None
        if self._drag_target is not None and self.attached:
            x,y=self._drag_target;self._drag_target=None
            self.u.SetWindowPos(self.hwnd,None,round(x),round(y),0,0,0x0001|0x0004|0x0010)

    def end_drag(self,event=None):
        if self._drag_job is not None:
            self.root.after_cancel(self._drag_job);self._drag_job=None
        self.flush_drag()
        self._drag_anchor=None
        if self._drag_widget is not None:
            try:self._drag_widget.grab_release()
            except tk.TclError:pass
            self._drag_widget=None
        return 'break'

    def cancel_drag(self,event=None):
        if self._native_moving or self._system_move_pending:
            self.u.PostMessageW(self.hwnd,0x001F,0,0)
            self._system_move_pending=False
            return 'break'
        if self._drag_anchor is None and self._drag_job is None:return None
        self._drag_target=None
        return self.end_drag(event)

    def double_click(self,event):
        self.end_drag()
        self.toggle_maximize();return 'break'

    def toggle_maximize(self):
        if not all(self.root.resizable()) and self.root.state()!='zoomed':return
        if self.window_flicker_workaround:
            self.cancel_restore()
            if self.root.state()!='zoomed':self.begin_erase_guard(duration=80)
            self.root.state('normal' if self.root.state()=='zoomed' else 'zoomed')
        else:
            self.begin_update()
            try:
                self.root.state('normal' if self.root.state()=='zoomed' else 'zoomed')
                self.root.update_idletasks()
            finally:self.end_update()
        self.schedule_cache()

    def begin_update(self):
        if self.attached:
            if self._batch_depth==0:self.u.SendMessageW(self.hwnd,0x000B,0,0)
            self._batch_depth+=1

    def end_update(self):
        if self.attached and self._batch_depth:
            self._batch_depth-=1
            if self._batch_depth==0:
                self.u.SendMessageW(self.hwnd,0x000B,1,0)
                self.u.RedrawWindow(self.hwnd,None,None,0x0001|0x0080|0x0100|0x0400)

    def remember_restore(self,hwnd):
        self._restore_zoomed=bool(self.u.IsZoomed(hwnd))

    def cancel_restore(self,native=False):
        self._restore_pending=False
        self._restore_prepare_requested=False
        self._minimize_requested=False
        self._restore_cancel_requested=native
        if self._restore_animation is not None:
            if native:self._restore_animation._destroy_overlay()
            else:self._restore_animation.discard()

    def prepare_restore(self):
        if not self.attached or not self.u.IsIconic(self.hwnd):return
        self.u.ShowWindow(self.hwnd,3 if self._restore_zoomed else 9)

    def minimize(self):
        if self.attached:
            self.sync_capabilities()
            if not self.get_long(self.hwnd,-16)&0x00020000:return
            if self.window_flicker_workaround:
                self.remember_restore(self.hwnd)
                # A rapid second minimize must not reuse a stale transition frame.
                busy=self._restore_animation.busy
                self.cancel_restore()
                if not busy:self._restore_pending=self._restore_animation.capture(self.hwnd)
                self._minimize_requested=True
        self.root.iconify()

    def close(self):
        return self.close_action()

    def _window_destroyed(self,event):
        if event.widget is self.root:self.detach()

    def detach(self):
        if sys.platform!='win32':return
        if self._bridge_job is not None:
            self.root.after_cancel(self._bridge_job);self._bridge_job=None
        self.cancel_restore()
        self._restore_zoomed=False
        if getattr(self,'hwnd',None):
            self.u.KillTimer(self.hwnd,self._erase_timer_id)
            self.end_erase_guard()
        for hwnd,(previous,callback) in self._snap_hooks.items():
            if self.u.IsWindow(hwnd):self.set_long(hwnd,-4,self.c.c_void_p(previous))
        self._snap_hooks.clear()
        self.cancel_drag()
        for handle in self.resize_handles.values():
            if handle.winfo_exists():handle.destroy()
        self.resize_handles.clear()
        while self._batch_depth:self.end_update()
        for job in (self._cache_job,getattr(self,'_attach_job',None),self._size_job):
            if job is not None:
                try:self.root.after_cancel(job)
                except tk.TclError:pass
        if self.attached and self._old_proc:
            if self.u.IsWindow(self.hwnd):self.set_long(self.hwnd,-4,self.c.c_void_p(self._old_proc))
            self.attached=False
        brush=getattr(self,'_erase_brush',None)
        if brush is not None:
            self.g.DeleteObject(brush)
            self._erase_brush=None
        brush=getattr(self,'_sidebar_erase_brush',None)
        if brush is not None:
            self.g.DeleteObject(brush)
            self._sidebar_erase_brush=None
        for widget,sequence,binding in self._bindings:
            if widget.winfo_exists():unbind(widget,sequence,binding)
        self._bindings.clear()
        for name,(owned,original) in self._geometry_originals.items():
            if getattr(self.root,name,None) is self._geometry_bridge:
                if owned:setattr(self.root,name,original)
                else:self.root.__dict__.pop(name,None)
        self._geometry_originals.clear()
