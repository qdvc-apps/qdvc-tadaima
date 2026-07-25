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
        # Register accelerators from the shared SHORTCUTS table.
        for sc in SHORTCUTS:
            self.set_accels_for_action(sc.action, [sc.accel])

    def do_activate(self) -> None:
        if self._window is None:
            self._window = MainWindow(application=self, config=self.config)
        self._window.present()


def main(argv: list[str]) -> int:
    app = TadaimaApplication()
    return app.run(argv)
