"""Lightweight sidebar/card UI for standard tkinter.ttk; no third-party imports.

Call enable_dpi_awareness() before Tk(), then apply_theme(root) before widgets.
Reusable pieces: Card, Sidebar, SegmentedControl, ScrollArea, icon.
"""
from __future__ import annotations

import math
import sys
import tkinter as tk
from tkinter import font as tkfont, ttk
from .tcl_bridge import unbind

PALETTE = dict(bg='#FDFEFE', sidebar='#EEF5F9', fg='#1A1A1A', muted='#606060',
               border='#E8EBED', hover='#F6F6F6', active='#E4EBF0',
               accent='#0067C0', accent_hover='#1975C5', blue='#0067C0',
               selected='#EAF3FF', disabled='#B5BAC4')
THEME = 'win11-fluent'


def enable_dpi_awareness():
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            try: ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except (AttributeError, OSError): pass


def _rgb(color):
    return tuple(int(color[i:i+2], 16) for i in (1, 3, 5))


def _blend(colors):
    return '#' + ''.join(f'{round(sum(c[i] for c in colors)/len(colors)):02x}' for i in range(3))


def _patch(root, fill, border, surrounding, radius, height=80, width=192):
    # Broad centers avoid thousands of tiny image tiles during a resize.
    width, height = max(width, radius*2+4), max(height, radius*2+4)
    image = tk.PhotoImage(master=root, width=width, height=height)
    image.put(fill, to=(0, 0, width, height))
    for rect in ((radius,0,width-radius,1), (radius,height-1,width-radius,height),
                 (0,radius,1,height-radius), (width-1,radius,width,height-radius)):
        image.put(border, to=rect)
    rgb_fill, rgb_border, rgb_bg = map(_rgb, (fill,border,surrounding))
    # Four bulk corner writes instead of one Tcl call per corner pixel.
    corner=[]
    for y in range(radius):
        row=[]
        for x in range(radius):
            samples=[]
            for dx,dy in ((.25,.25),(.75,.25),(.25,.75),(.75,.75)):
                distance=math.hypot(x+dx-radius,y+dy-radius)
                samples.append(rgb_fill if distance<=radius-1 else rgb_border if distance<=radius else rgb_bg)
            row.append(_blend(samples))
        corner.append(row)
    for right,bottom in ((False,False),(True,False),(False,True),(True,True)):
        rows=list(reversed(corner)) if bottom else corner
        data=' '.join('{'+ ' '.join(reversed(row) if right else row)+'}' for row in rows)
        if radius:image.put(data,to=(width-radius if right else 0,height-radius if bottom else 0))
    image._fluent_factory=lambda ratio:_patch(root,fill,border,surrounding,max(1,round(radius*ratio)),max(1,round(height*ratio)),max(1,round(width*ratio)))
    image._fluent_factory.cache_key=('patch',fill,border,surrounding,radius,height,width)
    resources=getattr(root._root(),'_dpi_resources',None)
    if resources is not None:resources.image_factories[str(image)]=image._fluent_factory
    return image


def _raster(root, width, height, sample, transparent=None):
    from .render import _png
    # One RGBA PNG instead of a hex-string put plus a transparency_set call per
    # background pixel; uniform 2x2 samples (most pixels) skip the averaging.
    transparent = tuple(transparent) if transparent is not None else None
    rgba = bytearray()
    for y in range(height):
        for x in range(width):
            a,b,c,d = [sample(x+dx,y+dy) for dx,dy in ((.25,.25),(.75,.25),(.25,.75),(.75,.75))]
            pixel = tuple(a) if a==b==c==d else tuple(round((a[i]+b[i]+c[i]+d[i])/4) for i in range(3))
            rgba.extend(pixel); rgba.append(0 if pixel==transparent else 255)
    image = tk.PhotoImage(master=root, data=_png(width,height,bytes(rgba)), format='png')
    image._fluent_factory=lambda ratio:_raster(root,max(1,round(width*ratio)),max(1,round(height*ratio)),lambda x,y:sample(x/ratio,y/ratio),transparent)
    resources=getattr(root._root(),'_dpi_resources',None)
    if resources is not None:resources.image_factories[str(image)]=image._fluent_factory
    return image


_ICON_COVERAGE={}  # (name, pixel size) -> alpha bytes; colour is applied per request


def _icon_coverage(shapes, extent):
    """Antialiased stroke coverage of a 20-unit line icon at ``extent`` pixels.

    Same per-pixel formula as before, but each stroke only evaluates the
    pixels within its reach; every other pixel has zero coverage anyway.
    """
    segments,circles,dots=shapes
    factor=extent/20;margin=.5/factor
    best=[None]*(extent*extent)
    def visit(left,top,right,bottom,value):
        for y in range(max(0,int(top*factor-.5)-1),min(extent,int(bottom*factor-.5)+2)):
            yy=(y+.5)/factor;row=y*extent
            for x in range(max(0,int(left*factor-.5)-1),min(extent,int(right*factor-.5)+2)):
                v=value((x+.5)/factor,yy)
                if best[row+x] is None or v<best[row+x]:best[row+x]=v
    for (ax,ay),(bx,by) in segments:
        vx,vy=bx-ax,by-ay;length=vx*vx+vy*vy or 1;reach=.55+margin
        def segment(x,y,ax=ax,ay=ay,vx=vx,vy=vy,length=length):
            t=max(0,min(1,((x-ax)*vx+(y-ay)*vy)/length))
            return math.hypot(x-ax-t*vx,y-ay-t*vy)-.55
        visit(min(ax,bx)-reach,min(ay,by)-reach,max(ax,bx)+reach,max(ay,by)+reach,segment)
    for cx,cy,r in circles:
        reach=r+.55+margin
        visit(cx-reach,cy-reach,cx+reach,cy+reach,lambda x,y,cx=cx,cy=cy,r=r:abs(math.hypot(x-cx,y-cy)-r)-.55)
    for cx,cy in dots:
        reach=1.25+margin
        visit(cx-reach,cy-reach,cx+reach,cy+reach,lambda x,y,cx=cx,cy=cy:math.hypot(x-cx,y-cy)-1.25)
    return bytes(0 if v is None else round(255*max(0,min(1,.5-v*factor))) for v in best)


def icon(root, name, size=20, color=None, background=None):
    root = root._root()
    color = color or PALETTE['muted']
    key=(name,size,color)
    cache=getattr(root,'_switch_icon_cache',None)
    if cache is None: cache=root._switch_icon_cache={}
    if key in cache:return cache[key]
    paths={
        'home': [[(3,9),(10,3),(17,9),(17,18),(12,18),(12,12),(8,12),(8,18),(3,18),(3,9)]],
        'layers': [[(2,7),(10,3),(18,7),(10,11),(2,7)],[(2,11),(10,15),(18,11)],[(2,15),(10,19),(18,15)]],
        'expandview': [[(3,8),(3,17),(12,17)],[(3,17),(17,3)],[(10,3),(17,3),(17,10)]],
        'window': [[(3,4),(17,4),(17,17),(3,17),(3,4)],[(3,7),(17,7)]],
        'timer': [[(8,2),(12,2)],[(10,6),(10,11)],[(15,3),(17,5)]],
        'plus': [[(10,3),(10,17)],[(3,10),(17,10)]],
        'search': [[(13,13),(18,18)]],
        'edit': [[(4,12),(13,3),(17,7),(8,16),(3,17),(4,12)],[(11,5),(15,9)]],
        'play': [[(6,3),(16,10),(6,17),(6,3)]],
        'check': [[(4,10),(8,14),(16,5)]],
        'library': [[(3,4),(8,4),(10,6),(12,4),(17,4),(17,16),(12,16),(10,18),(8,16),(3,16),(3,4)],[(10,6),(10,18)]],
        'sources': [[(5,6),(5,14),(15,14)],[(5,6),(15,6)]],
        'settings': [[(3,5),(17,5)],[(3,10),(17,10)],[(3,15),(17,15)]],
        'controls': [[(3,4),(17,4),(17,16),(3,16),(3,4)],[(3,8),(17,8)],[(8,8),(8,16)]],
        'folder': [[(2,5),(8,5),(10,7),(18,7),(16,16),(2,16),(2,5)]],
        'download': [[(10,2),(10,12)],[(6,8),(10,12),(14,8)],[(3,13),(3,17),(17,17),(17,13)]],
        'back': [[(12,4),(6,10),(12,16)]],
        'speed': [[(3,15),(3,10)],[(7,15),(7,7)],[(11,15),(11,4)],[(15,15),(15,9)]],
        'minimize': [[(4,10),(16,10)]],
        'maximize': [[(4,4),(16,4),(16,16),(4,16),(4,4)]],
        'restore': [[(7,6),(7,3),(17,3),(17,13),(14,13)],[(3,7),(13,7),(13,17),(3,17),(3,7)]],
        'close': [[(5,5),(15,15)],[(15,5),(5,15)]],
        'pin': [[(7,3),(13,3),(12,8),(15,12),(5,12),(8,8),(7,3)],[(10,12),(10,18)]],
        'chevrondown': [[(5,7),(10,12),(15,7)]],
        'chevronup': [[(5,12),(10,7),(15,12)]],
        'chevronright': [[(7,4),(13,10),(7,16)]],
        'collapse': [[(11,5),(6,10),(11,15)],[(16,5),(11,10),(16,15)]],
        'expand': [[(4,5),(9,10),(4,15)],[(9,5),(14,10),(9,15)]],
        'star': [[(10,2),(12.4,7.2),(18,7.8),(13.8,11.6),(15,17.2),(10,14.4),(5,17.2),(6.2,11.6),(2,7.8),(7.6,7.2),(10,2)]],
        'calendar': [[(3,5),(17,5),(17,17),(3,17),(3,5)],[(3,8),(17,8)],[(7,3),(7,6)],[(13,3),(13,6)]],
        'image': [[(3,4),(17,4),(17,16),(3,16),(3,4)],[(3,14),(8,9),(12,13),(14,11),(17,14)]],
        'chart': [[(3,17),(17,17)],[(5,14),(5,10)],[(9,14),(9,6)],[(13,14),(13,9)]],
        'list': [[(7,5),(17,5)],[(7,10),(17,10)],[(7,15),(17,15)],[(3,5),(4,5)],[(3,10),(4,10)],[(3,15),(4,15)]],
        'text': [[(4,5),(16,5)],[(10,5),(10,16)]],
        'navigation': [[(3,4),(7,4),(7,16),(3,16),(3,4)],[(10,6),(17,6)],[(10,10),(17,10)],[(10,14),(15,14)]],
        'bell': [[(5,14),(5,9),(7,5),(10,4),(13,5),(15,9),(15,14),(16,15),(4,15),(5,14)],[(9,17),(11,17)]],
        'palette': [[(10,3),(15,5),(17,10),(15,14),(12,13),(11,15),(10,17),(5,15),(3,10),(5,5),(10,3)]],
        'dialog': [[(3,4),(17,4),(17,14),(9,14),(5,17),(5,14),(3,14),(3,4)]],
        'refresh': [[(15,6),(13,4),(10,3),(6,4),(4,7),(3,10),(4,13),(6,15),(10,16),(13,15),(15,13)],[(15,2),(15,6),(11,6)]],
        'code': [[(7,6),(3,10),(7,14)],[(13,6),(17,10),(13,14)],[(11,4),(9,16)]],
    }
    circles={'palette':[(7,8,1),(11,7,1),(14,10,1)],'timer':[(10,12,7)],'search':[(8,8,5)],'sources':[(5,6,2),(15,6,2),(15,14,2)],
             'settings':[(7,5,2),(13,10,2),(8,15,2)],'info':[(10,10,8)]}
    dots={'more':[(4,10),(10,10),(16,10)],'info':[(10,6)]}
    if name=='info':paths['info']=[[(10,9),(10,14)]]
    foreground=_rgb(color)
    shapes=([(a,b) for path in paths.get(name,[]) for a,b in zip(path,path[1:])],circles.get(name,[]),dots.get(name,[]))
    def render(ratio=1):
        # Rasterize geometry at the destination DPI, with coverage alpha instead
        # of baking a particular card/sidebar background into edge pixels.
        from .render import _png
        extent=max(1,round(size*ratio))
        alpha=_ICON_COVERAGE.get((name,extent))
        if alpha is None:alpha=_ICON_COVERAGE[(name,extent)]=_icon_coverage(shapes,extent)
        rgba=bytearray(bytes(foreground)+b'\0')*(extent*extent)
        rgba[3::4]=alpha
        return tk.PhotoImage(master=root,data=_png(extent,extent,bytes(rgba)),format='png')
    render.cache_key=('icon',name,size,color)
    cache[key]=render()
    resources=getattr(root,'_dpi_resources',None)
    if resources is not None:resources.image_factories[str(cache[key])]=render
    return cache[key]


def apply_theme(root, *, font_family='Microsoft YaHei UI', font_size=10, accent=None, accent_hover=None):
    from .dpi import install
    install(root)
    root=root._root()
    if getattr(root,'_tkwin11_ready',False):
        ttk.Style(root).theme_use(THEME)
        from .switches import install_switch_compatibility
        install_switch_compatibility(root)
        from .dropdowns import install_dropdown_compatibility
        install_dropdown_compatibility(root)
        from .menus import install_menu_compatibility
        install_menu_compatibility(root)
        from .layouts import install_layout_compatibility
        install_layout_compatibility(root)
        from .button import install_button_compatibility
        install_button_compatibility(root)
        from .selection import install_selection
        install_selection(root)
        from .slider import install_scale_compatibility
        install_scale_compatibility(root)
        from .inputs import install_inputs
        install_inputs(root)
        return PALETTE.copy()
    if accent is not None:PALETTE['accent']=PALETTE['blue']=accent
    if accent_hover is not None:PALETTE['accent_hover']=accent_hover
    scale=root.winfo_fpixels('1i')/96
    px=lambda n:max(1,round(n*scale))
    root.tk.call('tk','scaling',root.winfo_fpixels('1i')/72)
    # Resolving one font is instant; tkfont.families() enumerates every
    # installed font (up to ~1 s on a cold Windows start).
    if tkfont.Font(root=root,family=font_family).actual('family')!=font_family:font_family=tkfont.nametofont('TkDefaultFont').actual('family')
    for name in ('TkDefaultFont','TkTextFont','TkMenuFont','TkHeadingFont','TkCaptionFont','TkTooltipFont'):
        tkfont.nametofont(name,root=root).configure(family=font_family,size=font_size)
    normal=(font_family,font_size);bold=(font_family,font_size,'bold');small=(font_family,max(9,font_size-1))
    c=PALETTE
    style=ttk.Style(root)
    if THEME not in style.theme_names():style.theme_create(THEME,parent='clam')
    style.theme_use(THEME)
    style.configure('.',background=c['bg'],foreground=c['fg'],font=normal,bordercolor=c['border'],
                    lightcolor=c['bg'],darkcolor=c['bg'],troughcolor=c['hover'])
    style.map('.',foreground=[('disabled',c['disabled'])])
    root.configure(background=c['bg'])
    # Classic widget classes only. A bare '*Font' is also read by ttk.Label /
    # ttk.Entry, whose explicit -font then overrides every style font
    # (Strong.TLabel, Muted.TLabel, section titles ...).
    for widget in ('Label','Button','Checkbutton','Radiobutton','Menubutton','Message','Labelframe',
                   'Scale','Text','Entry','Spinbox','Listbox'):
        root.option_add(f'*{widget}.Font','TkDefaultFont')
    for widget in ('Text','Listbox','Canvas','Menu'):
        root.option_add(f'*{widget}.background',c['bg'])
    for widget in ('Text','Listbox','Menu'):
        root.option_add(f'*{widget}.foreground',c['fg'])
        root.option_add(f'*{widget}.selectBackground',c['selected'])
        root.option_add(f'*{widget}.selectForeground',c['fg'])
    root._tkwin11_images=[]
    patch_cache={}
    def face(fill,border,background,radius=4,height=80):
        key=(fill,border,background,radius,height)
        if key not in patch_cache:
            patch_cache[key]=_patch(root,fill,border,background,px(radius),height)
            root._tkwin11_images.append(patch_cache[key])
        return patch_cache[key]
    def element(name,normal_image,states=(),radius=4,content_padding=0):
        style.element_create(name,'image',normal_image,*states,border=px(radius)+1,
                             width=px(radius)*2+2,height=px(radius)*2+2,sticky='nsew',padding=content_padding)
    for name,fill in [('TFrame',c['bg']),('Sidebar.TFrame',c['sidebar']),('Segment.TFrame',c['hover']),('Selected.TFrame',c['selected'])]:
        style.configure(name,background=fill)
    for prefix,bg in [('',c['bg']),('Sidebar.',c['sidebar']),('Selected.',c['selected'])]:
        for suffix,fg,font in [('TLabel',c['fg'],normal),('Muted.TLabel',c['muted'],small),('Strong.TLabel',c['fg'],bold)]:
            style.configure(prefix+suffix,background=bg,foreground=fg,font=font)
    style.configure('Blue.TLabel',foreground=c['blue'],background=c['selected'])
    for name,fill,border in [('Card.TFrame',c['bg'],c['border']),('Selected.Card.TFrame',c['selected'],c['blue']),('Segment.Card.TFrame',c['hover'],c['hover'])]:
        elem=name+'.surface'
        element(elem,face(fill,border,c['bg'],5,128),radius=5,content_padding=px(5)+1)
        style.layout(name,[(elem,{'sticky':'nsew'})])
        style.configure(name,background=fill)
    element('Selected.Logo.surface',face(c['bg'],c['border'],c['selected'],5,128),radius=5)
    style.layout('Selected.Logo.TFrame',[('Selected.Logo.surface',{'sticky':'nsew'})])
    definitions=[
        ('TButton',c['bg'],c['border'],c['hover'],c['fg'],c['bg']),
        ('Primary.TButton',c['accent'],c['accent'],c['accent_hover'],'#FFFFFF',c['bg']),
        ('Quiet.TButton',c['bg'],c['bg'],c['hover'],c['muted'],c['bg']),
        ('Close.TButton',c['bg'],c['bg'],'#FFE8E8',c['muted'],c['bg']),
        ('CardIcon.TButton',c['selected'],c['selected'],'#DCEBFE',c['muted'],c['selected']),
        ('Nav.TButton',c['sidebar'],c['sidebar'],c['active'],c['fg'],c['sidebar']),
        ('Selected.Nav.TButton',c['active'],c['active'],c['active'],c['fg'],c['sidebar']),
        ('Segment.TButton',c['hover'],c['hover'],c['active'],c['muted'],c['hover']),
        ('Selected.Segment.TButton',c['bg'],c['bg'],c['bg'],c['blue'],c['hover']),
    ]
    for name,fill,border,hover,fg,surround in definitions:
        elem=name+'.surface'
        pressed='#3184CA' if name=='Primary.TButton' else c['active']
        element(elem,face(fill,border,surround),[
            ('disabled',face('#C4C4C4' if name=='Primary.TButton' else surround if name=='Quiet.TButton' else c['hover'],surround if name=='Quiet.TButton' else c['border'],surround)),
            ('pressed focus',face(pressed,c['fg'],surround)),
            ('active focus',face(hover,c['fg'],surround)),
            ('focus',face(fill,c['fg'],surround)),
            ('pressed',face(pressed,border,surround)),('active',face(hover,border,surround))])
        style.layout(name,[(elem,{'sticky':'nsew','children':[
            ('Button.padding',{'sticky':'nsew','children':[('Button.label',{'sticky':'nsew'})]})]})])
        style.configure(name,foreground=fg,padding=(px(12),px(6)),font=normal,anchor='center')
        style.map(name,foreground=[('disabled','#FFFFFF' if name=='Primary.TButton' else c['disabled']),('pressed','#E5EFF8' if name=='Primary.TButton' else c['muted'])])
    for name in ('Nav.TButton','Selected.Nav.TButton'):
        style.configure(name,anchor='w',padding=(px(10),px(8)))
    for name in ('Quiet.TButton','CardIcon.TButton','Close.TButton'):style.configure(name,padding=px(5))
    style.configure('Dropdown.TButton',anchor='w')
    # Fixed width leaves a gap before the label; sticky 'e' keeps the chevron
    # from being tiled when a layout stretches the element vertically.
    style.element_create('Switch.Dropdown.arrow','image',icon(root,'chevrondown',px(13)),
                         ('disabled',icon(root,'chevrondown',px(13),color=c['disabled'])),width=px(13)+px(8),sticky='e')
    style.layout('Dropdown.TButton',[('TButton.surface',{'sticky':'nsew','children':[
        ('Button.padding',{'sticky':'nsew','children':[
            ('Switch.Dropdown.arrow',{'side':'right','sticky':''}),
            ('Button.label',{'side':'left','sticky':'nsew','expand':True})]})]})])
    style.configure('Popup.Quiet.TButton',anchor='w',padding=(px(12),px(8)))
    for selected in (False,True):
        source='Selected.Nav.TButton' if selected else 'Nav.TButton'
        name='Selected.Icon.Nav.TButton' if selected else 'Icon.Nav.TButton'
        style.layout(name,style.layout(source))
        style.configure(name,anchor='center',padding=(px(4),px(8)))
    # Native C-drawn controls; recolor only the small surfaces that need it.
    entry_face='Switch.Entry.field'
    def field(fill,line,thickness=1):
        image=_patch(root,fill,c['border'],c['bg'],px(4))
        inset=px(4)
        image.put(line,to=(inset,image.height()-px(thickness),image.width()-inset,image.height()))
        def render(ratio):
            scaled=_patch(root,fill,c['border'],c['bg'],max(1,round(px(4)*ratio)),height=round(80*ratio),width=round(192*ratio))
            inset=max(1,round(px(4)*ratio));depth=max(1,round(px(thickness)*ratio))
            scaled.put(line,to=(inset,scaled.height()-depth,scaled.width()-inset,scaled.height()))
            return scaled
        render.cache_key=('field',fill,line,thickness,scale)
        root._dpi_resources.image_factories[str(image)]=render
        root._tkwin11_images.append(image)
        return image
    element(entry_face,field(c['bg'],'#8A8A8A'),[
        ('disabled',field(c['sidebar'],c['border'])),
        ('invalid',field(c['bg'],'#C42B1C',2)),
        ('focus',field(c['bg'],c['accent'],2)),
        ('active',field(c['hover'],'#606060'))],radius=4)
    element('Fluent.Combo.field',field(c['bg'],'#8A8A8A'),[
        ('disabled',face(c['sidebar'],c['border'],c['bg'],4)),
        ('invalid',field(c['bg'],'#C42B1C',2)),
        ('readonly pressed focus',face(c['active'],c['fg'],c['bg'],4)),
        ('readonly pressed',face(c['active'],c['border'],c['bg'],4)),
        ('readonly active focus',face(c['hover'],c['fg'],c['bg'],4)),
        ('readonly focus',face(c['bg'],c['fg'],c['bg'],4)),
        ('readonly active',face(c['hover'],c['border'],c['bg'],4)),
        ('readonly',face(c['bg'],c['border'],c['bg'],4)),
        ('focus',field(c['bg'],c['accent'],2)),
        ('active',field(c['hover'],'#606060'))],radius=4)
    style.layout('TEntry',[(entry_face,{'sticky':'nsew','children':[
        ('Entry.padding',{'sticky':'nsew','children':[('Entry.textarea',{'sticky':'nsew'})]})]})])
    style.configure('TEntry',padding=(px(9),px(6)),fieldbackground=c['bg'],insertcolor=c['fg'])
    style.map('TEntry',fieldbackground=[('disabled',c['sidebar']),('invalid',c['bg']),('focus',c['bg']),('active',c['hover']),('readonly',c['bg'])])
    for name in ('TCombobox','TSpinbox'):
        style.configure(name,padding=px(6),fieldbackground=c['bg'],background=c['bg'],
                        arrowcolor=c['muted'],arrowsize=px(12),borderwidth=1)
        style.map(name,fieldbackground=[('disabled',c['sidebar']),('readonly',c['bg'])],bordercolor=[('focus',c['blue'])])
    style.map('TSpinbox',fieldbackground=[('disabled',c['sidebar']),('invalid',c['bg']),('focus',c['bg']),('active',c['hover']),('readonly',c['bg'])])
    for direction,symbol in [('up','chevronup'),('down','chevrondown')]:
        style.element_create('Switch.Spinbox.'+direction+'arrow','image',icon(root,symbol,px(9)),
                             ('disabled',icon(root,symbol,px(9),color=c['disabled'])),
                             width=px(28),height=px(28),sticky='')
    style.layout('TSpinbox',[(entry_face,{'sticky':'nsew','children':[
        ('null',{'side':'right','sticky':'ns','children':[
            ('Switch.Spinbox.uparrow',{'side':'left','sticky':'e'}),
            ('Switch.Spinbox.downarrow',{'side':'left','sticky':'e'})]}),
        ('Spinbox.padding',{'sticky':'nsew','children':[('Spinbox.textarea',{'sticky':'nsew'})]})]})])
    for widget in ('Checkbutton','Radiobutton'):
        if 'vista' in style.theme_names():
            indicator='Switch.'+widget+'.indicator'
            style.element_create(indicator,'from','vista',widget+'.indicator')
            style.layout('T'+widget,[(widget+'.padding',{'sticky':'nsew','children':[
                (indicator,{'side':'left'}),(widget+'.focus',{'side':'left','sticky':'nsew','children':[(widget+'.label',{'sticky':'nsew'})]})]})])
        style.configure('T'+widget,background=c['bg'],padding=(0,px(3)))
        style.map('T'+widget,background=[('active',c['bg'])])
    from .switches import configure_switch_style
    configure_switch_style(root,style)
    style.configure('Treeview',background=c['bg'],fieldbackground=c['bg'],borderwidth=0,
                    rowheight=tkfont.nametofont('TkDefaultFont').metrics('linespace')+px(14))
    style.map('Treeview',background=[('selected',c['selected'])],foreground=[('selected',c['fg'])])
    style.configure('Treeview.Heading',background=c['sidebar'],font=normal,relief='flat',padding=(px(10),px(10)))
    style.map('Treeview.Heading',background=[('active',c['hover'])])
    style.configure('TNotebook',borderwidth=0,background=c['bg'])
    style.configure('TNotebook.Tab',padding=(px(14),px(8)),background=c['sidebar'])
    style.map('TNotebook.Tab',background=[('selected',c['bg'])])
    for orient in ('Horizontal','Vertical'):
        direction='ew' if orient=='Horizontal' else 'ns'
        thickness=px(6)
        width,height=(96,thickness) if orient=='Horizontal' else (thickness,96)
        thumb_images=[]
        for color in ('#C6C8D0','#A8ADB8','#888F9F'):
            rgb_color=_rgb(color);rgb_bg=_rgb(c['bg'])
            def sample(x,y,w=width,h=height,fg=rgb_color,bg=rgb_bg):
                radius=min(w,h)/2
                cx=min(max(x,radius),w-radius);cy=min(max(y,radius),h-radius)
                return fg if math.hypot(x-cx,y-cy)<=radius else bg
            image=_raster(root,width,height,sample,transparent=rgb_bg)
            thumb_images.append(image)
        root._tkwin11_images.extend(thumb_images)
        track='Switch.'+orient+'.Scrollbar.trough'
        thumb='Switch.'+orient+'.Scrollbar.thumb'
        # The default-theme trough is flat; no clam border, arrows or groove.
        style.element_create(track,'from','default',orient+'.Scrollbar.trough')
        style.element_create(thumb,'image',thumb_images[0],('pressed',thumb_images[2]),('active',thumb_images[1]),
                             border=max(1,thickness//2),width=thickness if orient=='Vertical' else thickness*2,
                             height=thickness if orient=='Horizontal' else thickness*2,sticky='nsew')
        style.configure(orient+'.TScrollbar',background=c['bg'],troughcolor=c['bg'],
                        borderwidth=0,relief='flat',gripcount=0,width=thickness,arrowsize=thickness)
        style.layout(orient+'.TScrollbar',[(track,{'sticky':'nsew','children':[
            (thumb,{'expand':True,'sticky':direction})]})])
        style.configure(orient+'.TProgressbar',background=c['accent'],lightcolor=c['accent'],darkcolor=c['accent'],
                        troughcolor=c['hover'],borderwidth=0,thickness=px(4))
        dimensions={'height':px(4)} if orient=='Horizontal' else {'width':px(4),'height':128}
        track_image=_patch(root,'#E2E5E9','#E2E5E9',c['bg'],px(2),**dimensions)
        value_image=_patch(root,c['accent'],c['accent'],c['bg'],px(2),**dimensions)
        root._tkwin11_images.extend((track_image,value_image))
        track_name='Fluent.'+orient+'.Progressbar.trough';value_name='Fluent.'+orient+'.Progressbar.pbar'
        style.element_create(track_name,'image',track_image,border=px(2),padding=0,width=px(4),height=px(4),sticky='nsew')
        style.element_create(value_name,'image',value_image,border=px(2),padding=0,width=0,height=0,sticky='nsew')
        style.layout(orient+'.TProgressbar',[(track_name,{'sticky':'nsew','children':[(value_name,{'side':'left' if orient=='Horizontal' else 'bottom','sticky':'nsew'})]})])
        style.configure(orient+'.TScale',background=c['blue'],troughcolor=c['hover'],borderwidth=0)
    style.configure('TSeparator',background=c['border'])
    for orientation,width,height in [('Horizontal',256,1),('Vertical',1,256)]:
        image=tk.PhotoImage(master=root,width=width,height=height)
        image.put(c['border'],to=(0,0,width,height));root._tkwin11_images.append(image)
        element_name='Switch.'+orientation+'.hairline'
        style.element_create(element_name,'image',image,width=1,height=1,sticky='nsew')
        style.layout(orientation+'.TSeparator',[(element_name,{'sticky':'nsew'})])
    style.configure('TPanedwindow',background=c['border'])
    from .widgets import configure_widget_styles, install_input_focus
    configure_widget_styles(root,style)
    from .extras import configure_extra_styles
    configure_extra_styles(root,style)
    from .forms import configure_form_styles
    configure_form_styles(root,style)
    install_input_focus(root)
    from .switches import install_switch_compatibility
    install_switch_compatibility(root)
    from .dropdowns import install_dropdown_compatibility
    install_dropdown_compatibility(root)
    from .menus import install_menu_compatibility
    install_menu_compatibility(root)
    from .layouts import install_layout_compatibility
    install_layout_compatibility(root)
    root._tkwin11_ready=True
    from .button import install_button_compatibility
    install_button_compatibility(root)
    from .selection import install_selection
    install_selection(root)
    from .slider import install_scale_compatibility
    install_scale_compatibility(root)
    from .inputs import install_inputs
    install_inputs(root)
    return c.copy()


class Card(ttk.Frame):
    def __init__(self,master,*,selected=False,padding=18,border_color=None,command=None,**kwargs):
        self._selected=selected
        self._border_color=border_color
        self._last_size=None
        minimum=round(master.winfo_fpixels('1i')/96*5)+1
        padding=tuple(max(minimum,value) for value in padding) if isinstance(padding,(tuple,list)) else max(minimum,padding)
        super().__init__(master,style='Selected.TFrame' if selected else 'TFrame',padding=padding,**kwargs)
        self._surface=tk.Canvas(self,background=PALETTE['bg'],highlightthickness=0,borderwidth=0,takefocus=False)
        self._surface._dpi_native_images=True
        self._surface.place(x=0,y=0,relwidth=1,relheight=1,bordermode='outside')
        tk.Misc.lower(self._surface)
        self._fills=[self._surface.create_rectangle(0,0,0,0,width=0) for _ in range(2)]
        self._edges=[self._surface.create_line(0,0,0,0,width=1) for _ in range(4)]
        self._corners=[self._surface.create_image(0,0,anchor='nw') for _ in range(4)]
        self.bind('<Configure>',self._resize,add='+')
        self._recolor()
        from .cards import CardInteraction
        self.interaction=CardInteraction(self,command)
    def invoke(self):return self.interaction.invoke()
    def state(self,statespec=None):
        result=super().state(statespec)
        if statespec is not None and hasattr(self,'interaction'):self.interaction.state_changed()
        return result
    def _tkwin11_dpi_changed(self,dpi):
        self._card_dpi=dpi;self._last_size=None;self._recolor()
        from types import SimpleNamespace
        self._resize(SimpleNamespace(width=self.winfo_width(),height=self.winfo_height()))
    def _recolor(self):
        fill=getattr(self,'_feedback_fill',None) or (PALETTE['selected'] if self._selected else PALETTE['bg'])
        border=self._border_color or (PALETTE['blue'] if self._selected else PALETTE['border'])
        if getattr(self,'_feedback_focus',False):border=PALETTE['fg']
        for item in self._fills:self._surface.itemconfigure(item,fill=fill)
        for item in self._edges:self._surface.itemconfigure(item,fill=border)
        root=self._root();radius=round(getattr(self,'_card_dpi',self.winfo_fpixels('1i'))/96*5)
        cache=getattr(root,'_card_corner_cache',None)
        if cache is None:cache=root._card_corner_cache={}
        key=(fill,border,radius)
        if key not in cache:
            source=_patch(root,fill,border,PALETTE['bg'],radius)
            w,h=source.width(),source.height();size=radius+1
            images=[]
            for x,y in ((0,0),(w-size,0),(0,h-size),(w-size,h-size)):
                image=tk.PhotoImage(master=root,width=size,height=size)
                root.tk.call(image,'copy',source,'-from',x,y,x+size,y+size,'-to',0,0)
                images.append(image)
            cache[key]=images
        for item,image in zip(self._corners,cache[key]):self._surface.itemconfigure(item,image=image)
    def _resize(self,event):
        size=(event.width,event.height)
        if size==self._last_size:return
        self._last_size=size
        w,h=event.width,event.height
        r=round(getattr(self,'_card_dpi',self.winfo_fpixels('1i'))/96*5)+1
        # Native filled rectangles/lines + four fixed, antialiased corners.
        # Only coordinates change during resizing; no large image fills.
        for item,coords in zip(self._fills,((r,0,w-r,h),(0,r,w,h-r))):self._surface.coords(item,*coords)
        for item,coords in zip(self._edges,((r,0,w-r,0),(r,h-1,w-r,h-1),(0,r,0,h-r),(w-1,r,w-1,h-r))):self._surface.coords(item,*coords)
        for item,point in zip(self._corners,((0,0),(w-r,0),(0,h-r),(w-r,h-r))):self._surface.coords(item,*point)
    def configure(self,cnf=None,**kwargs):
        if isinstance(cnf,dict):kwargs={**cnf,**kwargs};cnf=None
        if kwargs.get('style') in ('Card.TFrame','Selected.Card.TFrame'):
            self._selected=kwargs['style'].startswith('Selected.')
            kwargs['style']='Selected.TFrame' if self._selected else 'TFrame'
            if hasattr(self,'interaction'):self.interaction.refresh()
            elif hasattr(self,'_surface'):self._recolor()
        return super().configure(cnf,**kwargs)
    config=configure


class SegmentedControl(ttk.Frame):
    def __init__(self,master,items,variable,command=None,**kwargs):
        scale=master.winfo_fpixels('1i')/96
        super().__init__(master,style='Segment.Card.TFrame',padding=round(4*scale),**kwargs)
        self.variable,self.command=variable,command
        self.buttons={}
        for value,label in items:
            button=ttk.Button(self,text=label,style='Segment.TButton',command=lambda v=value:self.select(v))
            button.pack(side='left',padx=round(2*scale))
            for sequence,action in (('<Left>','previous'),('<Right>','next'),('<Home>','first'),('<End>','last')):
                button.bind(sequence,lambda event,v=value,a=action:self.navigate(v,a))
            self.buttons[value]=button
        self._trace=variable.trace_add('write',self.sync)
        self.bind('<Destroy>',self.cleanup,add='+')
        self.sync()
    def sync(self,*_):
        for value,button in self.buttons.items():
            button.configure(style='Selected.Segment.TButton' if value==self.variable.get() else 'Segment.TButton')
    def select(self,value):
        if self.instate(['disabled']) or value not in self.buttons or self.buttons[value].instate(['disabled']):return
        if self.variable.get()==value:return
        self.variable.set(value)
        if self.command:self.command(value)
    def navigate(self,value,action):
        if self.instate(['disabled']):return 'break'
        enabled=[key for key,button in self.buttons.items() if not button.instate(['disabled'])]
        if not enabled:return 'break'
        index=enabled.index(value) if value in enabled else 0
        target=enabled[0] if action=='first' else enabled[-1] if action=='last' else enabled[(index+(-1 if action=='previous' else 1))%len(enabled)]
        self._root()._button_keyboard=True
        self.buttons[target].focus_set();self.select(target)
        return 'break'
    def state(self,statespec=None):
        was_disabled=self.instate(['disabled'])
        result=super().state(statespec)
        if statespec is not None and self.instate(['disabled'])!=was_disabled:
            disabled=self.instate(['disabled'])
            if disabled and not was_disabled:self._disabled_before={key for key,button in self.buttons.items() if button.instate(['disabled'])}
            for key,button in self.buttons.items():
                button.state(['disabled'] if disabled or key in getattr(self,'_disabled_before',set()) else ['!disabled'])
        return result
    def cleanup(self,event):
        if event.widget is self:self.variable.trace_remove('write',self._trace)


class Sidebar(ttk.Frame):
    def __init__(self,master,items,command,*,width=230):
        from .navigation import _Pill
        super().__init__(master,style='Sidebar.TFrame',width=width)
        self.pack_propagate(False)
        self.buttons={}
        self.labels={}
        self._markers={}
        self._px=lambda value:max(1,round(value*self.winfo_fpixels('1i')/96))
        self.collapsed=False
        self.selected_key=None
        self.command=command
        for key,label,symbol in items:
            button=ttk.Button(self,text='  '+label,image=icon(self,symbol,background=PALETTE['sidebar']),
                              compound='left',style='Nav.TButton',command=lambda k=key:self.select(k))
            button.pack(fill='x',padx=12,pady=3)
            self.buttons[key]=(button,symbol)
            self.labels[key]=label
            marker=_Pill(button,self._px)
            marker.bind('<Button-1>',lambda event,k=key:self.select(k))
            self._markers[key]=marker
            Tooltip(button,label)
            button.bind('<Enter>',lambda event,k=key:self.hover(k,True),add='+')
            button.bind('<Leave>',lambda event,k=key:self.hover(k,False),add='+')
    def select(self,key,notify=True):
        self.selected_key=key
        for name,(button,symbol) in self.buttons.items():
            selected=name==key
            if getattr(button,'_nav_visual',None)==(selected,self.collapsed):continue
            button._nav_visual=(selected,self.collapsed)
            style=('Selected.' if selected else '')+('Icon.Nav.TButton' if self.collapsed else 'Nav.TButton')
            button.configure(style=style,text='' if self.collapsed else '  '+self.labels[name],
                             image=icon(self,symbol,background=PALETTE['active'] if selected else PALETTE['sidebar']))
            marker=self._markers[name]
            if selected:
                marker.paint(self._px(18),PALETTE['active'])
                marker.place(x=0,rely=.5,anchor='w')
            else:marker.place_forget()
        if notify:self.command(key)
    def set_collapsed(self,collapsed):
        self.collapsed=bool(collapsed)
        self.select(self.selected_key,notify=False)
    def hover(self,key,active):
        button,symbol=self.buttons[key]
        background=PALETTE['active'] if active or key==self.selected_key else PALETTE['sidebar']
        button.configure(image=icon(self,symbol,background=background))


class ScrollArea(ttk.Frame):
    """A scrollable native widget container. Coalesces geometry work at idle."""
    def __init__(self,master,*,reserve_scrollbar=False,**kwargs):
        super().__init__(master,**kwargs)
        self.reserve_scrollbar=reserve_scrollbar
        self.gutter=None
        if reserve_scrollbar:
            self.gutter=ttk.Frame(self,width=max(8,round(10*self.winfo_fpixels('1i')/96)))
            self.gutter.pack(side='right',fill='y')
            self.gutter.pack_propagate(False)
        self.canvas=tk.Canvas(self,background=PALETTE['bg'],highlightthickness=0,borderwidth=0)
        from .scrolling import SmoothScroll
        self._smooth=SmoothScroll(self.canvas)
        self.bar=ttk.Scrollbar(self.gutter or self,command=self._smooth.direct)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side='left',fill='both',expand=True)
        self.body=ttk.Frame(self.canvas)
        self.window=self.canvas.create_window((0,0),window=self.body,anchor='nw')
        self.canvas._tkwin11_scroll_area=self
        self._job=None;self._width=0;self._wheel_remainder=0
        # Tk's canvas maps an embedded window whenever its requested size
        # changes, even while the canvas is hidden. A DPI or font change would
        # then map every widget of every cached page, and each later relayout
        # would redraw all of those invisible pages. Hide the item instead.
        self.bind('<Map>',self._shown,add='+')
        self.bind('<Unmap>',self._hidden,add='+')
        self.canvas.bind('<Configure>',self.resize)
        self.canvas.configure(takefocus=True)
        for sequence,amount in [('<Prior>',-1),('<Next>',1)]:
            self.canvas.bind(sequence,lambda e,n=amount:self._smooth.direct('scroll',n,'pages') or 'break')
        self.canvas.bind('<Home>',lambda e:self._smooth.direct('moveto',0) or 'break')
        self.canvas.bind('<End>',lambda e:self._smooth.direct('moveto',1) or 'break')
        self.body.bind('<Configure>',self.schedule)
        self.bind('<Destroy>',self.cleanup,add='+')
        # A per-container bindtag prevents wheel handlers leaking to other pages.
        self._wheel_tag='ScrollArea'+str(id(self))
        self.bind_class(self._wheel_tag,'<MouseWheel>',self.wheel)
        self.bind_class(self._wheel_tag,'<Destroy>',self.schedule)
        self.bind_class(self._wheel_tag,'<Unmap>',self.schedule)
        _install_scroll_dispatch(self.winfo_toplevel())
        self.install_wheel(self.canvas)
    def _hidden(self,event):
        if event.widget is self:self.canvas.itemconfigure(self.window,state='hidden')
    def _shown(self,event):
        if event.widget is self:
            self.canvas.itemconfigure(self.window,state='normal');self.schedule()
    def resize(self,event):
        if self._width!=event.width:
            self._width=event.width
            self.canvas.itemconfigure(self.window,width=event.width)
        self.schedule()
    def schedule(self,*_):
        if self._job is None:self._job=self.after(16,self.refresh)
    def refresh(self):
        self._job=None
        # A hidden body has no bbox; measuring now would reset the scroll
        # position of cached pages. _shown() schedules a refresh.
        if str(self.canvas.itemcget(self.window,'state'))=='hidden':return
        # Geometry managers retain the old requested size after their last
        # child disappears. Reset the body's request, retaining natural sizing
        # so adding new children can grow it again.
        empty=not any(child.winfo_manager() for child in self.body.winfo_children())
        if empty and self.body.winfo_reqheight()>max(1,self.body.winfo_pixels(self.body.cget('height'))):
            self.body.configure(height=max(1,self.body.winfo_pixels(self.body.cget('height'))))
            self.schedule()
        bounds=self.canvas.bbox('all') or (0,0,0,0)
        region=(0,0,max(bounds[2],self.canvas.winfo_width()),max(bounds[3],self.canvas.winfo_height()))
        if region!=getattr(self,'_last_region',None):
            self._last_region=region
            self._smooth.tween.cancel()
            self.canvas.configure(scrollregion=region)
        if bounds[3]<=self.canvas.winfo_height() or self.canvas.canvasy(0)<0:self.canvas.yview_moveto(0)
        need=bounds[3]>self.canvas.winfo_height()
        if need and not self.bar.winfo_manager():self.bar.pack(side='right',fill='y')
        elif not need and self.bar.winfo_manager():self.bar.pack_forget()
    def install_wheel(self,widget):
        tags=widget.bindtags()
        if self._wheel_tag not in tags:widget.bindtags((tags[0],self._wheel_tag,*tags[1:]))
        for child in widget.winfo_children():self.install_wheel(child)
    def _mapped(self,event):
        if str(event.widget).startswith(str(self.canvas)+'.'):
            tags=event.widget.bindtags()
            if self._wheel_tag not in tags:event.widget.bindtags((tags[0],self._wheel_tag,*tags[1:]))
    def wheel(self,event):
        if not event.delta:return
        direction=-1 if event.delta>0 else 1
        if isinstance(event.widget,(tk.Text,tk.Listbox,ttk.Treeview)):
            first,last=event.widget.yview()
            if (direction<0 and first>1e-7) or (direction>0 and last<1-1e-7):return None
        if self._smooth.wheel(event.delta):return 'break'
    def cleanup(self,event):
        if event.widget is self:
            if self._job is not None:self.after_cancel(self._job)
            self.unbind_class(self._wheel_tag,'<MouseWheel>')
            self.unbind_class(self._wheel_tag,'<Destroy>')
            self.unbind_class(self._wheel_tag,'<Unmap>')


def _install_scroll_dispatch(window):
    """One <Map> hook per toplevel gives late children their ScrollArea wheel tags.

    Replaces one root binding per ScrollArea: those never saw widgets inside
    popups/dialogs, cost O(areas) per Map, and unbinding one removed them all.
    """
    if getattr(window,'_tkwin11_scroll_dispatch',False):return
    window._tkwin11_scroll_dispatch=True
    def mapped(event):
        widget=event.widget
        if not isinstance(widget,tk.Misc):return
        areas=[];node=widget.master
        while node is not None:
            area=getattr(node,'_tkwin11_scroll_area',None)
            if area is not None:areas.append(area)
            node=node.master
        # Outer first, so the innermost area's tag ends up nearest the widget.
        for area in reversed(areas):
            tags=widget.bindtags()
            if area._wheel_tag not in tags:widget.bindtags((tags[0],area._wheel_tag,*tags[1:]))
    window.bind('<Map>',mapped,add='+')


from .tooltip import Tooltip


class Dropdown(ttk.Button):
    """Keyboard-operable, selected-mark popup for readonly choices."""
    def __init__(self,master,values,variable=None,command=None,**kwargs):
        self.values=tuple(values)
        self.variable=variable or tk.StringVar(value=self.values[0] if self.values else '')
        self.on_change=command
        self.popup=None;self._return_focus=None
        kwargs.setdefault('style','Dropdown.TButton')
        super().__init__(master,text=self.variable.get(),command=self.open,**kwargs)
        self._trace=self.variable.trace_add('write',self.sync)
        self.bind('<Down>',lambda _:self.open())
        self.bind('<Return>',lambda _:self.open())
        self.bind('<Destroy>',self.cleanup,add='+')
    def sync(self,*_):
        self.configure(text=self.variable.get())
        if self.popup is not None and not self.popup._closed:self.dismiss()
    def state(self,statespec=None):
        result=super().state(statespec)
        if statespec is not None and self.instate(['disabled']):self.dismiss()
        return result
    def configure(self,cnf=None,**kwargs):
        result=super().configure(cnf,**kwargs)
        if ('state' in kwargs or isinstance(cnf,dict) and 'state' in cnf) and self.instate(['disabled']):self.dismiss()
        return result
    config=configure
    def open(self):
        if self.instate(['disabled']) or not self.values:return 'break'
        if self.popup is not None and not self.popup._closed:return self.dismiss()
        from .popups import choice_popup
        self.cursor=self.values.index(self.variable.get()) if self.variable.get() in self.values else 0
        self.popup=choice_popup(self,self.values,self.cursor,lambda index:self.choose(self.values[index]))
        self.rows=self.popup.rows
        return 'break'
    def move(self,delta):
        if self.popup is not None and not self.popup._closed:
            self.popup.move(delta);self.cursor=self.popup.cursor
        return 'break'
    def choose(self,value):
        if self.instate(['disabled']):return
        self.variable.set(value);self.dismiss()
        if self.on_change:self.on_change(value)
        self.event_generate('<<DropdownSelected>>')
    def outside(self,event):
        if self.popup is not None and not self.popup._closed:self.popup.outside(event)
    def dismiss(self):
        if self.popup is not None:
            popup=self.popup;self.popup=None;popup.close()
        return 'break'
    def cleanup(self,event):
        if event.widget is self:
            self.dismiss();self.variable.trace_remove('write',self._trace)


class Accordion(Card):
    def __init__(self,master,title,*,opened=False,**kwargs):
        super().__init__(master,**kwargs)
        self.opened=opened
        self.header=ttk.Button(self,text='  '+title,compound='left',style='Popup.Quiet.TButton',command=self.toggle)
        self.header.pack(fill='x')
        self.viewport=ttk.Frame(self,height=1);self.viewport.pack_propagate(False)
        self.body=ttk.Frame(self.viewport,padding=(12,10));self.body.place(x=0,y=0,relwidth=1)
        from .expansion import Expansion
        self.expansion=Expansion(self,self.header,lambda:self.viewport.pack(fill='x'),self.viewport.pack_forget)
        self.animator=self.expansion.animator
    def toggle(self):return self.expansion.toggle()
    def sync(self):self.expansion.sync()
    def arrow(self):
        self.expansion.arrow()


class SettingRow(Card):
    def __init__(self,master,title,description='',*,help_text=None,command=None):
        super().__init__(master,padding=(18,14),command=command)
        self.columnconfigure(0,weight=1)
        text=ttk.Frame(self);text.grid(row=0,column=0,sticky='w')
        header=ttk.Frame(text);header.pack(anchor='w')
        ttk.Label(header,text=title).pack(side='left')
        if help_text:
            helper=ttk.Label(header,image=icon(self,'info',16))
            helper.pack(side='left',padx=8);Tooltip(helper,help_text)
        if description:ttk.Label(text,text=description,style='Muted.TLabel').pack(anchor='w',pady=(5,0))
        self.action=ttk.Frame(self);self.action.grid(row=0,column=1,sticky='e',padx=(20,0))


class BarChart(ttk.Frame):
    """Small native Canvas chart with persistent items and no plotting library."""
    def __init__(self,master,values,labels,**kwargs):
        super().__init__(master,**kwargs)
        from .motion import Tween
        self._value_tween=Tween(self);self._visual_values=[]
        self.scale=self.winfo_fpixels('1i')/96
        self.px=lambda n:round(n*self.scale)
        self.canvas=tk.Canvas(self,height=self.px(220),background=PALETTE['bg'],highlightthickness=0)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.configure(takefocus=True)
        self._key_index=-1
        self.canvas.bind('<Left>',lambda e:self.navigate(-1))
        self.canvas.bind('<Right>',lambda e:self.navigate(1))
        self.values=list(values);self.labels=list(labels)
        self.bars=[];self.captions=[];self._job=None
        self.lines=[self.canvas.create_line(0,0,0,0,fill=PALETTE['border']) for _ in range(3)]
        self.ticks=[self.canvas.create_text(0,0,anchor='e',fill=PALETTE['muted'],font='TkDefaultFont') for _ in range(3)]
        self.hint=tk.StringVar(value='将鼠标移到柱形上查看数值')
        ttk.Label(self,textvariable=self.hint,style='Muted.TLabel').pack(anchor='w')
        self.canvas.bind('<Configure>',self.schedule)
        self.bind('<Destroy>',self.cleanup,add='+')
        self.set_data(values,labels)
    def set_data(self,values,labels):
        self.values=list(values);self.labels=list(labels)
        previous=self._visual_values[:];target=self.values[:];self._value_tween.cancel()
        if len(previous)==len(target) and len(target)<=200:
            def paint(amount):
                self._visual_values=[a+(b-a)*amount for a,b in zip(previous,target)];self.schedule()
            self._value_tween.start(0.,1.,paint,duration=200)
        else:self._visual_values=target
        while len(self.bars)<len(values):
            index=len(self.bars)
            bar=self.canvas.create_rectangle(0,0,0,0,fill=PALETTE['accent'],outline='')
            self.canvas.tag_bind(bar,'<Enter>',lambda event,i=index:self.hint.set(f'{self.labels[i]} · {self.values[i]} 篇'))
            self.canvas.tag_bind(bar,'<Leave>',lambda event:self.hint.set('将鼠标移到柱形上查看数值'))
            self.bars.append(bar)
            self.captions.append(self.canvas.create_text(0,0,fill=PALETTE['muted'],font='TkDefaultFont'))
        for i in range(len(self.bars)):
            self.canvas.itemconfigure(self.bars[i],state='normal' if i<len(values) else 'hidden')
            self.canvas.itemconfigure(self.captions[i],state='normal' if i<len(values) else 'hidden')
        self.schedule()
    def _tkwin11_dpi_changed(self,dpi):
        self.scale=dpi/96;self.canvas.configure(height=self.px(220));self.schedule()
    def navigate(self,delta):
        if self.values and not self.instate(['disabled']):
            self._key_index=(self._key_index+delta)%len(self.values)
            i=self._key_index;self.hint.set(f'{self.labels[i]} · {self.values[i]}')
        return 'break'
    def schedule(self,event=None):
        if self._job is None:self._job=self.after_idle(self.draw)
    def draw(self):
        self._job=None
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height()
        left,right,top,bottom=self.px(48),w-self.px(12),self.px(14),h-self.px(34)
        maximum=max(1,max(self.values,default=1))
        for i in range(3):
            y=bottom-(bottom-top)*i/2
            self.canvas.coords(self.lines[i],left,y,right,y)
            self.canvas.coords(self.ticks[i],left-self.px(10),y)
            self.canvas.itemconfigure(self.ticks[i],text=f'{maximum*i/2:g}')
        step=(right-left)/max(1,len(self.values))
        for i,value in enumerate(self._visual_values):
            x=left+step*(i+.5);barwidth=max(3,step*.48)
            self.canvas.coords(self.bars[i],x-barwidth/2,bottom-(bottom-top)*value/maximum,x+barwidth/2,bottom)
            self.canvas.coords(self.captions[i],x,bottom+self.px(20))
            self.canvas.itemconfigure(self.captions[i],text=self.labels[i])
    def cleanup(self,event):
        if event.widget is self and self._job is not None:self.after_cancel(self._job)


class CardList(ttk.Frame):
    """Retained Canvas card list: one native window instead of many per row.

    Rows: id/name/detail/tag/mark/color. Callback: (id, action, screen_x, screen_y).
    Up/Down changes focused row; Left/Right chooses action; Enter invokes it.
    """
    def __init__(self,master,on_action,**kwargs):
        super().__init__(master,**kwargs)
        from .motion import Tween
        self._row_tween=Tween(self);self._row_positions={}
        self.on_action=on_action;self.rows=[];self.items=[];self.active_id=None
        self.row_index=0;self.action_index=0;self.job=None
        self._hover_target=None;self._painted_hover=None
        self.scale=self.winfo_fpixels('1i')/96
        self._title_font=tkfont.Font(root=self,family='Microsoft YaHei UI',size=10,weight='bold')
        self._body_font=tkfont.Font(root=self,family='Microsoft YaHei UI',size=10)
        self._measure_title=self._title_font;self._measure_body=self._body_font
        self.px=lambda n:max(1,round(n*self.scale))
        self.row_height=self.px(68);self.gap=self.px(8)
        self.canvas=tk.Canvas(self,background=PALETTE['bg'],highlightthickness=0,takefocus=True)
        from .scrolling import SmoothScroll
        self._smooth=SmoothScroll(self.canvas)
        self.bar=ttk.Scrollbar(self,command=self._smooth.direct)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side='left',fill='both',expand=True)
        self.canvas.bind('<Configure>',self.schedule)
        self.canvas.bind('<MouseWheel>',self.wheel)
        self.canvas.bind('<Button-1>',self.click)
        self.canvas.bind('<Motion>',self.hover)
        self.canvas.bind('<Leave>',self.clear_hover)
        self.canvas.bind('<Up>',lambda _:self.navigate(-1,0))
        self.canvas.bind('<Down>',lambda _:self.navigate(1,0))
        self.canvas.bind('<Left>',lambda _:self.navigate(0,-1))
        self.canvas.bind('<Right>',lambda _:self.navigate(0,1))
        self.canvas.bind('<Home>',lambda e:self.navigate(-len(self.rows),0))
        self.canvas.bind('<End>',lambda e:self.navigate(len(self.rows),0))
        self.canvas.bind('<Prior>',lambda e:self.navigate(-max(1,self.canvas.winfo_height()//(self.row_height+self.gap)),0))
        self.canvas.bind('<Next>',lambda e:self.navigate(max(1,self.canvas.winfo_height()//(self.row_height+self.gap)),0))
        self.canvas.bind('<Return>',self.invoke)
        self.canvas.bind('<space>',self.invoke)
        self.canvas.bind('<Shift-F10>',lambda _:self.invoke(action='menu'))
        self.canvas.bind('<FocusIn>',self.schedule)
        self.canvas.bind('<FocusOut>',self.schedule)
        self.bind('<Destroy>',self.cleanup,add='+')
        self.canvas._dpi_native_images=True
        self._build_faces()
        self.hover_box=self.canvas.create_image(0,0,state='hidden')
    def _build_faces(self):
        self._previous_faces=getattr(self,'faces',None)
        if hasattr(self,'hover_box'):
            self.canvas.itemconfigure(self.hover_box,image='',state='hidden')
            self._hover_target=None;self._painted_hover=None
        self.faces={}
        for selected in (False,True):
            fill=PALETTE['selected'] if selected else PALETTE['bg']
            edge=PALETTE['blue'] if selected else PALETTE['border']
            source=_patch(self._root(),fill,edge,PALETTE['bg'],self.px(5))
            size=self.px(5)+1;w,h=source.width(),source.height();corners=[]
            for x,y in ((0,0),(w-size,0),(0,h-size),(w-size,h-size)):
                image=tk.PhotoImage(master=self,width=size,height=size)
                self.tk.call(image,'copy',source,'-from',x,y,x+size,y+size,'-to',0,0);corners.append(image)
            logo=_patch(self._root(),PALETTE['bg'],PALETTE['border'],fill,self.px(4),height=self.px(34),width=self.px(34))
            actions=[icon(self,symbol,self.px(16),background=fill) for symbol in ('play','edit','more')]
            hover_face=_patch(self._root(),PALETTE['hover'],PALETTE['hover'],fill,self.px(3),height=self.px(30),width=self.px(30))
            hover_actions=[icon(self,symbol,self.px(16),background=PALETTE['hover']) for symbol in ('play','edit','more')]
            self.faces[selected]=(fill,edge,corners,logo,actions,hover_face,hover_actions)
    def _tkwin11_dpi_changed(self,dpi):
        old_scale=self.scale;logical_height=self.row_height/old_scale
        self.scale=dpi/96;self.row_height=self.px(logical_height);self.gap=self.px(8)
        resources=getattr(self._root(),'_dpi_resources',None)
        if resources:
            self._measure_title=tkfont.Font(root=self,name=resources.font(str(self._title_font),dpi),exists=True)
            self._measure_body=tkfont.Font(root=self,name=resources.font(str(self._body_font),dpi),exists=True)
        self._build_faces();self._finish_rows()
    def set_rows(self,rows,active_id,*,addresses=True,density='标准'):
        previous_positions=dict(self._row_positions)
        self._row_tween.cancel()
        focused_id=self.rows[self.row_index]['id'] if self.rows and self.row_index<len(self.rows) else None
        self._smooth.tween.cancel()
        self.rows=list(rows)
        self.row_index=next((i for i,row in enumerate(self.rows) if row['id']==focused_id),0)
        self.active_id=active_id;self.addresses=addresses
        self._hover_target=None;self._painted_hover=None;self.canvas.itemconfigure(self.hover_box,state='hidden')
        self.row_height=self.px({'紧凑':60,'标准':68,'宽松':80}[density])
        for group in self.items:
            for value in group.values():
                for item in value if isinstance(value,list) else [value]:self.canvas.delete(item)
        self.items=[]
        for index,row in enumerate(self.rows):
            tags=(f'row:{index}',)
            group={'fills':[self.canvas.create_rectangle(0,0,0,0,width=0,tags=tags) for _ in range(2)],
                   'edges':[self.canvas.create_line(0,0,0,0,tags=tags) for _ in range(4)],
                   'corners':[self.canvas.create_image(0,0,anchor='nw',tags=tags) for _ in range(4)],
                   'logo':self.canvas.create_image(0,0,anchor='nw',tags=tags)}
            for key,font,foreground,anchor in [('mark',('Microsoft YaHei UI',12,'bold'),row['color'],'center'),
                ('name',('Microsoft YaHei UI',10,'bold'),PALETTE['fg'],'w'),
                ('tag',('Microsoft YaHei UI',9),PALETTE['muted'],'w'),
                ('detail',('Microsoft YaHei UI',10),PALETTE['muted'],'w'),
                ('status',('Microsoft YaHei UI',10),PALETTE['blue'],'e')]:
                group[key]=self.canvas.create_text(0,0,text=row.get(key,''),font=font,fill=foreground,anchor=anchor,tags=tags)
            group['hits']=[self.canvas.create_rectangle(0,0,0,0,outline='',tags=(*tags,'action:'+action)) for action in ('use','edit','menu')]
            group['actions']=[self.canvas.create_image(0,0,tags=(*tags,'action:'+action)) for action in ('use','edit','menu')]
            self.items.append(group)
        self.row_index=min(self.row_index,max(0,len(self.rows)-1))
        targets={row['id']:i*(self.row_height+self.gap) for i,row in enumerate(self.rows)}
        if previous_positions and len(self.rows)<=200 and len(targets)==len(self.rows):
            starts={key:previous_positions.get(key,target+self.px(8)) for key,target in targets.items()}
            def paint(amount):
                self._row_positions={key:starts[key]+(target-starts[key])*amount for key,target in targets.items()}
                self.schedule()
            self._row_tween.start(0.,1.,paint,duration=167)
        else:self._row_positions=targets if len(targets)==len(self.rows) else {};self.schedule()
    def _finish_rows(self):
        self._row_tween.cancel()
        positions={row['id']:i*(self.row_height+self.gap) for i,row in enumerate(self.rows)}
        self._row_positions=positions if len(positions)==len(self.rows) else {}
        self.schedule()
    def set_active(self,key):self.active_id=key;self.schedule()
    @staticmethod
    def elide(text,font,width):
        if font.measure(text)<=width:return text
        low,high=0,len(text)
        while low<high:
            middle=(low+high+1)//2
            if font.measure(text[:middle]+'…')<=width:low=middle
            else:high=middle-1
        return text[:low]+'…'
    def schedule(self,event=None):
        if self.job is None:self.job=self.after_idle(self.draw)
    def draw(self):
        self.job=None
        w=max(50,self.canvas.winfo_width()-2);h=self.row_height;r=self.px(5)+1
        for i,(row,group) in enumerate(zip(self.rows,self.items)):
            y=self._row_positions.get(row['id'],i*(h+self.gap))
            selected=row['id']==self.active_id
            fill,edge,corners,logo,actions,_,_=self.faces[selected]
            for item,coords in zip(group['fills'],((r,y,w-r,y+h),(0,y+r,w,y+h-r))):
                self.canvas.coords(item,*coords);self.canvas.itemconfigure(item,fill=fill)
            for item,coords in zip(group['edges'],((r,y,w-r,y),(r,y+h-1,w-r,y+h-1),(0,y+r,0,y+h-r),(w-1,y+r,w-1,y+h-r))):
                self.canvas.coords(item,*coords);self.canvas.itemconfigure(item,fill=edge)
            for item,point,image in zip(group['corners'],((0,y),(w-r,y),(0,y+h-r),(w-r,y+h-r)),corners):
                self.canvas.coords(item,*point);self.canvas.itemconfigure(item,image=image)
            lx=self.px(16);ly=y+(h-self.px(34))/2
            self.canvas.coords(group['logo'],lx,ly);self.canvas.itemconfigure(group['logo'],image=logo)
            self.canvas.coords(group['mark'],lx+self.px(17),y+h/2)
            tx=self.px(66)
            self.canvas.coords(group['name'],tx,y+h/2-self.px(10))
            self.canvas.itemconfigure(group['name'],text=self.elide(row['name'],self._measure_title,max(80,w-tx-self.px(280))))
            bbox=self.canvas.bbox(group['name']);self.canvas.coords(group['tag'],bbox[2]+self.px(10),y+h/2-self.px(10))
            self.canvas.itemconfigure(group['detail'],text=self.elide(row['detail'],self._measure_body,max(80,w-tx-self.px(24))) if self.addresses else '')
            self.canvas.coords(group['detail'],tx,y+h/2+self.px(14))
            self.canvas.coords(group['status'],w-self.px(152),y+h/2)
            self.canvas.itemconfigure(group['status'],text='● 使用中' if selected else '')
            for j,(item,image) in enumerate(zip(group['actions'],actions)):
                action_x=w-self.px(110-j*40);action_y=y+h/2;pad=self.px(15)
                self.canvas.coords(group['hits'][j],action_x-pad,action_y-pad,action_x+pad,action_y+pad)
                self.canvas.itemconfigure(group['hits'][j],fill=fill)
                self.canvas.coords(item,action_x,action_y);self.canvas.itemconfigure(item,image=image)
        for i,group in enumerate(self.items):
            for key,normal in [('name',PALETTE['fg']),('detail',PALETTE['muted']),('tag',PALETTE['muted']),('status',PALETTE['blue']),('mark',self.rows[i]['color'])]:
                self.canvas.itemconfigure(group[key],fill=PALETTE['disabled'] if self.instate(['disabled']) else normal)
        total=max(1,len(self.rows)*(h+self.gap)-self.gap)
        self.canvas.configure(scrollregion=(0,0,w,total))
        needs=total>self.canvas.winfo_height()
        if needs and not self.bar.winfo_manager():self.bar.pack(side='right',fill='y')
        elif not needs and self.bar.winfo_manager():self.bar.pack_forget()
        self.paint_hover()
    def target(self,event):
        items=self.canvas.find_withtag('current')
        tags=self.canvas.gettags(items[-1]) if items else ()
        row=next((int(t.split(':')[1]) for t in tags if t.startswith('row:')),None)
        action=next((t.split(':')[1] for t in tags if t.startswith('action:')),None)
        return row,action
    def click(self,event):
        if self.instate(['disabled']):return 'break'
        row,action=self.target(event)
        if row is None:return
        self.canvas.focus_set();self.row_index=row
        self.action_index=('use','edit','menu').index(action or 'use')
        self.schedule();self.on_action(self.rows[row]['id'],action or 'use',event.x_root,event.y_root)
    def hover(self,event):
        if self.instate(['disabled']):return
        row,action=self.target(event);self.canvas.configure(cursor='hand2' if row is not None else '')
        target=(row,('use','edit','menu').index(action)) if row is not None and action else None
        if target!=self._hover_target:self._hover_target=target;self.paint_hover()
    def clear_hover(self,event=None):
        self.canvas.configure(cursor='');self._hover_target=None;self.paint_hover()
    def paint_hover(self):
        previous=self._painted_hover
        if previous is not None and previous[0]<len(self.items):
            i,j=previous;selected=self.rows[i]['id']==self.active_id
            self.canvas.itemconfigure(self.items[i]['actions'][j],image=self.faces[selected][4][j])
        self._painted_hover=self._hover_target
        if self._hover_target is None:
            self.canvas.itemconfigure(self.hover_box,state='hidden');return
        i,j=self._hover_target
        if i>=len(self.items):self.clear_hover();return
        selected=self.rows[i]['id']==self.active_id
        fill,edge,corners,logo,actions,hover_face,hover_actions=self.faces[selected]
        w=max(50,self.canvas.winfo_width()-2)
        x=w-self.px(110-j*40);y=self._row_positions.get(self.rows[i]['id'],i*(self.row_height+self.gap))+self.row_height/2
        self.canvas.coords(self.hover_box,x,y)
        self.canvas.itemconfigure(self.hover_box,image=hover_face,state='normal',tags=(f'row:{i}','action:'+('use','edit','menu')[j]))
        self.canvas.tag_raise(self.hover_box)
        item=self.items[i]['actions'][j]
        self.canvas.itemconfigure(item,image=hover_actions[j]);self.canvas.tag_raise(item)
    def navigate(self,rows,actions):
        self._finish_rows()
        self._smooth.tween.cancel()
        if self.instate(['disabled']) or not self.rows:return 'break'
        self.row_index=max(0,min(len(self.rows)-1,self.row_index+rows));self.action_index=(self.action_index+actions)%3
        self._hover_target=(self.row_index,self.action_index)
        top=self.row_index*(self.row_height+self.gap);total=max(1,len(self.rows)*(self.row_height+self.gap))
        visible_top=self.canvas.canvasy(0);visible_bottom=visible_top+self.canvas.winfo_height()
        if top<visible_top:self.canvas.yview_moveto(top/total)
        elif top+self.row_height>visible_bottom:self.canvas.yview_moveto((top+self.row_height-self.canvas.winfo_height())/total)
        self.schedule();return 'break'
    def invoke(self,event=None,action=None):
        if not self.instate(['disabled']) and self.rows:self.on_action(self.rows[self.row_index]['id'],action or ('use','edit','menu')[self.action_index],
                                   self.canvas.winfo_rootx()+self.canvas.winfo_width()-100,self.canvas.winfo_rooty()+80)
        return 'break'
    def state(self,statespec=None):
        result=super().state(statespec)
        if statespec is not None:
            self.canvas.configure(takefocus=not self.instate(['disabled']))
            self.clear_hover();self.schedule()
        return result
    def wheel(self,event):
        if self._smooth.wheel(event.delta):return 'break'
    def cleanup(self,event):
        if event.widget is self and self.job is not None:self.after_cancel(self.job)


def _round_thumb(root,size):
    """Generate a tiny RGBA PNG with stdlib only; Tk handles the alpha channel."""
    import struct,zlib
    raw=bytearray();center=size/2;radius=size*.36
    for y in range(size):
        raw.append(0)
        for x in range(size):
            d=math.hypot(x+.5-center,y+.5-center)
            shadow_d=math.hypot(x+.5-center,y-.5-center)
            shadow=max(0,min(.13,(radius+2-shadow_d)*.065))
            coverage=max(0,min(1,radius+.5-d))
            color=(255,255,255) if d<radius-1 else (218,222,230)
            alpha=coverage+shadow*(1-coverage)
            raw.extend([round(channel*coverage/alpha) if alpha else 0 for channel in color])
            raw.append(round(alpha*255))
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',size,size,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(bytes(raw)))+chunk(b'IEND',b'')
    return tk.PhotoImage(master=root,data=png,format='png')


class _ProgressSliderBase(tk.Canvas):
    """Tick-free rounded progress selector. Mouse, arrows, Home and End work."""
    def __init__(self,master,*,variable=None,from_=0,to=100,step=1,command=None,**kwargs):
        from_,to,step=map(float,(from_,to,step))
        if not all(math.isfinite(v) for v in (from_,to,step)) or step<0:raise ValueError('范围和步长必须有限，步长不能为负数')
        self.scale=master.winfo_fpixels('1i')/96
        self.px=lambda n:max(1,round(n*self.scale))
        super().__init__(master,height=self.px(34),background=PALETTE['bg'],highlightthickness=0,
                         takefocus=True,cursor='hand2',**kwargs)
        self.variable=variable if variable is not None else tk.DoubleVar(master=self,value=from_)
        self.from_,self.to,self.step=from_,to,step
        self._last_notified=self.get()
        self.command=command
        self._previous_grab=None
        self._previous_grab_global=False
        self._drag_offset=0.
        self.thumb_image=_round_thumb(self._root(),self.px(28))
        self.track=self.create_line(0,0,0,0,width=self.px(7),capstyle='butt',fill=PALETTE['hover'])
        self.fill=self.create_line(0,0,0,0,width=self.px(7),capstyle='butt',fill=PALETTE['accent'])
        self.thumb=self.create_image(0,0,image=self.thumb_image)
        self._trace=self.variable.trace_add('write',self.changed)
        self.bind('<Configure>',self.changed)
        self.bind('<Button-1>',self.press)
        self.bind('<B1-Motion>',self.motion)
        self.bind('<ButtonRelease-1>',self.release)
        self.bind('<Left>',lambda _:self.nudge(-1));self.bind('<Right>',lambda _:self.nudge(1))
        self.bind('<Down>',lambda _:self.nudge(-1));self.bind('<Up>',lambda _:self.nudge(1))
        self.bind('<Home>',lambda _:self.set(self.from_));self.bind('<End>',lambda _:self.set(self.to))
        self.bind('<Destroy>',self.cleanup,add='+')
        self.changed()
    def get(self):return float(self.variable.get())
    def set(self,value):
        if not math.isfinite(float(value)):raise ValueError('滑块数值必须有限')
        value=max(min(self.from_,self.to),min(max(self.from_,self.to),float(value)))
        if self.step and value not in (self.from_,self.to):value=self.from_+round((value-self.from_)/self.step)*self.step
        value=max(min(self.from_,self.to),min(max(self.from_,self.to),value))
        self.variable.set(int(value) if isinstance(self.variable,tk.IntVar) and float(value).is_integer() else value)
        return 'break'
    def changed(self,*_):
        left=self.px(14);right=max(left,self.winfo_width()-left);y=self.winfo_height()/2
        fraction=max(0,min(1,(self.get()-self.from_)/(self.to-self.from_ or 1)))
        x=left+(right-left)*fraction
        self.coords(self.track,left,y,right,y);self.coords(self.fill,left,y,x,y)
        self.itemconfigure(self.fill,state='hidden' if fraction==0 else 'normal')
        self.coords(self.thumb,x,y)
        value=self.get()
        if value!=self._last_notified:
            self._last_notified=value
            if self.command:self.command(value)
    def at(self,x):
        margin=self.track_margin();length=max(1,self.winfo_width()-2*margin)
        self.set(self.from_+(x-self._drag_offset-margin)/length*(self.to-self.from_))
    def track_margin(self):
        resources=getattr(self._root(),'_dpi_resources',None)
        ratio=resources.window_dpi(self.winfo_toplevel())/resources.base_dpi if resources else 1
        return self.px(14)*ratio
    def press(self,event):
        if str(self.cget('state'))=='disabled':return
        if self.grab_current() is not self:
            self._previous_grab=self.grab_current()
            self._previous_grab_global=self._previous_grab is not None and self._previous_grab.grab_status()=='global'
        center=self.coords(self.thumb)[0]
        self._drag_offset=event.x-center if abs(event.x-center)<=self.track_margin() else 0.
        self.focus_set();self.grab_set_global();self.at(event.x)
    def motion(self,event):
        if str(self.cget('state'))!='disabled':self.at(event.x)
    def release(self,event):
        if self.grab_current()==self:
            self.grab_release()
            previous=self._previous_grab
            if previous is not None and previous.winfo_exists():
                previous.grab_set_global() if self._previous_grab_global else previous.grab_set()
        self._previous_grab=None
        self._drag_offset=0.
    def nudge(self,direction):
        step=self.step or abs(self.to-self.from_)/100
        if str(self.cget('state'))!='disabled':self.set(self.get()+direction*step*(1 if self.to>=self.from_ else -1))
        return 'break'
    def cleanup(self,event):
        if event.widget is self:
            self.variable.trace_remove('write',self._trace)
            try:_ProgressSliderBase.release(self,None)
            except tk.TclError:pass


from .slider import ProgressSlider


class Modal(tk.Toplevel):
    """Rounded, dimmed application modal with fixed header/footer and scroll body."""
    def __init__(self,master,title,*,width=560,height=460):
        self.host=master.winfo_toplevel()
        existing=getattr(self.host,'_active_modal',None)
        if existing is not None and not existing._closed:existing.close()
        self.previous_focus=self.host.focus_get()
        self.previous_grab=self.host.grab_current()
        self.previous_grab_status=self.previous_grab.grab_status() if self.previous_grab else None
        self.resources=getattr(self.host._root(),'_dpi_resources',None)
        self.scale=(self.resources.window_dpi(self.host) if self.resources else self.host.winfo_fpixels('1i'))/96
        self.target_width=round(width*self.scale);self.target_height=round(height*self.scale)
        self.mask=tk.Toplevel(self.host)
        self.mask.withdraw();self.mask.overrideredirect(True);self.mask.configure(background='#000000')
        self.mask.attributes('-alpha',0.0);self.mask.transient(self.host)
        super().__init__(self.host)
        self.withdraw();self.overrideredirect(True);self.transient(self.host)
        self.title(title);self.configure(background=PALETTE['bg'])
        from .motion import prepare_popup
        prepare_popup(self);self.attributes('-alpha',0.0)
        self._dismissing=False
        self._closed=False;self._position_job=None;self._round_job=None;self._region_size=None
        self._ready_job=None;self._ready_attempts=0;self._shown=False
        self._host_topmost=bool(self.host.attributes('-topmost'))
        self.shell=ttk.Frame(self,padding=round(22*self.scale));self.shell.pack(fill='both',expand=True)
        header=ttk.Frame(self.shell);header.pack(fill='x',pady=(0,round(18*self.scale)))
        self.title_var=tk.StringVar(value=title)
        ttk.Label(header,textvariable=self.title_var,font=('Microsoft YaHei UI',12,'bold')).pack(side='left')
        close=ttk.Button(header,image=icon(self,'close',round(15*self.scale)),style='Quiet.TButton',command=self.dismiss)
        close.pack(side='right');Tooltip(close,'关闭')
        self.actions=ttk.Frame(self.shell);self.actions.pack(side='bottom',fill='x',pady=(round(18*self.scale),0))
        self.primary=ttk.Button(self.actions,text='知道了',style='Primary.TButton',command=self.close)
        self.primary.bind('<Return>',self.confirm)
        self.primary.pack(side='right')
        self.secondary=ttk.Button(self.actions,text='取消',command=self.close)
        self.secondary.bind('<Return>',self.confirm)
        self.area=ScrollArea(self.shell);self.area.pack(fill='both',expand=True)
        self.body=self.area.body
        self.bind('<Escape>',lambda _:self.dismiss());self.bind('<Alt-F4>',lambda _:self.dismiss())
        self.bind('<Return>',self.confirm)
        self.bind('<Tab>',lambda e:self.traverse(1));self.bind('<Shift-Tab>',lambda e:self.traverse(-1))
        self.bind('<Button-1>',self.outside,add='+')
        self.mask.bind('<Button-1>',lambda _:self.lift())
        self._host_binding=self.host.bind('<Configure>',self.schedule_position,add='+')
        self._escape_binding=self.host.bind('<Escape>',lambda _:self.dismiss(),add='+')
        self._show_job=self.after_idle(self.show)
        self.host._active_modal=self
        from .widgets import install_input_focus
        install_input_focus(self)
    def show(self):
        self._show_job=None
        if self._closed:return
        if self._shown:
            from .motion import reveal_popup
            reveal_popup(self)
            self._mask_fade.start(float(self.mask.attributes('-alpha')),.30,lambda v:self.mask.attributes('-alpha',v),duration=83,linear=True)
            self.grab_set();return
        try:
            if self.resources:self.resources.update(self,round(self.scale*96))
            self.position()
            # Map and raise both windows before taking a grab. A hidden
            # overrideredirect window must never lock its owner.
            self.mask.attributes('-topmost',True);self.attributes('-topmost',True)
            self.mask.deiconify();self.deiconify();self.lift()
            self.update_idletasks()
            self.focus_force()
            getattr(self,'initial_focus',self.primary).focus_force()
            self._ready_job=self.after(20,self.finish_show)
        except tk.TclError:
            self.close()
            raise
    def finish_show(self):
        self._ready_job=None
        if self._closed:return
        if not self.winfo_viewable() or self.winfo_width()<20 or self.winfo_height()<20:
            self._ready_attempts+=1
            if self._ready_attempts<10:self._ready_job=self.after(20,self.finish_show)
            else:self.close()
            return
        try:
            if self.secondary.winfo_manager()=='pack':self.secondary.pack_configure(side='right',before=self.primary)
            if not hasattr(self,'initial_focus'):
                content=[w for w in self.focusables() if str(w).startswith(str(self.body)+'.')]
                self.initial_focus=content[0] if content else self.primary
            self.position();self.update_idletasks();self.round_corners()
            from .motion import reveal_popup,Tween
            reveal_popup(self)
            self._mask_fade=Tween(self.mask)
            self._mask_fade.start(0.,.30,lambda v:self.mask.attributes('-alpha',v),duration=167)
            self.lift();getattr(self,'initial_focus',self.primary).focus_force()
            self.grab_set();self._shown=True
            if not self._host_topmost:
                self.mask.attributes('-topmost',False)
                self.attributes('-topmost',False)
                self.lift();getattr(self,'initial_focus',self.primary).focus_force()
        except tk.TclError:
            self.close()
            raise
    def title(self,string=None):
        if string is not None and hasattr(self,'title_var'):self.title_var.set(string)
        return super().title(string)
    def confirm(self,event=None):
        if self._dismissing:return 'break'
        focus=self.focus_get()
        if isinstance(focus,tk.Text):return
        if isinstance(focus,ttk.Button) and focus is not self.primary:
            if not focus.instate(['disabled']):focus.invoke()
        elif not self.primary.instate(['disabled']):self.primary.invoke()
        return 'break'
    def outside(self,event):
        # ContentDialog is modal: a click on the dimmed owner never cancels it.
        return
    def focusables(self):
        items=[]
        def walk(parent):
            for child in parent.winfo_children():
                if isinstance(child,tk.Toplevel):continue
                if child.winfo_viewable() and self.tk.getboolean(self.tk.call('tk::FocusOK',str(child))):items.append(child)
                walk(child)
        walk(self.shell)
        return items
    def traverse(self,direction):
        items=self.focusables();focus=self.focus_get()
        if items:
            i=(items.index(focus)+direction)%len(items) if focus in items else 0
            items[i].focus_set()
        return 'break'
    def dismiss(self):
        from .motion import dismiss_popup
        return dismiss_popup(self)
    def schedule_position(self,event):
        if event.widget is self.host and self._position_job is None:
            self._position_job=self.after_idle(self.position)
    def position(self):
        self._position_job=None
        if self._closed:return
        x,y=self.host.winfo_rootx(),self.host.winfo_rooty()
        w,h=self.host.winfo_width(),self.host.winfo_height()
        self.mask.geometry(f'{w}x{h}+{x}+{y}')
        width=min(self.target_width,max(1,w-round(32*self.scale)));height=min(self.target_height,max(1,h-round(32*self.scale)))
        self.geometry(f'{width}x{height}+{x+(w-width)//2}+{y+(h-height)//2}')
        self.round_corners()
    def round_corners(self):
        self._round_job=None
        if self._closed or sys.platform!='win32':return
        if not self.winfo_ismapped():return
        import ctypes
        from ctypes import wintypes as w
        user=ctypes.WinDLL('user32',use_last_error=True);gdi=ctypes.WinDLL('gdi32',use_last_error=True)
        user.GetParent.argtypes=[w.HWND];user.GetParent.restype=w.HWND
        hwnd=user.GetParent(self.winfo_id())
        if not hwnd:return
        size=(self.winfo_width(),self.winfo_height())
        if size==self._region_size:return
        self._region_size=size
        gdi.CreateRoundRectRgn.argtypes=[ctypes.c_int]*6;gdi.CreateRoundRectRgn.restype=w.HANDLE
        user.SetWindowRgn.argtypes=[w.HWND,w.HANDLE,w.BOOL]
        gdi.DeleteObject.argtypes=[w.HANDLE]
        radius=round(2*getattr(self,'corner_radius',6)*self.scale)
        region=gdi.CreateRoundRectRgn(0,0,size[0]+1,size[1]+1,radius,radius)
        if not user.SetWindowRgn(hwnd,region,True):gdi.DeleteObject(region)
    def close(self):
        if self._closed:return 'break'
        self._closed=True
        if getattr(self.host,'_active_modal',None) is self:self.host._active_modal=None
        for job in (self._show_job,self._position_job,self._round_job,self._ready_job):
            if job is not None:self.after_cancel(job)
        unbind(self.host,'<Configure>',self._host_binding)
        unbind(self.host,'<Escape>',self._escape_binding)
        for name in ('_choice_popup','_form_flyout','_active_modal'):
            child=getattr(self,name,None)
            if child is not None and not child._closed:child.close()
        self.grab_release();self.mask.destroy()
        super().destroy()
        if self.previous_grab is not None and self.previous_grab.winfo_exists():
            if self.previous_grab_status=='global':self.previous_grab.grab_set_global()
            else:self.previous_grab.grab_set()
        if self.previous_focus is not None and self.previous_focus.winfo_exists() and self.previous_focus.winfo_viewable():
            self.previous_focus.focus_force()
        elif self.host.winfo_exists():self.host.focus_force()
        return 'break'
    def destroy(self):self.close()
