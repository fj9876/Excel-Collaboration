"""Rounded context menus and ComboBox popups, retaining original widget APIs."""
import time
import tkinter as tk
from tkinter import ttk, font as tkfont
from .core import Card, ScrollArea, Modal, PALETTE
from .motion import prepare_popup, reveal_popup
from .tcl_bridge import unbind

class ChoicePopup(tk.Toplevel):
    def __init__(self,owner,items,x,y,*,width=180,selected=0,above=None,align_selected=False):
        self.owner=owner
        self.host=owner.winfo_toplevel()
        existing=getattr(self.host,'_choice_popup',None)
        if existing is not None:existing.close()
        self.previous_focus=owner
        self.previous_grab=owner.grab_current()
        self.previous_grab_status=self.previous_grab.grab_status() if self.previous_grab else None
        super().__init__(self.host)
        self.withdraw();prepare_popup(self);self.overrideredirect(True);self.transient(self.host)
        self.attributes('-alpha',0.0)
        self.configure(background=PALETTE['bg'])
        resources=getattr(owner._root(),'_dpi_resources',None)
        dpi=resources.window_dpi(self.host) if resources else owner.winfo_fpixels('1i')
        self.scale=dpi/96
        self._search='';self._search_time=0
        self.corner_radius=8
        self._closed=False;self._dismissing=False;self._region_size=None;self._focus_job=None
        self.host._choice_popup=self
        self.items=items;self.rows=[];self.cursor=None
        shell=Card(self,padding=6,border_color=PALETTE['border'])
        # Keep the one-pixel outline inside the native rounded window region.
        shell.pack(fill='both',expand=True,padx=1,pady=1)
        self.area=ScrollArea(shell);self.area.pack(fill='both',expand=True)
        font=tkfont.nametofont(resources.font('TkDefaultFont',dpi) if resources else 'TkDefaultFont')
        width=max(width,max((font.measure(str(item.get('label','')))+round(68*self.scale) for item in items),default=width))
        height=round(22*self.scale)
        for index,item in enumerate(items):
            if item.get('separator'):
                ttk.Separator(self.area.body).pack(fill='x',pady=round(5*self.scale))
                self.rows.append(None);height+=round(11*self.scale);continue
            text='  '+str(item['label'])
            button=ttk.Button(self.area.body,text=text,style='Popup.Quiet.TButton',command=lambda i=index:self.choose(i),takefocus=False)
            if item.get('disabled'):button.state(['disabled'])
            if item.get('checked'):
                button.configure(style='Selected.Nav.TButton')
                from .navigation import _Pill
                marker=_Pill(button,lambda v:max(1,round(v*self.scale)))
                marker.paint(round(16*self.scale),PALETTE['active'],not item.get('disabled'))
                marker.place(x=0,rely=.5,anchor='w')
                marker.bind('<Button-1>',lambda e,i=index:self.choose(i))
            button.pack(fill='x',pady=1)
            button.bind('<Enter>',lambda _,i=index:self.highlight(i))
            self.rows.append(button);height+=button.winfo_reqheight()+2
        # Measure adapted rows before exposing the popup, including child-window DPI.
        if resources:resources.update(self,dpi)
        self.update_idletasks()
        height=sum(row.winfo_reqheight()+2 if row is not None else round(11*self.scale) for row in self.rows)+round(22*self.scale)
        from .extras import MenuPopup
        left,top,right,bottom=MenuPopup.work_area(owner.winfo_rootx(),owner.winfo_rooty())
        width=min(width,right-left-16)
        height=min(height,round(430*self.scale),bottom-top-24)
        if above is not None and y+height>bottom-8:y=above-height-4
        x=max(left+8,min(x,right-width-8));y=max(top+8,min(y,bottom-height-8))
        self.geometry(f'{int(width)}x{int(height)}+{int(x)}+{int(y)}')
        self.bind('<Escape>',lambda _:self.dismiss())
        self.bind('<Up>',lambda _:self.move(-1));self.bind('<Down>',lambda _:self.move(1))
        self.bind('<Home>',lambda _:self.highlight(self.enabled()[0]) if self.enabled() else 'break')
        self.bind('<End>',lambda _:self.highlight(self.enabled()[-1]) if self.enabled() else 'break')
        self.bind('<Return>',lambda _:self.choose(self.cursor))
        self.bind('<Tab>',lambda _:self.traverse(1))
        self.bind('<Shift-Tab>',lambda _:self.traverse(-1))
        self.bind('<Alt-Up>',lambda _:self.dismiss())
        self.bind('<F4>',lambda _:self.dismiss())
        self.bind('<KeyPress>',self.search)
        self.bind('<ButtonPress-1>',self.outside,add='+')
        self.bind('<FocusOut>',self.defer_focus_check,add='+')
        self._host_binding=self.host.bind('<Configure>',self.host_configured,add='+')
        self.attributes('-topmost',True)
        self.deiconify();self.update_idletasks()
        Modal.round_corners(self)
        self.lift();self.focus_force();self.grab_set()
        self.highlight(selected if selected in self.enabled() else next(iter(self.enabled()),None))
        if align_selected and selected in self.enabled():
            if self.area._job is not None:
                self.area.after_cancel(self.area._job)
                self.area._job=None
            self.area.refresh()
            self.update_idletasks()
            self.highlight(selected)
            row=self.rows[selected]
            offset=row.winfo_rooty()-self.winfo_rooty()+row.winfo_height()/2
            aligned=owner.winfo_rooty()+owner.winfo_height()/2-offset
            aligned=max(top+8,min(aligned,bottom-height-8))
            self.geometry(f'{int(width)}x{int(height)}+{int(x)}+{int(aligned)}')
        self.update_idletasks()
        reveal_popup(self)


    def traverse(self,direction):
        target=self.owner.tk_focusNext() if direction>0 else self.owner.tk_focusPrev()
        self.close(restore_focus=False)
        def restore():
            if target is not None and target.winfo_exists():target.focus_force()
        self.host.after_idle(restore)
        return 'break'

    def search(self,event):
        if not event.char or not event.char.isprintable() or event.state & 12:return
        now=time.monotonic()
        self._search=(self._search if now-self._search_time<1 else '')+event.char.casefold()
        self._search_time=now
        enabled=self.enabled()
        start=enabled.index(self.cursor)+1 if self.cursor in enabled else 0
        ordered=enabled[start:]+enabled[:start]
        matches=[i for i in ordered if str(self.items[i]['label']).casefold().startswith(self._search)]
        if not matches and len(set(self._search))==1:
            self._search=event.char.casefold()
            matches=[i for i in ordered if str(self.items[i]['label']).casefold().startswith(self._search)]
        if matches:self.highlight(matches[0])
        return 'break'

    def enabled(self):return [i for i,row in enumerate(self.rows) if row is not None and not row.instate(['disabled'])]
    def highlight(self,index):
        self.area._smooth.tween.cancel()
        if index not in self.enabled() or index==self.cursor:return 'break'
        if self.cursor is not None:self.rows[self.cursor].state(['!active'])
        self.cursor=index;row=self.rows[index];row.state(['active'])
        if row.winfo_height()<=1:self.update_idletasks()
        canvas=self.area.canvas;total=max(1,self.area.body.winfo_reqheight())
        first,last=canvas.yview()
        y=row.winfo_y();end=y+row.winfo_height()
        if y<first*total:canvas.yview_moveto(y/total)
        elif end>last*total:canvas.yview_moveto((end-canvas.winfo_height())/total)
        return 'break'
    def move(self,delta):
        enabled=self.enabled()
        if enabled:self.highlight(enabled[(enabled.index(self.cursor)+delta)%len(enabled)] if self.cursor in enabled else enabled[0])
        return 'break'
    def choose(self,index):
        if self._dismissing:return 'break'
        if index not in self.enabled():return 'break'
        action=self.items[index].get('command')
        self.close()
        if action:action()
        return 'break'
    def outside(self,event):
        if not (self.winfo_rootx()<=event.x_root<self.winfo_rootx()+self.winfo_width() and self.winfo_rooty()<=event.y_root<self.winfo_rooty()+self.winfo_height()):self.dismiss()
    def host_configured(self,event):
        if event.widget is self.host:self.close()
    def defer_focus_check(self,event):
        if not self._closed and self._focus_job is None:self._focus_job=self.after(100,self.check_focus)
    def check_focus(self):
        self._focus_job=None
        if self._closed:return
        focus=self.focus_get()
        if focus is None or focus.winfo_toplevel() is not self:self.close(restore_focus=False)
    def dismiss(self):
        from .motion import dismiss_popup
        return dismiss_popup(self)
    def close(self,*,restore_focus=True):
        if self._closed:return 'break'
        self._closed=True
        if self._focus_job is not None:self.after_cancel(self._focus_job)
        unbind(self.host,'<Configure>',self._host_binding)
        if getattr(self.host,'_choice_popup',None) is self:self.host._choice_popup=None
        self.grab_release();super().destroy()
        if self.previous_grab is not None and self.previous_grab.winfo_exists():
            if self.previous_grab_status=='global':self.previous_grab.grab_set_global()
            else:self.previous_grab.grab_set()
        if restore_focus:
            target=self.previous_focus
            if target is None or not target.winfo_exists() or not target.winfo_viewable():target=self.host
            if isinstance(target,ttk.Widget) and target.instate(['disabled']):target=self.host
            if target.winfo_exists():target.focus_force()
        return 'break'
    def destroy(self):self.close()

def choice_popup(owner,values,current,on_choose,*,overlay=True):
    items=[dict(label=value,checked=index==current,command=lambda i=index:on_choose(i)) for index,value in enumerate(values)]
    return ChoicePopup(owner,items,owner.winfo_rootx(),owner.winfo_rooty()+owner.winfo_height()+4,
                       width=owner.winfo_width(),selected=max(0,current),above=owner.winfo_rooty(),align_selected=overlay)


class ThemedCombobox(ttk.Combobox):
    """Explicit entry point to the same controller installed on plain ttk.Combobox."""
    def __init__(self,master=None,**kwargs):
        super().__init__(master,**kwargs)
        from .dropdowns import attach_dropdown
        self.controller=attach_dropdown(self)

    @property
    def popup(self):return self.controller.popup

    def click(self,event):return self.controller.click(event)
    def post_from_event(self,event=None):return self.controller.post_event(event)
    def open_popup(self,event=None):return self.controller.open()
    def choose(self,index):return self.controller.choose(index)


class ThemedMenu(tk.Menu):
    """Keep Tcl's menu model/invoke/checkbutton semantics; replace its popup."""
    def __init__(self,*args,**kw):
        super().__init__(*args,**kw);self.popup=None
    def tk_popup(self,x,y,entry='',*,owner=None,min_width=190):
        from .extras import MenuPopup
        if self.index('end') is None and not str(self.cget('postcommand')).strip():return
        if self.popup is not None and not self.popup._closed:self.popup.close()
        selected=None if entry=='' else self.index(entry)
        self.popup=MenuPopup(owner or self.master,self,x,y,min_width=min_width,selected=selected)
    post=tk_popup
    def unpost(self):
        if self.popup is not None:self.popup.close()
    def grab_release(self):
        # Legacy callers use finally: menu.grab_release(). The custom popup
        # owns its own grab until selection/dismissal, and restores it itself.
        pass
    def destroy(self):
        self.unpost();super().destroy()

