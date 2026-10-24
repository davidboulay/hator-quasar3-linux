#!/usr/bin/env bash
# Quick no-.deb install for the current user (runs from the repo).
# Installs the udev rule (needs sudo once), a desktop entry + icon under
# ~/.local, and launcher scripts that run quasar3 straight from this checkout.
set -euo pipefail

HERE="$(cd "$(dirname "$0")/.." && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/scalable/apps"
mkdir -p "$BIN" "$APPS" "$ICONS"

echo "Installing udev rule (sudo)…"
sudo cp "$HERE/udev/99-hator-quasar3.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger --subsystem-match=hidraw || true

cat > "$BIN/quasar3" <<EOF
#!/usr/bin/env bash
exec env PYTHONPATH="$HERE/src" python3 -m quasar3.cli "\$@"
EOF
cat > "$BIN/quasar3-gui" <<EOF
#!/usr/bin/env bash
exec env PYTHONPATH="$HERE/src" python3 -m quasar3.app.main "\$@"
EOF
chmod 755 "$BIN/quasar3" "$BIN/quasar3-gui"

cp "$HERE/packaging/com.hator.Quasar3.svg" "$ICONS/"
sed "s|^Exec=.*|Exec=$BIN/quasar3-gui|" "$HERE/packaging/com.hator.Quasar3.desktop" \
    > "$APPS/com.hator.Quasar3.desktop"
update-desktop-database -q "$APPS" 2>/dev/null || true
gtk-update-icon-cache -q "$HOME/.local/share/icons/hicolor" 2>/dev/null || true

echo "Done. Launchers in $BIN (ensure it's on PATH). Run 'quasar3 show' or launch 'HATOR Quasar 3'."
