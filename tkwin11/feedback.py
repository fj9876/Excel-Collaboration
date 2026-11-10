"""Nonblocking toasts, text progress and radial meters, all native Tk."""
import math
import time
import tkinter as tk
from tkinter import ttk
from .core import PALETTE, Card, Modal
from .forms import SEMANTIC, Badge
from .render import SmoothImage
from .motion import Tween
from .tcl_bridge import unbind

class ToastNotification(tk.Toplevel):
    def __init__(self,master,message,*,title='',kind='info',duration=3000,position='bottom-right'):
        self.host=master.winfo_toplevel();self.kind=kind;self.duration=duration;self.position=position
        self._closed=False;self._timer=None;self._ready_job=None;self._shown=False;self._region_size=None
        resources=getattr(master._root(),'_dpi_resources',None)
        self.scale=(resources.window_dpi(self.host) if resources else master.winfo_fpixels('1i'))/96;self.corner_radius=5
        self._remaining=max(0,duration);self._deadline=None
        super().__init__(self.host);self.withdraw();self.overrideredirect(True);self.transient(self.host)
        from .motion import prepare_popup
        prepare_popup(self)
        self.configure(background=PALETTE['bg'])
        body=Card(self,padding=14,border_color=SEMANTIC.get(kind,SEMANTIC['info']));body.pack(fill='both',expand=True,padx=1,pady=1)
        head=ttk.Frame(body);head.pack(fill='x')
        Badge(head,text=title or {'success':'已完成','danger':'操作失败','warning':'请注意','info':'提示'}.get(kind,'提示'),kind=kind).pack(side='left')
        ttk.Button(head,text='×',width=2,style='Quiet.TButton',command=self.dismiss).pack(side='right')
        ttk.Label(body,text=message,wraplength=round(280*self.scale),justify='left').pack(fill='x',pady=(10,0))
        self.bind('<Enter>',self._pause,add='+');self.bind('<Leave>',self._leave,add='+')
        self.bind('<Escape>',lambda e:self.dismiss())
        self._configure_bind=self.host.bind('<Configure>',self._host_configure,add='+')
        self._unmap_bind=self.host.bind('<Unmap>',lambda e:self.hide() if e.widget is self.host else None,add='+')
        self._destroy_bind=self.host.bind('<Destroy>',lambda e:self.hide() if e.widget is self.host else None,add='+')
    def show_toast(self):
        if self._closed:return self
        self._fade.cancel();self._dismissing=False
        self.update_idletasks()
        if not self._shown:
            stack=getattr(self.host,'_web_toasts',None)
            if stack is None:stack=self.host._web_toasts=[]
            same=[toast for toast in stack if toast.position==self.position]
            gap=round(10*self.scale)
            while same and (len(same)>=3 or sum(toast.winfo_reqheight()+gap for toast in same)+self.winfo_reqheight()+2*gap>self.host.winfo_height()):
                same.pop(0).hide()
            stack.append(self);self._shown=True
        self.update_idletasks();self.deiconify();self.reflow();self.update_idletasks();Modal.round_corners(self)
        if self._ready_job is not None:self.after_cancel(self._ready_job)
        self._ready_job=self.after(20,self._finish_show)
        return self
    def _finish_show(self):
        self._ready_job=None
        if self._closed:return
        self.update_idletasks();self.reflow();self.update_idletasks();self._region_size=None;Modal.round_corners(self)
        from .motion import reveal_popup
        reveal_popup(self);self._resume()
    def _host_configure(self,event):
        if event.widget is self.host:self.reflow()
    def reflow(self):
        if self._closed:return
        gap=round(10*self.scale);offset=gap
        for toast in list(getattr(self.host,'_web_toasts',[])):
            if toast._closed or toast.position!=self.position:continue
            w=toast.winfo_reqwidth();h=toast.winfo_reqheight()
            x=self.host.winfo_rootx()+max(gap,self.host.winfo_width()-w-gap)
            if self.position=='top-right':y=self.host.winfo_rooty()+offset
            else:y=self.host.winfo_rooty()+max(gap,self.host.winfo_height()-offset-h)
            toast.geometry(f'{w}x{h}+{x}+{y}');offset+=h+gap
    def _pause(self,event=None):
        if self._timer is not None:
            self._remaining=max(0,round((self._deadline-time.monotonic())*1000))
            self.after_cancel(self._timer);self._timer=None
    def _leave(self,event=None):
        x,y=self.winfo_pointerxy()
        if not(self.winfo_rootx()<=x<self.winfo_rootx()+self.winfo_width() and self.winfo_rooty()<=y<self.winfo_rooty()+self.winfo_height()):self._resume()
    def _resume(self,event=None):
        if self._closed or getattr(self,'_dismissing',False):return
        self._pause()
        if self.duration>0:
            self._deadline=time.monotonic()+self._remaining/1000
            self._timer=self.after(max(1,self._remaining),self.dismiss)
    def dismiss(self):
        if self._closed or getattr(self,'_dismissing',False):return 'break'
        self._dismissing=True;self._pause()
        if self._ready_job is not None:self.after_cancel(self._ready_job);self._ready_job=None
        self._fade.start(float(self.attributes('-alpha')),0.,lambda v:self.attributes('-alpha',v),duration=83,done=self.hide,linear=True)
        return 'break'
    def hide(self):
        if self._closed:return
        self._closed=True;self._pause()
        if self._ready_job is not None:self.after_cancel(self._ready_job);self._ready_job=None
        stack=getattr(self.host,'_web_toasts',[])
        if self in stack:stack.remove(self)
        for event,binding in [('<Configure>',self._configure_bind),('<Unmap>',self._unmap_bind),('<Destroy>',self._destroy_bind)]:
            try:unbind(self.host,event,binding)
            except tk.TclError:pass
        super().destroy()
        for toast in list(stack):toast.reflow()
    def destroy(self):self.hide()

def notify(master,message,**kwargs):return ToastNotification(master,message,**kwargs).show_toast()

class TextProgress(ttk.Frame):
    def __init__(self,master,*,variable=None,value=0,maximum=100,mask='{value:.0f}%',text='',orient='horizontal',mode='determinate',kind='primary',length=240,thickness=28,**kwargs):
        super().__init__(master,**kwargs)
        if not math.isfinite(maximum) or maximum<=0 or orient not in ('horizontal','vertical'):raise ValueError('进度范围或方向无效')
        self.variable=variable if variable is not None else tk.DoubleVar(self,value=value)
        self.maximum,self.mask,self.text,self.orient,self.mode,self.kind=maximum,mask,text,orient,mode,kind
        self._timer=None;self._phase=0;self._closed=False
        self.dpi=self.winfo_fpixels('1i')/96;self.px=lambda n:max(1,round(n*self.dpi))
        self._logical_dimensions=(length,thickness)
        w,h=(self.px(length),self.px(thickness)) if orient=='horizontal' else (self.px(thickness),self.px(length))
        self.canvas=tk.Canvas(self,width=w,height=h,background=PALETTE['bg'],highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.draw())
        self.canvas._tkwin11_dpi_changed=lambda dpi:self.draw()
        self._visual=self.get();self._value_tween=Tween(self)
        self._trace=self.variable.trace_add('write',self._value_changed);self.bind('<Destroy>',self._cleanup,add='+')
        self.draw()
    def _tkwin11_dpi_changed(self,dpi):
        self.dpi=dpi/96
        length,thickness=self._logical_dimensions
        w,h=(self.px(length),self.px(thickness)) if self.orient=='horizontal' else (self.px(thickness),self.px(length))
        self.canvas.configure(width=w,height=h);self.draw()
    def _value_changed(self,*_):
        def paint(value):self._visual=value;self.draw()
        self._value_tween.start(self._visual,self.get(),paint,duration=167)
    def get(self):
        try:value=float(self.variable.get())
        except (ValueError,tk.TclError):value=getattr(self,'minimum',0)
        if not math.isfinite(value):value=getattr(self,'minimum',0)
        return max(getattr(self,'minimum',0),min(self.maximum,value))
    def set(self,value):
        value=float(value)
        if not math.isfinite(value):raise ValueError('进度必须是有限数值')
        self.variable.set(max(0,min(self.maximum,value)))
    def step(self,amount=1):self.set(self.get()+amount)
    def draw(self):
        if self._closed:return
        c=self.canvas;w=c.winfo_width();h=c.winfo_height()
        if w<2 or h<2:return
        resources=getattr(self._root(),'_dpi_resources',None)
        dpi=resources.window_dpi(self.winfo_toplevel())/96 if resources else self.dpi
        px=lambda n:max(1,round(n*dpi))
        color=PALETTE['accent'] if self.kind=='primary' else SEMANTIC.get(self.kind,PALETTE['accent'])
        ratio=max(0,min(1,self._visual/self.maximum))
        horizontal=self.orient=='horizontal'
        if self.mode=='indeterminate':a=max(0,self._phase*1.3-.3);b=min(1,self._phase*1.3)
        else:a=0;b=ratio
        caption=self.text or ('正在处理…' if self.mode=='indeterminate' else self.mask.format(value=self._visual,maximum=self.maximum,percent=ratio*100))
        width=px(4);margin=px(2)
        if not hasattr(self,'_items'):
            self._items=(c.create_line(0,0,0,0,capstyle='round'),c.create_line(0,0,0,0,capstyle='round'),c.create_text(0,0,font='TkDefaultFont'))
        track,fill,label=self._items
        if horizontal:
            y=h-px(3);span=max(0,w-2*margin)
            c.coords(track,margin,y,w-margin,y);c.coords(fill,margin+span*a,y,margin+span*b,y)
            c.coords(label,0,0);c.itemconfigure(label,anchor='nw',angle=0)
        else:
            x=w-px(3);span=max(0,h-2*margin)
            c.coords(track,x,margin,x,h-margin);c.coords(fill,x,margin+span*(1-b),x,margin+span*(1-a))
            c.coords(label,px(10),h/2);c.itemconfigure(label,anchor='center',angle=90)
        c.itemconfigure(track,fill='#E2E5E9',width=width)
        c.itemconfigure(fill,fill=color,width=width,state='normal' if b>a else 'hidden')
        c.itemconfigure(label,text=caption,fill=PALETTE['fg'])
    def start(self,interval=16):
        if self._timer is not None:return
        self._interval=max(15,interval);self._started=time.monotonic();self._tick()
    def _tick(self):
        if self._closed:return
        self._timer=None
        if self.mode=='indeterminate':
            from .motion import enabled
            self._phase=((time.monotonic()-self._started)/1.0)%1 if enabled(self) else .5
            self.draw()
        else:
            self.set(self.get()+1)
            if self.get()>=self.maximum:return
        self._timer=self.after(self._interval,self._tick)
    def stop(self):
        if self._timer is not None:self.after_cancel(self._timer);self._timer=None
    def _cleanup(self,event):
        if event.widget is self:
            self._closed=True;self.stop();self.variable.trace_remove('write',self._trace)

class Meter(ttk.Frame):
    def __init__(self,master,*,variable=None,value=0,minimum=0,maximum=100,meter_type='full',unit='%',subtext='',interactive=False,thresholds=None,command=None,size=190,**kwargs):
        super().__init__(master,**kwargs)
        if not math.isfinite(minimum) or not math.isfinite(maximum) or maximum<=minimum or meter_type not in ('full','semi'):raise ValueError('仪表量程或类型无效')
        self.minimum,self.maximum,self.meter_type=minimum,maximum,meter_type
        self.unit,self.subtext,self.interactive,self.thresholds,self.command=unit,subtext,interactive,thresholds or [],command
        self.variable=variable if variable is not None else tk.DoubleVar(self,value=value)
        self.dpi=self.winfo_fpixels('1i')/96;self.px=lambda n:max(1,round(n*self.dpi))
        self._closed=False;self._geometry=None;self._meter_image=None;self._draw_job=None
        self.canvas=tk.Canvas(self,width=self.px(size),height=self.px(size if meter_type=='full' else size*.72),background=PALETTE['bg'],highlightthickness=0,takefocus=interactive)
        self._logical_size=size;self.canvas._dpi_native_images=True
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',lambda e:self.schedule())
        if interactive:
            self.canvas.configure(cursor='hand2');self.canvas.bind('<Button-1>',self._pointer);self.canvas.bind('<B1-Motion>',self._pointer)
            for event,delta in [('<Left>',-1),('<Down>',-1),('<Right>',1),('<Up>',1)]:self.canvas.bind(event,lambda e,d=delta:self._key_value(self.get()+d))
            self.canvas.bind('<Home>',lambda e:self._key_value(self.minimum));self.canvas.bind('<End>',lambda e:self._key_value(self.maximum))
        self._visual=self.get();self._value_tween=Tween(self)
        self._trace=self.variable.trace_add('write',self._value_changed);self.bind('<Destroy>',self._cleanup,add='+')
        self.draw()
    def _tkwin11_dpi_changed(self,dpi):
        self.dpi=dpi/96
        self.canvas.configure(width=self.px(self._logical_size),height=self.px(self._logical_size if self.meter_type=='full' else self._logical_size*.72))
        self.schedule()
    def _key_value(self,value):
        if not self.instate(['disabled']):self.set(value)
        return 'break'
    def state(self,statespec=None):
        result=super().state(statespec)
        if statespec is not None:
            self.canvas.configure(takefocus=self.interactive and not self.instate(['disabled']),cursor='hand2' if self.interactive and not self.instate(['disabled']) else '')
            self.schedule()
        return result
    def schedule(self):
        if not self._closed and self._draw_job is None:self._draw_job=self.after(16,self.draw)
    def _value_changed(self,*_):
        now=time.monotonic();previous=getattr(self,'_last_value_time',0);self._last_value_time=now
        if now-previous<.12:
            self._value_tween.cancel();self._visual=self.get();self.schedule();return
        def paint(value):self._visual=value;self.schedule()
        self._value_tween.start(self._visual,self.get(),paint,duration=167)
    def get(self):
        try:value=float(self.variable.get())
        except (ValueError,tk.TclError):value=getattr(self,'minimum',0)
        if not math.isfinite(value):value=getattr(self,'minimum',0)
        return max(getattr(self,'minimum',0),min(self.maximum,value))
    def set(self,value):
        value=float(value)
        if not math.isfinite(value):raise ValueError('仪表数值必须有限')
        value=max(self.minimum,min(self.maximum,value))
        if value==self.get():return 'break'
        self.variable.set(value)
        self.event_generate('<<MeterChanged>>',when='tail')
        if self.command:self.command(self.get())
        return 'break'
    def draw(self):
        if self._draw_job is not None:self.after_cancel(self._draw_job);self._draw_job=None
        if self._closed:return
        c=self.canvas;w=c.winfo_width();h=c.winfo_height()
        if w<5 or h<5:return
        full=self.meter_type=='full';width=self.px(12)
        radius=max(1,min(w/2-self.px(14),h/2-self.px(12) if full else h-self.px(42)))
        cx=w/2;cy=h/2 if full else radius+self.px(10)
        self._geometry=(cx,cy,radius)
        bounds=(cx-radius,cy-radius,cx+radius,cy+radius)
        ratio=max(0,min(1,(self._visual-self.minimum)/(self.maximum-self.minimum)))
        color=PALETTE['accent']
        for limit,kind in sorted(self.thresholds):
            if ratio*100<=limit:color=SEMANTIC.get(kind,kind);break
        if self.instate(['disabled']):color=PALETTE['disabled']
        left=max(0,math.floor(cx-radius-width));top=max(0,math.floor(cy-radius-width))
        right=min(w,math.ceil(cx+radius+width));bottom=min(h,math.ceil(cy+radius+width))
        local=(bounds[0]-left,bounds[1]-top,bounds[2]-left,bounds[3]-top)
        with SmoothImage(self,right-left,bottom-top) as surface:
            surface.arc(local,270 if full else 180,360 if full else 180,PALETTE['hover'],width)
            surface.arc(local,270 if full else 180,ratio*(360 if full else 180),color,width)
            image=surface.photo()
        if not hasattr(self,'_items'):
            self._items=(c.create_image(0,0,anchor='nw'),c.create_text(0,0,font=('Microsoft YaHei UI',17,'bold')),c.create_text(0,0,font='TkDefaultFont'))
        arc,label,subtitle=self._items
        c.coords(arc,left,top);c.itemconfigure(arc,image=image);self._meter_image=image
        target=self.get()
        shown=f'{self._visual:.0f}' if float(target).is_integer() else f'{self._visual:.1f}'.rstrip('0').rstrip('.')
        c.coords(label,cx,cy-self.px(9));c.itemconfigure(label,text=f'{shown}{self.unit}',fill=PALETTE['fg'])
        c.coords(subtitle,cx,cy+self.px(19));c.itemconfigure(subtitle,text=self.subtext,fill=PALETTE['muted'])
    def _pointer(self,event):
        if self.instate(['disabled']):return 'break'
        if self._geometry is None:return
        self.canvas.focus_set();cx,cy,r=self._geometry
        angle=math.degrees(math.atan2(cy-event.y,event.x-cx))
        if self.meter_type=='full':ratio=((90-angle)%360)/360
        else:ratio=max(0,min(1,(180-angle)/180)) if angle>=0 else (1 if event.x>=cx else 0)
        return self.set(round(self.minimum+ratio*(self.maximum-self.minimum),0 if self.unit=='%' else 1))
    def _cleanup(self,event):
        if event.widget is self:
            self._closed=True;self.variable.trace_remove('write',self._trace)
            if self._draw_job is not None:self.after_cancel(self._draw_job);self._draw_job=None
