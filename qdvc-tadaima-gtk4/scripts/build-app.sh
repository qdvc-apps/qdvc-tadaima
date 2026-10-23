#!/usr/bin/env bash
# Build and install QDVC Tadaima for the current user (no root needed).
#
#   scripts/build-app.sh               # check dependencies, run the tests, stage build/
#   scripts/build-app.sh --install     # ...and install to ~/.local (restarts a running copy)
#   scripts/build-app.sh --uninstall   # remove the installed copy
#   scripts/build-app.sh --no-tests    # skip the tests
#   scripts/build-app.sh --run         # after building/installing, start the app
#
# Installs:
#   ~/.local/share/qdvc-tadaima/app/                   the program (qdvc_tadaima.py, qdvc/, data/)
#   ~/.local/bin/qdvc-tadaima                          launcher script
#   ~/.local/share/applications/qdvc.Tadaima.desktop   menu entry (Activities, Dock)
#   ~/.local/share/icons/hicolor/.../qdvc.Tadaima*.svg app icons
# Set PREFIX to install elsewhere (default: ~/.local).
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
APP_ID="qdvc.Tadaima"
PREFIX="${PREFIX:-$HOME/.local}"
SHARE="$PREFIX/share"
APP_DIR="$SHARE/qdvc-tadaima/app"
BIN_DIR="$PREFIX/bin"
DESKTOP="$SHARE/applications/$APP_ID.desktop"
# The launcher the README used to have you write by hand; replaced by $DESKTOP.
OLD_DESKTOP="$SHARE/applications/qdvc-tadaima.desktop"
ICONS="$SHARE/icons/hicolor"
STAGE="$ROOT/build/qdvc-tadaima"

install=0 uninstall=0 tests=1 run=0
for arg in "$@"; do
    case "$arg" in
        --install) install=1 ;;
        --uninstall) uninstall=1 ;;
        --no-tests) tests=0 ;;
        --run) run=1 ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg (see --help)" >&2; exit 2 ;;
    esac
done

say() { printf '==> %s\n' "$*"; }

# Is a copy running? Ask it to quit through its D-Bus actions (it saves first).
running() {
    command -v gdbus >/dev/null 2>&1 || return 1
    gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
        --method org.freedesktop.DBus.NameHasOwner "$APP_ID" 2>/dev/null | grep -q true
}
quit_running() {
    gdbus call --session --dest "$APP_ID" --object-path "/${APP_ID//./\/}" \
        --method org.gtk.Actions.Activate quit [] {} >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do running || return 0; sleep 0.1; done
}

refresh_caches() {
    command -v update-desktop-database >/dev/null 2>&1 \
        && update-desktop-database -q "$SHARE/applications" 2>/dev/null || true
    touch "$ICONS" 2>/dev/null || true
    command -v gtk4-update-icon-cache >/dev/null 2>&1 \
        && gtk4-update-icon-cache -q -t -f "$ICONS" 2>/dev/null \
        || { command -v gtk-update-icon-cache >/dev/null 2>&1 \
             && gtk-update-icon-cache -q -t -f "$ICONS" 2>/dev/null; } || true
}

remove_old_launcher() {
    # Only if it is the hand-made Tadaima launcher (it runs qdvc_tadaima.py).
    if [[ -f "$OLD_DESKTOP" ]] && grep -q 'qdvc_tadaima\.py' "$OLD_DESKTOP"; then
        rm -f "$OLD_DESKTOP"
        say "Removed the old hand-made launcher $OLD_DESKTOP"
    fi
}

if [[ $uninstall -eq 1 ]]; then
    running && { say "Quitting the running copy"; quit_running; }
    rm -rf "$SHARE/qdvc-tadaima" "$BIN_DIR/qdvc-tadaima" "$DESKTOP" \
        "$ICONS/scalable/apps/$APP_ID.svg" "$ICONS/symbolic/apps/$APP_ID-symbolic.svg"
    remove_old_launcher
    refresh_caches
    say "Uninstalled (your photos, ~/.config/qdvc-tadaima and ~/.cache/qdvc-tadaima are untouched)"
    exit 0
fi

# --- dependencies ---------------------------------------------------------
say "Checking dependencies"
if ! python3 - <<'PY'
import sys
problems = []
if sys.version_info < (3, 10):
    problems.append("Python 3.10 or later")
try:
    import gi
    gi.require_version("Gtk", "4.0"); gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 4):
        problems.append("libadwaita 1.4 or later (found %d.%d)"
                        % (Adw.get_major_version(), Adw.get_minor_version()))
    print("    GTK %d.%d.%d, libadwaita %d.%d.%d, Python %s" % (
        Gtk.get_major_version(), Gtk.get_minor_version(), Gtk.get_micro_version(),
        Adw.get_major_version(), Adw.get_minor_version(), Adw.get_micro_version(),
        sys.version.split()[0]))
except (ImportError, ValueError) as e:
    problems.append(f"GTK 4 and libadwaita for Python ({e})")
try:
    import yaml  # noqa: F401
except ImportError:
    problems.append("PyYAML")
try:
    import PIL  # noqa: F401  (thumbnails: without them no photo is shown)
except ImportError:
    problems.append("Pillow")
if problems:
    print("Missing: " + "; ".join(problems), file=sys.stderr)
    sys.exit(1)
try:
    import pillow_heif  # noqa: F401
except ImportError:
    print("    (optional) pillow-heif is not installed, so HEIC photos are skipped")
PY
then
    cat >&2 <<'HELP'
Install the dependencies, then run this script again:
  Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 python3-yaml python3-pil
  Fedora:        sudo dnf install python3-gobject gtk4 libadwaita python3-pyyaml python3-pillow
  Arch:          sudo pacman -S python-gobject gtk4 libadwaita python-yaml python-pillow
For HEIC photos also: pip install --user pillow-heif
HELP
    exit 1
fi

# --- tests and build ------------------------------------------------------
if [[ $tests -eq 1 ]]; then
    say "Running the tests"
    for t in tests/test_*.py; do
        if ! out="$(python3 "$t" 2>&1)"; then
            printf '%s\n' "$out" >&2
            echo "$t failed; nothing was installed." >&2
            exit 1
        fi
        printf '    %s\n' "$(printf '%s\n' "$out" | tail -n 1)"
    done
fi

say "Staging $STAGE"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp qdvc_tadaima.py "$STAGE/"
cp -r qdvc data "$STAGE/"
find "$STAGE" -name '__pycache__' -prune -exec rm -rf {} +
python3 -m compileall -q "$STAGE" >/dev/null

if [[ $install -eq 1 ]]; then
    was_running=0
    if running; then
        say "Quitting the running copy (it saves first)"
        quit_running
        was_running=1
    fi
    say "Installing to $PREFIX"
    rm -rf "$APP_DIR"
    mkdir -p "$APP_DIR" "$BIN_DIR" "$SHARE/applications" \
        "$ICONS/scalable/apps" "$ICONS/symbolic/apps"
    cp -r "$STAGE/." "$APP_DIR/"
    cat > "$BIN_DIR/qdvc-tadaima" <<LAUNCHER
#!/bin/sh
exec python3 "$APP_DIR/qdvc_tadaima.py" "\$@"
LAUNCHER
    chmod +x "$BIN_DIR/qdvc-tadaima"
    sed "s|@BINDIR@|$BIN_DIR|g" "data/$APP_ID.desktop.in" > "$DESKTOP"
    cp "data/icons/hicolor/scalable/apps/$APP_ID.svg" "$ICONS/scalable/apps/"
    cp "data/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg" "$ICONS/symbolic/apps/"
    remove_old_launcher
    if command -v desktop-file-validate >/dev/null 2>&1; then
        desktop-file-validate "$DESKTOP" || echo "warning: desktop-file-validate complained" >&2
    fi
    refresh_caches
    say "Installed: open QDVC Tadaima from Activities, or run qdvc-tadaima"
    case ":$PATH:" in *":$BIN_DIR:"*) ;; *) echo "note: $BIN_DIR is not on your PATH" >&2 ;; esac
    if [[ $was_running -eq 1 ]]; then run=1; fi
fi

if [[ $run -eq 1 ]]; then
    if [[ $install -eq 1 ]]; then cmd=("$BIN_DIR/qdvc-tadaima"); else cmd=(python3 "$STAGE/qdvc_tadaima.py"); fi
    say "Starting QDVC Tadaima"
    nohup "${cmd[@]}" >/dev/null 2>&1 &
fi
say "Done"
