"""Opt-in per-root compatibility for existing Python tkinter Menu calls."""
import tkinter as tk


def bind_context_menu(widget,menu):
    """Associate pointer and keyboard context requests without demo-side effects."""
    def show(event,keyboard=False):
        if keyboard:
            x,y=widget.winfo_rootx()+8,widget.winfo_rooty()+widget.winfo_height()
        else:x,y=event.x_root,event.y_root
        from .popups import ThemedMenu
        if isinstance(menu,ThemedMenu):menu.tk_popup(x,y,owner=widget)
        else:menu.tk_popup(x,y)
        return 'break'
    widget.bind('<Button-3>',show)
    widget.bind('<Shift-F10>',lambda e:show(e,True))
    widget.bind('<App>',lambda e:show(e,True))


def install_menu_compatibility(root):
    root._fluent_menus=True
    if getattr(tk.Menu,'_fluent_methods_installed',False):return
    tk.Menu._fluent_methods_installed=True
    originals={name:getattr(tk.Menu,name) for name in ('tk_popup','post','unpost','grab_release','destroy')}
    def post(menu,x,y,entry=''):
        if not getattr(menu._root(),'_fluent_menus',False):
            return originals['tk_popup'](menu,x,y,entry)
        from .extras import MenuPopup
        old=getattr(menu,'_web_popup',None)
        if old is not None and not old._closed:old.close()
        owner=menu.master
        while isinstance(owner,tk.Menu):owner=owner.master
        menu._web_popup=MenuPopup(owner,menu,x,y,selected=None if entry=='' else menu.index(entry))
    def unpost(menu):
        popup=getattr(menu,'_web_popup',None)
        if popup is not None and not popup._closed:popup.close()
        else:return originals['unpost'](menu)
    def release(menu):
        popup=getattr(menu,'_web_popup',None)
        if popup is None or popup._closed:return originals['grab_release'](menu)
    def destroy(menu):
        for opened in list(getattr(menu,'_fluent_open_popups',())):opened.root_popup.close(restore_focus=False)
        popup=getattr(menu,'_web_popup',None)
        if popup is not None and not popup._closed:popup.close(restore_focus=False)
        return originals['destroy'](menu)
    tk.Menu.tk_popup=post
    def plain_post(menu,x,y):
        if not getattr(menu._root(),'_fluent_menus',False):return originals['post'](menu,x,y)
        return post(menu,x,y)
    tk.Menu.post=plain_post;tk.Menu.unpost=unpost;tk.Menu.grab_release=release;tk.Menu.destroy=destroy

    # Keep the native menu model authoritative when callers edit a visible menu.
    for name in ('add','insert','delete','entryconfigure'):
        original=getattr(tk.Menu,name)
        def mutate(menu,*args,_original=original,_name=name,**kwargs):
            changing=_name!='entryconfigure' or bool(kwargs) or len(args)>1 and isinstance(args[1],dict)
            if changing:
                for opened in list(getattr(menu,'_fluent_open_popups',())):opened.root_popup.close()
            return _original(menu,*args,**kwargs)
        setattr(tk.Menu,name,mutate)
    tk.Menu.entryconfig=tk.Menu.entryconfigure
