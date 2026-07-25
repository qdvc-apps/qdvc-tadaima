"""Adw.PreferencesWindow — live-apply, no Save/Cancel.

Exposes the Density control (Compact / Relaxed) and the backend selector (spec
parity; only GTK 4 is implemented, so the selector is informational).
"""

from __future__ import annotations

from gi.repository import Adw, Gtk


class PreferencesWindow(Adw.PreferencesWindow):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.config = main_window.config
        self.set_transient_for(main_window)
        self.set_modal(True)
        self.set_title("Preferences")
        self.set_search_enabled(False)

        page = Adw.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("preferences-system-symbolic")

        # --- Appearance / density -------------------------------------
        appearance = Adw.PreferencesGroup()
        appearance.set_title("Sidebar")
        appearance.set_description(
            "Density controls how tightly folders are packed in the sidebar."
        )

        self.density_row = Adw.ComboRow()
        self.density_row.set_title("Density")
        self.density_row.set_subtitle(
            "Compact is tuned for laptops and workstations"
        )
        model = Gtk.StringList()
        model.append("Compact")
        model.append("Relaxed")
        self.density_row.set_model(model)
        self.density_row.set_selected(0 if self.config.density == "compact" else 1)
        self.density_row.connect("notify::selected", self._on_density_changed)
        appearance.add(self.density_row)
        page.add(appearance)

        # --- Backend selector -----------------------------------------
        backend = Adw.PreferencesGroup()
        backend.set_title("Toolkit")
        row = Adw.ComboRow()
        row.set_title("UI backend")
        row.set_subtitle("Takes effect after restart")
        bmodel = Gtk.StringList()
        bmodel.append("GTK 4 / libadwaita")
        row.set_model(bmodel)
        row.set_selected(0)
        row.set_sensitive(False)  # only GTK4 implemented in Tadaima
        backend.add(row)
        page.add(backend)

        self.add(page)

    def _on_density_changed(self, row, _param) -> None:
        value = "compact" if row.get_selected() == 0 else "relaxed"
        self.config.density = value
        self.main._apply_density()  # live-apply
