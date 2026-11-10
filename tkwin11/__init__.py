"""TkWin11: Windows 11 Fluent-style theme and widgets for standard tkinter/ttk.

Standard library only. Existing programs need one line before the first Tk():

    import tkwin11; tkwin11.install()

New programs can call enable_dpi_awareness() before Tk(), then
apply_theme(root) before creating widgets:

    import tkinter as tk
    from tkwin11 import enable_dpi_awareness, apply_theme

    enable_dpi_awareness()
    root = tk.Tk()
    apply_theme(root)

Every component can also be imported from its own module, e.g.
``from tkwin11.charts import LineChart``.
"""
__version__ = '0.2.3'

from .menus import bind_context_menu
from .scrolling import AutoScrollbar
from .inputs import set_readonly
from .placeholder import set_placeholder, PlaceholderEntry
from .core import (PALETTE, THEME, enable_dpi_awareness, icon, apply_theme, Card, SegmentedControl,
                   Sidebar, ScrollArea, Tooltip, Dropdown, Accordion, SettingRow, BarChart, CardList,
                   ProgressSlider, Modal)
from .widgets import (RoundedText, SectionCard, ResponsiveTable, configure_table, auto_hide_text_scrollbar,
                      focus_when_mapped, initial_window_bounds, align_tree_scrollbar)
from .popups import ChoicePopup, ThemedCombobox, ThemedMenu
from .extras import SquareSwitch, ToggleButton, ToolButton, MenuButton, OptionMenu, RoundedListbox
from .table import SortableTreeview, enable_sorting, Spreadsheet
from .charts import LineChart, ColumnChart, ScatterChart, DonutChart, PieChart
from .forms import (Badge, ValidatedEntry, ResultDialog, MessageDialog, InputDialog, Dialogs, DateEntry,
                    CalendarPopup, ValueSlider)
from .feedback import ToastNotification, notify, TextProgress, Meter
from .dataview import DataTable
from .media import ColorPicker, ColorDialog, FontPicker, FontDialog, ImageViewer
from .window_chrome import WindowChrome
from .slider import FluentSlider
from .button import FluentButton
from .titlebar import FluentTitleBar
from .fluent import (configure_fluent, FluentPage, SettingsCard, NavigationView,
                     FluentWindowChrome, FluentSwitch, ExpanderCard, KeyChord, FormFlyout)
from .classic import install, uninstall, theme_classic

__all__ = [
    'set_readonly', 'set_placeholder', 'PlaceholderEntry', 'bind_context_menu',
    'AutoScrollbar',
    'PALETTE', 'THEME', 'enable_dpi_awareness', 'icon', 'apply_theme',
    'Card', 'SegmentedControl', 'Sidebar', 'ScrollArea', 'Tooltip', 'Dropdown', 'Accordion', 'SettingRow',
    'BarChart', 'CardList', 'ProgressSlider', 'Modal',
    'RoundedText', 'SectionCard', 'ResponsiveTable', 'configure_table', 'auto_hide_text_scrollbar',
    'focus_when_mapped', 'initial_window_bounds', 'align_tree_scrollbar',
    'ChoicePopup', 'ThemedCombobox', 'ThemedMenu',
    'SquareSwitch', 'ToggleButton', 'ToolButton', 'MenuButton', 'OptionMenu', 'RoundedListbox',
    'SortableTreeview', 'enable_sorting', 'Spreadsheet',
    'LineChart', 'ColumnChart', 'ScatterChart', 'DonutChart', 'PieChart',
    'Badge', 'ValidatedEntry', 'ResultDialog', 'MessageDialog', 'InputDialog', 'Dialogs', 'DateEntry',
    'CalendarPopup', 'ValueSlider',
    'ToastNotification', 'notify', 'TextProgress', 'Meter',
    'DataTable',
    'ColorPicker', 'ColorDialog', 'FontPicker', 'FontDialog', 'ImageViewer',
    'WindowChrome',
    'FluentSlider',
    'FluentButton',
    'FluentTitleBar',
    'configure_fluent', 'FluentPage', 'SettingsCard', 'NavigationView',
    'FluentWindowChrome', 'FluentSwitch', 'ExpanderCard', 'KeyChord', 'FormFlyout',
    'install', 'uninstall', 'theme_classic',
]
