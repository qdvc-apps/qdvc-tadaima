#!/usr/bin/env bash
# Build (and with --install, install) QDVC Tadaima for this computer: the
# macOS edition on a Mac, the GTK 4 edition anywhere else.
#
#   scripts/build-app.sh             # build the edition for this OS
#   scripts/build-app.sh --install   # ...and install it
#
# Every option is passed through to that edition's own script:
#   qdvc-tadaima-mac/scripts/build-app.sh   (--install, --universal)
#   qdvc-tadaima-gtk4/scripts/build-app.sh  (--install, --uninstall, --no-tests, --run)
# Run either directly to build the other edition's way, or for its --help.
set -euo pipefail

cd "$(dirname "$0")/.."
case "$(uname -s)" in
    Darwin) edition=qdvc-tadaima-mac ;;
    *) edition=qdvc-tadaima-gtk4 ;;
esac
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    echo
    echo "On this computer: $edition"
    exit 0
fi
echo "==> Building $edition"
exec "$edition/scripts/build-app.sh" "$@"
