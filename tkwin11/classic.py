"""Win11 colours for classic tk.* widgets and a one-line upgrade for old programs.

ttk widgets, classic Entry/Text/Spinbox/Listbox and tk.Menu popups are already
handled by apply_theme(). This module covers the remaining classic widgets
(Button, Label, Frame, Checkbutton, Radiobutton, Scale, LabelFrame ...).
"""
import tkinter as tk
from .core import PALETTE, THEME, apply_theme, enable_dpi_awareness


_RESOURCE={name.lower():name for name in (
    'activeBackground','activeForeground','disabledForeground','highlightThickness','highlightBackground',
    'highlightColor','borderWidth','selectColor','troughColor','sliderRelief','sashRelief','overRelief','padX','padY')}


def _classic_options(scale):
    c=PALETTE;px=lambda n:max(1,round(n*scale))
    plain={'background':c['bg'],'foreground':c['fg']}
    choice={**plain,'activebackground':c['bg'],'activeforeground':c['fg'],'selectcolor':'#FFFFFF',
            'highlightthickness':0,'disabledforeground':c['disabled']}
    return {
        'Frame':{'background':c['bg']},
        'Toplevel':{'background':c['bg']},
        'Label':{**plain,'disabledforeground':c['disabled']},
        'Message':plain,
        'Button':{**plain,'activebackground':c['hover'],'activeforeground':c['fg'],'disabledforeground':c['disabled'],
                  # Tk's Windows button never paints an unfocused highlight
                  # ring; a 2px groove derived from the white face reads as a
                  # light Win11 outline. The ring still shows keyboard focus.
                  'relief':'groove','borderwidth':2,'highlightthickness':1,
                  'highlightbackground':c['bg'],'highlightcolor':c['accent'],'padx':px(12),'pady':px(4)},
        'Menubutton':{**plain,'activebackground':c['hover'],'activeforeground':c['fg'],'relief':'groove','borderwidth':2,
                      'highlightthickness':1,'highlightbackground':c['bg'],'padx':px(10),'pady':px(4)},
        'Checkbutton':choice,
        'Radiobutton':choice,
        'Labelframe':{'background':c['bg'],'foreground':c['muted']},
        'Scale':{**plain,'troughcolor':'#D6DAE0','activebackground':c['accent'],'highlightthickness':0,
                 'borderwidth':1,'sliderrelief':'raised'},
        'Canvas':{'background':c['bg'],'highlightthickness':0},
        'Panedwindow':{'background':c['border'],'sashrelief':'flat'},
    }


def theme_classic(root):
    """Restyle classic widgets. New widgets use the option database; existing
    widgets change only options still at their Tk default, so colours a program
    set explicitly (bg='red') are kept."""
    root=root._root()
    resources=getattr(root,'_dpi_resources',None)
    scale=(resources.base_dpi if resources else root.winfo_fpixels('1i'))/96
    table=_classic_options(scale)
    for cls,options in table.items():
        # The option database uses resource names (highlightBackground), not
        # the lowercase configure() spellings.
        for key,value in options.items():root.option_add(f'*{cls}.{_RESOURCE.get(key,key)}',value,'widgetDefault')
    def walk(widget):
        for child in widget.winfo_children():
            options=table.get(child.winfo_class())
            if options is not None and not hasattr(child,'_tkwin11_classic'):
                child._tkwin11_classic=True
                changed={}
                for key,value in options.items():
                    try:spec=child.configure(key)
                    except tk.TclError:continue
                    # (name, dbName, dbClass, default, current)
                    if spec is not None and len(spec)==5 and str(spec[3])==str(spec[4]):changed[key]=value
                if changed:
                    try:child.configure(**changed)
                    except tk.TclError:pass
            walk(child)
    walk(root)


def install(*,dpi=True,classic=True,lock_theme=True,window_flicker_workaround=True,**theme):
    """One-line upgrade for an existing tkinter program::

        import tkwin11; tkwin11.install()   # before the first tk.Tk()

    Every Tk() created afterwards (including subclasses and the implicit
    default root) gets the Win11 theme. ``lock_theme`` ignores later
    ``ttk.Style().theme_use('clam')`` style calls so the program keeps the
    Win11 look; style.configure() customisations still apply on top.
    Pass ``dpi=False`` for programs that hard-code pixel geometry and rely on
    Windows bitmap scaling.
    Experimental WindowChrome transition workarounds are enabled for future
    roots and their Toplevels by default; pass ``window_flicker_workaround=False``
    to disable them. This does not add a custom title bar to ordinary Tk windows
    or change roots already created.
    """
    if dpi:enable_dpi_awareness()
    settings=dict(classic=classic,lock_theme=lock_theme,
                  window_flicker_workaround=bool(window_flicker_workaround),theme=theme)
    current=getattr(tk.Tk,'_tkwin11_install',None)
    if current is not None:
        current.update(settings);return
    original=tk.Tk.__init__
    def __init__(self,*args,**kwargs):
        original(self,*args,**kwargs)
        if not getattr(self,'_tkloaded',False):return
        options=tk.Tk._tkwin11_install
        self._tkwin11_window_flicker_workaround=options['window_flicker_workaround']
        apply_theme(self,**options['theme'])
        if options['classic']:theme_classic(self)
        if options['lock_theme']:self._dpi_resources.locked_theme=THEME
    __init__.__wrapped__=original
    tk.Tk.__init__=__init__
    tk.Tk._tkwin11_install=settings


def uninstall():
    """Undo install() for Tk roots created afterwards."""
    hooked=tk.Tk.__init__
    original=getattr(hooked,'__wrapped__',None)
    if original is not None:tk.Tk.__init__=original
    if hasattr(tk.Tk,'_tkwin11_install'):del tk.Tk._tkwin11_install
