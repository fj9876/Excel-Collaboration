"""Shared dropdown behavior for original ttk and public themed entry points."""
import tkinter as tk
from tkinter import ttk
from .tcl_bridge import install_proxy, delete_callback, on_map


class DropdownController:
    def __init__(self,widget):
        self.widget=widget;self.tk=widget.tk;self.popup=None;self.closed=False
        self.path=str(widget);self.native_path=self.path+'__dropdown_native'
        self.menu_button=isinstance(widget,ttk.Menubutton)
        self.tag='FluentDropdown'+str(id(self))
        self.commands=[]
        events={'<ButtonPress-1>':self.click,'<Down>':self.post_event,'<Alt-Down>':self.post_event,'<Alt-Up>':self.escape,'<F4>':self.post_event,'<space>':self.space,'<Escape>':self.escape,'<Destroy>':self.destroyed}
        if self.menu_button:events.update({'<Return>':self.post_event,'<ButtonRelease-1>':lambda e:'break'})
        for event,callback in events.items():
            self.commands.append((event,widget.bind_class(self.tag,event,callback)))
        tags=widget.bindtags();widget.bindtags((tags[0],self.tag,*tags[1:]))
        self.tk.call('rename',self.path,self.native_path)
        self._proxy_dispatch=install_proxy(self.tk,self.path,self.dispatch,self.native_path,routed=('configure','config','state','current','set'))

    def dispatch(self,*args):
        result=self.tk.call(self.native_path,*args)
        if args and args[0] in ('configure','config','state','current','set') and len(args)>1:
            if self.popup is not None and not self.popup._closed:
                disabled=self.widget.instate(['disabled'])
                changing=args[0] in ('set','current') or args[0] in ('configure','config') and len(args)>2 and any(key in ('-values','-textvariable','-menu') for key in args[1::2])
                if disabled or changing:self.popup.close()
        return result

    def click(self,event):
        if self.widget.instate(['disabled']):return 'break'
        if self.menu_button or self.widget.instate(['readonly']) or 'downarrow' in self.widget.identify(event.x,event.y):return self.post_event(event)

    def space(self,event):
        if self.menu_button or self.widget.instate(['readonly']):return self.post_event(event)

    def escape(self,event=None):
        if self.popup is not None and not self.popup._closed:
            self.popup.close();return 'break'
        # With no list open, let Escape reach the owning dialog/flyout.
        return None

    def post_event(self,event=None):
        try:self.open()
        except Exception:
            import sys
            self.widget._root().report_callback_exception(*sys.exc_info())
        return 'break'

    def open(self):
        w=self.widget
        if self.closed or w.instate(['disabled']):return 'break'
        if self.popup is not None and not self.popup._closed:
            self.popup.close();return 'break'
        if self.menu_button:return self.open_menu()
        command=str(w.cget('postcommand')).strip()
        if command:self.tk.call('uplevel','#0',command)
        if self.closed or w.instate(['disabled']):return 'break'
        values=self.tk.splitlist(w.cget('values'))
        if not values:return 'break'
        from .popups import choice_popup
        self.popup=choice_popup(w,values,w.current(),self.choose,overlay=w.instate(['readonly']))
        self.track_popup()
        return 'break'

    def track_popup(self):
        popup=self.popup
        if popup is None or popup._closed:return
        self.widget.state(['pressed'])
        variables=[]
        if not self.menu_button:
            variables=[str(self.widget.cget('textvariable'))]
        else:
            menu=self.widget.nametowidget(str(self.widget.cget('menu')))
            end=menu.index('end')
            for i in range(end+1 if end is not None else 0):
                if menu.type(i)=='radiobutton':variables.append(str(menu.entrycget(i,'variable')))
        traces=[]
        callback=popup.register(lambda *args:popup.close())
        for variable in set(filter(None,variables)):
            self.tk.call('trace','add','variable',variable,'write',callback)
            traces.append(variable)

        def closed(event):
            if event.widget is popup:
                for variable in traces:
                    self.tk.call('trace','remove','variable',variable,'write',callback)
                if not self.closed and self.widget.winfo_exists():self.widget.state(['!pressed'])
        popup.bind('<Destroy>',closed,add='+')

    def choose(self,index):
        if self.closed or self.widget.instate(['disabled']):return
        self.widget.current(index)
        self.widget.event_generate('<<ComboboxSelected>>',when='tail')

    def open_menu(self):
        w=self.widget
        name=str(w.cget('menu'))
        if not name:return 'break'
        menu=w.nametowidget(name)
        command=str(menu.cget('postcommand')).strip()
        if command:menu.tk.call('uplevel','#0',command)
        if self.closed or w.instate(['disabled']):return 'break'
        end=menu.index('end')
        indices=list(range(end+1)) if end is not None else []
        selection=bool(indices) and all(menu.type(i)=='radiobutton' for i in indices)
        if selection:
            end=menu.index('end');indices=list(range(end+1)) if end is not None else []
            items=[];selected=0
            for index in indices:
                value=menu.entrycget(index,'value');variable=menu.entrycget(index,'variable')
                try:checked=bool(self.tk.call('string','equal',menu.getvar(variable),value))
                except tk.TclError:checked=False
                if checked:selected=index
                def choose(i=index):
                    if not self.closed and not w.instate(['disabled']):menu.invoke(i)
                items.append(dict(label=menu.entrycget(index,'label'),checked=checked,disabled=str(menu.entrycget(index,'state'))=='disabled',command=choose))
            if not items:return 'break'
            from .popups import ChoicePopup
            self.popup=ChoicePopup(w,items,w.winfo_rootx(),w.winfo_rooty()+w.winfo_height()+4,width=w.winfo_width(),selected=selected,above=w.winfo_rooty(),align_selected=True)
        else:
            from .extras import MenuPopup
            self.popup=MenuPopup(w,menu,w.winfo_rootx(),w.winfo_rooty()+w.winfo_height()+4,min_width=w.winfo_width(),run_postcommand=False)
        if hasattr(menu,'popup'):menu.popup=self.popup
        else:menu._web_popup=self.popup
        self.track_popup()
        return 'break'

    def destroyed(self,event):
        if event.widget is not self.widget or self.closed:return
        self.closed=True
        if self.popup is not None and not self.popup._closed:self.popup.close(restore_focus=False)
        for event,command in self.commands:
            self.widget.unbind_class(self.tag,event)
            self.widget._root().deletecommand(command)
        if self.tk.call('info','commands',self.path):self.tk.call('rename',self.path,'')
        delete_callback(self.tk,self._proxy_dispatch)
        if self.tk.call('info','commands',self.native_path):self.tk.call('rename',self.native_path,self.path)


def attach_dropdown(widget):
    if isinstance(widget,(ttk.Combobox,ttk.Menubutton)) and not getattr(widget,'_tkwin11_dropdown',None):
        widget._tkwin11_dropdown=DropdownController(widget)
    return getattr(widget,'_tkwin11_dropdown',None)


def install_dropdown_compatibility(root):
    if not getattr(root,'_combobox_post_redirected',False):
        # Native TCombobox bindings also post from padding, Shift/double clicks
        # and keyboard paths. Route their common endpoint, not just the arrow.
        native='ttk::combobox::Post__fluent_native'
        root.tk.call('rename','ttk::combobox::Post',native)
        def post(path):
            widget=root.nametowidget(str(path))
            controller=attach_dropdown(widget)
            if controller is not None:
                controller.open()
                return ''
            return root.tk.call(native,path)
        install_proxy(root.tk,'ttk::combobox::Post',post,native)
        root._combobox_post_redirected=True
    if not getattr(root,'_dropdown_compatibility_installed',False):
        on_map(root,attach_dropdown)
        root._dropdown_compatibility_installed=True
    def walk(widget):
        attach_dropdown(widget)
        for child in widget.winfo_children():walk(child)
    walk(root)
