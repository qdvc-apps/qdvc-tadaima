"""Keyboard-shortcuts window, built from the shared ui_prefs.SHORTCUTS table."""

from __future__ import annotations

from gi.repository import Gtk

from ..ui_prefs import SHORTCUTS


def build_shortcuts_window(parent) -> Gtk.ShortcutsWindow:
    win = Gtk.ShortcutsWindow()
    win.set_transient_for(parent)
    win.set_modal(True)

    section = Gtk.ShortcutsSection()
    section.props.section_name = "shortcuts"
    section.props.title = "Shortcuts"

    # Group by the shared table's group field.
    groups: dict[str, Gtk.ShortcutsGroup] = {}
    for sc in SHORTCUTS:
        grp = groups.get(sc.group)
        if grp is None:
            grp = Gtk.ShortcutsGroup()
            grp.props.title = sc.group
            groups[sc.group] = grp
            section.add_group(grp)
        item = Gtk.ShortcutsShortcut()
        item.props.title = sc.label
        item.props.accelerator = sc.accel
        grp.add_shortcut(item)

    win.add_section(section)
    return win
