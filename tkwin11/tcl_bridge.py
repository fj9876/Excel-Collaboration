"""Preserve Tcl script scope and catch semantics across Python command adapters."""
import sys
import tkinter as tk


def install_proxy(interpreter, path, callback, native, *, scoped=(), routed=None):
    """Replace command ``path`` with a proc that forwards to ``callback``.

    ``scoped`` subcommands always run natively in the caller's frame.
    When ``routed`` is given, only those subcommands reach Python; every other
    subcommand is a native tailcall. Hot read-only calls made by Tk's own
    bindings (instate, identify, coords, measure ...) then skip Python entirely.
    """
    dispatch = native + '__bridge'
    def call(*args):
        try:
            return (0, callback(*args), ())
        except tk.TclError as error:
            info = interpreter.call('set', '::errorInfo')
            code = interpreter.call('set', '::errorCode')
            return (1, str(error), ('-code', 1, '-level', 0,
                                   '-errorinfo', info, '-errorcode', code))
    interpreter.createcommand(dispatch, call)
    words = lambda values: '[list '+' '.join(tk._stringify(value) for value in values)+']'
    body = ''
    if routed is not None:
        body = 'if {[lindex $args 0] ni '+words(routed)+'} {tailcall '+tk._stringify(native)+' {*}$args}\n'
    elif scoped:
        body = 'if {[lindex $args 0] in '+words(scoped)+'} {tailcall '+tk._stringify(native)+' {*}$args}\n'
    body += ('lassign ['+tk._stringify(dispatch)+' {*}$args] failed result options\n'
             'if {$failed} {return -options $options $result}\nreturn $result')
    interpreter.call('proc', path, 'args', body)
    return dispatch


def delete_callback(interpreter, dispatch):
    if interpreter.call('info', 'commands', dispatch):
        interpreter.deletecommand(dispatch)


_LAZY_THEME_CHANGED = r'''
namespace eval ::tkwin11 {variable stale; array set stale {}; variable additive 0; variable other 0}
# sweep=1: the ttk::ThemeChanged walk. sweep=0: a stale window was mapped again.
proc ::tkwin11::Deliver {root sweep} {
    variable stale
    set queue [list $root]
    while {[llength $queue]} {
        set next [list]
        foreach w $queue {
            if {![winfo exists $w]} {unset -nocomplain stale($w); continue}
            if {[winfo viewable $w]} {
                if {$sweep || [info exists stale($w)]} {
                    unset -nocomplain stale($w)
                    event generate $w <<ThemeChanged>>
                    if {![winfo exists $w]} continue
                }
            } elseif {$sweep} {
                set stale($w) 1
            }
            lappend next {*}[winfo children $w]
        }
        set queue $next
    }
}
# ttk schedules this after any style write. Writes that only declared new
# names (animation slots, DPI clones) cannot affect an existing widget.
proc ::ttk::ThemeChanged {} {
    set skip [expr {$::tkwin11::additive && !$::tkwin11::other}]
    set ::tkwin11::additive 0
    set ::tkwin11::other 0
    if {!$skip} {::tkwin11::Deliver . 1}
}
bind all <Map> {+if {[info exists ::tkwin11::stale(%W)]} {::tkwin11::Deliver %W 0}}
bind all <Destroy> {+unset -nocomplain ::tkwin11::stale(%W)}
'''


def install_lazy_theme_changed(interpreter):
    """Send ttk's <<ThemeChanged>> only to widgets that are on screen.

    ttk answers every style write (configure, map, layout, element create)
    by sending <<ThemeChanged>> to every widget in the application, and each
    ttk widget then rebuilds its layout and redraws -- cached pages that are
    not displayed included. Hidden windows are marked instead and get the
    event when they are mapped again, before they are drawn. A round whose
    writes were all flagged additive (see ``dpi.additive_styles``) is skipped.
    """
    if not interpreter.call('info', 'procs', '::tkwin11::Deliver'):
        interpreter.eval(_LAZY_THEME_CHANGED)


def on_map(root, callback):
    """Call ``callback(widget)`` whenever any widget is mapped.

    All hooks share one Tcl binding that passes only the window path. A
    ``bind_all('<Map>', ...)`` per hook made tkinter build a full Event (19
    converted fields) for every hook and every widget mapped, a large part of
    building a page. Hooks run in registration order; one failing does not
    stop the others.
    """
    root = root._root()
    hooks = getattr(root, '_tkwin11_map_hooks', None)
    if hooks is None:
        hooks = root._tkwin11_map_hooks = []
        def mapped(path):
            try:
                widget = root.nametowidget(path)
            except KeyError:  # created in Tcl (e.g. a combobox popdown)
                return
            for hook in tuple(hooks):
                try:
                    hook(widget)
                except Exception:
                    root.report_callback_exception(*sys.exc_info())
        root.tk.call('bind', 'all', '<Map>', '+' + root.register(mapped) + ' %W')
    hooks.append(callback)


def unbind(widget, sequence, funcid):
    """Remove one ``bind(..., add='+')`` handler and keep the others.

    Before Python 3.13, ``Misc.unbind(sequence, funcid)`` clears every script
    bound to ``sequence`` on that widget, silently breaking other components.
    """
    if not funcid:
        return
    try:
        script = str(widget.tk.call('bind', widget._w, sequence))
        prefix = 'if {"[' + funcid + ' '
        kept = [line for line in script.split('\n') if line.strip() and not line.startswith(prefix)]
        widget.tk.call('bind', widget._w, sequence, '\n'.join(kept))
    except tk.TclError:
        pass
    try:
        widget.deletecommand(funcid)
    except (tk.TclError, ValueError):
        pass
