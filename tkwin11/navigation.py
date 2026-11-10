"""Hierarchical Fluent navigation. Layout, feedback and motion belong to the package."""
import math
import tkinter as tk
from tkinter import ttk
from .core import PALETTE, ScrollArea, icon, _raster, _rgb
from .motion import Tween


class _Pill(tk.Canvas):
    def __init__(self,master,px):
        self.px=px
        super().__init__(master,width=px(3),height=px(18),highlightthickness=0,borderwidth=0,takefocus=False)
        self.item=self.create_image(0,0,anchor='nw')

    def paint(self,height,background,enabled=True):
        w,h=self.px(3),max(self.px(3),round(height))
        root=self._root()
        if not hasattr(root,'_nav_pills'):root._nav_pills={}
        color=PALETTE['accent'] if enabled else PALETTE['disabled']
        key=(w,h,background,color)
        if key not in root._nav_pills:
            fg,bg=_rgb(color),_rgb(background)
            def sample(x,y):
                cy=min(max(y,w/2),h-w/2)
                return fg if math.hypot(x-w/2,y-cy)<=w/2 else bg
            root._nav_pills[key]=_raster(root,w,h,sample)
        self.configure(width=w,height=h,background=background)
        self.itemconfigure(self.item,image=root._nav_pills[key])


class _Node:
    def __init__(self,view,key,title,symbol,parent,group,opened,bottom):
        self.view,self.key,self.parent=view,key,parent
        self.group,self.opened,self.enabled=group,opened,True
        self.children=[]
        self.depth=0 if parent is None else parent.depth+1
        host=view.body if parent is None else parent.body
        self.frame=ttk.Frame(host,style='Sidebar.TFrame')
        self.frame.pack(side='bottom' if bottom else 'top',fill='x')
        self.header=ttk.Frame(self.frame,style='Sidebar.TFrame',height=view.px(42))
        self.header.pack(fill='x');self.header.pack_propagate(False)
        self.button=ttk.Button(self.header,text='   '+title,image=icon(view,symbol,view.px(20),color=PALETTE['fg'],background=PALETTE['sidebar']),compound='left',style='Nav.TButton',padding=(view.px(10+20*self.depth),view.px(8),view.px(30 if group else 10),view.px(8)),command=lambda:view.toggle_group(key) if group else view.select(key))
        self.button.pack(fill='both',expand=True,padx=view.px(8),pady=view.px(3))
        self.marker=_Pill(self.header,view.px)
        self.marker.bind('<Button-1>',lambda e:self.button.invoke())
        for event,action in [('<Up>','up'),('<Down>','down'),('<Home>','first'),('<End>','last'),('<Right>','right'),('<Left>','left')]:
            self.button.bind(event,lambda e,a=action:view._key(key,a))
        self.button.bind('<FocusIn>',lambda e:view._schedule_visible(key),add='+')
        if group:
            self.arrow=ttk.Label(self.button,style='Sidebar.TLabel')
            self.arrow.place(relx=1,x=-view.px(12),rely=.5,anchor='e')
            self.arrow.bind('<Button-1>',lambda e:self.button.invoke())
            self.button.bind('<Enter>',lambda e:view._group_feedback(self,True),add='+')
            self.button.bind('<Leave>',lambda e:view._group_feedback(self,False),add='+')
            self.arrow.bind('<Enter>',lambda e:view._group_feedback(self,True))
            self.arrow.bind('<Leave>',lambda e:view._group_feedback(self,False))
            self.viewport=ttk.Frame(self.frame,style='Sidebar.TFrame',height=1)
            self.viewport.pack_propagate(False)
            self.body=ttk.Frame(self.viewport,style='Sidebar.TFrame')
            self.body.place(x=0,y=0,relwidth=1)
            self.height=0.
            self.motion=Tween(self.frame)
            self.body.bind('<Configure>',self.resized,add='+')
            if opened:self.viewport.pack(fill='x')

    def resize(self,value):
        self.height=value
        self.viewport.configure(height=max(1,round(value)))

    def resized(self,event):
        if self.opened and self.motion.job is None:
            size=self.body.winfo_reqheight()
            if size!=round(self.height):self.resize(size)


class NavigationView(ttk.Frame):
    """Declare add()/add_group(); package handles nested layout, scrolling and animation.

    Groups toggle only; leaf selection calls command(key). select() expands ancestors.
    Closing a selected group retains the page and highlights its collapsed parent.
    """
    def __init__(self,master,command,width=320):
        self.px=lambda n:max(1,round(n*master.winfo_fpixels('1i')/96))
        super().__init__(master,style='Sidebar.TFrame',width=self.px(width))
        self.pack_propagate(False)
        self.command=command
        self.items={}
        self.nodes={}
        self.groups={}
        self.roots=[]
        self.selected_key=None
        self._scroll_job=None
        self.bind('<Destroy>',self._cleanup,add='+')
        self._marked=None
        self.indicator_motion=Tween(self)
        self.area=ScrollArea(self,style='Sidebar.TFrame',reserve_scrollbar=True)
        self.area.pack(fill='both',expand=True)
        self.area.canvas.configure(background=PALETTE['sidebar'])
        self.area.gutter.configure(style='Sidebar.TFrame')
        self.body=self.area.body
        self.body.configure(style='Sidebar.TFrame')

    def section(self,text):
        ttk.Label(self.body,text=text,style='Sidebar.Muted.TLabel').pack(anchor='w',padx=self.px(22),pady=(self.px(20),self.px(8)))

    def _add(self,key,title,symbol,parent,group,opened=False,bottom=False):
        if key in self.nodes:raise ValueError(f'Duplicate navigation key: {key}')
        ancestor=None if parent is None else self.groups[parent]
        node=_Node(self,key,title,symbol,ancestor,group,opened,bottom)
        self.nodes[key]=node
        (self.roots if ancestor is None else ancestor.children).append(node)
        if group:self.groups[key]=node
        else:self.items[key]=(node.button,node.marker)
        self._paint()
        return node.button

    def add(self,key,title,symbol='controls',bottom=False,*,parent=None):
        return self._add(key,title,symbol,parent,False,bottom=bottom)

    def add_group(self,key,title,symbol='folder',*,opened=True,parent=None):
        return self._add(key,title,symbol,parent,True,opened)

    def _enabled(self,node):
        while node is not None:
            if not node.enabled:return False
            node=node.parent
        return True

    def _visible(self,node):
        node=node.parent
        while node is not None:
            if not node.opened:return False
            node=node.parent
        return True

    def set_enabled(self,key,enabled=True):
        self.nodes[key].enabled=bool(enabled)
        self._paint()

    def toggle_group(self,key):
        self.set_expanded(key,not self.groups[key].opened)

    def set_expanded(self,key,expanded=True,*,animate=True):
        node=self.groups[key]
        if not self._enabled(node):return
        expanded=bool(expanded)
        if node.opened==expanded:return
        node.opened=expanded
        focus=self.focus_get()
        if not expanded and focus is not None and str(focus).startswith(str(node.viewport)+'.'):
            node.button.focus_set()
        node.viewport.pack(fill='x')
        target=node.body.winfo_reqheight() if expanded else 0
        def done():
            if not node.opened:node.viewport.pack_forget()
        if animate:node.motion.start(node.height,target,node.resize,duration=167,done=done)
        else:
            node.motion.cancel();node.resize(target);done()
        self._paint()

    def select(self,key,notify=True):
        node=self.nodes[key]
        if not self._enabled(node):return False
        if node.group:
            self.toggle_group(key);return False
        ancestors=[];parent=node.parent
        while parent is not None:ancestors.append(parent);parent=parent.parent
        for ancestor in reversed(ancestors):self.set_expanded(ancestor.key,True,animate=False)
        self.selected_key=key
        self._paint()
        if notify:self.command(key)
        return True

    def _paint(self):
        representative=self.nodes.get(self.selected_key)
        if representative is not None:
            ancestor=representative.parent
            while ancestor is not None:
                if not ancestor.opened:representative=ancestor
                ancestor=ancestor.parent
        for node in self.nodes.values():
            selected=node is representative
            node.button.configure(style='Selected.Nav.TButton' if selected else 'Nav.TButton',takefocus=self._visible(node) and self._enabled(node))
            node.button.state(['!disabled' if self._enabled(node) else 'disabled'])
            if node.group:
                self._group_feedback(node,node.button.instate(['active']),selected)
            if selected:
                # A repaint (select(notify=False) from the page command) must
                # not snap a still-growing indicator to full height.
                if node.key!=self._marked or self.indicator_motion.job is None:
                    node.marker.paint(self.px(18),PALETTE['active'],self._enabled(node))
                node.marker.place(x=self.px(8+20*node.depth),rely=.5,anchor='w')
            else:node.marker.place_forget()
        if representative is not None and representative.key!=self._marked:
            self.indicator_motion.cancel()
            marker=representative.marker
            self.indicator_motion.start(self.px(5),self.px(18),lambda value:marker.paint(value,PALETTE['active'],self._enabled(representative)),duration=167)
        self._marked=None if representative is None else representative.key

    def _group_feedback(self,node,hover=False,selected=None):
        if selected is None:selected=str(node.button.cget('style'))=='Selected.Nav.TButton'
        allowed=self._enabled(node)
        bg=PALETTE['hover'] if not allowed else PALETTE['active'] if selected or hover else PALETTE['sidebar']
        node.arrow.configure(background=bg,image=icon(self,'chevrondown' if node.opened else 'chevronright',self.px(12),color=PALETTE['fg'] if allowed else PALETTE['disabled'],background=bg))

    def _key(self,key,action):
        node=self.nodes[key]
        visible=[]
        def walk(nodes):
            for item in nodes:
                if self._enabled(item):visible.append(item)
                if item.group and item.opened:walk(item.children)
        walk(self.roots)
        visible.sort(key=lambda item:item.header.winfo_rooty())
        if action in ('up','down','first','last') and visible:
            index=visible.index(node) if node in visible else 0
            index=0 if action=='first' else len(visible)-1 if action=='last' else max(0,min(len(visible)-1,index+(-1 if action=='up' else 1)))
            visible[index].button.focus_set()
        elif action=='right' and node.group:
            if not node.opened:self.set_expanded(key,True)
            elif node.children:
                child=next((n for n in node.children if self._enabled(n)),None)
                if child:child.button.focus_set()
        elif action=='left':
            if node.group and node.opened:self.set_expanded(key,False)
            elif node.parent:node.parent.button.focus_set()
        return 'break'

    def _ensure_visible(self,key):
        self._scroll_job=None
        if not self.winfo_exists():return
        node=self.nodes[key]
        if not node.button.winfo_viewable():return
        top=node.header.winfo_rooty()-self.body.winfo_rooty()
        total=max(1,self.body.winfo_reqheight())
        first,last=self.area.canvas.yview()
        if top<first*total:self.area.canvas.yview_moveto(top/total)
        elif top+node.header.winfo_height()>last*total:
            self.area.canvas.yview_moveto((top+node.header.winfo_height()-self.area.canvas.winfo_height())/total)

    def _schedule_visible(self,key):
        if self._scroll_job is not None:self.after_cancel(self._scroll_job)
        self._scroll_job=self.after_idle(lambda:self._ensure_visible(key))

    def _cleanup(self,event):
        if event.widget is self and self._scroll_job is not None:
            self.after_cancel(self._scroll_job)
            self._scroll_job=None
