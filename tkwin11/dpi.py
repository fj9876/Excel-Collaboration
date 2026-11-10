"""Window-local font, image and ttk style views, without changing tk scaling.

The Tcl adapters live in this interpreter only. Public widget style/font/image
queries retain their source values; native widgets consume the DPI-specific view.
"""
import math
from collections import OrderedDict
from contextlib import contextmanager
import sys
import tkinter as tk
from tkinter import ttk, font
from .tcl_bridge import install_proxy, delete_callback, install_lazy_theme_changed, on_map


def _freeze(value):
    """Comparable snapshot of Tcl values (tuples, Tcl_Obj, numbers)."""
    if isinstance(value,(tuple,list)):return tuple(_freeze(item) for item in value)
    return str(value)


class Resources:
    def __init__(self,root):
        self.root=root;self.tk=root.tk
        self.base_dpi=round(root.winfo_fpixels('1i'))
        self.windows={};self.widgets={};self.elements={};self.styles=set()
        self.image_factories={};self.render_cache=OrderedDict();self.image_dpis={}
        self.images={};self.image_hooks={};self.fonts={};self.cloned_elements=set()
        self.serial=0;self.busy=False
        self.dirty=set();self.redraw_job=None
        self.image_users={}
        self.style_specs={}
        self.additive=0  # depth of additive_styles() blocks
        self.locked_theme=None  # set by tkwin11.install(lock_theme=True)
        # Only mutating subcommands need Python. Queries such as style lookup,
        # font measure/metrics and image width stay native and allocation-free.
        self.tk.call('rename','ttk::style','ttk::style__dpi_source')
        install_proxy(self.tk,'ttk::style',self.style_command,'ttk::style__dpi_source',routed=('configure','map','layout','element','theme'))
        self.tk.call('rename','image','image__dpi_source')
        install_proxy(self.tk,'image',self.image_command,'image__dpi_source',routed=('create','delete'))
        self.tk.call('rename','font','font__dpi_source')
        install_proxy(self.tk,'font',self.font_command,'font__dpi_source',routed=('configure','config'))
        # Lazy styles (animated controls, DPI clones) are written while pages
        # are built; hidden cached pages then skip the relayout until shown.
        install_lazy_theme_changed(self.tk)
        on_map(root,self.mapped)
        root.bind_all('<Destroy>',self.destroyed,add='+')
        # Only toplevels matter: filter in Tcl so the resize of every child
        # widget does not build a tkinter Event.
        configured=root.register(self.toplevel_configured)
        root.tk.call('bind','all','<Configure>','+if {[winfo toplevel %W] eq "%W"} {'+configured+' %W}')
        self.window_jobs={}
        if sys.platform=='win32':
            import ctypes
            from ctypes import wintypes
            self.user32=ctypes.WinDLL('user32')
            self.user32.GetParent.argtypes=[wintypes.HWND];self.user32.GetParent.restype=wintypes.HWND
            self.user32.GetDpiForWindow.argtypes=[wintypes.HWND];self.user32.GetDpiForWindow.restype=wintypes.UINT

    def destroyed(self,event):
        adapter=self.widgets.get(str(event.widget))
        if adapter is not None:adapter.destroy(event)
        if event.widget is self.root and self.redraw_job is not None:
            self.root.after_cancel(self.redraw_job);self.redraw_job=None
        self.windows.pop(str(event.widget),None)
        job=self.window_jobs.pop(str(event.widget),None)
        if job is not None:self.root.after_cancel(job)

    def raw_style(self,*args):return self.tk.call('ttk::style__dpi_source',*args)

    def font_command(self,*args):
        result=self.tk.call('font__dpi_source',*args)
        if len(args)>3 and args[0] in ('configure','config'):
            for (source,dpi),target in list(self.fonts.items()):
                if source==args[1]:
                    values=self.tk.splitlist(self.tk.call('font__dpi_source','actual',source))
                    options=dict(zip(values[::2],values[1::2]))
                    size=int(self.tk.call('font__dpi_source','configure',source,'-size'))
                    options['-size']=-max(1,round(size*dpi/72 if size>0 else -size*dpi/self.base_dpi))
                    self.tk.call('font__dpi_source','configure',str(target),*(v for pair in options.items() for v in pair))
        return result

    def style_command(self,*args):
        # Every effective ttk::style configure/map/layout/element create makes
        # ttk walk the widget tree (<<ThemeChanged>>) and relayout and redraw
        # every displayed ttk widget (hidden ones when next mapped; see
        # tcl_bridge). Drop no-op writes.
        if self.locked_theme and len(args)==3 and tuple(map(str,args[:2]))==('theme','use') and str(args[2])!=self.locked_theme:
            return ''
        mutating=len(args)>=3 and (args[0]=='layout' or args[0] in ('configure','map') and len(args)>=4)
        if mutating and args[0]!='layout' and self.unchanged(args):return ''
        result=self.raw_style(*args)
        element=len(args)>3 and args[:2]==('element','create')
        if element:
            self.elements[args[2]]=args[3:]
        if mutating or element or len(args)>2 and args[0]=='theme' and args[1] in ('create','settings','use'):
            self.mark_write()
        if mutating and not str(args[1]).startswith('DPI'):
            # Lazy styles (animated buttons/switches) are recorded as they appear.
            for dpi,base in list(self.styles):
                if args[1] in self.ancestors(base):self.make_style(base,dpi,refresh=True)
        return result

    def mark_write(self,additive=None):
        # Read by ::ttk::ThemeChanged (tcl_bridge): a round of additive writes
        # only is not broadcast to the widget tree.
        if additive is None:additive=self.additive>0
        self.tk.call('set','::tkwin11::additive' if additive else '::tkwin11::other',1)

    def same(self,a,b):
        call=self.tk.call
        if call('string','equal',a,b):return True
        try:return bool(call('string','equal',call('lrange',a,0,'end'),call('lrange',b,0,'end')))
        except tk.TclError:return False

    def unchanged(self,args):
        pairs=args[2:]
        if len(pairs)%2:return False
        try:
            # The style's own settings only. A per-option query would fall back
            # to parent styles and hide an explicit child value equal to it.
            values=self.tk.splitlist(self.raw_style(args[0],args[1]))
            own=dict(zip(map(str,values[::2]),values[1::2]))
            return all(str(option) in own and self.same(own[str(option)],value) for option,value in zip(pairs[::2],pairs[1::2]))
        except tk.TclError:return False

    def image_command(self,*args):
        if args and args[0]=='delete':
            for name in args[1:]:
                self.image_factories.pop(str(name),None);self.image_dpis.pop(str(name),None)
                hook=self.image_hooks.pop(str(name),None)
                if hook:
                    self.tk.call('rename',str(name),'')
                    delete_callback(self.tk,hook+'__bridge')
                    self.tk.call('rename',hook,str(name))
                for key in [key for key in self.images if key[0]==str(name)]:
                    target=self.images.pop(key,None)
                    self.tk.call('image__dpi_source','delete',target)
        result=self.tk.call('image__dpi_source',*args)
        if len(args)>1 and args[:2]==('create','photo'):
            self.watch_image(str(result))
        return result

    def watch_image(self,name):
        if name in self.image_hooks:return
        native=name+'__dpi_source'
        self.tk.call('rename',name,native);self.image_hooks[name]=native
        def command(*args):
            result=self.tk.call(native,*args)
            if args and args[0] in ('copy','put','read','blank','configure'):
                if args[0]=='copy' and len(args)==2 and str(args[1]) in self.image_factories:
                    self.image_factories[name]=self.image_factories[str(args[1])]
                else:self.image_factories.pop(name,None)
                for dpi in tuple(self.image_dpis.get(name,())):self.refresh_image(name,dpi)
                self.dirty.update(self.image_users.get(name,()))
                if self.dirty and self.redraw_job is None:self.redraw_job=self.root.after_idle(self.redraw)
            return result
        install_proxy(self.tk,name,command,native,routed=('copy','put','read','blank','configure','config'))

    def redraw(self):
        self.redraw_job=None
        pending=self.dirty;self.dirty=set()
        for adapter in pending:
            if str(adapter.widget) in self.widgets:
                adapter.widget.event_generate('<Expose>')

    def style_sources(self,base):
        result=set()
        def collect(items):
            items=self.tk.splitlist(items);i=0
            while i<len(items):
                spec=self.elements.get(str(items[i]));i+=1
                if spec and spec[0]=='image':result.update(str(v) for v in self.tk.splitlist(spec[1])[::2])
                while i<len(items) and str(items[i]).startswith('-'):
                    if items[i]=='-children':collect(items[i+1])
                    i+=2
        collect(self.raw_style('layout',base))
        return result

    def image(self,name,dpi):
        name=str(name)
        if not name or dpi==self.base_dpi:return name
        key=(name,dpi)
        if key not in self.images:
            if str(self.tk.call('image__dpi_source','type',name))!='photo':return name
            self.watch_image(name)
            # Use native image creation to avoid watching derivative images.
            target=self.tk.call('image__dpi_source','create','photo')
            self.images[key]=str(target)
            self.image_dpis.setdefault(name,set()).add(dpi)
            self.refresh_image(name,dpi)
        return self.images[key]

    def refresh_image(self,name,dpi):
        target=self.images[(name,dpi)]
        factory=self.image_factories.get(name)
        if factory is not None:
            key=(getattr(factory,'cache_key',factory),dpi)
            rendered=self.render_cache.get(key)
            if rendered is None:
                rendered=factory(dpi/self.base_dpi);self.render_cache[key]=rendered
                if len(self.render_cache)>512:self.render_cache.popitem(last=False)
            else:self.render_cache.move_to_end(key)
            self.tk.call(target,'blank');self.tk.call(target,'copy',str(rendered),'-shrink')
            return
        divisor=math.gcd(dpi,self.base_dpi);up=dpi//divisor;down=self.base_dpi//divisor
        temp=self.tk.call('image__dpi_source','create','photo')
        try:
            self.tk.call(temp,'copy',name,'-zoom',up,up)
            self.tk.call(target,'blank')
            self.tk.call(target,'copy',temp,'-subsample',down,down,'-shrink')
        finally:self.tk.call('image__dpi_source','delete',temp)

    def font(self,value,dpi):
        key=(str(value),dpi)
        if key not in self.fonts:
            source=font.Font(self.root,font=value)
            options=source.actual();size=source.cget('size')
            if str(value) in self.tk.splitlist(self.tk.call('font__dpi_source','names')):
                size=int(self.tk.call('font__dpi_source','configure',value,'-size'))
            else:
                parts=self.tk.splitlist(value)
                if len(parts)>1:
                    try:size=int(str(parts[1]))
                    except ValueError:pass
            options['size']=-max(1,round(size*dpi/72 if size>0 else -size*dpi/self.base_dpi))
            self.fonts[key]=font.Font(self.root,**options)
        return str(self.fonts[key])

    def pixels(self,value,dpi):
        values=self.tk.splitlist(value) if not isinstance(value,(int,float)) else (value,)
        return tuple(round(float(str(v))*dpi/self.base_dpi) for v in values)

    def option(self,key,value,dpi):
        if key=='font' and value:return self.font(value,dpi)
        if key=='image' and value:
            parts=list(self.tk.splitlist(value))
            # ttk image specification: default image, state spec, image, ...
            for i in range(0,len(parts),2):parts[i]=self.image(parts[i],dpi)
            return tuple(parts)
        if key in ('padding','border','borderwidth','arrowsize','rowheight','thickness','indicatordiameter','indicatormargin') and value:
            try:return self.pixels(value,dpi)
            except (ValueError,tk.TclError):pass
        return value

    def ancestors(self,name):
        result=[name]
        while '.' in name:name=name.split('.',1)[1];result.append(name)
        return [*result,'.']

    def element(self,name,dpi):
        spec=self.elements.get(name)
        if not spec or spec[0]!='image':return name
        target=f'DPI{dpi}.{name}'
        if target in self.cloned_elements:return target
        args=list(spec)
        images=list(self.tk.splitlist(args[1]))
        for i in range(0,len(images),2):images[i]=self.image(images[i],dpi)
        args[1]=tuple(images)
        for i in range(2,len(args)-1,2):
            key=args[i].lstrip('-')
            if key in ('width','height'):
                args[i+1]=round(float(args[i+1])*dpi/self.base_dpi)
            else:args[i+1]=self.option(key,args[i+1],dpi)
        self.raw_style('element','create',target,*args)
        self.cloned_elements.add(target)
        self.mark_write(True)
        return target

    def make_style(self,base,dpi,refresh=False):
        name=f'DPI{dpi}.{base}'
        if (dpi,base) in self.styles and not refresh:return name
        self.styles.add((dpi,base))
        options={};maps={}
        for ancestor in reversed(self.ancestors(base)):
            for command,target in [('configure',options),('map',maps)]:
                values=self.tk.splitlist(self.raw_style(command,ancestor))
                target.update(zip(values[::2],values[1::2]))
        config=[]
        for key,value in options.items():config.extend((key,self.option(str(key).lstrip('-'),value,dpi)))
        mapped=[]
        for key,value in maps.items():
            entries=list(self.tk.splitlist(value))
            for i in range(1,len(entries),2):
                entries[i]=self.option(str(key).lstrip('-'),entries[i],dpi)
            mapped.append((key,tuple(entries)))
        def layout(items):
            result=[];items=self.tk.splitlist(items)
            i=0
            while i<len(items):
                result.append(self.element(str(items[i]),dpi));i+=1
                while i<len(items) and str(items[i]).startswith('-'):
                    key=items[i];value=items[i+1];i+=2
                    result.extend((key,layout(value) if key=='-children' else value))
            return tuple(result)
        spec=layout(self.raw_style('layout',base))
        # Refreshes are frequent (any base-style write) but usually change
        # nothing for a given DPI clone. Writing anyway costs a full relayout.
        frozen=(_freeze(config),_freeze(mapped),_freeze(spec))
        previous=self.style_specs.get((dpi,base))
        self.style_specs[(dpi,base)]=frozen
        wrote=False
        if previous is None or previous[0]!=frozen[0]:
            if config:self.raw_style('configure',name,*config);wrote=True
        if previous is None or previous[1]!=frozen[1]:
            for key,entries in mapped:self.raw_style('map',name,key,entries);wrote=True
        if previous is None or previous[2]!=frozen[2]:
            self.raw_style('layout',name,spec);wrote=True
        # A new clone has no widgets yet; changing one in use needs the sweep.
        if wrote:self.mark_write(previous is None or self.additive>0)
        # Heading and tab substyles are looked up relative to the widget style.
        for suffix in ('.Heading','.Tab'):
            if suffix=='.Heading' and base.endswith('Treeview') or suffix=='.Tab' and base.endswith('TNotebook'):
                self.make_style(base+suffix,dpi)
        return name

    def manual(self,widget):
        window=widget.winfo_toplevel();chrome=getattr(window,'_window_chrome',None)
        if chrome:
            path=str(chrome.toolbar)
            return str(widget)==path or str(widget).startswith(path+'.') or widget in chrome.resize_handles.values()
        return False

    def mapped(self,widget):
        self.window_configured(widget)
        if isinstance(widget,tk.Misc) and not isinstance(widget,(tk.Tk,tk.Toplevel)):
            first=str(widget) not in self.widgets
            self.attach(widget)
            if first and str(widget) in self.widgets:
                callback=getattr(widget,'_tkwin11_dpi_changed',None)
                if callback is not None:callback(self.window_dpi(widget.winfo_toplevel()))

    def toplevel_configured(self,path):
        try:self.window_configured(self.root.nametowidget(path))
        except KeyError:pass

    def window_configured(self,window):
        if not isinstance(window,(tk.Tk,tk.Toplevel)) or not hasattr(self,'user32'):return
        if getattr(window,'_window_chrome',None) is not None:return
        path=str(window)
        if path in self.window_jobs:return
        def update():
            self.window_jobs.pop(path,None)
            if not window.winfo_exists():return
            hwnd=self.user32.GetParent(window.winfo_id()) or window.winfo_id()
            anchor=getattr(window,'_dpi_anchor',None)
            dpi=self.window_dpi(anchor) if anchor is not None else self.user32.GetDpiForWindow(hwnd) or self.base_dpi
            if self.windows.get(path)!=dpi:self.update(window,dpi)
        self.window_jobs[path]=self.root.after_idle(update)

    def window_dpi(self,window):
        if str(window) in self.windows:return self.windows[str(window)]
        chrome=getattr(window,'_window_chrome',None)
        if chrome is not None:return getattr(chrome,'dpi',self.base_dpi)
        if isinstance(window,tk.Toplevel) and window.master is not None:
            return self.window_dpi(window.master.winfo_toplevel())
        return self.base_dpi

    def attach(self,widget):
        path=str(widget)
        if path in self.widgets or self.manual(widget):return
        if not isinstance(widget,(ttk.Widget,tk.Canvas)) and not any(k in tk.Misc.keys(widget) for k in ('font','image')):return
        adapter=WidgetResources(self,widget)
        self.widgets[path]=adapter
        adapter.apply()

    def update(self,window,dpi):
        self.windows[str(window)]=dpi
        def walk(widget):
            for child in widget.winfo_children():
                if isinstance(child,tk.Toplevel):continue
                self.attach(child);walk(child)
        walk(window)
        for adapter in list(self.widgets.values()):
            if adapter.window is window:
                adapter.apply()
                callback=getattr(adapter.widget,'_tkwin11_dpi_changed',None)
                if callback is not None:callback(dpi)


class WidgetResources:
    def __init__(self,resources,widget):
        self.resources=resources;self.widget=widget;self.tk=widget.tk
        self.window=widget.winfo_toplevel();self.path=str(widget)
        self.sources=set()
        self.canvas_items={}
        self.parts={}
        self.native=self.path+'__dpi_widget'
        keys=tk.Misc.keys(widget);self.values={}
        for key in ('style','font','image','padding'):
            if key in keys:self.values[key]=widget.cget(key)
        self.tk.call('rename',self.path,self.native)
        # Subcommands command() inspects; all others (instate, identify, coords,
        # bbox, xview, index ...) are native tailcalls with no Python round trip.
        self.dispatch=install_proxy(self.tk,self.path,self.command,self.native,routed=(
            'cget','configure','config','state','invoke','set','create','itemconfigure','itemconfig',
            'delete','item','heading','tag','start','stop'))
        if isinstance(widget,tk.Canvas):
            for item in self.tk.splitlist(self.tk.call(self.native,'find','all')):
                kind=str(self.tk.call(self.native,'type',item))
                key='font' if kind=='text' else 'image' if kind=='image' else None
                if key:self.canvas_items[str(item)]={key:self.tk.call(self.native,'itemcget',item,'-'+key)}
        if isinstance(widget,ttk.Treeview):
            def items(parent=''):
                for item in self.tk.splitlist(self.tk.call(self.native,'children',parent)):
                    image=self.tk.call(self.native,'item',item,'-image')
                    if image:self.parts[('item',str(item))]={'image':image}
                    items(item)
            items()
            for column in ('#0',*self.tk.splitlist(self.tk.call(self.native,'cget','-columns'))):
                image=self.tk.call(self.native,'heading',column,'-image')
                if image:self.parts[('heading',str(column))]={'image':image}
        if isinstance(widget,(tk.Text,ttk.Treeview)):
            for tag in self.tk.splitlist(self.tk.call(self.native,'tag','names')):
                value=self.tk.call(self.native,'tag','configure',tag,'-font')
                if isinstance(widget,tk.Text):value=self.tk.splitlist(value)[-1]
                if value:self.parts[('tag','configure',str(tag))]={'font':value}

    def dpi(self):return self.resources.window_dpi(self.window)

    def transformed(self,key,value):
        if key=='image' and getattr(self.widget,'_dpi_native_images',False):return value
        button=getattr(self.widget,'_tkwin11_button',None)
        if key=='image' and value and button is not None:value=button.image_spec(value)
        if key=='style':
            base=str(value) or self.widget.winfo_class()
            if base in ('TScrollbar','TScale','TProgressbar','TSeparator'):
                orient=str(self.tk.call(self.native,'cget','-orient')).title()
                base=orient+'.'+base
            self.watch_sources(self.resources.style_sources(base))
            return self.resources.make_style(base,self.dpi())
        if key=='image' and value:self.watch_sources(str(v) for v in self.tk.splitlist(value)[::2])
        return self.resources.option(key,value,self.dpi())

    def watch_sources(self,names):
        for name in names:
            self.sources.add(name)
            self.resources.image_users.setdefault(name,set()).add(self)

    def apply(self):
        args=[]
        for key,value in self.values.items():args.extend(('-'+key,self.transformed(key,value)))
        if args:self.tk.call(self.native,'configure',*args)
        for item,values in self.canvas_items.items():
            if not self.tk.call(self.native,'type',item):continue
            args=[]
            for key,value in values.items():args.extend(('-'+key,self.transformed(key,value)))
            self.tk.call(self.native,'itemconfigure',item,*args)
        for prefix,values in list(self.parts.items()):
            args=[]
            for key,value in values.items():args.extend(('-'+key,self.transformed(key,value)))
            try:self.tk.call(self.native,*prefix,*args)
            except tk.TclError:self.parts.pop(prefix,None)

    def command(self,*args):
        if not self.tk.call('info','commands',self.native):return ''
        if isinstance(self.widget,ttk.Progressbar) and args and args[0] in ('start','stop'):
            # Tcl's progress timer is keyed by widget path, not command aliases.
            return self.tk.call('ttk::progressbar::'+args[0],self.path,*args[1:])
        button=getattr(self.widget,'_tkwin11_button',None) or getattr(self.widget,'_tkwin11_choice',None) or getattr(self.widget,'_tkwin11_scale',None)
        if button is not None and args[:2]==('cget','-style'):return button.public_style
        if args and args[0]=='cget' and args[1][1:] in self.values:return self.values[args[1][1:]]
        call=list(args)
        prefix=None
        if isinstance(self.widget,ttk.Treeview) and args and args[0] in ('item','heading') and len(args)>3:prefix=tuple(args[:2])
        if isinstance(self.widget,(tk.Text,ttk.Treeview)) and args[:2]==('tag','configure') and len(args)>4:prefix=tuple(args[:3])
        if prefix:
            values={}
            for i in range(len(prefix),len(call)-1,2):
                key=call[i].lstrip('-')
                if key in ('image','font'):
                    values[key]=call[i+1];call[i+1]=self.transformed(key,call[i+1])
            self.parts.setdefault(prefix,{}).update(values)
        if isinstance(self.widget,tk.Canvas) and args:
            if args[0]=='delete':
                for tag in args[1:]:
                    for item in self.tk.splitlist(self.tk.call(self.native,'find','withtag',tag)):
                        self.canvas_items.pop(str(item),None)
            if args[0] in ('itemconfigure','itemconfig') and len(args)>2:
                values={}
                for i in range(2,len(call)-1,2):
                    key=call[i].lstrip('-')
                    if key in ('font','image'):
                        values[key]=call[i+1];call[i+1]=self.transformed(key,call[i+1])
                for item in self.tk.splitlist(self.tk.call(self.native,'find','withtag',args[1])):
                    self.canvas_items.setdefault(str(item),{}).update(values)
            if args[0]=='create':
                result=self.tk.call(self.native,*args)
                kind=str(args[1]);key='font' if kind=='text' else 'image' if kind=='image' else None
                if key:
                    value=self.tk.call(self.native,'itemcget',result,'-'+key)
                    self.canvas_items[str(result)]={key:value}
                    self.tk.call(self.native,'itemconfigure',result,'-'+key,self.transformed(key,value))
                return result
        if args and args[0] in ('configure','config') and len(args)>2:
            for i in range(1,len(call)-1,2):
                key=call[i].lstrip('-')
                input_controller=getattr(self.widget,'_tkwin11_input',None)
                if key=='takefocus' and input_controller is not None:input_controller.takefocus=call[i+1]
                if key in self.values:
                    if key=='style' and button is not None:
                        button.use_style(call[i+1]);call[i+1]=button.style
                    self.values[key]=call[i+1]
                    call[i+1]=self.transformed(key,call[i+1])
        result=self.tk.call(self.native,*call)
        input_controller=getattr(self.widget,'_tkwin11_input',None)
        if input_controller is not None and args and (args[0]=='state' and len(args)>1 or args[0] in ('configure','config') and len(args)>2):input_controller.schedule()
        if button is not None and args and (args[0] in ('invoke','set') or args[0]=='state' and len(args)>1 or args[0] in ('configure','config') and len(args)>2):button.schedule()
        placeholder=getattr(self.widget,'_tkwin11_placeholder',None)
        if placeholder is not None and args and (args[0]=='state' and len(args)>1 or args[0] in ('configure','config') and len(args)>2):placeholder.schedule()
        if args and args[0] in ('configure','config') and len(args)<=2:
            def public(row):
                row=self.tk.splitlist(row);key=str(row[0]).lstrip('-')
                if key=='style' and button is not None:return (*row[:-1],button.public_style)
                return (*row[:-1],self.values[key]) if key in self.values and len(row)>2 else row
            if len(args)==1:return tuple(public(row) for row in self.tk.splitlist(result))
            return public(result)
        return result

    def destroy(self,event):
        if event.widget is not self.widget:return
        if isinstance(self.widget,ttk.Progressbar):
            try:
                timer='::ttk::progressbar::Timers('+self.path+')'
                if self.tk.getboolean(self.tk.call('info','exists',timer)):
                    self.tk.call('after','cancel',self.tk.globalgetvar(timer));self.tk.call('unset',timer)
            except tk.TclError:pass
        if self.tk.call('info','commands',self.path):self.tk.call('rename',self.path,'')
        if self.tk.call('info','commands',self.dispatch):self.tk.deletecommand(self.dispatch)
        self.resources.widgets.pop(self.path,None)
        self.resources.dirty.discard(self)
        for name in self.sources:
            users=self.resources.image_users.get(name,set());users.discard(self)
            if not users:self.resources.image_users.pop(name,None)


@contextmanager
def additive_styles(widget):
    """Flag style writes that only declare new names for one widget.

    Pooled animation slots are new element/layout names, and the widget that
    adopts one relayouts itself on ``configure -style``. ttk would still send
    <<ThemeChanged>> to every visible widget for each round of such writes.
    """
    resources=getattr(widget._root(),'_dpi_resources',None)
    if resources is not None:resources.additive+=1
    try:yield
    finally:
        if resources is not None:resources.additive-=1


def install(root):
    root=root._root()
    if not hasattr(root,'_dpi_resources'):root._dpi_resources=Resources(root)
    return root._dpi_resources

