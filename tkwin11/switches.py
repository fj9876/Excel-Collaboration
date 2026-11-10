"""One switch renderer for FluentSwitch and existing styled ttk.Checkbutton users."""
import math
import tkinter as tk
from tkinter import ttk
from .tcl_bridge import install_proxy, delete_callback, on_map
from .core import PALETTE, _raster, _rgb
from .dpi import additive_styles
from .motion import Tween, blend


def frame(root,index,*,hover=False,pressed=False,disabled=False,focus=False,square=False,hover_amount=None,press_amount=None):
    square=False  # Compatibility parameter; all public entries use Win11 geometry.
    scale=root.winfo_fpixels('1i')/96
    if not hasattr(root,'_switch_frames'):root._switch_frames={}
    h=0 if disabled else round((float(hover) if hover_amount is None else hover_amount)*4)/4
    p=0 if disabled else round((float(pressed) if press_amount is None else press_amount)*4)/4
    key=(scale,index,h,p,disabled,focus,square,PALETTE['accent'])
    if key in root._switch_frames:return root._switch_frames[key]
    t=index/12
    bg=PALETTE['bg'];accent=PALETTE['disabled'] if disabled else PALETTE['accent']
    if not disabled:accent=blend(blend(accent,PALETTE['accent_hover'],h),'#3184CA',p)
    track=_rgb(blend(bg,accent,t));border=_rgb(blend('#A0A0A0' if disabled else '#707070',accent,t))
    knob=_rgb(blend('#A0A0A0' if disabled else '#606060','#FFFFFF',t));background=_rgb(bg)
    radius=6+max(h,p)
    def sample(x,y):
        x,y=x/scale,y/scale
        focus_edge=math.hypot(max(abs(x-(20 if square else 23))-(18 if square else 20),0),max(abs(y-(13 if square else 14))-11,0))
        if focus and not disabled and (1 if square else 2)<focus_edge<=(2 if square else 3):return _rgb(PALETTE['fg'])
        if square:
            if not (3<=x<=37 and 3<=y<=23):return background
            center=13+14*t;half=7+h+p
            if abs(x-center)<=half and abs(y-13)<=7+h:return _rgb('#F7F8FA' if disabled else '#FFFFFF')
            return _rgb(blend('#D9DEE6' if disabled else blend('#9B9DA7','#858E9C',h),accent,t))
        distance=math.hypot(x-min(max(x,13),33),y-14)
        if distance>10:return background
        center=13+20*t
        cx=min(max(x,center-1.5*p),center+1.5*p)
        if math.hypot(x-cx,y-14)<radius:return knob
        return border if distance>9 else track
    image=_raster(root,round((40 if square else 46)*scale),round((26 if square else 28)*scale),sample)
    root._switch_frames[key]=image
    return image


def _layout(element):
    return [('Checkbutton.padding',{'sticky':'nsew','children':[
        (element,{'side':'left'}),('Checkbutton.label',{'side':'left','sticky':'w'})]})]


def configure_switch_style(root,style):
    # Only the four static faces; frame() renders and caches the in-between
    # animation frames on first use, as it already does for hover and press.
    off,on=frame(root,0),frame(root,12)
    disabled_off,disabled_on=frame(root,0,disabled=True),frame(root,12,disabled=True)
    element='Fluent.Switch.indicator'
    style.element_create(element,'image',off,('disabled selected',disabled_on),('disabled',disabled_off),('selected',on),width=off.width(),height=off.height(),sticky='')
    style.layout('Switch.TCheckbutton',_layout(element))
    style.configure('Switch.TCheckbutton',padding=0,background=PALETTE['bg'])
    style.map('Switch.TCheckbutton',foreground=[('disabled',PALETTE['disabled'])],background=[('active',PALETTE['bg'])])


class SwitchController:
    """Per-widget command proxy. No global ttk/Python monkey patch, command preserved."""
    def __init__(self,widget):
        self.widget=widget;self.root=widget._root();self.tk=widget.tk
        self.path=str(widget);self.native_path=self.path+'__fluent_native'
        self.original_style=str(widget.cget('style'))
        self.square=False  # Legacy Square entry uses the Win11 round appearance.
        self.animator=Tween(widget)
        self.feedback_animator=Tween(widget)
        self.hover_amount=self.press_amount=0.
        self._last_frame=None
        self.on_change=None
        self.renderer=self.draw
        self.hover=self.pressed=False
        self._closed=False;self._sync_job=None;self._variable=None;self._bindings=[]
        self.position=float(self.value())
        self.target=self.position
        pool=getattr(self.root,'_switch_slots',None)
        if pool is None:pool=self.root._switch_slots=[]
        self.slot=next((slot for slot in pool if not slot['used'] and slot.get('square',False)==self.square),None)
        if self.slot is None:
            name=f'AnimatedSwitch{len(pool)}.{self.original_style}'
            image=tk.PhotoImage(master=self.root,width=frame(self.root,0,square=self.square).width(),height=frame(self.root,0,square=self.square).height())
            element=name+'.indicator'
            style=ttk.Style(widget)
            with additive_styles(widget):
                style.element_create(element,'image',image,('disabled selected',frame(self.root,12,disabled=True,square=self.square)),('disabled',frame(self.root,0,disabled=True,square=self.square)),width=image.width(),height=image.height(),sticky='')
                style.layout(name,_layout(element))
            self.slot={'style':name,'image':image,'used':False,'square':self.square}
            pool.append(self.slot)
        self.slot['used']=True
        self.native('configure','-style',self.slot['style'])
        self.tk.call('rename',self.path,self.native_path)
        self._proxy_dispatch=install_proxy(self.tk,self.path,self.dispatch,self.native_path,routed=('cget','configure','config','state','invoke'))
        self._trace_command=widget.register(self.variable_changed)
        self.bind_variable()
        self.tag='FluentSwitchInput'+str(id(self))
        tags=widget.bindtags();widget.bindtags((tags[0],self.tag,*tags[1:]))
        for event,callback in [('<Enter>',lambda e:self.feedback('hover',True)),('<Leave>',lambda e:self.feedback('hover',False)),('<ButtonPress-1>',self.mouse_press),('<ButtonRelease-1>',self.mouse_release),('<FocusIn>',self.focus_in),('<FocusOut>',lambda e:self.renderer(self.position)),('<KeyPress>',self.keyboard),('<KeyPress-Return>',self.key_activate),('<Destroy>',self.destroyed)]:
            self._bindings.append((event,widget.bind_class(self.tag,event,callback)))
        self.draw(self.position)

    def native(self,*args):
        command=self.native_path if self.tk.call('info','commands',self.native_path) else self.path
        return self.tk.call(command,*args)

    def value(self):
        name=str(self.native('cget','-variable'))
        try:return bool(self.tk.call('string','equal',self.tk.globalgetvar(name),self.native('cget','-onvalue')))
        except tk.TclError:return False

    def bind_variable(self):
        name=str(self.native('cget','-variable'))
        if name==self._variable:return
        self.untrace()
        self._variable=name
        self.tk.call('trace','add','variable',name,('write','unset'),self._trace_command)

    def untrace(self):
        if self._variable is not None:
            try:self.tk.call('trace','remove','variable',self._variable,('write','unset'),self._trace_command)
            except tk.TclError:pass
        self._variable=None

    def variable_changed(self,*args):
        if args and args[-1]=='unset':self._variable=None
        self.schedule()

    def schedule(self):
        if not self._closed and self._sync_job is None:self._sync_job=self.widget.after_idle(self.sync)

    def dispatch(self,*args):
        if args and args[0]=='cget' and args[1]=='-style':return self.original_style
        call=list(args)
        config=args and args[0] in ('configure','config')
        if config and len(args)>2:
            for index in range(1,len(call)-1,2):
                if call[index]=='-style':
                    if call[index+1]!=self.original_style:
                        result=self.native(*call)
                        self.detach(restore_style=False)
                        attach_switch(self.widget)
                        return result
                    call[index+1]=self.slot['style']
        result=self.native(*call)
        if config and len(args)<=2:
            def public_option(option):
                option=self.tk.splitlist(option)
                return (*option[:-1],self.original_style) if option[0]=='-style' else option
            if len(args)==1:return tuple(public_option(option) for option in self.tk.splitlist(result))
            if args[1]=='-style':return public_option(result)
        if config and len(args)>2:
            self.bind_variable()
            self.sync()
        elif args and args[0] in ('state','invoke') and (len(args)>1 or args[0]=='invoke'):
            selected_override=args[0]=='state' and any(value in ('selected','!selected') for value in self.tk.splitlist(args[1]))
            if args[0]=='state' and any(value in ('active','!active') for value in self.tk.splitlist(args[1])):
                self.feedback('hover','active' in self.tk.splitlist(self.native('state')))
            self.sync(from_state=selected_override)
        return result

    def draw(self,value):
        self.position=value
        index=max(0,min(12,round(value*12)))
        states=self.tk.splitlist(self.native('state'))
        focus='focus' in states and getattr(self.root,'_switch_keyboard_focus',False)
        image=frame(self.root,index,disabled='disabled' in states,focus=focus,square=self.square,hover_amount=self.hover_amount,press_amount=self.press_amount)
        if image is self._last_frame:return
        self.tk.call(self.slot['image'],'copy',image)
        self._last_frame=image

    def feedback(self,attribute,value):
        setattr(self,attribute,value)
        start_h,start_p=self.hover_amount,self.press_amount
        disabled=self.widget.instate(['disabled'])
        target_h=0. if disabled else float(self.hover);target_p=0. if disabled else float(self.pressed)
        def paint(amount):
            self.hover_amount=start_h+(target_h-start_h)*amount
            self.press_amount=start_p+(target_p-start_p)*amount
            self.renderer(self.position)
        if disabled:
            self.feedback_animator.cancel();paint(1)
        else:self.feedback_animator.start(0.,1.,paint,duration=83,linear=True)

    def keyboard(self,event):
        self.root._switch_keyboard_focus=True
        self.renderer(self.position)

    def key_activate(self,event):
        self.keyboard(event)
        self.widget.invoke()
        return 'break'

    def focus_in(self,event):
        import sys
        if sys.platform=='win32':
            import ctypes
            if ctypes.windll.user32.GetKeyState(9)&0x8000:self.root._switch_keyboard_focus=True
        self.renderer(self.position)

    def mouse_press(self,event):
        self.root._switch_keyboard_focus=False
        if self.widget.instate(['disabled']):return 'break'
        self.widget.focus_set()
        self.native('state','pressed')
        self.feedback('pressed',True)
        # Commit on press. Pointer movement and release never cancel or toggle.
        self.widget.invoke()
        return 'break'

    def mouse_release(self,event):
        self.native('state','!pressed')
        self.feedback('pressed',False)
        return 'break'

    def sync(self,from_state=False):
        if self._closed:return
        if self._sync_job is not None:
            self.widget.after_cancel(self._sync_job);self._sync_job=None
        self.bind_variable()
        states=self.tk.splitlist(self.native('state'))
        if ('pressed' in states)!=self.pressed:self.feedback('pressed','pressed' in states)
        target=float('selected' in states) if from_state else float(self.value())
        if self.on_change:self.on_change(bool(target),'disabled' in states)
        if 'disabled' in states:
            self.feedback_animator.cancel();self.hover_amount=self.press_amount=0.
            self.target=target
            self.animator.cancel();self.renderer(target)
        else:
            if self.feedback_animator.job is None and (self.hover_amount!=float(self.hover) or self.press_amount!=float(self.pressed)):
                self.feedback('hover',self.hover)
            if target!=self.target or self.animator.job is None:
                self.target=target
                self.animator.start(self.position,target,self.renderer,duration=167,done=lambda:self.renderer(target))

    def destroyed(self,event):
        if event.widget is self.widget:self.detach()

    def detach(self,restore_style=True):
        if self._closed:return
        self._closed=True
        self.animator.dispose()
        self.feedback_animator.dispose()
        if self._sync_job is not None:self.widget.after_cancel(self._sync_job)
        self.untrace()
        self.widget.bindtags(tuple(tag for tag in self.widget.bindtags() if tag!=self.tag))
        for event,binding in self._bindings:
            self.widget.unbind_class(self.tag,event)
            self.root.deletecommand(binding)
        self.widget.deletecommand(self._trace_command)
        if self.tk.call('info','commands',self.path):self.tk.call('rename',self.path,'')
        delete_callback(self.tk,self._proxy_dispatch)
        if self.tk.call('info','commands',self.native_path):
            self.tk.call('rename',self.native_path,self.path)
            if restore_style:self.tk.call(self.path,'configure','-style',self.original_style)
        self.slot['used']=False
        self.widget._tkwin11_switch=None


def attach_switch(widget):
    if isinstance(widget,ttk.Checkbutton) and not getattr(widget,'_tkwin11_switch',None) and str(widget.cget('style')) in ('Switch.TCheckbutton','Square.Switch.TCheckbutton'):
        resources=getattr(widget._root(),'_dpi_resources',None)
        if resources is not None and resources.manual(widget):return None
        if resources is not None:resources.attach(widget)
        widget._tkwin11_switch=SwitchController(widget)
    return getattr(widget,'_tkwin11_switch',None)


def install_switch_compatibility(root):
    if not getattr(root,'_switch_compatibility_installed',False):
        on_map(root,attach_switch)
        def modality(keyboard):
            root._switch_keyboard_focus=keyboard
            focused=root.focus_get()
            controller=getattr(focused,'_tkwin11_switch',None)
            if controller is not None:controller.renderer(controller.position)
        root.bind_all('<KeyPress>',lambda event:modality(True),add='+')
        root.bind_all('<ButtonPress-1>',lambda event:modality(False),add='+')
        root._switch_compatibility_installed=True
    def walk(widget):
        attach_switch(widget)
        for child in widget.winfo_children():walk(child)
    walk(root)
