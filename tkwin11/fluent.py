"""Reusable Fluent-style layout helpers, built on the bundled TkWin11 controls."""
import sys
import tkinter as tk
from tkinter import ttk
from .core import PALETTE, _patch, _raster, _rgb, ScrollArea, icon
from .motion import Tween, blend, prepare_popup, reveal_popup
from .window_chrome import WindowChrome
from .tcl_bridge import unbind

WORKSPACE = '#F7FAFC'


def configure_fluent(root):
    """Call after apply_theme. Keep native resizing, snapping and title bar."""
    window=root
    root=root._root()
    style = ttk.Style(root)
    scale = root.winfo_fpixels('1i') / 96
    style.configure('Workspace.TFrame', background=WORKSPACE)
    style.configure('Workspace.TLabel', background=WORKSPACE)
    style.configure('Workspace.Muted.TLabel', background=WORKSPACE, foreground=PALETTE['muted'])
    style.configure('Workspace.Section.TLabel', background=WORKSPACE, font=('Microsoft YaHei UI', 11, 'bold'))
    style.configure('CaptionTitle.TLabel',background=PALETTE['sidebar'],foreground=PALETTE['fg'])
    if not hasattr(root, '_fluent_surface'):
        root._fluent_surface = _patch(root, PALETTE['bg'], PALETTE['border'], WORKSPACE, round(7*scale))
        style.element_create('Fluent.Card.surface', 'image', root._fluent_surface, border=round(8*scale), sticky='nsew')
        style.layout('Fluent.Card.TFrame', [('Fluent.Card.surface', {'sticky': 'nsew'})])
    style.configure('Fluent.Card.TFrame', background=PALETTE['bg'])
    style.layout('Key.TLabel',[('Primary.TButton.surface',{'sticky':'nsew','children':[('Label.padding',{'sticky':'nsew','children':[('Label.label',{'sticky':'nsew'})]})]})])
    style.configure('Key.TLabel',foreground='white',font=('Segoe UI',10,'bold'),padding=(round(12*scale),round(9*scale)),anchor='center')
    style.map('Key.TLabel',foreground=[('disabled','#FFFFFF')])
    if not hasattr(root, '_caption_images'):
        root._caption_images = []
        for name, hover in [('Caption.TButton', '#E9EDF0'), ('CaptionClose.TButton', '#C42B1C')]:
            images = [_patch(root, fill, fill, WORKSPACE, 0) for fill in (WORKSPACE,hover,'#A82518' if name=='CaptionClose.TButton' else '#D9DEE2')]
            root._caption_images.extend(images)
            style.element_create(name+'.face','image',images[0],('disabled',images[0]),('pressed',images[2]),('active',images[1]),border=0,width=1,height=1,sticky='nsew')
            style.layout(name,[(name+'.face',{'sticky':'nsew','children':[('Button.padding',{'sticky':'nsew','children':[('Button.label',{'sticky':'nsew'})]})]})])
            style.configure(name,width=0,anchor='center')
    window.configure(background=WORKSPACE)
    if sys.platform == 'win32':
        import ctypes
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        # DWM gracefully ignores unsupported attributes on older Windows.
        corner = ctypes.c_int(2)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), 4)


class FluentWindowChrome(WindowChrome):
    """Integrated caption with native window move/resize behavior on Windows."""
    def __init__(self,root,toolbar,**kwargs):
        self.active=True
        self._control_job=None
        self._control_hover={}
        self.title_widgets=tuple(kwargs.pop('title_widgets',())) or tuple(w for w in kwargs.get('drag_widgets',()) if isinstance(w,ttk.Label))
        self._activation_job=None
        self._activation_tween=Tween(root)
        self._activation_amount=1.
        super().__init__(root,toolbar,**kwargs)
        self._focus_in=root.bind('<FocusIn>',self.schedule_activation,add='+')
        self._focus_out=root.bind('<FocusOut>',self.schedule_activation,add='+')
        if sys.platform == 'win32':
            px=lambda n:round(n*root.winfo_fpixels('1i')/96)
            self.controls.configure(style='Workspace.TFrame',width=px(138))
            self.controls.pack_configure(padx=0,fill='y')
            self.controls.pack_propagate(False)
            self.buttons['pin'].pack_forget()
            for index,key in enumerate(('minimize','maximize','close')):
                self.buttons[key].configure(padding=0)
                self.buttons[key].pack_forget()
                self.buttons[key].place(x=px(46*index),y=0,width=px(46),relheight=1)
                self.buttons[key].bind('<ButtonPress-1>',self.schedule_controls,add='+')
                self.buttons[key].bind('<ButtonRelease-1>',self.schedule_controls,add='+')
                self.paint_control(key)
            self.schedule_activation()

    def paint_control(self,key,hover=None):
        button=self.buttons.get(key)
        if button is None or not button.winfo_exists():return
        if hover is not None:self._control_hover[key]=hover
        hover=self._control_hover.get(key,False)
        disabled=self.buttons[key].instate(['disabled'])
        pressed=self.buttons[key].instate(['pressed']) and not disabled
        hover=hover and not disabled
        symbol='restore' if key=='maximize' and self.attached and self.u.IsZoomed(self.hwnd) else key
        bg=('#A82518' if key=='close' else '#D9DEE2') if pressed else ('#C42B1C' if key=='close' else '#E9EDF0') if hover else WORKSPACE
        color=PALETTE['disabled'] if disabled else '#FFFFFF' if key=='close' and (hover or pressed) else PALETTE['fg'] if self.active or hover else '#969696'
        signature=(symbol,bg,color)
        if self._drawn_controls.get(key)==signature:return
        self._drawn_controls[key]=signature
        self.buttons[key].configure(image=icon(self.root,symbol,self.icon_size,color=color,background=bg),style='CaptionClose.TButton' if key=='close' else 'Caption.TButton')
        self._sync_caption_edges()

    def schedule_controls(self,event=None):
        if self._control_job is None:self._control_job=self.root.after_idle(self.refresh_controls)

    def refresh_controls(self):
        self._control_job=None
        for key in ('minimize','maximize','close'):self.paint_control(key)

    def close(self):
        # A callback may veto closing. Keep chrome attached until it accepts.
        return self.close_action()


    def schedule_activation(self,event=None):
        if self._activation_job is not None:self.root.after_cancel(self._activation_job)
        self._activation_job=self.root.after(60,self.check_activation)

    def check_activation(self):
        self._activation_job=None
        if self.window_flicker_workaround and self._native_moving:
            self.schedule_activation()
            return
        focus=self.root.focus_displayof()
        window=focus.winfo_toplevel() if focus is not None else None
        active=window is self.root
        if window is not None and window.overrideredirect() and str(window.transient())==str(self.root):active=True
        self.set_active(active)

    def set_active(self,active):
        self.active=bool(active)
        def paint(amount):
            self._activation_amount=amount
            for widget in self.title_widgets:
                if widget.winfo_exists():widget.configure(foreground=blend('#969696',PALETTE['fg'],amount))
        self._activation_tween.start(self._activation_amount,float(active),paint,duration=83,linear=True)
        if sys.platform=='win32':
            for key in ('minimize','maximize','close'):self.paint_control(key)

    def detach(self):
        if self._control_job is not None:
            self.root.after_cancel(self._control_job)
            self._control_job=None
        if self._activation_job is not None:
            self.root.after_cancel(self._activation_job)
            self._activation_job=None
        self._activation_tween.cancel()
        for event,name in [('<FocusIn>','_focus_in'),('<FocusOut>','_focus_out')]:
            binding=getattr(self,name,None)
            if binding:
                unbind(self.root,event,binding)
                setattr(self,name,None)
        super().detach()

    def install_resize_handles(self):
        super().install_resize_handles()
        for name,handle in self.resize_handles.items():
            if 'left' not in name:
                handle.configure(background=WORKSPACE)
        self._sync_caption_edges()

    def layout_resize_handles(self):
        super().layout_resize_handles()
        self._sync_caption_edges()

    def _sync_caption_edges(self):
        if not self.resize_handles:return
        top=self.resize_handles.get('top')
        right=self.resize_handles.get('right')
        corner=self.resize_handles.get('topright')
        if top is None or not top.winfo_exists():return
        if not hasattr(top,'caption_fills'):top.caption_fills={}
        for key in ('minimize','maximize','close'):
            button=self.buttons[key]
            if not button.winfo_exists() or not button.winfo_ismapped():
                fill=top.caption_fills.get(key)
                if fill is not None:fill.place_forget()
                if key=='close':
                    corner.configure(background=WORKSPACE)
                    if hasattr(right,'caption_fill'):right.caption_fill.place_forget()
                continue
            signature=self._drawn_controls.get(key)
            bg=signature[1] if signature else WORKSPACE
            if key not in top.caption_fills:
                fill=tk.Frame(top,borderwidth=0,highlightthickness=0,cursor='size_ns')
                fill.bind('<ButtonPress-1>',lambda event:self.start_resize(3,event))
                top.caption_fills[key]=fill
            fill=top.caption_fills[key]
            fill.configure(background=bg)
            fill.place(x=button.winfo_rootx()-top.winfo_rootx(),y=0,width=button.winfo_width(),relheight=1)
            if key=='close':
                corner.configure(background=bg)
                if not hasattr(right,'caption_fill'):
                    right.caption_fill=tk.Frame(right,borderwidth=0,highlightthickness=0,cursor='size_we')
                    right.caption_fill.bind('<ButtonPress-1>',lambda event:self.start_resize(2,event))
                right.caption_fill.configure(background=bg)
                right.caption_fill.place(x=0,y=0,relwidth=1,height=max(0,button.winfo_rooty()+button.winfo_height()-right.winfo_rooty()))


class FluentPage(ScrollArea):
    def __init__(self, master, **kwargs):
        kwargs.setdefault('reserve_scrollbar',True)
        super().__init__(master, **kwargs)
        self._page_tween=Tween(self)
        self.bind('<Unmap>',lambda e:self._page_tween.cancel() if e.widget is self else None,add='+')
        self.configure(style='Workspace.TFrame')
        if self.gutter is not None:self.gutter.configure(style='Workspace.TFrame')
        self.canvas.configure(background=WORKSPACE)
        self.body.configure(style='Workspace.TFrame', padding=round(40*self.winfo_fpixels('1i')/96))

    def prepare(self,width,height):
        """Resolve layout outside the viewport before presenting a page."""
        self._page_tween.cancel()
        self._smooth.tween.cancel()
        self.place(x=width+8,y=0,width=width,height=height,relwidth=0,relheight=0)
        gutter=self.gutter.winfo_reqwidth() if self.gutter is not None else 0
        self.canvas.itemconfigure(self.window,width=max(1,width-gutter))
        def descendants(widget):
            yield widget
            for child in widget.winfo_children():yield from descendants(child)
        # Flush only geometry-related deferred work; never pump user events.
        previous=None
        for _ in range(3):
            self.update_idletasks()
            widgets=list(descendants(self))
            geometry=tuple((str(w),w.winfo_width(),w.winfo_height(),w.winfo_reqwidth(),w.winfo_reqheight()) for w in widgets)
            if geometry==previous:break
            previous=geometry
            for widget in widgets:
                if isinstance(widget,SettingsCard):
                    if widget._wrap_job is not None:
                        widget.after_cancel(widget._wrap_job)
                        widget._wrap_job=None
                    widget.refresh_wrap()
                if isinstance(widget,ScrollArea):
                    if widget._job is not None:
                        widget.after_cancel(widget._job)
                        widget._job=None
                    widget.refresh()
        self.update_idletasks()

    def present(self):
        resources=getattr(self._root(),'_dpi_resources',None)
        scale=resources.window_dpi(self.winfo_toplevel())/96 if resources else 1
        self.place(x=0,y=0,width=0,height=0,relwidth=1,relheight=1)
        self.lift()
        previous=getattr(self.master,'_fluent_presented_page',None)
        if previous is not None and previous is not self:previous._page_tween.cancel()
        self.master._fluent_presented_page=self
        def position(y):
            if self.winfo_manager()=='place' and getattr(self.master,'_fluent_presented_page',None) is self:self.place_configure(y=round(y))
        self._page_tween.start(round(10*scale),0,position,duration=167)


from .core import Card

class SettingsCard(Card):
    """A settings row: line icon, title/description and a trailing control area."""
    def __init__(self, master, title, description='', symbol='settings', *, command=None):
        px = lambda n: round(n*master.winfo_fpixels('1i')/96)
        super().__init__(master, padding=px(16),command=command)
        self.columnconfigure(1, weight=1)
        self._icon = icon(self, symbol, px(23), color=PALETTE['fg'])
        ttk.Label(self, image=self._icon).grid(row=0, column=0, rowspan=2, padx=(0,px(20)))
        self.title_label=ttk.Label(self, text=title)
        self.title_label.grid(row=0, column=1, sticky='w')
        self.description = ttk.Label(self, text=description, style='Muted.TLabel')
        if description:
            self.description.grid(row=1, column=1, sticky='w', pady=(px(4),0))
        self.actions = ttk.Frame(self)
        self.actions.grid(row=0, column=2, rowspan=2, padx=(px(16),0), sticky='e')
        self._wrap_job=None
        self._wrap_width=None
        self._px=px
        self.bind('<Configure>',self.schedule_wrap,add='+')
        self.bind('<Destroy>',self.cleanup_wrap,add='+')

    def schedule_wrap(self,event):
        if event.widget is self and self._wrap_job is None:
            self._wrap_job=self.after(16,self.refresh_wrap)

    def refresh_wrap(self):
        self._wrap_job=None
        resources=getattr(self._root(),'_dpi_resources',None)
        scale=resources.window_dpi(self.winfo_toplevel())/96 if resources else self.winfo_fpixels('1i')/96
        px=lambda n:round(n*scale)
        stacked=self.winfo_width()<px(600)
        if stacked!=getattr(self,'_stacked_actions',None):
            self._stacked_actions=stacked
            if stacked:self.actions.grid_configure(row=2,column=0,columnspan=3,rowspan=1,padx=0,pady=(px(12),0),sticky='ew')
            else:self.actions.grid_configure(row=0,column=2,columnspan=1,rowspan=2,padx=(px(16),0),pady=0,sticky='e')
        width=max(px(60),self.winfo_width()-px(110)-(0 if stacked else self.actions.winfo_reqwidth()))
        if width!=self._wrap_width:
            self._wrap_width=width
            self.description.configure(wraplength=width)
            self.title_label.configure(wraplength=width)

    def cleanup_wrap(self,event):
        if event.widget is self and self._wrap_job is not None:
            self.after_cancel(self._wrap_job)
            self._wrap_job=None


from .navigation import NavigationView


class FluentSwitch(ttk.Frame):
    """Labeled switch, sharing the same controller as styled ttk.Checkbutton."""
    def __init__(self,master,variable=None,command=None):
        from .switches import attach_switch
        super().__init__(master)
        self.variable=variable if variable is not None else tk.BooleanVar(self,value=False)
        self.label=ttk.Label(self)
        self.label.pack(side='left',padx=(0,round(12*self.winfo_fpixels('1i')/96)))
        self.control=ttk.Checkbutton(self,variable=self.variable,command=command,style='Switch.TCheckbutton')
        self.control.pack(side='left')
        self.controller=attach_switch(self.control)
        self.animator=self.controller.animator
        self.controller.renderer=lambda value:self.draw(value)
        self.controller.on_change=self._changed
        self._changed(bool(self.controller.position),False)
        self.label.bind('<Enter>',lambda event:self.controller.feedback('hover',True))
        self.label.bind('<Leave>',lambda event:self.controller.feedback('hover',False))
        self.label.bind('<ButtonPress-1>',self.controller.mouse_press)
        self.label.bind('<ButtonRelease-1>',self._label_release)
        self.bind('<ButtonPress-1>',self.controller.mouse_press)
        self.bind('<ButtonRelease-1>',self.controller.mouse_release)
        self.bind('<Enter>',lambda event:self.controller.feedback('hover',True))
        self.bind('<Leave>',lambda event:self.controller.feedback('hover',False))

    def _label_release(self,event):
        return self.controller.mouse_release(event)

    @property
    def position(self):return self.controller.position

    def draw(self,value):self.controller.draw(value)

    def feedback(self,attribute,value):self.controller.feedback(attribute,value)

    def _changed(self,on,disabled):
        self.label.configure(text='开' if on else '关',foreground=PALETTE['disabled'] if disabled else PALETTE['fg'])

    def state(self,statespec=None):return self.control.state(statespec)


class ExpanderCard(SettingsCard):
    """Height-clipped content with interruptible expansion/collapse."""
    def __init__(self,master,title,description='',symbol='settings',opened=False):
        super().__init__(master,title,description,symbol)
        self.opened=opened
        self._height=0.
        self.toggle_button=ttk.Button(self.actions,width=2,style='Quiet.TButton',command=self.toggle)
        self.toggle_button.pack(side='right',padx=(14,0))
        self.viewport=ttk.Frame(self,height=1)
        self.viewport.grid_propagate(False)
        self.body=ttk.Frame(self.viewport,padding=(0,16,0,0))
        self.body.place(x=0,y=0,relwidth=1)
        from .expansion import Expansion
        self.expansion=Expansion(self,self.toggle_button,
            lambda:self.viewport.grid(row=3,column=1,columnspan=2,sticky='ew'),self.viewport.grid_remove)
        self.animator=self.expansion.animator
        self.arrow()

    def initialize(self):self.expansion.initialize()
    def arrow(self):
        self.expansion.arrow()
    def height(self,value):self.expansion.paint(value)
    def content_resized(self,event):self.expansion.resized(event)
    def toggle(self):return self.expansion.toggle()
    def sync(self):self.expansion.sync()
    def cleanup_expander(self,event):self.expansion.destroy(event)


class KeyChord(ttk.Frame):
    """Editable shortcut badges; dispatch is left to the host application."""
    def __init__(self,master,value='Alt+Shift+A',command=None):
        super().__init__(master)
        self.value,self.command=value,command
        self.badges=ttk.Frame(self)
        self.badges.pack(side='left')
        size=round(20*self.winfo_fpixels('1i')/96)
        self._edit_images=(icon(self,'edit',size),icon(self,'edit',size,color=PALETTE['disabled']))
        self.edit_button=ttk.Button(self,image=(self._edit_images[0],'disabled',self._edit_images[1]),style='Quiet.TButton',command=self.edit)
        self.edit_button.pack(side='left',padx=(12,0))
        self.draw()

    def draw(self):
        for child in self.badges.winfo_children():child.destroy()
        for part in self.value.split('+'):
            label=ttk.Label(self.badges,text=part,style='Key.TLabel')
            if self.instate(['disabled']):label.state(['disabled'])
            label.pack(side='left',padx=2)

    def state(self,statespec=None):
        result=super().state(statespec)
        if statespec is not None:
            disabled=self.instate(['disabled'])
            for widget in (self.edit_button,*self.badges.winfo_children()):
                widget.state(['disabled'] if disabled else ['!disabled'])
        return result

    def edit(self):
        if self.instate(['disabled']):return
        from .forms import Dialogs
        result=Dialogs.ask_string(self,'输入组合键，例如 Alt+Shift+A',title='编辑快捷键')
        if result:
            parts=[p.strip() for p in result.split('+')]
            if not 1<=len(parts)<=4 or any(not p or len(p)>10 for p in parts):
                Dialogs.warning(self,'请输入 1 至 4 个由 + 分隔的按键，每个按键不超过 10 个字符。')
                return
            self.value='+'.join(parts)
            self.draw()
            if self.command:self.command(self.value)


from .flyouts import FormFlyout
