"""Anchored form lifetime, keyboard focus and nested picker coordination."""
import tkinter as tk
from tkinter import ttk
from .motion import prepare_popup,reveal_popup
from .tcl_bridge import unbind


class FormFlyout(tk.Toplevel):
    def __init__(self,owner,width=310):
        from .core import Card
        self.owner=owner;self.host=owner.winfo_toplevel()
        old=getattr(self.host,'_form_flyout',None)
        if old is not None:old.close()
        self.previous_grab=owner.grab_current()
        self.previous_grab_status=self.previous_grab.grab_status() if self.previous_grab else None
        super().__init__(self.host);self.withdraw();prepare_popup(self)
        self.overrideredirect(True);self.transient(self.host)
        self.resources=getattr(owner._root(),'_dpi_resources',None)
        self.dpi=self.resources.window_dpi(self.host) if self.resources else owner.winfo_fpixels('1i')
        self.scale=self.dpi/96;self._closed=False;self._dismissing=False;self._focus_job=None;self._show_job=None
        self._region_size=None;self.corner_radius=8
        self.body=Card(self,padding=round(18*self.scale));self.body.pack(fill='both',expand=True)
        self._width=round(width*self.scale);self.host._form_flyout=self
        self.bind('<Escape>',lambda e:self.dismiss())
        self.bind('<Tab>',lambda e:self.traverse(1));self.bind('<Shift-Tab>',lambda e:self.traverse(-1))
        self.bind('<ButtonPress-1>',self.outside,add='+');self.bind('<FocusOut>',self.defer_focus,add='+')
        self._host_binding=self.host.bind('<Configure>',self.host_configured,add='+')
        self._owner_binding=owner.bind('<Destroy>',self.owner_destroyed,add='+')

    def show(self):
        if self._closed:return
        if self.winfo_ismapped():
            reveal_popup(self);self.lift();self.grab_set();return
        if isinstance(self.owner,ttk.Widget) and self.owner.instate(['disabled']):self.close();return
        from .core import Modal
        from .extras import MenuPopup
        if self.resources:self.resources.update(self,self.dpi)
        self.attributes('-alpha',0.0);self.update_idletasks()
        height=self.winfo_reqheight();width=max(self._width,self.winfo_reqwidth())
        px=lambda n:round(n*self.scale)
        x=self.owner.winfo_rootx()+self.owner.winfo_width()+px(6);y=self.owner.winfo_rooty()
        left,top,right,bottom=MenuPopup.work_area(self.owner.winfo_rootx(),y)
        width=min(width,right-left-px(16));height=min(height,bottom-top-px(16))
        if x+width>right-px(8):x=self.owner.winfo_rootx()-width-px(6)
        x=max(left+px(8),min(x,right-width-px(8)));y=max(top+px(8),min(y,bottom-height-px(8)))
        self.geometry(f'{width}x{height}+{x}+{y}');self.deiconify();self.update_idletasks()
        # Map adapters may change requested button sizes. Finish that layout
        # while transparent, before exposing the form.
        height=min(self.winfo_reqheight(),bottom-top-px(16))
        y=max(top+px(8),min(y,bottom-height-px(8)))
        self.geometry(f'{width}x{height}+{x}+{y}');self.update_idletasks()
        Modal.round_corners(self);self.lift();self.grab_set()
        def initial_focus():
            self._show_job=None
            if self._closed:return
            self.update_idletasks()
            final_height=min(self.winfo_reqheight(),bottom-top-px(16))
            final_y=max(top+px(8),min(y,bottom-final_height-px(8)))
            self.geometry(f'{width}x{final_height}+{x}+{final_y}');self.update_idletasks()
            Modal.round_corners(self)
            items=self.focusables()
            (items[0] if items else self).focus_force()
            reveal_popup(self)
        if self._show_job is not None:self.after_cancel(self._show_job)
        self._show_job=self.after(20,initial_focus)

    def focusables(self):
        items=[]
        def walk(parent):
            for child in parent.winfo_children():
                if isinstance(child,tk.Toplevel):continue
                if child.winfo_viewable() and self.tk.getboolean(self.tk.call('tk::FocusOK',str(child))):items.append(child)
                walk(child)
        walk(self.body)
        return items

    def traverse(self,direction):
        items=self.focusables();focus=self.focus_get()
        if items:
            index=(items.index(focus)+direction)%len(items) if focus in items else (0 if direction>0 else -1)
            items[index].focus_set()
        return 'break'

    def contains_window(self,window):
        while window is not None:
            if window is self:return True
            window=getattr(window,'master',None)
        return False

    def outside(self,event):
        if self.winfo_rootx()<=event.x_root<self.winfo_rootx()+self.winfo_width() and self.winfo_rooty()<=event.y_root<self.winfo_rooty()+self.winfo_height():return
        if self.contains_window(event.widget.winfo_toplevel()) and event.widget.winfo_toplevel() is not self:return
        self.dismiss()

    def defer_focus(self,event=None):
        if not self._closed and self._focus_job is None:self._focus_job=self.after(100,self.check_focus)
    def check_focus(self):
        self._focus_job=None
        if self._closed:return
        focus=self.focus_get()
        if focus is None or not self.contains_window(focus.winfo_toplevel()):self.close(restore_focus=False)
    def host_configured(self,event):
        if event.widget is self.host:self.close()
    def owner_destroyed(self,event):
        if event.widget is self.owner:self.close(restore_focus=False)
    def dismiss(self):
        from .motion import dismiss_popup
        return dismiss_popup(self)
    def close(self,*,restore_focus=True):
        if self._closed:return 'break'
        self._closed=True
        if self._focus_job is not None:self.after_cancel(self._focus_job)
        if self._show_job is not None:self.after_cancel(self._show_job)
        child=getattr(self,'_choice_popup',None)
        if child is not None and not child._closed:child.close(restore_focus=False)
        nested=getattr(self,'_form_flyout',None)
        if nested is not None:nested.close(restore_focus=False)
        unbind(self.host,'<Configure>',self._host_binding)
        if self.owner.winfo_exists():unbind(self.owner,'<Destroy>',self._owner_binding)
        if getattr(self.host,'_form_flyout',None) is self:self.host._form_flyout=None
        self.grab_release();super().destroy()
        if self.previous_grab is not None and self.previous_grab.winfo_exists():
            if self.previous_grab_status=='global':self.previous_grab.grab_set_global()
            else:self.previous_grab.grab_set()
        if restore_focus and self.owner.winfo_exists():self.owner.focus_force()
        return 'break'
    def destroy(self):self.close()

