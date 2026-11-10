"""Snapshot descendant state before composite controls propagate disabling."""
import tkinter as tk
from tkinter import ttk
from .tcl_bridge import on_map


def descendants_disabled(owner, disabled, saved):
    owner._disabled_state_snapshot=saved
    root=owner._root()
    if not getattr(root,'_dynamic_disabled_installed',False):
        root._dynamic_disabled_installed=True
        on_map(root,_inherit_disabled)
    children=[]
    def walk(parent):
        for child in parent.winfo_children():
            if isinstance(child,tk.Toplevel):continue
            children.append(child);walk(child)
    walk(owner)
    # Capture the whole subtree first. A child's state() may recursively disable
    # its own children, so snapshotting during mutation loses their old state.
    for child in list(saved):
        if not child.winfo_exists():saved.pop(child,None)
    if disabled:
        for child in children:
            if child in saved:continue
            if isinstance(child,ttk.Widget):saved[child]=('ttk',child.instate(['disabled']))
            elif isinstance(child,tk.Canvas):saved[child]=('canvas',str(child.cget('takefocus')),str(child.cget('cursor')))
            elif isinstance(child,tk.Widget) and 'state' in child.keys():saved[child]=('classic',str(child.cget('state')))
        for child in children:
            if isinstance(child,ttk.Widget):child.state(['disabled'])
            elif isinstance(child,tk.Canvas):child.configure(takefocus=False,cursor='')
            elif child in saved and saved[child][0]=='classic':child.configure(state='disabled')
    else:
        for child in children:
            original=saved.pop(child,None)
            if original is None:continue
            if original[0]=='ttk':child.state(['disabled'] if original[1] else ['!disabled'])
            elif original[0]=='classic':child.configure(state=original[1])
            else:child.configure(takefocus=original[1],cursor=original[2])


def _inherit_disabled(widget):
    parent=getattr(widget,'master',None);owners=[]
    while parent is not None and not isinstance(parent,tk.Toplevel):
        if hasattr(parent,'_disabled_state_snapshot') and isinstance(parent,ttk.Widget) and parent.instate(['disabled']):owners.append(parent)
        parent=getattr(parent,'master',None)
    if owners:
        outer=owners[-1]
        descendants_disabled(outer,True,outer._disabled_state_snapshot)
