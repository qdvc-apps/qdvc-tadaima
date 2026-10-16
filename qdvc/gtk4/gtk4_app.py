"""Adw.Application subclass; prgname + icon; accelerator registration."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

import os  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .. import APP_ID, FALLBACK_ICON_NAME, ICON_NAME  # noqa: E402
from ..config import Config  # noqa: E402
from ..ui_prefs import SHORTCUTS  # noqa: E402
from .gtk4_window import MainWindow  # noqa: E402

# Load-bearing: WM_CLASS matches the .desktop StartupWMClass.
GLib.set_prgname("qdvc-tadaima")

# data/icons is a theme root laid out like hicolor, next to the qdvc package
# both in a checkout and in the copy scripts/build-app.sh installs.
ICONS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "icons")


def resolve_icon_name() -> str:
    """The app icon if the icon theme (or the bundled data/icons) has it, else
    a stock themed icon."""
    display = Gdk.Display.get_default()
    if display is not None:
        theme = Gtk.IconTheme.get_for_display(display)
        if os.path.isdir(ICONS_DIR):
            theme.add_search_path(os.path.abspath(ICONS_DIR))
        if theme.has_icon(ICON_NAME):
            return ICON_NAME
    return FALLBACK_ICON_NAME


class TadaimaApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.config = Config()
        self.icon_name = FALLBACK_ICON_NAME
        self._window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        self.icon_name = resolve_icon_name()
        Gtk.Window.set_default_icon_name(self.icon_name)
        self.apply_color_scheme(self.config.color_scheme)
        # app.quit: scripts/build-app.sh calls it over D-Bus to close a running
        # copy before replacing the installed files. Closing the window (rather
        # than quitting outright) saves the window and sidebar state first.
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_a: self._quit())
        self.add_action(quit_action)
        # Register accelerators from the shared SHORTCUTS table. Arrow-key photo
        # navigation is handled by a key controller on the full-view page only
        # (registering it globally would hijack arrow keys in the sidebar and
        # grid), so it is documented in the table but not bound app-wide.
        _full_view_only = {"win.prev-photo", "win.next-photo"}
        # Extra accelerators for actions where one label doesn't cover every
        # keyboard layout (e.g. "+" usually needs Shift; numpad variants).
        _extra_accels = {
            "win.zoom-in": ["<Primary>plus", "<Primary>equal", "<Primary>KP_Add"],
            "win.zoom-out": ["<Primary>minus", "<Primary>KP_Subtract"],
            "win.zoom-reset": ["<Primary>0", "<Primary>KP_0"],
            # "?" is Shift+"/" on most layouts, so accept several spellings.
            "win.shortcuts": [
                "<Primary>question",
                "<Primary><Shift>question",
                "<Primary>slash",
                "<Primary><Shift>slash",
            ],
        }
        for sc in SHORTCUTS:
            if sc.action in _full_view_only:
                continue
            self.set_accels_for_action(
                sc.action, _extra_accels.get(sc.action, [sc.accel])
            )

    def apply_color_scheme(self, scheme: str) -> None:
        """Map the config value onto Adw.StyleManager. Dark is the default."""
        mgr = self.get_style_manager()
        mapping = {
            "dark": Adw.ColorScheme.FORCE_DARK,
            "light": Adw.ColorScheme.FORCE_LIGHT,
            "auto": Adw.ColorScheme.DEFAULT,
        }
        mgr.set_color_scheme(mapping.get(scheme, Adw.ColorScheme.FORCE_DARK))

    def _quit(self) -> None:
        if self._window is not None:
            self._window.close()  # do_close_request saves state, then quits
        else:
            self.quit()

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MainWindow(application=self, config=self.config)
        self._window.present()


def main(argv: list[str]) -> int:
    app = TadaimaApplication()
    return app.run(argv)
