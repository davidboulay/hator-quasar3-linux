"""Entry point for the quasar3 GUI."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv if argv is None else argv
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
    except (ImportError, ValueError) as e:
        sys.stderr.write(
            "GTK4 / libadwaita is not available.\n"
            "Install it with:\n"
            "  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1\n"
            f"(detail: {e})\n"
        )
        return 1

    from .window import QuasarApp

    return QuasarApp().run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
