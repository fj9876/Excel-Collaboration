"""Animated indicators; ttk remains responsible for selection and invocation."""
import math
import tkinter as tk
from tkinter import ttk
from .button import ButtonController
from .core import PALETTE, _raster, _rgb
from .dpi import additive_styles
from .tcl_bridge import on_map
from .motion import Tween, blend


def distance(x,y,a,b):
    dx,dy=b[0]-a[0],b[1]-a[1]
    t=max(0,min(1,((x-a[0])*dx+(y-a[1])*dy)/(dx*dx+dy*dy or 1)))
    return math.hypot(x-a[0]-t*dx,y-a[1]-t*dy)


class SelectionController(ButtonController):
    def __init__(self,widget):
        self.widget=widget;self.root=widget._root();self.resources=self.root._dpi_resources
        self.resources.attach(widget);self.adapter=self.resources.widgets[str(widget)]
        self.public_style=str(widget.cget('style'));self.base=self.public_style or widget.winfo_class()
        self.kind='radio' if isinstance(widget,ttk.Radiobutton) else 'check'
        self.position=float(widget.instate(['selected']) or self.kind=='check' and widget.instate(['alternate']))
        self.target=self.position;self.focus_visible=False
        self.animator=Tween(widget);self.job=None;self.closed=False;self.variable=None
        self.trace_command=widget.register(self.variable_changed)
        self.scale=self.resources.base_dpi/96
        pool=getattr(self.root,'_choice_slots',None)
        if pool is None:pool=self.root._choice_slots=[]
        self.slot=next((s for s in pool if not s['used']),None)
        if self.slot is None:
            serial=len(pool)+1;image=tk.PhotoImage(master=self.root,width=round(28*self.scale),height=round(28*self.scale))
            element=f'ChoiceMotion{serial}.indicator'
            with additive_styles(widget):
                ttk.Style(widget).element_create(element,'image',image,width=image.width(),height=image.height(),sticky='')
            self.slot={'serial':serial,'element':element,'face':image,'used':False};pool.append(self.slot)
        self.slot['used']=True;self.face=self.slot['face'];self.element=self.slot['element'];self.serial=self.slot['serial']
        widget._tkwin11_choice=self
        self.use_style(self.public_style)
        for seq in ('<Enter>','<Leave>','<ButtonPress-1>','<ButtonRelease-1>','<FocusIn>','<FocusOut>','<KeyPress>','<KeyRelease>'):
            widget.bind(seq,self.event,add='+')
        if self.kind=='radio':
            for key,step in (('<Left>',-1),('<Up>',-1),('<Right>',1),('<Down>',1)):
                widget.bind(key,lambda event,d=step:self.navigate(d))
        widget.bind('<Destroy>',self.destroy,add='+');self.sync()

    def navigate(self,step):
        if self.widget.instate(['disabled']):return 'break'
        name=str(self.widget.cget('variable'))
        siblings=[w for w in self.widget.master.winfo_children() if isinstance(w,ttk.Radiobutton)
                  and str(w.cget('variable'))==name and w.winfo_ismapped() and not w.instate(['disabled'])]
        if self.widget in siblings:
            target=siblings[(siblings.index(self.widget)+step)%len(siblings)]
            self.root._button_keyboard=True;target.focus_set()
            if not target.instate(['selected']):target.invoke()
        return 'break'

    def use_style(self,base):
        self.public_style=str(base);self.base=str(base) or self.widget.winfo_class()
        if self.base not in ('TCheckbutton','TRadiobutton'):
            self.style=self.base;self.adapter.values['style']=self.base;self.adapter.apply();self.animator.cancel();return
        self.style=f'ChoiceMotion{self.serial}.{self.base}'
        prefix='Radiobutton' if self.kind=='radio' else 'Checkbutton'
        layouts=self.slot.setdefault('layouts',set())
        with additive_styles(self.widget):
            if self.base not in layouts:
                # Declared once per pooled slot; repeats would relayout the whole app.
                layouts.add(self.base)
                ttk.Style(self.widget).layout(self.style,[(prefix+'.padding',{'sticky':'nsew','children':[
                    (self.element,{'side':'left'}),(prefix+'.label',{'side':'left','sticky':'w'})]})])
            self.adapter.values['style']=self.style;self.adapter.apply()
        self.schedule()

    def variable_changed(self,*args):
        if args and args[-1]=='unset':self.variable=None
        self.schedule()

    def sync(self):
        if self.closed:return
        if self.job is not None:self.widget.after_cancel(self.job);self.job=None
        if self.base not in ('TCheckbutton','TRadiobutton'):return
        name=str(self.widget.cget('variable'))
        if name!=self.variable:
            if self.variable:self.widget.tk.call('trace','remove','variable',self.variable,('write','unset'),self.trace_command)
            self.variable=name
            if name:self.widget.tk.call('trace','add','variable',name,('write','unset'),self.trace_command)
        states=set(self.widget.state());target=float('selected' in states or self.kind=='check' and 'alternate' in states)
        if 'disabled' in states:
            self.animator.cancel();self.target=target;self.draw(target)
        elif target!=self.target:
            self.target=target;self.animator.start(self.position,target,self.draw,duration=167)
        else:self.draw(self.position)

    def draw(self,value):
        self.position=value;states=set(self.widget.state())
        disabled='disabled' in states;pressed='pressed' in states and not disabled;hover='active' in states and not disabled
        mixed='alternate' in states and self.kind=='check'
        focus='focus' in states and not disabled and getattr(self.root,'_button_keyboard',False)
        self.focus_visible=focus
        index=round(value*12);t=index/12
        cache=getattr(self.root,'_choice_frames',None)
        if cache is None:cache=self.root._choice_frames={}
        key=(self.kind,self.scale,index,disabled,pressed,hover,mixed,focus,PALETTE['accent'])
        if key not in cache:
            bg=PALETTE['bg'];accent='#B8B8B8' if disabled else '#3184CA' if pressed else PALETTE['accent_hover'] if hover else PALETTE['accent']
            empty=PALETTE['active'] if pressed else PALETTE['hover'] if hover else bg
            fill=_rgb(blend(empty,accent,t));edge=_rgb(blend('#B8B8B8' if disabled else '#A0A0A0' if pressed else '#606060',accent,t))
            def sample(x,y):
                x/=self.scale;y/=self.scale
                focus_edge=math.hypot(max(abs(x-12)-8,0),max(abs(y-14)-8,0))
                if focus and 3<focus_edge<=4:return _rgb(PALETTE['fg'])
                if self.kind=='radio':
                    d=math.hypot(x-12,y-14)
                    if d>10:return _rgb(bg)
                    if d<((4 if pressed else 5 if hover else 4.5)*t):return _rgb('#FFFFFF')
                    return edge if d>8.5 else fill
                dx=max(abs(x-12)-6,0);dy=max(abs(y-14)-6,0)
                d=math.hypot(dx,dy)
                if d>4:return _rgb(bg)
                if t:
                    if mixed:
                        marked=distance(x,y,(7,14),(7+10*t,14))<1
                    else:
                        progress=t*17
                        first=min(1,progress/5.66);second=max(0,min(1,(progress-5.66)/11.32))
                        marked=distance(x,y,(6,14),(6+4*first,14+4*first))<1
                        if second:marked=marked or distance(x,y,(10,18),(10+8*second,18-8*second))<1
                    if marked:return _rgb('#FFFFFF')
                return edge if d>2.5 else fill
            cache[key]=_raster(self.root,round(28*self.scale),round(28*self.scale),sample)
        self.widget.tk.call(self.face,'copy',cache[key])

    def destroy(self,event):
        if event.widget is not self.widget:return
        self.closed=True;self.animator.cancel()
        if self.job is not None:self.widget.after_cancel(self.job);self.job=None
        if self.variable:self.widget.tk.call('trace','remove','variable',self.variable,('write','unset'),self.trace_command)
        self.widget.deletecommand(self.trace_command);self.slot['used']=False


def attach_selection(widget):
    if not isinstance(widget,(ttk.Checkbutton,ttk.Radiobutton)):return
    resources=getattr(widget._root(),'_dpi_resources',None)
    if resources is None or resources.manual(widget):return
    if getattr(widget,'_tkwin11_choice',None):return
    if (str(widget.cget('style')) or widget.winfo_class()) not in ('TCheckbutton','TRadiobutton'):return
    SelectionController(widget)


def install_selection(root):
    if not getattr(root,'_selection_installed',False):
        on_map(root,attach_selection);root._selection_installed=True
    def walk(widget):
        attach_selection(widget)
        for child in widget.winfo_children():walk(child)
    walk(root)
