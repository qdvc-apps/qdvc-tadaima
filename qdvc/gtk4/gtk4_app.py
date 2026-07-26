"""Adw.Application subclass; prgname + icon; accelerator registration."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, Gtk  # noqa: E402

from .. import APP_ID, ICON_NAME  # noqa: E402
from ..config import Config  # noqa: E402
from ..ui_prefs import SHORTCUTS  # noqa: E402
from .gtk4_window import MainWindow  # noqa: E402

# Load-bearing: WM_CLASS matches the .desktop StartupWMClass.
GLib.set_prgname("qdvc-tadaima")


class TadaimaApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APP_ID)
        self.config = Config()
        self._window: MainWindow | None = None

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(ICON_NAME)
        self.apply_color_scheme(self.config.color_scheme)
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

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MainWindow(application=self, config=self.config)
        self._window.present()


def main(argv: list[str]) -> int:
    app = TadaimaApplication()
    return app.run(argv)
