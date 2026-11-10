"""Keyboard behavior for unmodified ttk Notebook and Panedwindow entry points."""
import tkinter as tk
from tkinter import ttk
from .tcl_bridge import on_map


def attach_layout(widget):
    if getattr(widget,'_fluent_layout',False):return
    if isinstance(widget,tk.Listbox):
        from .core import PALETTE
        widget._fluent_layout=True
        widget.configure(background=PALETTE['bg'],foreground=PALETTE['fg'],
                         selectbackground=PALETTE['selected'],selectforeground=PALETTE['accent'],
                         disabledforeground=PALETTE['disabled'],activestyle='none',exportselection=False,
                         relief='flat',borderwidth=0,highlightthickness=1,highlightbackground=PALETTE['border'],highlightcolor=PALETTE['accent'])
    elif isinstance(widget,ttk.Treeview):
        widget._fluent_layout=True
        tag='FluentTreeInput'
        tags=widget.bindtags();widget.bindtags((tags[0],tag,*tags[1:]))
    elif isinstance(widget,ttk.Notebook):
        widget._fluent_layout=True;widget.enable_traversal()
        def edge(last=False):
            tabs=[t for t in widget.tabs() if widget.tab(t,'state')=='normal']
            if tabs:widget.select(tabs[-1] if last else tabs[0])
            return 'break'
        widget.bind('<Home>',lambda e:edge());widget.bind('<End>',lambda e:edge(True))
    elif isinstance(widget,ttk.Panedwindow):
        widget._fluent_layout=True;widget._fluent_sash=0;widget.configure(takefocus=True)
        def clicked(event):
            sash=widget.identify(event.x,event.y)
            if sash!='':widget._fluent_sash=int(sash);widget.focus_set()
        widget.bind('<ButtonPress-1>',clicked,add='+')
        def key(event):
            count=len(widget.panes())-1
            if count<=0 or widget.instate(['disabled']):return 'break'
            index=min(widget._fluent_sash,count-1)
            if event.keysym=='F6':widget._fluent_sash=(index+1)%count;return 'break'
            horizontal=str(widget.cget('orient'))=='horizontal'
            axis=('Left','Right') if horizontal else ('Up','Down')
            if event.keysym not in (*axis,'Home','End'):return
            size=widget.winfo_width() if horizontal else widget.winfo_height()
            step=1 if event.state&1 else 10
            position=0 if event.keysym=='Home' else size if event.keysym=='End' else widget.sashpos(index)+(-step if event.keysym==axis[0] else step)
            widget.sashpos(index,max(0,min(size,position)))
            return 'break'
        for name in ('Left','Right','Up','Down','Home','End','F6'):widget.bind('<'+name+'>',key)


def install_layout_compatibility(root):
    if not getattr(root,'_fluent_layouts',False):
        root._fluent_layouts=True
        def blocked(event):
            if event.widget.instate(['disabled']):return 'break'
        for sequence in ('<ButtonPress>','<ButtonRelease>','<KeyPress>','<B1-Motion>'):
            root.bind_class('FluentTreeInput',sequence,blocked)
        on_map(root,attach_layout)
    def walk(widget):
        attach_layout(widget)
        for child in widget.winfo_children():walk(child)
    walk(root)
