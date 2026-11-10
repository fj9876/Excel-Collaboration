"""Small interruptible Tk animations. Timings follow Windows motion guidance."""
import sys
import time
import tkinter as tk
from .tcl_bridge import unbind


_system_motion=[-10.,True]


def enabled(widget):
    root=widget._root()
    override=getattr(root,'motion_enabled',None)
    if override is False:return False
    if sys.platform=='win32':
        # Asked on every animation frame; the OS setting rarely changes.
        now=time.monotonic()
        if now-_system_motion[0]>1.:
            import ctypes
            result=ctypes.c_int(1)
            value=bool(result.value) if ctypes.windll.user32.SystemParametersInfoW(0x1042,0,ctypes.byref(result),0) else True
            _system_motion[:]=[now,value]
        return _system_motion[1]
    return True


def ease(progress):
    # Solve x(t) for cubic-bezier(.55,.55,0,1), then evaluate y(t).
    if progress==0:return 0.
    if progress==1:return 1.
    lo,hi=0.,1.
    for _ in range(14):
        t=(lo+hi)/2
        x=3*(1-t)**2*t*.55+t**3
        if x<progress:lo=t
        else:hi=t
    t=(lo+hi)/2
    return 3*(1-t)**2*t*.55+3*(1-t)*t*t+t**3


def blend(a,b,amount):
    return '#'+''.join(f'{round(int(a[i:i+2],16)*(1-amount)+int(b[i:i+2],16)*amount):02x}' for i in (1,3,5))


class Tween:
    def __init__(self,owner):
        self.owner,self.job=owner,None
        self._generation=0
        self._destroy_binding=owner.bind('<Destroy>',self.destroyed,add='+')

    def dispose(self):
        self.cancel()
        if self._destroy_binding is not None:
            unbind(self.owner,'<Destroy>',self._destroy_binding)
            self._destroy_binding=None

    def cancel(self):
        self._generation+=1
        if self.job is not None:
            self.owner.after_cancel(self.job)
            self.job=None

    def destroyed(self,event):
        if event.widget is self.owner:self.cancel()

    def alive(self):
        try:return bool(self.owner.winfo_exists())
        except tk.TclError:return False

    def start(self,start,end,update,duration=167,done=None,linear=False):
        self.cancel()
        generation=self._generation
        if not enabled(self.owner) or start==end:
            update(end)
            if done and generation==self._generation and self.alive():done()
            return
        clock=[time.monotonic()]*2  # effective start, previous frame
        def tick():
            if generation!=self._generation or not self.alive():return
            self.job=None
            now=time.monotonic()
            # A frame counts for at most ~two frames of motion: after the event
            # loop was blocked (building or first drawing a page) the motion
            # resumes instead of jumping to its end.
            if now-clock[1]>.033:clock[0]+=now-clock[1]-.033
            clock[1]=now
            progress=1 if not enabled(self.owner) else min(1,(now-clock[0])*1000/duration)
            amount=progress if linear else ease(progress)
            update(end if progress>=1 else start+(end-start)*amount)
            if generation!=self._generation or not self.alive():return
            if progress<1:self.job=self.owner.after(16,tick)
            elif done:done()
        tick()


def prepare_popup(window):
    window._fade=Tween(window)
    try:window.attributes('-alpha',0.0 if enabled(window) else 1.0)
    except tk.TclError:pass


def reveal_popup(window):
    def opacity(value):
        try:window.attributes('-alpha',value)
        except tk.TclError:pass
    window._dismissing=False
    window._fade.start(float(window.attributes('-alpha')),1.,opacity,duration=83,linear=True)


def dismiss_popup(window):
    """Cancel from the current frame; modal content and scrim fade together."""
    if window._closed or getattr(window,'_dismissing',False):return 'break'
    window._dismissing=True
    for attribute in ('_show_job','_ready_job'):
        job=getattr(window,attribute,None)
        if job is not None:window.after_cancel(job);setattr(window,attribute,None)
    mask=getattr(window,'mask',None)
    mask_tween=getattr(window,'_mask_fade',None)
    if mask_tween is not None:mask_tween.cancel()
    alpha=float(window.attributes('-alpha'))
    mask_alpha=float(mask.attributes('-alpha')) if mask is not None and mask.winfo_exists() else 0
    def paint(amount):
        window.attributes('-alpha',alpha*(1-amount))
        if mask is not None and mask.winfo_exists():mask.attributes('-alpha',mask_alpha*(1-amount))
    window._fade.start(0.,1.,paint,duration=83,done=window.close,linear=True)
    return 'break'
