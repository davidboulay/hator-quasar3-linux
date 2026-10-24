#!/usr/bin/env bash
# Build a .deb for quasar3. Needs only dpkg-deb (no root). Output: dist/*.deb
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(grep -m1 '^version' "$HERE/pyproject.toml" | sed -E 's/.*"(.*)".*/\1/')"
PKG="quasar3"
STAGE="$HERE/dist/${PKG}_${VERSION}_all"

rm -rf "$STAGE"
mkdir -p "$STAGE"

# --- Python package -> dist-packages ---
DEST="$STAGE/usr/lib/python3/dist-packages"
mkdir -p "$DEST"
cp -r "$HERE/src/quasar3" "$DEST/"
find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} +

# --- launchers ---
mkdir -p "$STAGE/usr/bin"
cat > "$STAGE/usr/bin/quasar3" <<'EOF'
#!/usr/bin/python3
import sys
from quasar3.cli import main
sys.exit(main())
EOF
cat > "$STAGE/usr/bin/quasar3-gui" <<'EOF'
#!/usr/bin/python3
import sys
from quasar3.app.main import main
sys.exit(main())
EOF
chmod 755 "$STAGE/usr/bin/quasar3" "$STAGE/usr/bin/quasar3-gui"

# --- udev rule (system path) ---
mkdir -p "$STAGE/usr/lib/udev/rules.d"
cp "$HERE/udev/99-hator-quasar3.rules" "$STAGE/usr/lib/udev/rules.d/"

# --- desktop entry + icon ---
mkdir -p "$STAGE/usr/share/applications" "$STAGE/usr/share/icons/hicolor/scalable/apps"
cp "$HERE/packaging/com.hator.Quasar3.desktop" "$STAGE/usr/share/applications/"
cp "$HERE/packaging/com.hator.Quasar3.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/"

# --- docs ---
mkdir -p "$STAGE/usr/share/doc/$PKG"
cp "$HERE/README.md" "$HERE/docs/PROTOCOL.md" "$STAGE/usr/share/doc/$PKG/" 2>/dev/null || true

# --- control + maintainer scripts ---
mkdir -p "$STAGE/DEBIAN"
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1
Maintainer: David Boulay <89959743+davidboulay@users.noreply.github.com>
Description: HATOR Quasar 3 / Quasar 3 Ultra 8K mouse configuration app
 Linux configurator (CLI + GTK4/libadwaita GUI) for the HATOR Quasar 3 wireless
 mouse family (Compx 379a:0cf0, 3307, 3308): DPI stages, polling rate, per-stage RGB, main backlight,
 debounce, motion sync, lift-off distance, sleep timer and battery readout.
EOF

cat > "$STAGE/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --subsystem-match=hidraw 2>/dev/null || true
gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
update-desktop-database -q 2>/dev/null || true
EOF
chmod 755 "$STAGE/DEBIAN/postinst"

cat > "$STAGE/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
udevadm control --reload-rules 2>/dev/null || true
EOF
chmod 755 "$STAGE/DEBIAN/postrm"

dpkg-deb --build --root-owner-group "$STAGE"
echo "built: ${STAGE}.deb"
