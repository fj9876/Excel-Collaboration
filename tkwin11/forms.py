"""Standard-library Fluent form controls and result-returning dialogs."""
import calendar
from datetime import date, datetime, timedelta
import math
import re
import tkinter as tk
from tkinter import ttk
from .core import PALETTE, Modal, Card, ProgressSlider, _patch
from .render import calendar_icon
from .tcl_bridge import unbind

SEMANTIC={'primary':'#087DFF','success':'#159576','warning':'#B77717','danger':'#D54B58','info':'#197FA8','neutral':'#657080'}

def tint(color,white=.91):
    return '#'+''.join(f'{round(int(color[i:i+2],16)*(1-white)+255*white):02x}' for i in (1,3,5))

def configure_form_styles(root,style):
    style.configure('ValidationError.TLabel',foreground='#C42B1C',background=PALETTE['bg'])
    px=lambda n:max(1,round(n*root.winfo_fpixels('1i')/96))
    for key,base in SEMANTIC.items():
        color=PALETTE['accent'] if key=='primary' else base;prefix=key.title()
        style.configure(prefix+'.TLabel',background=PALETTE['bg'],foreground=color)
        style.configure(prefix+'.Badge.TLabel',background=tint(color),foreground=color,padding=(px(8),px(4)))
        style.map(prefix+'.Badge.TLabel',foreground=[('disabled',PALETTE['disabled'])])
        for variant in ('','Outline.','Link.'):
            name=prefix+'.'+variant+'TButton'
            if name=='Primary.TButton':continue
            fill=color if not variant else PALETTE['bg'];border=color if variant!='Link.' else PALETTE['bg']
            normal=_patch(root,fill,border,PALETTE['bg'],px(4))
            hover=_patch(root,tint(color,.84) if variant else color,color if variant!='Link.' else tint(color,.84),PALETTE['bg'],px(4))
            disabled=_patch(root,PALETTE['sidebar'],PALETTE['border'] if variant!='Link.' else PALETTE['sidebar'],PALETTE['bg'],px(4))
            focus=_patch(root,fill,PALETTE['fg'],PALETTE['bg'],px(4))
            root._tkwin11_images.extend((normal,hover,disabled,focus))
            element='Web.'+name+'.surface'
            style.element_create(element,'image',normal,('disabled',disabled),('pressed',hover),('active',hover),('focus',focus),border=px(4)+1,padding=0,width=px(10),height=px(10),sticky='nsew')
            style.layout(name,[(element,{'sticky':'nsew','children':[('Button.padding',{'sticky':'nsew','children':[('Button.label',{'sticky':'nsew'})]})]})])
            style.configure(name,padding=(px(12),px(7)),anchor='center',foreground=color if variant else '#FFFFFF')
            style.map(name,foreground=[('disabled',PALETTE['disabled'])])

class Badge(ttk.Label):
    def __init__(self,master,*,kind='info',**kwargs):
        kwargs.setdefault('style',kind.title()+'.Badge.TLabel');super().__init__(master,**kwargs)

class ValidatedEntry(ttk.Frame):
    def __init__(self,master,*,variable=None,kind='text',required=False,minimum=None,maximum=None,pattern=None,validator=None,label='',**kwargs):
        super().__init__(master)
        self.variable=variable if variable is not None else tk.StringVar(self)
        self.kind,self.required,self.minimum,self.maximum=kind,required,minimum,maximum
        self.pattern,self.validator=pattern,validator
        self.columnconfigure(0,weight=1)
        row=0
        if label:ttk.Label(self,text=label).grid(row=0,column=0,sticky='w',pady=(0,5));row=1
        self.entry=ttk.Entry(self,textvariable=self.variable,**kwargs);self.entry.grid(row=row,column=0,sticky='ew')
        self.error=tk.StringVar(self)
        self.error_label=ttk.Label(self,textvariable=self.error,style='ValidationError.TLabel',wraplength=320)
        self.error_label.grid(row=row+1,column=0,sticky='ew',pady=(4,0))
        self.entry.bind('<Configure>',lambda e:self.error_label.configure(wraplength=max(80,e.width)),add='+')
        self.entry.bind('<FocusOut>',lambda e:self.validate(),add='+')
        self.entry.bind('<Return>',lambda e:self.validate(),add='+')
        self._trace=self.variable.trace_add('write',self._edited)
        self.bind('<Destroy>',self._cleanup,add='+')
    def get(self):return str(self.variable.get())
    def set(self,value):self.variable.set('' if value is None else str(value))
    def get_value(self):
        text=self.get().strip()
        if not text:
            if self.required:raise ValueError('此项不能为空')
            return '' if self.kind=='text' else None
        try:
            if self.kind=='integer':value=int(text)
            elif self.kind=='float':
                value=float(text)
                if not math.isfinite(value):raise ValueError()
            elif self.kind=='date':value=date.fromisoformat(text)
            else:value=text
        except (ValueError,OverflowError) as exc:
            raise ValueError({'integer':'请输入整数','float':'请输入有限数值','date':'请输入有效日期，格式 YYYY-MM-DD'}.get(self.kind,'输入无效')) from exc
        if self.kind=='email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',text):raise ValueError('请输入有效的邮箱地址')
        if self.minimum is not None and value<self.minimum:raise ValueError(f'不能小于 {self.minimum}')
        if self.maximum is not None and value>self.maximum:raise ValueError(f'不能大于 {self.maximum}')
        if self.pattern and not re.fullmatch(self.pattern,text):raise ValueError('输入格式不符合要求')
        if self.validator:
            result=self.validator(value)
            if result is False:raise ValueError('输入未通过校验')
            if isinstance(result,str) and result:raise ValueError(result)
        return value
    def validate(self):
        if self.entry.instate(['disabled']):return True
        try:self.get_value()
        except (ValueError,TypeError) as exc:
            self.error.set(str(exc));self.entry.state(['invalid']);self.error_label.grid();return False
        self.error.set('');self.entry.state(['!invalid']);return True
    def state(self,statespec=None):
        result=self.entry.state(statespec)
        if statespec is not None:
            disabled=self.entry.instate(['disabled'])
            super().state(['disabled'] if disabled else ['!disabled'])
            self.error_label.state(['disabled'] if disabled else ['!disabled'])
        return result
    def _edited(self,*_):
        if self.error.get():self.validate()
    def _cleanup(self,event):
        if event.widget is self:self.variable.trace_remove('write',self._trace)

class ResultDialog(Modal):
    def __init__(self,master,title,**kwargs):
        self.result=None;super().__init__(master,title,**kwargs)
    def accept(self,value):
        if self._closed or self._dismissing:return
        self.result=value;self.close()
    def show_result(self):self.wait_window();return self.result

class MessageDialog(ResultDialog):
    def __init__(self,master,message,*,title='提示',kind='info',question=False):
        super().__init__(master,title,width=440,height=260)
        Badge(self.body,text={'info':'提示','success':'完成','warning':'注意','danger':'错误'}.get(kind,'提示'),kind=kind).pack(anchor='w',pady=(0,12))
        ttk.Label(self.body,text=message,wraplength=round(380*self.scale),justify='left').pack(fill='x')
        self.primary.configure(text='确认' if question else '知道了',style='Primary.TButton',command=lambda:self.accept(True))
        if question:
            self.secondary.configure(command=lambda:self.accept(False));self.secondary.pack(side='right',padx=8,before=self.primary)

class InputDialog(ResultDialog):
    def __init__(self,master,prompt,*,title='输入',initial='',kind='text',minimum=None,maximum=None,required=False):
        super().__init__(master,title,width=450,height=285)
        self.field=ValidatedEntry(self.body,label=prompt,kind=kind,minimum=minimum,maximum=maximum,required=required)
        self.field.pack(fill='x');self.field.set(initial)
        self.initial_focus=self.field.entry
        self.primary.configure(text='确定',command=self.submit)
        self.secondary.pack(side='right',padx=8,before=self.primary)
        self.field.entry.bind('<Return>',lambda e:self.submit())
    def submit(self):
        if self._closed or self._dismissing or self.primary.instate(['disabled']):return 'break'
        if self.field.validate():self.accept(self.field.get_value())
        else:self.field.entry.focus_set()
        return 'break'

class Dialogs:
    @staticmethod
    def info(master,message,title='提示'):return MessageDialog(master,message,title=title).show_result()
    @staticmethod
    def error(master,message,title='错误'):return MessageDialog(master,message,title=title,kind='danger').show_result()
    @staticmethod
    def warning(master,message,title='注意'):return MessageDialog(master,message,title=title,kind='warning').show_result()
    @staticmethod
    def confirm(master,message,title='确认操作',danger=False):
        return MessageDialog(master,message,title=title,kind='danger' if danger else 'info',question=True).show_result() is True
    @staticmethod
    def ask_string(master,prompt,**kwargs):return InputDialog(master,prompt,kind='text',**kwargs).show_result()
    @staticmethod
    def ask_integer(master,prompt,**kwargs):return InputDialog(master,prompt,kind='integer',**kwargs).show_result()
    @staticmethod
    def ask_float(master,prompt,**kwargs):return InputDialog(master,prompt,kind='float',**kwargs).show_result()

def _as_date(value):
    if value is None or value=='':return None
    if isinstance(value,datetime):return value.date()
    if isinstance(value,date):return value
    return date.fromisoformat(str(value))

class DateEntry(ttk.Frame):
    def __init__(self,master,*,value=None,minimum=None,maximum=None,command=None,**kwargs):
        super().__init__(master,**kwargs)
        self.minimum=_as_date(minimum);self.maximum=_as_date(maximum)
        if self.minimum and self.maximum and self.minimum>self.maximum:raise ValueError('最小日期不能大于最大日期')
        self.command=command;self.popup=None;self.variable=tk.StringVar(self)
        self.columnconfigure(0,weight=1)
        self.entry=ttk.Entry(self,textvariable=self.variable)
        self.entry.grid(row=0,column=0,sticky='ew')
        size=max(18,round(20*self.winfo_fpixels('1i')/96))
        self._calendar_icon=calendar_icon(self,size,PALETTE['muted'])
        self._calendar_disabled=calendar_icon(self,size,PALETTE['disabled'])
        self.button=ttk.Button(self,image=self._calendar_icon,command=self.open_calendar,padding=(8,5))
        self.button.grid(row=0,column=1,padx=(5,0))
        self.entry.bind('<Alt-Down>',lambda e:self.open_calendar())
        self.entry.bind('<F4>',lambda e:self.open_calendar())
        self.entry.bind('<FocusOut>',lambda e:self.validate(),add='+')
        self.entry.bind('<Return>',lambda e:self.validate(),add='+')
        self.bind('<Destroy>',self._cleanup,add='+')
        self.set_date(value)
    def get_date(self):
        value=_as_date(self.variable.get().strip())
        if value and self.minimum and value<self.minimum:raise ValueError('日期早于允许范围')
        if value and self.maximum and value>self.maximum:raise ValueError('日期晚于允许范围')
        return value
    def set_date(self,value):
        value=_as_date(value)
        if value and ((self.minimum and value<self.minimum) or (self.maximum and value>self.maximum)):raise ValueError('日期超出允许范围')
        self.variable.set(value.isoformat() if value else '');self.entry.state(['!invalid'])
    def clear(self):self.set_date(None)
    def validate(self):
        if self.entry.instate(['disabled']):return True
        try:self.get_date()
        except ValueError:self.entry.state(['invalid']);return False
        self.entry.state(['!invalid']);return True
    def state(self,statespec=None):
        result=self.entry.state(statespec)
        if statespec is not None:
            disabled=self.entry.instate(['disabled'])
            self.button.state(['disabled'] if disabled else ['!disabled'])
            self.button.configure(image=self._calendar_disabled if disabled else self._calendar_icon)
            super().state(['disabled'] if disabled else ['!disabled'])
            if disabled and self.popup is not None and not self.popup._closed:self.popup.close()
        return result
    def open_calendar(self):
        if self.entry.instate(['disabled']):return 'break'
        if self.popup is not None and not self.popup._closed:self.popup.close();return 'break'
        self.popup=CalendarPopup(self)
        return 'break'
    def _chosen(self,value):
        if self.entry.instate(['disabled']):return
        self.set_date(value);self.event_generate('<<DateEntrySelected>>',when='tail')
        if self.command:self.command(value)
    def _cleanup(self,event):
        if event.widget is self and self.popup is not None and not self.popup._closed:self.popup.close(False)

class CalendarPopup(tk.Toplevel):
    def __init__(self,owner):
        self.owner=owner;self.host=owner.winfo_toplevel();self._closed=False;self._dismissing=False
        existing=getattr(self.host,'_choice_popup',None)
        if existing is not None:existing.close()
        self.previous_focus=owner.entry;self.previous_grab=owner.grab_current()
        self.previous_grab_status=self.previous_grab.grab_status() if self.previous_grab else None
        super().__init__(self.host);self.withdraw();self.overrideredirect(True);self.transient(self.host)
        self._dpi_anchor=self.host
        from .motion import prepare_popup
        prepare_popup(self);self.attributes('-alpha',0.0)
        self.resources=getattr(owner._root(),'_dpi_resources',None)
        dpi=self.resources.window_dpi(self.host) if self.resources else owner.winfo_fpixels('1i')
        self.scale=dpi/96;self.corner_radius=8;self._region_size=None;self._focus_job=None
        try:selected=owner.get_date()
        except ValueError:selected=None
        focused=selected or date.today()
        if owner.minimum:focused=max(focused,owner.minimum)
        if owner.maximum:focused=min(focused,owner.maximum)
        self.focused=focused;self.view='month';self.period_buttons={}
        self.selected=selected;self.year=tk.IntVar(self,value=focused.year);self.month=tk.IntVar(self,value=focused.month)
        card=Card(self,padding=10);card.pack(fill='both',expand=True,padx=1,pady=1)
        head=ttk.Frame(card);head.pack(fill='x',pady=(0,10))
        self.heading=ttk.Button(head,text='',style='Quiet.TButton',command=self.level_up)
        self.heading.pack(side='left',padx=8)
        ttk.Button(head,text='›',width=2,style='Quiet.TButton',command=lambda:self.page(1)).pack(side='right')
        ttk.Button(head,text='‹',width=2,style='Quiet.TButton',command=lambda:self.page(-1)).pack(side='right')
        self.days=ttk.Frame(card,width=round(340*self.scale),height=round(270*self.scale));self.days.pack(fill='both',expand=True)
        self.days.grid_propagate(False)
        self._popup_size=(round(368*self.scale),round(384*self.scale))
        foot=ttk.Frame(card);foot.pack(fill='x',pady=(10,0))
        self.today_button=ttk.Button(foot,text='今天',command=lambda:self.choose(date.today()));self.today_button.pack(side='left')
        if not self.allowed(date.today()):self.today_button.state(['disabled'])
        ttk.Button(foot,text='清空',command=lambda:self.choose(None)).pack(side='right')
        self.bind('<Control-Up>',lambda e:self.key_level(True))
        self.bind('<Control-Down>',lambda e:self.key_level(False))
        self.bind('<Escape>',lambda e:self.dismiss())
        self.bind('<F4>',lambda e:self.dismiss())
        self.bind('<Tab>',lambda e:self.traverse(1));self.bind('<Shift-Tab>',lambda e:self.traverse(-1))
        self.bind('<ButtonPress-1>',self.outside,add='+');self.bind('<FocusOut>',self.focus_out,add='+')
        self._host_bind=self.host.bind('<Configure>',lambda e:self.close() if e.widget is self.host else None,add='+')
        self.host._choice_popup=self
        self.draw()
        if self.resources:self.resources.update(self,dpi)
        # Initialize button states while hidden so Map does not create styles
        # and replace image elements in the first visible paint.
        from .button import attach_button
        def prepare_buttons(parent):
            for child in parent.winfo_children():
                attach_button(child);prepare_buttons(child)
        prepare_buttons(self)
        from .extras import MenuPopup
        x=owner.winfo_rootx();y=owner.winfo_rooty()+owner.winfo_height()+4
        left,top,right,bottom=MenuPopup.work_area(x,y);w,h=self._popup_size
        if y+h>bottom:y=owner.winfo_rooty()-h-4
        x=max(left+4,min(x,right-w-4));y=max(top+4,min(y,bottom-h-4))
        self.geometry(f'{w}x{h}+{x}+{y}')
        self.attributes('-topmost',True);self.deiconify()
        self._ready_job=self.after(20,self.ready)
    def draw(self):
        try:y=int(self.year.get());m=int(self.month.get());date(y,m,1)
        except (ValueError,tk.TclError):return
        self.day_buttons={};self.period_buttons={}
        if self.view!='month':self.draw_periods();return
        self.heading.configure(text=f'{y} 年 {m} 月')
        if not hasattr(self,'_month_grid'):
            self._month_grid=ttk.Frame(self.days)
            self._month_cells=[]
            header_height=round(30*self.scale)
            self._month_body=ttk.Frame(self._month_grid)
            self._month_body.place(x=0,y=header_height,relwidth=1,relheight=1,height=-header_height)
            for col,text in enumerate('一二三四五六日'):
                ttk.Label(self._month_grid,text=text,style='Muted.TLabel',anchor='center').place(relx=col/7,relwidth=1/7,y=0,height=header_height)
            for r in range(6):
                for col in range(7):
                    button=ttk.Button(self._month_body,width=3,style='Quiet.TButton',takefocus=False)
                    button._date=None
                    button.configure(command=lambda b=button:self.choose(b._date) if b._date is not None else None)
                    button.place(relx=col/7,rely=r/6,relwidth=1/7,relheight=1/6,x=1,y=2,width=-2,height=-4)
                    self._month_cells.append(button)
                    for key,delta in [('Left',-1),('Right',1),('Up',-7),('Down',7)]:
                        button.bind('<'+key+'>',lambda e,n=delta:self.navigate(n))
                    button.bind('<Control-Up>',lambda e:self.key_level(True));button.bind('<Control-Down>',lambda e:self.key_level(False))
                    button.bind('<Prior>',lambda e:self.page(-1));button.bind('<Next>',lambda e:self.page(1))
                    button.bind('<Control-Prior>',lambda e:self.page(-12));button.bind('<Control-Next>',lambda e:self.page(12))
                    button.bind('<Home>',lambda e:self.navigate(1-self.focused.day))
                    button.bind('<End>',lambda e:self.navigate(calendar.monthrange(self.focused.year,self.focused.month)[1]-self.focused.day))
                    button.bind('<Return>',lambda e,b=button:self.choose(b._date) if b._date is not None else 'break')
                    button.bind('<FocusIn>',lambda e,b=button:setattr(self,'focused',b._date) if b._date is not None else None)
        if hasattr(self,'_period_grid'):self._period_grid.place_forget()
        self._month_grid.place(x=0,y=0,relwidth=1,relheight=1)
        weeks=calendar.Calendar().monthdayscalendar(y,m)
        days=[day for week in weeks for day in week]
        days.extend([0]*(42-len(days)))
        for button,day in zip(self._month_cells,days):
            value=date(y,m,day) if day else None
            button._date=value
            style='Primary.TButton' if value and value==self.selected else 'Primary.Outline.TButton' if value==date.today() else 'Quiet.TButton'
            options=dict(text=str(day) if day else '',takefocus=value==self.focused)
            if str(button.cget('style'))!=style:options['style']=style
            button.configure(**options)
            button.state(['!disabled' if value and self.allowed(value) else 'disabled'])
            if value:self.day_buttons[value]=button
    def allowed(self,value):
        return not ((self.owner.minimum and value<self.owner.minimum) or (self.owner.maximum and value>self.owner.maximum))
    def ready(self):
        self._ready_job=None
        if self._closed:return
        # Idle callbacks may run inside Tk's pending Map operation. Setting
        # alpha there leaves a valid backing store but an invisible HWND.
        if not self.winfo_ismapped():
            self._ready_job=self.after(16,self.ready)
            return
        self.lift()
        # Outer geometry and the content viewport are fixed before first Map.
        # Do not drain all application idle redraws again or resize the popup here.
        from .motion import reveal_popup
        Modal.round_corners(self)
        reveal_popup(self)
        self.grab_set()
        self.focus_force()
        if self.focused in self.day_buttons:self.day_buttons[self.focused].focus_force()
    def navigate(self,delta):
        ordinal=max(date.min.toordinal(),min(date.max.toordinal(),self.focused.toordinal()+delta))
        value=date.fromordinal(ordinal)
        if self.owner.minimum:value=max(value,self.owner.minimum)
        if self.owner.maximum:value=min(value,self.owner.maximum)
        self.focused=value
        if (value.year,value.month)!=(self.year.get(),self.month.get()):
            self.year.set(value.year);self.month.set(value.month);self.draw()
        for day,button in self.day_buttons.items():button.configure(takefocus=day==value)
        self.day_buttons[value].focus_set()
        return 'break'
    def page(self,delta):
        if self._closed or self._dismissing or self.owner.entry.instate(['disabled']):return 'break'
        if self.view=='month':self.move_month(delta)
        else:
            year=max(1,min(9999,self.focused.year+delta*(10 if self.view=='decade' else 1)))
            self._set_focus_date(year,self.focused.month)
            self.draw();self.focus_period()
        return 'break'
    def _set_focus_date(self,year,month):
        value=date(year,month,min(self.focused.day,calendar.monthrange(year,month)[1]))
        if self.owner.minimum:value=max(value,self.owner.minimum)
        if self.owner.maximum:value=min(value,self.owner.maximum)
        self.focused=value;self.year.set(value.year);self.month.set(value.month)
    def key_level(self,up):
        self._root()._button_keyboard=True
        return self.level_up() if up else self.level_down()
    def level_up(self):
        if self._closed or self._dismissing or self.owner.entry.instate(['disabled']):return 'break'
        self.view='year' if self.view=='month' else 'decade'
        self.draw();self.focus_period();return 'break'
    def level_down(self):
        if self.view=='month':return 'break'
        return self.choose_period(self.focused.month if self.view=='year' else self.focused.year)
    def interval_allowed(self,first,last):
        return not ((self.owner.minimum and last<self.owner.minimum) or (self.owner.maximum and first>self.owner.maximum))
    def draw_periods(self):
        year=self.focused.year;decade=(year//10)*10
        keys=list(range(1,13)) if self.view=='year' else list(range(decade-1,decade+11))
        self.heading.configure(text=f'{year} 年' if self.view=='year' else f'{max(1,decade)} – {min(9999,decade+9)}')
        if not hasattr(self,'_period_grid'):
            self._period_grid=ttk.Frame(self.days);self._period_cells=[]
            for col in range(3):self._period_grid.columnconfigure(col,weight=1,uniform='calendar-period')
            for row in range(4):self._period_grid.rowconfigure(row,weight=1,uniform='period-row')
            for i in range(12):
                button=ttk.Button(self._period_grid,style='Quiet.TButton',takefocus=False)
                button._period=None
                button.configure(command=lambda b=button:self.choose_period(b._period))
                button.place(relx=(i%3)/3,rely=(i//3)/4,relwidth=1/3,relheight=1/4,x=2,y=2,width=-4,height=-4);self._period_cells.append(button)
                for name,delta in [('Left',-1),('Right',1),('Up',-3),('Down',3)]:button.bind('<'+name+'>',lambda e,n=delta:self.move_period(n))
                button.bind('<Control-Up>',lambda e:self.key_level(True));button.bind('<Control-Down>',lambda e:self.key_level(False))
                button.bind('<Prior>',lambda e:self.page(-1));button.bind('<Next>',lambda e:self.page(1))
                button.bind('<Home>',lambda e:self.move_period(-100));button.bind('<End>',lambda e:self.move_period(100))
                button.bind('<Return>',lambda e,b=button:self.choose_period(b._period))
                button.bind('<FocusIn>',lambda e,b=button:self._set_focus_date(self.focused.year,b._period) if self.view=='year' else self._set_focus_date(b._period,self.focused.month) if b._period is not None else None)
        if hasattr(self,'_month_grid'):self._month_grid.place_forget()
        self._period_grid.place(x=0,y=0,relwidth=1,relheight=1)
        for button,key in zip(self._period_cells,keys):
            if self.view=='decade' and not 1<=key<=9999:
                button._period=None;button.configure(text='',takefocus=False);button.state(['disabled']);continue
            button._period=key
            first=date(year,key,1) if self.view=='year' else date(key,1,1)
            last=date(year,key,calendar.monthrange(year,key)[1]) if self.view=='year' else date(key,12,31)
            selected=bool(self.selected and (self.selected.year==year and self.selected.month==key if self.view=='year' else self.selected.year==key))
            style='Primary.TButton' if selected else 'Quiet.TButton'
            options=dict(text=f'{key} 月' if self.view=='year' else str(key),takefocus=False)
            if str(button.cget('style'))!=style:options['style']=style
            button.configure(**options);button.state(['!disabled' if self.interval_allowed(first,last) else 'disabled'])
            self.period_buttons[key]=button
    def focus_period(self):
        self.update_idletasks()
        key=self.focused.month if self.view=='year' else self.focused.year
        buttons=self.day_buttons if self.view=='month' else self.period_buttons
        if self.view=='month':key=self.focused
        for k,button in buttons.items():button.configure(takefocus=k==key and not button.instate(['disabled']))
        if key in buttons and not buttons[key].instate(['disabled']):buttons[key].focus_force()
    def move_period(self,delta):
        self._root()._button_keyboard=True
        enabled=[key for key,button in self.period_buttons.items() if not button.instate(['disabled'])]
        if not enabled:return 'break'
        current=self.focused.month if self.view=='year' else self.focused.year
        target=max(enabled[0],min(enabled[-1],current+delta))
        target=min(enabled,key=lambda k:abs(k-target))
        if self.view=='year':self._set_focus_date(self.focused.year,target)
        else:self._set_focus_date(target,self.focused.month)
        self.focus_period();return 'break'
    def choose_period(self,key):
        if self._closed or self._dismissing or self.owner.entry.instate(['disabled']):return 'break'
        button=self.period_buttons.get(key)
        if button is None or button.instate(['disabled']):return 'break'
        if self.view=='year':self._set_focus_date(self.focused.year,key);self.view='month'
        else:self._set_focus_date(key,self.focused.month);self.view='year'
        self.draw();self.focus_period();return 'break'
    def traverse(self,direction):
        widgets=[]
        def walk(parent):
            for child in parent.winfo_children():
                if child.winfo_viewable() and self.tk.getboolean(self.tk.call('tk::FocusOK',str(child))):widgets.append(child)
                walk(child)
        walk(self);focus=self.focus_get()
        if widgets:widgets[(widgets.index(focus)+direction)%len(widgets) if focus in widgets else 0].focus_set()
        return 'break'
    def dismiss(self):
        from .motion import dismiss_popup
        return dismiss_popup(self)
    def move_month(self,delta):
        try:index=(int(self.year.get())-1)*12+int(self.month.get())-1+delta
        except (ValueError,tk.TclError):return
        index=max(0,min(9999*12-1,index))
        value=date(index//12+1,index%12+1,min(self.focused.day,calendar.monthrange(index//12+1,index%12+1)[1]))
        self.navigate((value-self.focused).days)
    def choose(self,value):
        if self._closed or self._dismissing or self.owner.entry.instate(['disabled']):return 'break'
        if value and ((self.owner.minimum and value<self.owner.minimum) or (self.owner.maximum and value>self.owner.maximum)):return
        self.close();self.owner._chosen(value)
    def outside(self,event):
        if not(self.winfo_rootx()<=event.x_root<self.winfo_rootx()+self.winfo_width() and self.winfo_rooty()<=event.y_root<self.winfo_rooty()+self.winfo_height()):self.dismiss()
    def focus_out(self,event):
        if self._focus_job is None and not self._closed:self._focus_job=self.after(100,self.check_focus)
    def check_focus(self):
        self._focus_job=None
        if self._closed:return
        focus=self.focus_get()
        if focus is None or focus.winfo_toplevel() is not self:self.close(False)
    def close(self,restore_focus=True):
        if self._closed:return
        self._closed=True
        if self._focus_job is not None:self.after_cancel(self._focus_job)
        if getattr(self,'_ready_job',None) is not None:self.after_cancel(self._ready_job)
        unbind(self.host,'<Configure>',self._host_bind)
        if getattr(self.host,'_choice_popup',None) is self:self.host._choice_popup=None
        self.grab_release();super().destroy()
        if self.previous_grab is not None and self.previous_grab.winfo_exists():
            if self.previous_grab_status=='global':self.previous_grab.grab_set_global()
            else:self.previous_grab.grab_set()
        if restore_focus:
            focus=self.previous_focus
            if focus is not None and focus.winfo_exists() and focus.winfo_viewable():focus.focus_force()
    def destroy(self):self.close(False)

class ValueSlider(ttk.Frame):
    def __init__(self,master,*,variable=None,from_=0,to=100,step=1,unit='',command=None,**kwargs):
        super().__init__(master,**kwargs)
        if to<=from_ or step<=0:raise ValueError('需要 to > from_ 且 step > 0')
        self.minimum,self.maximum,self.step=from_,to,step;self.unit=unit;self.command=command;self._busy=False
        self.variable=variable if variable is not None else tk.DoubleVar(self,value=from_)
        self.number=tk.StringVar(self)
        self.columnconfigure(0,weight=1)
        self.slider=ProgressSlider(self,from_=from_,to=to,step=step,variable=self.variable);self.slider.grid(row=0,column=0,sticky='ew',padx=(0,8))
        self.spin=ttk.Spinbox(self,from_=from_,to=to,increment=step,textvariable=self.number,width=7,command=self.commit)
        self.spin.grid(row=0,column=1)
        ttk.Label(self,text=unit,style='Muted.TLabel').grid(row=0,column=2,padx=(5,0))
        self.spin.bind('<Return>',lambda e:self.commit());self.spin.bind('<FocusOut>',lambda e:self.commit())
        self._last_notified=None
        self._trace=self.variable.trace_add('write',self._sync);self._sync(notify=False)
        self.bind('<Destroy>',self._cleanup,add='+')
    def get(self):return float(self.variable.get())
    def set(self,value):
        value=float(value)
        if not math.isfinite(value):raise ValueError('数值必须有限')
        value=max(self.minimum,min(self.maximum,value))
        if value not in (self.minimum,self.maximum):value=max(self.minimum,min(self.maximum,self.minimum+round((value-self.minimum)/self.step)*self.step))
        self.variable.set(round(value,10))
    def commit(self):
        if self.spin.instate(['disabled']):return
        try:self.set(float(self.number.get()));self.spin.state(['!invalid'])
        except ValueError:self.spin.state(['invalid'])
    def _sync(self,*_,notify=True):
        if self._busy:return
        self._busy=True
        try:
            try:value=self.get()
            except (ValueError,tk.TclError):value=self.minimum
            if not math.isfinite(value):value=self.minimum
            value=max(self.minimum,min(self.maximum,value))
            if value not in (self.minimum,self.maximum):value=max(self.minimum,min(self.maximum,self.minimum+round((value-self.minimum)/self.step)*self.step))
            self.variable.set(round(value,10));self.number.set(f'{value:g}');self.spin.state(['!invalid'])
        finally:self._busy=False
        if value!=self._last_notified:
            self._last_notified=value
            if notify and self.command:self.command(value)
    def state(self,statespec=None):
        result=self.spin.state(statespec)
        if statespec is not None:
            disabled=self.spin.instate(['disabled'])
            self.slider.configure(state='disabled' if disabled else 'normal')
            super().state(['disabled'] if disabled else ['!disabled'])
        return result
    def _cleanup(self,event):
        if event.widget is self:self.variable.trace_remove('write',self._trace)
