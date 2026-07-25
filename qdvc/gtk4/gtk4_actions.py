"""Installs the win.* Gio.SimpleActions for the main window.

One action drives every surface (menu items, header buttons, accelerators);
``set_action_enabled`` greys every bound item at once.
"""

from __future__ import annotations

from gi.repository import Gio


def install_actions(window) -> None:
    """Attach win.* actions to *window* (a MainWindow)."""
    specs = [
        ("add-folder", window.on_add_folder),
        ("manage-folders", window.on_manage_folders),
        ("regenerate-cache", window.on_regenerate_cache),
        ("cache-status", window.on_cache_status),
        ("preferences", window.on_preferences),
        ("back", window.on_back),
        ("prev-photo", window.on_prev_photo),
        ("next-photo", window.on_next_photo),
        ("about", window.on_about),
        ("shortcuts", window.on_shortcuts),
        ("quit", window.on_quit),
    ]
    for name, handler in specs:
        action = Gio.SimpleAction.new(name, None)
        action.connect("activate", lambda a, p, h=handler: h())
        window.add_action(action)
        window._actions[name] = action


def set_enabled(window, name: str, enabled: bool) -> None:
    action = window._actions.get(name)
    if action is not None:
        action.set_enabled(enabled)
