"""Fluent surfaces around native Tk input engines."""
import math
import tkinter as tk
from tkinter import ttk
from .core import PALETTE
from .tcl_bridge import unbind, on_map

class InputShell(ttk.Frame):
    def destroy(self):
        if getattr(self,'closing',False):return
        self.closing=True
        if self.content.winfo_exists():self.content_destroy()
        super().destroy()

class InputController:
    def __init__(self,widget):
        self.widget=widget;self.root=widget._root();self.closed=False;self.job=None
        self.readonly=False;self.hover=False
        self.resources=self.root._dpi_resources;self.resources.attach(widget)
        self.adapter=self.resources.widgets.get(str(widget))
        self.shell=getattr(widget,'container',None);self.classic=not isinstance(widget,ttk.Widget)
        self.takefocus=widget.cget('takefocus')
        self.auto_number=isinstance(widget,(ttk.Spinbox,tk.Spinbox)) and not any(str(widget.cget(key)) for key in ('command','validatecommand')) and not widget.bind('<Return>') and not widget.bind('<FocusOut>')
        self.last_valid=widget.get() if self.auto_number else ''
        widget._tkwin11_input=self
        if self.classic and self.shell is None:self.wrap()
        self.tag=f'FluentInput{id(self)}';tags=widget.bindtags();widget.bindtags((tags[0],self.tag,*tags[1:]))
        self.bindings=[]
        if self.shell:
            self.shell_click=self.shell.bind('<ButtonPress-1>',lambda e:widget.focus_set() if not self.closed and not self.disabled() else None,add='+')
        for event,callback in [('<Enter>',lambda e:self.enter(True)),('<Leave>',lambda e:self.enter(False)),('<FocusIn>',self.focus_in),('<FocusOut>',self.focus_out),('<ButtonRelease-1>',self.remember_number),('<KeyRelease-Up>',self.remember_number),('<KeyRelease-Down>',self.remember_number),('<Control-a>',self.select_all),('<Control-A>',self.select_all),('<KeyPress>',self.key),('<<Paste>>',self.block_edit),('<<Cut>>',self.block_edit),('<<PasteSelection>>',self.block_edit),('<<Undo>>',self.block_edit),('<<Redo>>',self.block_edit),('<<Clear>>',self.block_edit),('<ButtonRelease-2>',self.block_edit),('<Return>',self.commit),('<Destroy>',self.destroy)]:
            self.bindings.append((event,widget.bind_class(self.tag,event,callback)))
        self.sync()

    def wrap(self):
        w=self.widget;manager=w.winfo_manager();info=getattr(w,manager+'_info')() if manager in ('pack','grid','place') else {}
        if manager=='pack':
            siblings=info.get('in',w.master).pack_slaves()
            index=siblings.index(w)
            if index+1<len(siblings):info['before']=siblings[index+1]
        if manager:getattr(w,manager+'_forget')()
        scale=self.resources.base_dpi/96
        padding=(round(6*scale),round(4*scale)) if isinstance(w,tk.Text) else (round(9*scale),round(6*scale))
        self.shell=InputShell(w.master,style='RoundedText.TFrame',padding=padding)
        self.shell.content=w;self.shell.content_destroy=w.destroy
        self.shell.rowconfigure(0,weight=1);self.shell.columnconfigure(0,weight=1);w.container=self.shell
        tk.Grid.grid_configure(w,in_=self.shell,row=0,column=0,sticky='nsew')
        w.tk.call('raise',str(w),str(self.shell))
        self.shell.bind('<Map>',lambda e:w.tk.call('raise',str(w),str(self.shell)) if e.widget is self.shell and w.winfo_exists() else None,add='+')
        for name in ('pack','pack_configure','pack_forget','pack_info','grid','grid_configure','grid_forget','grid_remove','grid_info','place','place_configure','place_forget','place_info'):
            setattr(w,name,getattr(self.shell,name))
        w.destroy=self.shell.destroy
        if manager:getattr(self.shell,manager)(**info)

    def disabled(self):
        return self.widget.instate(['disabled']) if isinstance(self.widget,ttk.Widget) else str(self.widget.cget('state'))=='disabled'

    def enter(self,active):self.hover=active;self.schedule()

    def schedule(self,event=None):
        if not self.closed and self.job is None:self.job=self.widget.after_idle(self.sync)

    def sync(self):
        if self.closed:return
        if self.job is not None:self.widget.after_cancel(self.job);self.job=None
        w=self.widget;disabled=self.disabled()
        if self.classic:
            bg=PALETTE['sidebar'] if disabled else PALETTE['hover'] if self.hover and w.focus_get() is not w else PALETTE['bg']
            options={'background':bg,'foreground':PALETTE['disabled'] if disabled else PALETTE['fg'],'insertbackground':PALETTE['fg'],'selectbackground':PALETTE['selected'],'selectforeground':PALETTE['fg'],'borderwidth':0,'highlightthickness':0,'relief':'flat','takefocus':0 if disabled else self.takefocus}
            if isinstance(w,(tk.Entry,tk.Spinbox)):
                options.update(disabledbackground=PALETTE['sidebar'],disabledforeground=PALETTE['disabled'],readonlybackground=bg)
            command=self.adapter.native if self.adapter is not None else str(w)
            w.tk.call(command,'configure',*(v for pair in options.items() for v in ('-'+pair[0],pair[1])))
            if self.shell:self.shell.state(['disabled' if disabled else '!disabled','active' if self.hover and not disabled else '!active','focus' if w.focus_get() is w and not disabled else '!focus'])

    def select_all(self,event=None):
        if self.disabled():return 'break'
        if isinstance(self.widget,tk.Text):self.widget.tag_add('sel','1.0','end-1c');self.widget.mark_set('insert','end-1c')
        else:self.widget.selection_range(0,'end');self.widget.icursor('end')
        return 'break'

    def block_edit(self,event=None):
        if self.readonly or self.disabled():return 'break'

    def key(self,event):
        if not self.readonly:return
        if event.keysym in ('Left','Right','Up','Down','Home','End','Prior','Next','Tab','ISO_Left_Tab','Escape','Shift_L','Shift_R','Control_L','Control_R'):return
        if event.state&4 and event.keysym.lower() in ('c','a','insert'):return
        return 'break'

    def focus_out(self,event):self.commit();self.schedule()

    def focus_in(self,event):self.remember_number();self.schedule()

    def remember_number(self,event=None):
        if not self.auto_number or self.disabled():return
        text=self.widget.get().strip()
        try:
            value=float(text)
            a=float(self.widget.cget('from'));b=float(self.widget.cget('to'))
            if math.isfinite(value) and (a==b==0 or min(a,b)<=value<=max(a,b)):self.last_valid=text
        except ValueError:pass

    def commit(self,event=None):
        if self.readonly and isinstance(self.widget,tk.Text):return 'break' if event is not None else None
        if not self.auto_number or self.disabled():return
        w=self.widget
        if any(str(w.cget(key)) for key in ('command','validatecommand')) or str(w.cget('validate'))!='none' or w.bind('<Return>') or w.bind('<FocusOut>'):return
        if str(w.cget('state'))=='readonly' or w.cget('values'):return
        text=w.get().strip()
        if not text:return
        try:
            value=float(text)
            if not math.isfinite(value):raise ValueError
            a=float(w.cget('from'));b=float(w.cget('to'))
            if a!=b or a!=0:value=max(min(a,b),min(max(a,b),value))
            spec=str(w.cget('format'));replacement=str(w.tk.call('format',spec,value)) if spec else format(value,'.15g')
        except ValueError:replacement=self.last_valid
        if replacement!=w.get():
            if isinstance(w,ttk.Spinbox):w.set(replacement)
            elif w.cget('textvariable'):w.setvar(w.cget('textvariable'),replacement)
            else:w.delete(0,'end');w.insert(0,replacement)
        self.last_valid=replacement;self.schedule()

    def destroy(self,event):
        if event.widget is not self.widget:return
        self.closed=True
        if self.job is not None:self.widget.after_cancel(self.job)
        if self.shell is not None and self.shell.winfo_exists():unbind(self.shell,'<ButtonPress-1>',self.shell_click)
        for sequence,binding in self.bindings:
            self.widget.unbind_class(self.tag,sequence);self.widget._root().deletecommand(binding)

def attach_input(widget):
    if not isinstance(widget,(tk.Text,tk.Entry,tk.Spinbox,ttk.Entry)) or isinstance(widget,ttk.Combobox):return None
    current=getattr(widget,'_tkwin11_input',None)
    if current:return current
    resources=getattr(widget._root(),'_dpi_resources',None)
    if resources is None or resources.manual(widget):return None
    return InputController(widget)

def install_inputs(root):
    if not getattr(root,'_inputs_installed',False):
        on_map(root,attach_input);root._inputs_installed=True
    def walk(widget):
        attach_input(widget)
        for child in widget.winfo_children():walk(child)
    walk(root)

def set_readonly(widget,readonly=True):
    controller=attach_input(widget)
    if isinstance(widget,tk.Text):
        if controller is None:raise ValueError('请先对窗口调用 apply_theme()')
        controller.readonly=bool(readonly);controller.schedule()
    else:widget.configure(state='readonly' if readonly else 'normal')
