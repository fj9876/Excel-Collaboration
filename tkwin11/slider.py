"""Fluent-style slider with direct drag and eased keyboard/programmatic changes."""
import math
import tkinter as tk
from tkinter import ttk
from .core import _ProgressSliderBase, PALETTE, _raster, _rgb
from .dpi import additive_styles
from .tcl_bridge import on_map
from .motion import Tween


class PointerDrag:
    """Recover coalesced/lost Windows motion only while a physical drag is held."""
    def __init__(self,owner,motion,release):
        self.owner=owner;self.motion=motion;self.release=release;self.job=None;self.active=False
        owner.bind('<Destroy>',lambda e:self.cancel() if e.widget is owner else None,add='+')

    def start(self):
        import sys
        self.cancel()
        if sys.platform!='win32':return
        import ctypes
        self.user32=ctypes.WinDLL('user32')
        if not self.user32.GetAsyncKeyState(1)&0x8000:return
        self.last=self.owner.winfo_pointerxy();self.active=True
        self.job=self.owner.after(16,self.poll)

    def poll(self):
        from types import SimpleNamespace
        self.job=None
        if not self.active:return
        point=self.owner.winfo_pointerxy()
        event=SimpleNamespace(x=point[0]-self.owner.winfo_rootx(),y=point[1]-self.owner.winfo_rooty())
        if point!=self.last:self.last=point;self.motion(event)
        if not self.active:return
        if not self.user32.GetAsyncKeyState(1)&0x8000:self.release(event);return
        self.job=self.owner.after(16,self.poll)

    def cancel(self):
        self.active=False
        if self.job is not None:self.owner.after_cancel(self.job);self.job=None


def thumb_image(root,size,radius,disabled,tick=False):
    if not hasattr(root,'_slider_images'):root._slider_images={}
    key=(size,radius,disabled,tick)
    if key not in root._slider_images:
        def sample(x,y):
            x=x*24/size-12;y=y*24/size-12
            d=math.hypot(x,y)
            if tick:
                inside=abs(x)<radius/2 and abs(y)<8
                return _rgb(('#A0A0A0' if disabled else PALETTE['accent']) if inside else PALETTE['bg'])
            return _rgb(('#A0A0A0' if disabled else PALETTE['accent']) if d<radius else '#FFFFFF' if d<10 else '#D6D6D6' if d<11 else PALETTE['bg'])
        root._slider_images[key]=_raster(root,size,size,sample,transparent=_rgb(PALETTE['bg']))
    return root._slider_images[key]


def configure_scale_styles(root,style):
    scale=root.winfo_fpixels('1i')/96;px=lambda n:max(1,round(n*scale))
    for orient in ('Horizontal','Vertical'):
        horizontal=orient=='Horizontal';width,height=(px(128),px(24)) if horizontal else (px(24),px(128))
        def sample(x,y):
            long,short=(x,y) if horizontal else (y,x)
            length,thickness=(width,height) if horizontal else (height,width)
            center=min(max(long,px(12)),length-px(12))
            return _rgb('#8A8A8A' if math.hypot(long-center,short-thickness/2)<=px(2) else PALETTE['bg'])
        track=_raster(root,width,height,sample);root._tkwin11_images.append(track)
        name='Fluent.'+orient+'.Scale'
        style.element_create(name+'.trough','image',track,border=(px(12),0) if horizontal else (0,px(12)),padding=0,width=px(24),height=px(24),sticky='nsew')
        style.element_create(name+'.slider','image',thumb_image(root,px(24),6,False),('disabled',thumb_image(root,px(24),6,True)),width=px(24),height=px(24),sticky='')
        layout=[(name+'.trough',{'sticky':'nsew','children':[(name+'.slider',{'side':'left' if horizontal else 'top','sticky':''})]})]
        for variant in ('','Tick.'):
            style.layout(orient+'.'+variant+'TScale',layout)
            style.configure(orient+'.'+variant+'TScale',background=PALETTE['bg'],borderwidth=0)


class ProgressSlider(_ProgressSliderBase):
    def __init__(self,master,**kwargs):
        self._ready=False
        self._dragging=False
        super().__init__(master,**kwargs)
        self._ready=True
        self._visual=self.get()
        self._target=self._visual
        self.animator=Tween(self)
        self.thumb_animator=Tween(self)
        self._pointer_drag=PointerDrag(self,self.motion,self.release)
        self._thumb_radius=6.
        self._focused=False
        self.focus_ring=self.create_rectangle(1,1,1,1,outline=PALETTE['fg'],width=self.px(1),state='hidden')
        self.itemconfigure(self.track,width=self.px(4),capstyle='round',fill='#8A8A8A')
        self.itemconfigure(self.fill,width=self.px(4),capstyle='round')
        self.bind('<Enter>',lambda e:self.thumb_state('pressed' if self._dragging else 'hover'),add='+')
        self.bind('<Leave>',lambda e:self.thumb_state('pressed' if self._dragging else 'normal'),add='+')
        self._keyboard=False
        self.bind('<FocusIn>',self.focus_in,add='+')
        self.bind('<FocusOut>',lambda e:self.focus_visual(False),add='+')
        self.bind('<Home>',lambda e:self.key_set(self.from_))
        self.bind('<End>',lambda e:self.key_set(self.to))
        self.bind('<Prior>',lambda e:self.nudge(10))
        self.bind('<Next>',lambda e:self.nudge(-10))
        self.bind('<Escape>',self.cancel_drag)
        self.thumb_state('normal')
        self.draw(self.get())
        self.configure(state=self.cget('state'))

    def thumb_state(self,state):
        disabled=str(self.cget('state'))=='disabled'
        if disabled:state='disabled'
        target=4.5 if state=='pressed' else 7 if state=='hover' else 6
        def paint(radius):
            self._thumb_radius=radius
            self.paint_thumb(round(radius*2)/2,disabled)
        if disabled:self.thumb_animator.cancel();paint(target)
        else:self.thumb_animator.start(self._thumb_radius,target,paint,duration=83)

    def paint_thumb(self,radius,disabled):
        self.thumb_image=thumb_image(self._root(),self.px(24),radius,disabled)
        self.itemconfigure(self.thumb,image=self.thumb_image)

    def focus_visual(self,focused):
        self._focused=focused
        self.itemconfigure(self.focus_ring,state='normal' if focused and str(self.cget('state'))!='disabled' else 'hidden')

    def key_set(self,value):
        self._keyboard=True
        self.focus_visual(True)
        if str(self.cget('state'))!='disabled':self.set(value)
        return 'break'

    def configure(self,cnf=None,**kwargs):
        result=super().configure(cnf,**kwargs)
        settings=cnf if isinstance(cnf,dict) else {}
        if getattr(self,'_ready',False) and ('state' in kwargs or 'state' in settings):
            disabled=str(self.cget('state'))=='disabled'
            if disabled:
                self._pointer_drag.cancel()
                self._dragging=False
                self.animator.cancel()
                self._target=self.get();self.draw(self._target)
                if self.grab_current() is self:super().release(None)
            super().configure(takefocus=not disabled)
            self.itemconfigure(self.fill,fill='#B8B8B8' if disabled else PALETTE['accent'])
            self.itemconfigure(self.track,fill='#D4D4D4' if disabled else '#8A8A8A')
            self.thumb_state('normal')
            self.focus_visual(self._focused)
        return result

    config=configure

    def draw(self,value):
        self._visual=value
        left=self.track_margin();right=max(left,self.winfo_width()-left);y=self.winfo_height()/2
        width=left/14*4
        self.itemconfigure(self.track,width=width);self.itemconfigure(self.fill,width=width)
        fraction=max(0,min(1,(value-self.from_)/(self.to-self.from_ or 1)))
        x=left+(right-left)*fraction
        self.coords(self.track,left,y,right,y)
        start=left if self.from_<=self.to else right
        self.coords(self.fill,start,y,x,y)
        self.itemconfigure(self.fill,state='hidden' if abs(start-x)<.01 else 'normal')
        self.coords(self.thumb,x,y)
        self.coords(self.focus_ring,1,1,max(1,self.winfo_width()-2),max(1,self.winfo_height()-2))

    def _tkwin11_dpi_changed(self,dpi):self.draw(self._visual)

    def changed(self,*args):
        if not self._ready:return super().changed(*args)
        if args and hasattr(args[0],'widget'):
            self.draw(self._visual)
            return
        target=self.get()
        if self._dragging or str(self.cget('state'))=='disabled':
            self.animator.cancel();self.draw(target)
        elif target!=self._target:self.animator.start(self._visual,target,self.draw,duration=167)
        self._target=target
        if target!=self._last_notified:
            self._last_notified=target
            if self.command:self.command(target)

    def press(self,event):
        if str(self.cget('state'))=='disabled':return
        self._keyboard=False;self.focus_visual(False)
        self._dragging=True
        self.animator.cancel()
        self.thumb_state('pressed')
        super().press(event)
        self._pointer_drag.start()

    def motion(self,event):
        if self._dragging:super().motion(event)

    def cancel_drag(self,event=None):
        if not self._dragging:return
        self.release(None);return 'break'

    def focus_in(self,event):
        import sys
        if sys.platform=='win32':
            import ctypes
            if ctypes.windll.user32.GetKeyState(9)&0x8000:self._keyboard=True
        self.focus_visual(self._keyboard)

    def nudge(self,direction):
        self._keyboard=True;self.focus_visual(True)
        return super().nudge(direction)

    def release(self,event):
        self._pointer_drag.cancel()
        self._dragging=False
        super().release(event)
        inside=event is not None and 0<=event.x<self.winfo_width() and 0<=event.y<self.winfo_height()
        self.thumb_state('hover' if inside else 'normal')


class FluentSlider(ProgressSlider):
    """Explicit Fluent spelling of the same ProgressSlider implementation."""


class ScaleController:
    """Native ttk.Scale data API with shared thumb feedback and direct input."""
    def __init__(self,widget):
        self.widget=widget;self.root=widget._root();self.resources=self.root._dpi_resources
        self.resources.attach(widget);self.adapter=self.resources.widgets[str(widget)]
        self.public_style=str(widget.cget('style'));self.dragging=False;self.hover=False;self.keyboard=False
        self.radius=6.;self.radius_target=6.;self.animator=Tween(widget);self.job=None;self.closed=False
        self.position_animator=Tween(widget);self.visual=float(widget.get());self.target=self.visual
        self.pointer_drag=PointerDrag(widget,self.motion,self.release)
        self.previous_grab=None;self.variable=None;self.trace=widget.register(self.variable_changed)
        self.previous_grab_global=False
        self.drag_offset=0.
        pool=getattr(self.root,'_scale_slots',None)
        if pool is None:pool=self.root._scale_slots=[]
        self.slot=next((s for s in pool if not s['used']),None)
        self.scale=self.resources.base_dpi/96;size=round(24*self.scale)
        if self.slot is None:
            serial=len(pool)+1;thumb=tk.PhotoImage(master=self.root,width=size,height=size)
            self.slot={'serial':serial,'tracks':{},'thumb':thumb,'used':False};pool.append(self.slot)
            style=ttk.Style(widget)
            with additive_styles(widget):
                style.element_create(f'ScaleMotion{serial}.slider','image',thumb,width=size,height=size,sticky='')
        self.slot['used']=True;self.serial=self.slot['serial']
        widget._tkwin11_scale=self
        widget._tkwin11_dpi_changed=lambda dpi:self.schedule()
        self.use_style(self.public_style)
        self.tag=f'FluentScaleInput{id(self)}';tags=widget.bindtags();widget.bindtags((tags[0],self.tag,*tags[1:]))
        self.bindings=[]
        for event,callback in [('<ButtonPress-1>',self.press),('<B1-Motion>',self.motion),('<ButtonRelease-1>',self.release),
                ('<Escape>',self.cancel_drag),
                ('<Enter>',lambda e:self.feedback(True)),('<Leave>',lambda e:self.feedback(False)),
                ('<Configure>',self.schedule),('<FocusIn>',self.focus_in),('<FocusOut>',self.schedule),('<Destroy>',self.destroy)]:
            self.bindings.append((event,widget.bind_class(self.tag,event,callback)))
        for key in ('Left','Right','Up','Down','Home','End','Prior','Next'):
            event='<'+key+'>';self.bindings.append((event,widget.bind_class(self.tag,event,lambda e,k=key:self.key(k))))
        self.sync()

    def use_style(self,base):
        self.public_style=str(base)
        orient=str(self.widget.cget('orient')).title()
        self.style=f'ScaleMotion{self.serial}.{orient}.TScale'
        self.track_key=None;self.select_track(32,32);self.schedule()

    def select_track(self,width,height):
        key=(1<<max(1,width-1).bit_length(),1<<max(1,height-1).bit_length())
        if key==self.track_key:return
        self.track_key=key;name=f'ScaleMotion{self.serial}.{key[0]}x{key[1]}.trough'
        # Slot styles belong to this widget alone; apply() relayouts it.
        with additive_styles(self.widget):
            if key not in self.slot['tracks']:
                image=tk.PhotoImage(master=self.root,width=key[0],height=key[1]);self.slot['tracks'][key]=image
                ttk.Style(self.widget).element_create(name,'image',image,border=0,width=1,height=1,sticky='nsew')
            self.track_image=self.slot['tracks'][key]
            layouts=self.slot.setdefault('layouts',{})
            if layouts.get(self.style)!=name:
                layouts[self.style]=name
                ttk.Style(self.widget).layout(self.style,[(name,{'sticky':'nsew','children':[(f'ScaleMotion{self.serial}.slider',{'side':'left','sticky':''})]})])
            self.adapter.values['style']=self.style;self.adapter.apply()

    def schedule(self,event=None):
        if not self.closed and self.job is None:self.job=self.widget.after_idle(self.sync)

    def feedback(self,hover):self.hover=hover;self.schedule()

    def focus_in(self,event):
        import sys
        if sys.platform=='win32':
            import ctypes
            if ctypes.windll.user32.GetKeyState(9)&0x8000:self.keyboard=True
        self.schedule()

    def variable_changed(self,*args):
        if args and args[-1]=='unset':self.variable=None
        self.schedule()

    def sync(self):
        if self.closed:return
        if self.job is not None:self.widget.after_cancel(self.job);self.job=None
        name=str(self.widget.cget('variable'))
        if name!=self.variable:
            if self.variable:self.widget.tk.call('trace','remove','variable',self.variable,('write','unset'),self.trace)
            self.variable=name
            if name:self.widget.tk.call('trace','add','variable',name,('write','unset'),self.trace)
        disabled=self.widget.instate(['disabled'])
        if disabled:self.release(None)
        value=float(self.widget.get())
        def move(value):self.visual=value;self.draw()
        if disabled or self.dragging:self.position_animator.cancel();self.visual=value
        elif value!=self.target:self.position_animator.start(self.visual,value,move,duration=167)
        self.target=value
        target=6 if disabled else 4.5 if self.dragging or self.widget.instate(['pressed']) else 7 if self.hover or self.widget.instate(['active']) else 6
        def paint(radius):self.radius=radius;self.draw()
        if disabled:self.animator.cancel();paint(target)
        elif target!=self.radius_target:self.animator.start(self.radius,target,paint,duration=83)
        else:self.draw()
        self.radius_target=target

    def draw(self):
        disabled=self.widget.instate(['disabled']);vertical=str(self.widget.cget('orient'))=='vertical'
        dpi=self.resources.window_dpi(self.widget.winfo_toplevel());ratio=dpi/self.resources.base_dpi
        width=max(round(24*self.scale),round(self.widget.winfo_width()/ratio));height=max(round(24*self.scale),round(self.widget.winfo_height()/ratio))
        self.select_track(width,height)
        root=self.root;source_width=width;source_height=height
        source_track=self.track_key;source_scale=self.scale;radius=self.radius
        a=float(self.widget.cget('from'));b=float(self.widget.cget('to'));visual=self.visual
        focused=self.keyboard and self.widget.instate(['focus'])
        fraction=max(0,min(1,(visual-a)/(b-a or 1)))
        name=str(self.track_image)
        # Equal source pixels can differ in a DPI-specific image. Include every
        # existing derivative, including views retained after a monitor move.
        dpis={self.resources.base_dpi,dpi,*self.resources.image_dpis.get(name,())}
        positions=[]
        for target_dpi in sorted(dpis):
            target_ratio=target_dpi/self.resources.base_dpi
            margin=round(12*source_scale*target_ratio)
            length=max(1,round((height if vertical else width)*target_ratio))
            positions.append((target_dpi,round(margin+(max(margin,length-margin)-margin)*fraction)))
        rendered_key=(source_width,source_height,source_track,source_scale,tuple(positions),
                      round(radius*2)/2,a<=b,focused,disabled,vertical)
        def render(ratio=1):
            width=max(1,round(source_width*ratio));height=max(1,round(source_height*ratio))
            track_size=tuple(max(1,round(v*ratio)) for v in source_track);scale=source_scale*ratio
            image=tk.PhotoImage(master=root,width=track_size[0],height=track_size[1]);image.put(PALETTE['bg'],to=(0,0,*track_size))
            margin=round(12*scale);half=max(1,round(2*scale))
            length=height if vertical else width;end=max(margin,length-margin)
            fraction=max(0,min(1,(visual-a)/(b-a or 1)))
            pos=round(margin+(end-margin)*fraction)
            low=margin if a<=b else end
            def line(start,stop,color):
                start,stop=sorted((start,stop))
                if stop<=start:return
                bounds=(width//2-half,start,width//2+half,stop) if vertical else (start,height//2-half,stop,height//2+half)
                image.put(color,to=bounds)
                cache=getattr(root,'_scale_caps',None)
                if cache is None:cache=root._scale_caps={}
                key=(half,color)
                if key not in cache:
                    cache[key]=_raster(root,2*half,2*half,lambda x,y:_rgb(color if math.hypot(x-half,y-half)<=half else PALETTE['bg']))
                if vertical:
                    image.tk.call(image,'copy',cache[key],'-from',0,0,2*half,half,'-to',width//2-half,start-half)
                    image.tk.call(image,'copy',cache[key],'-from',0,half,2*half,2*half,'-to',width//2-half,stop)
                else:
                    image.tk.call(image,'copy',cache[key],'-from',0,0,half,2*half,'-to',start-half,height//2-half)
                    image.tk.call(image,'copy',cache[key],'-from',half,0,2*half,2*half,'-to',stop,height//2-half)
            line(margin,end,'#D4D4D4' if disabled else '#8A8A8A')
            line(low,pos,'#B8B8B8' if disabled else PALETTE['accent'])
            if focused and not disabled:
                for bounds in ((0,0,width,1),(0,height-1,width,height),(0,0,1,height),(width-1,0,width,height)):
                    image.put(PALETTE['fg'],to=bounds)
            thumb=thumb_image(root,round(24*scale),round(radius*2)/2,disabled)
            x=width//2 if vertical else pos;y=pos if vertical else height//2
            image.tk.call(image,'copy',thumb,'-to',max(0,x-thumb.width()//2),max(0,y-thumb.height()//2))
            return image
        render.cache_key=('scale-track',source_width,source_height,source_track,source_scale,radius,a,b,visual,focused,disabled,vertical)
        if rendered_key==getattr(self,'_rendered_key',None):
            # A future DPI view must use the latest continuous value even when
            # all currently rendered views still have identical pixels.
            self.resources.image_factories[name]=render
            return
        image=render()
        self.resources.image_factories[str(image)]=render
        self.widget.tk.call(self.track_image,'copy',image)
        self._rendered_key=rendered_key

    def at(self,event):
        vertical=str(self.widget.cget('orient'))=='vertical';length=self.widget.winfo_height() if vertical else self.widget.winfo_width()
        dpi=self.resources.window_dpi(self.widget.winfo_toplevel());margin=12*dpi/96
        fraction=max(0,min(1,((event.y if vertical else event.x)-self.drag_offset-margin)/max(1,length-2*margin)))
        a=float(self.widget.cget('from'));b=float(self.widget.cget('to'));value=a+(b-a)*fraction
        if not math.isclose(value,self.widget.get(),rel_tol=0,abs_tol=1e-12):self.widget.set(value)

    def press(self,event):
        if self.widget.instate(['disabled']):return 'break'
        self.keyboard=False;self.dragging=True
        if self.root.grab_current() is not self.widget:
            self.previous_grab=self.root.grab_current()
            self.previous_grab_global=self.previous_grab is not None and self.previous_grab.grab_status()=='global'
        self.position_animator.cancel()
        vertical=str(self.widget.cget('orient'))=='vertical';length=self.widget.winfo_height() if vertical else self.widget.winfo_width()
        margin=12*self.resources.window_dpi(self.widget.winfo_toplevel())/96
        a=float(self.widget.cget('from'));b=float(self.widget.cget('to'))
        center=margin+max(0,length-2*margin)*max(0,min(1,(self.visual-a)/(b-a or 1)))
        point=event.y if vertical else event.x
        self.drag_offset=point-center if abs(point-center)<=margin else 0.
        self.widget.focus_set();self.widget.grab_set_global();self.at(event);self.pointer_drag.start();self.schedule();return 'break'

    def motion(self,event):
        if self.dragging and not self.widget.instate(['disabled']):self.at(event)
        return 'break'

    def release(self,event):
        self.pointer_drag.cancel()
        was_dragging=self.dragging;self.dragging=False
        current=self.root.grab_current()
        if current is self.widget:
            self.widget.grab_release()
        if was_dragging and current in (None,self.widget) and self.previous_grab is not None and self.previous_grab is not self.widget and self.previous_grab.winfo_exists():
            self.previous_grab.grab_set_global() if self.previous_grab_global else self.previous_grab.grab_set()
        self.previous_grab=None
        self.drag_offset=0.
        if was_dragging:self.schedule()
        return 'break'

    def cancel_drag(self,event=None):
        if self.dragging:return self.release(None)

    def key(self,key):
        if self.widget.instate(['disabled']):return 'break'
        self.keyboard=True;a=float(self.widget.cget('from'));b=float(self.widget.cget('to'))
        step=min(1,abs(b-a)/100) if abs(b-a)<1 else 1
        direction=1 if key in ('Right','Up','Prior') else -1
        if str(self.widget.cget('orient'))=='vertical':direction=-1 if key in ('Up','Left','Prior') else 1
        direction*=1 if b>=a else -1
        target=a if key=='Home' else b if key=='End' else self.widget.get()+direction*step*(10 if key in ('Prior','Next') else 1)
        target=max(min(a,b),min(max(a,b),target))
        if target!=self.widget.get():self.widget.set(target)
        self.schedule();return 'break'

    def destroy(self,event):
        if event.widget is not self.widget:return
        self.closed=True;self.animator.cancel();self.position_animator.cancel()
        try:self.release(None)
        except tk.TclError:pass
        if self.job is not None:self.widget.after_cancel(self.job)
        if self.variable:self.widget.tk.call('trace','remove','variable',self.variable,('write','unset'),self.trace)
        self.widget.deletecommand(self.trace)
        for event,binding in self.bindings:
            self.widget.unbind_class(self.tag,event);self.widget._root().deletecommand(binding)
        self.slot['used']=False


def install_scale_compatibility(root):
    def attach(widget):
        if isinstance(widget,ttk.Scale) and not getattr(widget,'_tkwin11_scale',None) and not root._dpi_resources.manual(widget):ScaleController(widget)
    if not getattr(root,'_scale_compatibility',False):
        on_map(root,attach);root._scale_compatibility=True
    def walk(widget):
        attach(widget)
        for child in widget.winfo_children():walk(child)
    walk(root)

