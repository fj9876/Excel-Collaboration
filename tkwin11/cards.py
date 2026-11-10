"""Shared card interaction; passive cards stay outside the keyboard tab order."""
import tkinter as tk
from tkinter import ttk
from .dpi import additive_styles
from .tcl_bridge import on_map
from .motion import Tween


class CardInteraction:
    def __init__(self,card,command):
        self.card=card;self.command=command;self.hover=False;self.pressed=False
        self.saved={};self.bound=set();self.tween=Tween(card);self.fill=None
        card.configure(takefocus=bool(command))
        card.bind('<FocusIn>',self.refresh,add='+');card.bind('<FocusOut>',self.cancel_press,add='+')
        card.bind('<Return>',self.keyboard);card.bind('<space>',self.keyboard)
        card.bind('<KeyRelease-space>',self.key_release)
        self.job=card.after_idle(self.attach)
        card.bind('<Destroy>',self.destroy,add='+')
        root=card._root()
        if not getattr(root,'_dynamic_cards_installed',False):
            root._dynamic_cards_installed=True
            on_map(root,_mapped_card)

    def attach(self):
        self.job=None
        card=self.card
        def walk(widget):
            if widget is not card and hasattr(widget,'interaction'):return
            if widget is not card and isinstance(widget,ttk.Frame):
                style=str(widget.cget('style'))
                if style not in ('','TFrame') and not style.startswith('CardFill'):return
            if widget in (getattr(card,'actions',None),getattr(card,'action',None)):return
            if isinstance(widget,tk.Canvas) and widget is not card._surface:return
            if widget is not card and not isinstance(widget,(ttk.Frame,ttk.Label,tk.Canvas)):return
            if widget not in self.bound:
                self.bound.add(widget)
                widget.bind('<Enter>',lambda e:self.pointer(True),add='+')
                widget.bind('<Leave>',lambda e:self.pointer(False),add='+')
                widget.bind('<ButtonPress-1>',self.press,add='+')
                widget.bind('<ButtonRelease-1>',self.release,add='+')
            for child in widget.winfo_children():walk(child)
        known=len(self.bound)
        walk(card)
        # Repaint only when new children joined (or on first attach).
        self.refresh(force=self.fill is None or len(self.bound)!=known)

    def cancel_press(self,event=None):
        self.pressed=False;self.refresh()
    def pointer(self,inside):
        self.hover=inside
        if not inside:self.pressed=False
        self.refresh()
    def press(self,event):
        if not self.command or self.card.instate(['disabled']):return
        self.card._root()._button_keyboard=False
        self.pressed=True;self.card.focus_set();self.refresh()
    def release(self,event):
        pressed=self.pressed;self.pressed=False;self.refresh()
        c=self.card
        if pressed and c.winfo_rootx()<=event.x_root<c.winfo_rootx()+c.winfo_width() and c.winfo_rooty()<=event.y_root<c.winfo_rooty()+c.winfo_height():self.invoke()
    def keyboard(self,event):
        if event.widget is not self.card:return
        self.card._root()._button_keyboard=True
        if event.keysym=='space':
            self.pressed=True;self.refresh()
        else:self.refresh();self.invoke()
        return 'break'
    def key_release(self,event):
        if event.widget is self.card and self.pressed:
            self.pressed=False;self.refresh();self.invoke()
        return 'break'
    def invoke(self):
        if self.command and not self.card.instate(['disabled']):return self.command()

    def state_changed(self):
        disabled=self.card.instate(['disabled'])
        from .states import descendants_disabled
        descendants_disabled(self.card,disabled,self.saved)
        if disabled:self.pressed=False
        self.refresh()

    def refresh(self,event=None,force=False):
        from .core import PALETTE
        from .motion import blend
        c=self.card;disabled=c.instate(['disabled'])
        fill=PALETTE['selected'] if c._selected else PALETTE['bg']
        if self.command and not disabled:
            if self.pressed:fill=PALETTE['active']
            elif self.hover:fill=PALETTE['hover']
        focus=bool(self.command and not disabled and c.focus_get() is c and getattr(c._root(),'_button_keyboard',False))
        focus_changed=focus!=getattr(c,'_feedback_focus',False)
        c._feedback_focus=focus
        if not force and self.fill is not None and fill==getattr(self,'target',None):
            # Passive cards receive Enter/Leave from every child; nothing to do.
            if focus_changed:c._recolor()
            return
        self.target=fill
        before=self.fill or fill
        def paint(amount):
            # Quarter steps bound the colours (corner images, frame styles) to a
            # small reusable set instead of one new ttk style per frame.
            color=fill if amount>=1 else blend(before,fill,round(amount*4)/4)
            if color==self.fill and not force:return
            self.fill=color;c._feedback_fill=color;c._recolor()
            root=c._root();declared=getattr(root,'_card_fill_styles',None)
            if declared is None:declared=root._card_fill_styles=set()
            name='CardFill'+color[1:]+'.TFrame'
            for widget in self.bound:
                if widget is c or not widget.winfo_exists():continue
                if isinstance(widget,ttk.Label):widget.configure(background=color)
                elif isinstance(widget,ttk.Frame):
                    if name not in declared:
                        declared.add(name)
                        with additive_styles(c):ttk.Style(c).configure(name,background=color)
                    widget.configure(style=name)
        if before==fill or force:self.tween.cancel();paint(1)
        else:self.tween.start(0.,1.,paint,duration=83)

    def destroy(self,event):
        if event.widget is self.card and self.job is not None:
            self.card.after_cancel(self.job);self.job=None


def _mapped_card(widget):
    widget=getattr(widget,'master',None)
    while widget is not None:
        controller=getattr(widget,'interaction',None)
        if controller is not None and controller.job is None:
            controller.job=widget.after_idle(controller.attach)
        widget=getattr(widget,'master',None)
