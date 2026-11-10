"""Shared native ttk button feedback, including legacy construction."""
import tkinter as tk
from tkinter import ttk
from .core import PALETTE, _patch
from .dpi import additive_styles
from .tcl_bridge import on_map
from .motion import Tween, blend

STYLES={'TButton','Primary.TButton','Quiet.TButton','Close.TButton','CardIcon.TButton','Dropdown.TButton','Toggle.TButton','Toolbutton','TMenubutton','TOptionMenu','Popup.Quiet.TButton'}
SEMANTIC={'Primary':PALETTE['accent'],'Success':'#159576','Warning':'#B77717','Danger':'#D54B58','Info':'#197FA8','Neutral':'#657080'}
STYLES.update(name+'.'+variant+'TButton' for name in SEMANTIC for variant in ('','Outline.','Link.'))
STYLES.update(('Segment.TButton','Selected.Segment.TButton'))

def _face(root,fill,edge,background,radius):
    cache=getattr(root,'_button_face_cache',None)
    if cache is None:
        from collections import OrderedDict
        cache=root._button_face_cache=OrderedDict()
    key=(fill.lower(),edge.lower(),background.lower(),radius)
    image=cache.get(key)
    if image is None:
        image=cache[key]=_patch(root,fill,edge,background,radius)
        if len(cache)>192:cache.popitem(last=False)
    else:cache.move_to_end(key)
    return image


class ButtonController:
    def __init__(self,widget):
        self.widget=widget;self.root=widget._root()
        self.resources=self.root._dpi_resources;self.resources.attach(widget)
        self.adapter=self.resources.widgets[str(widget)]
        self.public_style=str(widget.cget('style'))
        self.base=self.public_style or widget.winfo_class()
        self.fill=PALETTE['bg'];self.focus_visible=False
        self.animator=Tween(widget);self.job=None;self.closed=False
        self.variable=None
        self.trace_command=widget.register(lambda *args:self.schedule())
        self.scale=self.resources.base_dpi/96
        pool=getattr(self.root,'_button_slots',None)
        if pool is None:pool=self.root._button_slots=[]
        self.slot=next((slot for slot in pool if not slot['used']),None)
        if self.slot is None:
            serial=len(pool)+1;element=f'ButtonMotion{serial}.surface'
            face=_patch(self.root,self.fill,PALETTE['border'],PALETTE['bg'],round(4*self.scale))
            style=ttk.Style(widget);border=round(4*self.scale)+1
            # padding=0 like the theme's own button surfaces this replaces; ttk
            # would default it to the border and grow the button on attach.
            with additive_styles(widget):
                style.element_create(element,'image',face,border=border,padding=0,width=border*2,height=border*2,sticky='nsew')
            self.slot={'serial':serial,'element':element,'face':face,'used':False};pool.append(self.slot)
        self.slot['used']=True
        self.serial=self.slot['serial'];self.element=self.slot['element'];self.face=self.slot['face']
        widget._tkwin11_button=self
        self.use_style(self.public_style)
        for sequence in ('<Enter>','<Leave>','<ButtonPress-1>','<ButtonRelease-1>','<FocusIn>','<FocusOut>','<KeyPress>','<KeyRelease>'):
            widget.bind(sequence,self.event,add='+')
        widget.bind('<Destroy>',self.destroy,add='+');self.sync()

    def use_style(self,base):
        self.public_style=str(base)
        self.base=str(base) or self.widget.winfo_class()
        if self.base not in STYLES:
            self.style=self.base;self.adapter.values['style']=self.base
            self.animator.cancel();self.adapter.apply();return
        self.style=f'ButtonMotion{self.serial}.{self.base}'
        style=ttk.Style(self.widget);layout=style.layout(self.base)
        if layout:
            _,options=layout[0];layout[0]=(self.element,options)
        # Pooled slots are reused by every popup row. Re-declaring an identical
        # layout would still broadcast <<ThemeChanged>> to the whole app.
        layouts=self.slot.setdefault('layouts',{})
        # The slot is this widget's alone, and apply() below relayouts it.
        with additive_styles(self.widget):
            if layouts.get(self.base)!=repr(layout):
                style.layout(self.style,layout);layouts[self.base]=repr(layout)
            self.adapter.values['style']=self.style;self.adapter.apply()
        self.schedule()

    def event(self,event):
        if event.type==tk.EventType.ButtonPress:self.root._button_keyboard=False
        elif event.type==tk.EventType.KeyPress:self.root._button_keyboard=True
        elif event.type==tk.EventType.FocusIn:
            import sys
            if sys.platform=='win32':
                import ctypes
                if ctypes.windll.user32.GetKeyState(9)&0x8000:self.root._button_keyboard=True
        self.schedule()

    def image_spec(self,value):
        parts=self.widget.tk.splitlist(value)
        if len(parts)!=1:return value
        name=str(parts[0])
        if str(self.widget.tk.call('image','type',name))!='photo':return value
        cache=getattr(self.root,'_button_disabled_icons',None)
        if cache is None:cache=self.root._button_disabled_icons={}
        if name not in cache:
            factory=self.resources.image_factories.get(name)
            disabled_factory=getattr(factory,'disabled_factory',None)
            if disabled_factory is not None:
                image=disabled_factory(1);self.resources.image_factories[str(image)]=disabled_factory
                cache[name]=image
                return (name,'disabled',str(image))
            spec=getattr(factory,'cache_key',())
            if spec and spec[0]=='icon':
                from .core import icon
                # Preserve the vector factory and partial alpha for disabled
                # icons too; pixel mutation would discard their DPI factory.
                cache[name]=icon(self.root,spec[1],spec[2],color=blend(spec[3],PALETTE['bg'],.65))
                return (name,'disabled',str(cache[name]))
            width=int(self.widget.tk.call('image','width',name));height=int(self.widget.tk.call('image','height',name))
            if width*height>16384:return value
            image=tk.PhotoImage(master=self.root,width=width,height=height)
            image.tk.call(image,'copy',name)
            bg=tuple(int(PALETTE['bg'][i:i+2],16) for i in (1,3,5))
            for y in range(height):
                for x in range(width):
                    if self.widget.tk.getboolean(self.widget.tk.call(name,'transparency','get',x,y)):continue
                    color=self.widget.tk.call(name,'get',x,y)
                    muted='#'+''.join(f'{round(int(a)*.35+b*.65):02x}' for a,b in zip(color,bg))
                    image.put(muted,(x,y))
            cache[name]=image
        return (name,'disabled',str(cache[name]))

    def schedule(self,event=None):
        if not self.closed and self.job is None:self.job=self.widget.after_idle(self.sync)

    def sync(self):
        if self.closed:return
        if self.job is not None:self.widget.after_cancel(self.job);self.job=None
        if self.base not in STYLES:return
        if isinstance(self.widget,ttk.Checkbutton):
            variable=str(self.widget.cget('variable'))
            if variable!=self.variable:
                if self.variable:
                    self.widget.tk.call('trace','remove','variable',self.variable,'write',self.trace_command)
                self.variable=variable
                if variable:self.widget.tk.call('trace','add','variable',variable,'write',self.trace_command)
        states=set(self.widget.state());disabled='disabled' in states
        primary=self.base=='Primary.TButton';quiet=self.base in ('Quiet.TButton','Toolbutton','Popup.Quiet.TButton')
        selected='selected' in states
        background=PALETTE['selected'] if self.base=='CardIcon.TButton' else PALETTE['bg']
        normal=PALETTE['accent'] if primary else PALETTE['selected'] if selected else background
        if disabled:target='#C4C4C4' if primary else background if quiet else PALETTE['hover']
        elif 'pressed' in states:target='#3184CA' if primary else '#DCEAF7' if selected else PALETTE['active']
        elif 'active' in states:target=PALETTE['accent_hover'] if primary else '#DCEAF7' if selected else '#FFE8E8' if self.base=='Close.TButton' else PALETTE['hover']
        else:target=normal
        self.focus_visible='focus' in states and not disabled and getattr(self.root,'_button_keyboard',False)
        edge=PALETTE['fg'] if self.focus_visible else target if quiet or primary else PALETTE['accent'] if selected and not disabled else PALETTE['border']
        if self.base in ('Segment.TButton','Selected.Segment.TButton'):
            background=PALETTE['hover']
            normal=PALETTE['bg'] if self.base.startswith('Selected.') else background
            target=background if disabled else PALETTE['active'] if 'pressed' in states else PALETTE['hover'] if 'active' in states else normal
            edge=PALETTE['fg'] if self.focus_visible else target
        prefix=self.base.split('.')[0]
        if prefix in SEMANTIC and self.base!='Primary.TButton':
            color=PALETTE['accent'] if prefix=='Primary' else SEMANTIC[prefix]
            outline='.Outline.' in self.base;link='.Link.' in self.base
            if disabled:target=background if link else PALETTE['hover']
            elif 'pressed' in states:target=blend(color,'#FFFFFF',.72 if outline or link else .2)
            elif 'active' in states:target=blend(color,'#FFFFFF',.86 if outline or link else .1)
            else:target=background if outline or link else color
            edge=PALETTE['fg'] if self.focus_visible else target if link or not outline else PALETTE['border'] if disabled else color
        signature=(target,edge,background)
        if getattr(self,'_paint_target',None)==signature:return
        self._paint_target=signature
        start=self.fill
        def paint(amount):
            # Eight steps per transition keep the 83 ms fade smooth while every
            # frame becomes a cache hit after first use (no PhotoImage churn).
            self.fill=target if amount>=1 else blend(start,target,round(amount*8)/8)
            self.widget.tk.call(self.face,'copy',_face(self.root,self.fill,edge,background,round(4*self.scale)))
        if disabled or start.lower()==target.lower():self.animator.cancel();paint(1)
        else:self.animator.start(0.,1.,paint,duration=83,linear=True)

    def destroy(self,event):
        if event.widget is not self.widget:return
        self.closed=True
        if self.job is not None:self.widget.after_cancel(self.job);self.job=None
        self.animator.cancel()
        if self.variable:
            self.widget.tk.call('trace','remove','variable',self.variable,'write',self.trace_command)
        self.widget.deletecommand(self.trace_command)
        self.slot['used']=False

def attach_button(widget):
    if not isinstance(widget,(ttk.Button,ttk.Checkbutton,ttk.Menubutton)):return None
    resources=getattr(widget._root(),'_dpi_resources',None)
    if resources is None or resources.manual(widget):return None
    current=getattr(widget,'_tkwin11_button',None)
    if current:return current
    base=str(widget.cget('style')) or widget.winfo_class()
    if base not in STYLES:return None
    if isinstance(widget,ttk.Checkbutton) and base not in ('Toggle.TButton','Toolbutton'):return None
    return ButtonController(widget)

def install_button_compatibility(root):
    if not getattr(root,'_button_compatibility',False):
        on_map(root,attach_button)
        root.bind_all('<KeyPress>',lambda event:setattr(root,'_button_keyboard',True),add='+')
        def pointer(event):
            root._button_keyboard=False
            focused=root.focus_get()
            controller=getattr(focused,'_tkwin11_button',None) or getattr(focused,'_tkwin11_choice',None)
            if controller is not None:controller.schedule()
        root.bind_all('<ButtonPress-1>',pointer,add='+')
        root._button_compatibility=True
    def walk(widget):
        attach_button(widget)
        for child in widget.winfo_children():walk(child)
    walk(root)

class FluentButton(ttk.Button):
    """Identical controller and interaction to themed ttk.Button."""
    def __init__(self,master=None,**kwargs):
        super().__init__(master,**kwargs)
        self.controller=attach_button(self)
        self.animator=self.controller.animator if self.controller is not None else Tween(self)
