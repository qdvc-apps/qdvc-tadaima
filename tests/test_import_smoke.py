"""Import smoke test: stub `gi` so every qdvc.gtk4.* module imports.

A permissive fake `gi` returns a catch-all object for any attribute, exercising
class bodies and top-level code without a real GTK runtime.
"""

import importlib
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class _Anything:
    """Catch-all: any attribute access / call returns another _Anything.

    Subclassable so `class Foo(Gtk.Window)` works.
    """

    def __init__(self, *a, **k):
        pass

    def __getattr__(self, name):
        return _Anything()

    def __call__(self, *a, **k):
        return _Anything()

    # Allow use as a base class metaclass-wise.
    def __mro_entries__(self, bases):
        return (object,)


class _Module(types.ModuleType):
    def __getattr__(self, name):
        return _Anything()


def _install_fake_gi():
    gi = types.ModuleType("gi")
    gi.require_version = lambda *a, **k: None

    repository = types.ModuleType("gi.repository")
    for name in ("Gtk", "Gdk", "Adw", "Gio", "GLib", "Pango", "GObject", "GdkPixbuf"):
        setattr(repository, name, _Anything())

    gi.repository = repository
    sys.modules["gi"] = gi
    sys.modules["gi.repository"] = repository
    for name in ("Gtk", "Gdk", "Adw", "Gio", "GLib", "Pango", "GObject", "GdkPixbuf"):
        sys.modules[f"gi.repository.{name}"] = getattr(repository, name)


def main():
    _install_fake_gi()
    mods = [
        "qdvc.gtk4.gtk4_items",
        "qdvc.gtk4.gtk4_actions",
        "qdvc.gtk4.gtk4_shortcuts",
        "qdvc.gtk4.gtk4_preferences",
        "qdvc.gtk4.gtk4_window",
        "qdvc.gtk4.gtk4_app",
    ]
    for m in mods:
        importlib.import_module(m)
        print(f"imported {m}")
    print("ALL IMPORT SMOKE TESTS PASSED")


if __name__ == "__main__":
    main()
