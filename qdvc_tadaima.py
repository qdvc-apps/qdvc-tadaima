#!/usr/bin/env python3
"""QDVC Tadaima — thin entry point / backend dispatcher.

Selects the UI toolkit *before importing any GTK*. Backend order:

1. explicit CLI flag ``--gtk4`` (``--gtk3`` is accepted but not implemented);
2. the ``ui_backend`` key in config;
3. default ``gtk4``.

Only the GTK 4 front-end exists in Tadaima (deviation from the shared spec, per
the app brief), so ``--gtk3`` prints a note and falls through to GTK 4.
"""

from __future__ import annotations

import sys


def _select_backend(argv: list[str]) -> tuple[str, list[str]]:
    """Return (backend, remaining_argv) with the flag consumed."""
    remaining = [argv[0]]
    backend: str | None = None
    for arg in argv[1:]:
        if arg == "--gtk4":
            backend = "gtk4"
        elif arg == "--gtk3":
            backend = "gtk3"
        else:
            remaining.append(arg)

    if backend is None:
        try:
            from qdvc.config import Config

            backend = Config().ui_backend
        except Exception:
            backend = "gtk4"

    return backend, remaining


def main() -> int:
    backend, argv = _select_backend(sys.argv)

    if backend == "gtk3":
        print(
            "qdvc-tadaima: GTK 3 front-end is not implemented; using GTK 4.",
            file=sys.stderr,
        )
        backend = "gtk4"

    try:
        from qdvc.gtk4.gtk4_app import main as gtk4_main
    except Exception as exc:  # libadwaita / GTK4 absent
        print(f"qdvc-tadaima: failed to load GTK 4 front-end: {exc}", file=sys.stderr)
        return 1

    return gtk4_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
