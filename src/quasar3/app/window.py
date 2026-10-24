"""Main GTK4 / libadwaita window for quasar3.

Reads the mouse's real settings on launch (in a worker thread so the UI never
blocks) and writes changes back when you press Apply. Only features confirmed
against hardware are enabled (see ``backend.CAPS``).
"""

from __future__ import annotations

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk  # noqa: E402

from .. import protocol as P  # noqa: E402
from .backend import Backend  # noqa: E402

PENDING = "Awaiting protocol decode"


def rgba_to_rgb(c: Gdk.RGBA) -> tuple[int, int, int]:
    return (round(c.red * 255), round(c.green * 255), round(c.blue * 255))


def rgb_to_rgba(r: int, g: int, b: int) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r / 255, g / 255, b / 255, 1.0
    return c


class QuasarWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app)
        self.set_title("HATOR Quasar 3")
        self.set_default_size(900, 720)
        self.backend = Backend()
        self.config = None  # last-loaded Config

        self.stack = Adw.ViewStack(vexpand=True)
        switcher = Adw.ViewSwitcher(stack=self.stack, policy=Adw.ViewSwitcherPolicy.WIDE)

        header = Adw.HeaderBar(title_widget=switcher)
        self.apply_btn = Gtk.Button(label="Apply")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", self.on_apply)
        header.pack_end(self.apply_btn)
        self.battery_lbl = Gtk.Label(label="", css_classes=["dim-label"])
        header.pack_end(self.battery_lbl)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reload from device")
        refresh.connect("clicked", lambda *_: self.reload())
        header.pack_start(refresh)

        self.banner = Adw.Banner(revealed=False)
        self.banner.connect("button-clicked", lambda *_: self.reload())
        self.toasts = Adw.ToastOverlay()

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.append(self.banner)
        body.append(self.stack)
        self.toasts.set_child(body)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(header)
        toolbar.set_content(self.toasts)
        toolbar.add_bottom_bar(Adw.ViewSwitcherBar(stack=self.stack))
        self.set_content(toolbar)

        self._build_performance()
        self._build_lighting()
        self._build_tuning()
        self._build_buttons()

        self.reload()

    # ------------------------------------------------------------------ pages
    def _page(self, name, title, icon):
        page = Adw.PreferencesPage()
        self.stack.add_titled_with_icon(page, name, title, icon)
        return page

    def _build_performance(self):
        page = self._page("perf", "Performance", "speedometer-symbolic")

        g = Adw.PreferencesGroup(title="Report rate")
        self.poll_combo = Adw.ComboRow(title="Polling rate")
        self.poll_combo.set_model(Gtk.StringList.new([f"{h} Hz" for h in P.POLLING_RATES_HZ]))
        g.add(self.poll_combo)
        page.add(g)

        g = Adw.PreferencesGroup(title="DPI stages",
                                 description="Per-stage sensitivity and indicator colour")
        self.stage_combo = Adw.ComboRow(title="Active stage")
        g.add(self.stage_combo)
        self.dpi_spins: list[Gtk.SpinButton] = []
        self.color_btns: list[Gtk.ColorDialogButton] = []
        self.dpi_rows: list[Adw.ActionRow] = []
        for i in range(8):
            row = Adw.ActionRow(title=f"Stage {i + 1}")
            spin = Gtk.SpinButton(valign=Gtk.Align.CENTER)
            spin.set_adjustment(Gtk.Adjustment(lower=50, upper=32000, step_increment=50, page_increment=400))
            spin.set_climb_rate(50)
            cbtn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(), valign=Gtk.Align.CENTER)
            row.add_suffix(spin)
            row.add_suffix(cbtn)
            self.dpi_spins.append(spin)
            self.color_btns.append(cbtn)
            self.dpi_rows.append(row)
            g.add(row)
        page.add(g)

    def _build_lighting(self):
        page = self._page("light", "Lighting", "applications-graphics-symbolic")
        g = Adw.PreferencesGroup(title="Backlight",
                                 description="Main lighting zone")

        self.light_mode = Adw.ComboRow(title="Effect")
        self.light_mode.set_model(Gtk.StringList.new(list(P.LIGHT_MODES)))
        g.add(self.light_mode)

        color_row = Adw.ActionRow(title="Color")
        self.light_color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(), valign=Gtk.Align.CENTER)
        color_row.add_suffix(self.light_color)
        g.add(color_row)

        self.light_brightness = Adw.SpinRow.new_with_range(0, 9, 1)
        self.light_brightness.set_title("Brightness")
        g.add(self.light_brightness)

        self.light_speed = Adw.SpinRow.new_with_range(0, 9, 1)
        self.light_speed.set_title("Effect speed")
        g.add(self.light_speed)

        self.light_color_row = color_row
        for w in (self.light_mode, color_row, self.light_brightness, self.light_speed):
            w.set_sensitive(self.backend.cap("backlight"))
        self.light_mode.connect("notify::selected", lambda *_: self._sync_light_color_hint())
        page.add(g)

        g = Adw.PreferencesGroup(
            title="DPI indicator",
            description="How the DPI light shows the active stage's colour "
                        "(the per-stage colours are on the Performance page).")
        self.dpi_effect_mode = Adw.ComboRow(title="Effect")
        self.dpi_effect_mode.set_model(Gtk.StringList.new(list(P.DPI_EFFECT_MODES)))
        g.add(self.dpi_effect_mode)
        self.dpi_effect_brightness = Adw.SpinRow.new_with_range(1, 10, 1)
        self.dpi_effect_brightness.set_title("Brightness")
        g.add(self.dpi_effect_brightness)
        self.dpi_effect_speed = Adw.SpinRow.new_with_range(1, 10, 1)
        self.dpi_effect_speed.set_title("Breathing speed")
        g.add(self.dpi_effect_speed)
        page.add(g)

    def _sync_light_color_hint(self):
        mode = self.light_mode.get_selected()
        if mode == 0:
            self.light_color_row.set_subtitle("")
        elif mode in P.LIGHT_MODES_USING_COLOR:
            self.light_color_row.set_subtitle("")
        else:
            self.light_color_row.set_subtitle(
                f"Ignored by “{P.LIGHT_MODES[mode]}” — pick Fixed Color or Single Color Breath "
                "for a single colour")

    def _build_tuning(self):
        page = self._page("tuning", "Tuning", "preferences-system-symbolic")
        g = Adw.PreferencesGroup(title="Switches and sensor")
        self.debounce_spin = Adw.SpinRow.new_with_range(0, 15, 1)
        self.debounce_spin.set_title("Debounce time")
        self.debounce_spin.set_subtitle("ms")
        g.add(self.debounce_spin)

        self.motionsync_row = Adw.SwitchRow(title="Motion Sync")
        g.add(self.motionsync_row)

        self.lod_spin = Adw.SpinRow.new_with_range(0, 2, 1)
        self.lod_spin.set_title("Lift-off distance")
        g.add(self.lod_spin)
        page.add(g)

        g = Adw.PreferencesGroup(title="Power")
        self.sleep_spin = Adw.SpinRow.new_with_range(0, 30, 1)
        self.sleep_spin.set_title("Sleep timer")
        self.sleep_spin.set_subtitle("minutes (0 = never)")
        g.add(self.sleep_spin)
        page.add(g)

    def _build_buttons(self):
        page = self._page("buttons", "Buttons", "input-mouse-symbolic")
        g = Adw.PreferencesGroup(title="Button mapping")
        for name in ("Left", "Right", "Middle", "Back", "Forward"):
            row = Adw.ComboRow(title=name)
            row.set_model(Gtk.StringList.new(["Default", "Left", "Right", "Middle",
                                              "Back", "Forward", "DPI cycle", "Macro…", "Disabled"]))
            row.set_subtitle(PENDING)
            row.set_sensitive(self.backend.cap("buttons"))
            g.add(row)
        page.add(g)

    # --------------------------------------------------------------- threading
    def _bg(self, fn, on_done):
        def worker():
            try:
                res, err = fn(), None
            except Exception as e:  # noqa: BLE001 - surfaced to UI
                res, err = None, e
            GLib.idle_add(on_done, res, err)
        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------- load
    def reload(self):
        self.apply_btn.set_sensitive(False)
        self.banner.set_title("Reading settings from device…")
        self.banner.set_revealed(True)
        self._bg(self.backend.load, self._on_loaded)

    def _on_loaded(self, config, err):
        if config is None:
            if self.backend.present():
                self.banner.set_title("Mouse asleep — move or click it, then press refresh ↻")
            else:
                self.banner.set_title("HATOR Quasar 3 not detected — plug in the dongle or connect the USB cable")
            self.banner.set_button_label("Refresh")
            self.banner.set_revealed(True)
            self.apply_btn.set_sensitive(False)
            self.battery_lbl.set_label("")
            return
        self.config = config
        self.banner.set_revealed(False)

        # polling
        rates = P.POLLING_RATES_HZ
        self.poll_combo.set_selected(rates.index(config.polling_hz) if config.polling_hz in rates else 0)

        # stages
        n = max(config.max_dpi_stage, 1)
        self.stage_combo.set_model(Gtk.StringList.new([f"Stage {i + 1}" for i in range(n)]))
        self.stage_combo.set_selected(min(config.current_dpi_stage, n - 1))
        for i, row in enumerate(self.dpi_rows):
            row.set_visible(i < n)
        for i in range(n):
            self.dpi_spins[i].set_value(config.dpi_values[i])
            self.color_btns[i].set_rgba(rgb_to_rgba(*config.dpi_colors[i]))

        # tuning
        self.debounce_spin.set_value(config.debounce_ms)
        self.motionsync_row.set_active(bool(config.motion_sync))
        self.lod_spin.set_value(config.lod)
        self.sleep_spin.set_value(config.sleep_time)

        # backlight
        bl = config.backlight
        self.light_mode.set_selected(bl["mode"] if bl["mode"] < len(P.LIGHT_MODES) else 0)
        self.light_color.set_rgba(rgb_to_rgba(*bl["rgb"]))
        self.light_brightness.set_value(bl["brightness"])
        self.light_speed.set_value(bl["speed"])
        if not bl["on"]:
            self.light_mode.set_selected(0)
        self._sync_light_color_hint()
        de = config.dpi_effect
        self.dpi_effect_mode.set_selected(
            (de["mode"] if de["on"] and de["mode"] < len(P.DPI_EFFECT_MODES) else 0))
        self.dpi_effect_brightness.set_value(de["brightness"])
        self.dpi_effect_speed.set_value(max(de["speed"], 1))

        self.apply_btn.set_sensitive(True)
        # battery is slow (RF poll) — fetch separately so the UI isn't blocked
        self.battery_lbl.set_label("battery …")
        self._bg(self.backend.battery, self._on_battery)

    def _on_battery(self, batt, err):
        if not batt:
            self.battery_lbl.set_label("")
            return
        icon = "⚡" if batt["charging"] else ""
        self.battery_lbl.set_label(f"{icon}🔋 {batt['level']}%")

    # ------------------------------------------------------------------ apply
    def _collect_changes(self) -> dict:
        c = self.config
        changes: dict = {}
        rates = P.POLLING_RATES_HZ
        hz = rates[self.poll_combo.get_selected()]
        if hz != c.polling_hz:
            changes["polling"] = hz
        stage = self.stage_combo.get_selected()
        if stage != c.current_dpi_stage:
            changes["stage"] = stage
        dpi, color = {}, {}
        for i in range(max(c.max_dpi_stage, 1)):
            v = int(self.dpi_spins[i].get_value())
            if v != c.dpi_values[i]:
                dpi[i] = v
            rgb = rgba_to_rgb(self.color_btns[i].get_rgba())
            if rgb != c.dpi_colors[i]:
                color[i] = rgb
        if dpi:
            changes["dpi"] = dpi
        if color:
            changes["color"] = color
        if int(self.debounce_spin.get_value()) != c.debounce_ms:
            changes["debounce"] = int(self.debounce_spin.get_value())
        if self.motionsync_row.get_active() != bool(c.motion_sync):
            changes["motionsync"] = self.motionsync_row.get_active()
        if int(self.lod_spin.get_value()) != c.lod:
            changes["lod"] = int(self.lod_spin.get_value())
        if int(self.sleep_spin.get_value()) != c.sleep_time:
            changes["sleep"] = int(self.sleep_spin.get_value())
        # backlight
        bl = c.backlight
        mode = self.light_mode.get_selected()
        rgb = rgba_to_rgb(self.light_color.get_rgba())
        speed = int(self.light_speed.get_value())
        bright = int(self.light_brightness.get_value())
        cur_mode = bl["mode"] if bl["on"] else 0
        if (mode, rgb, speed, bright) != (cur_mode, bl["rgb"], bl["speed"], bl["brightness"]):
            changes["backlight"] = (mode, rgb[0], rgb[1], rgb[2], speed, bright)
        # DPI indicator effect
        de = c.dpi_effect
        cur_de_mode = de["mode"] if de["on"] else 0
        de_mode = self.dpi_effect_mode.get_selected()
        de_br = int(self.dpi_effect_brightness.get_value())
        de_sp = int(self.dpi_effect_speed.get_value())
        if (de_mode, de_br, de_sp) != (cur_de_mode, de["brightness"], de["speed"]):
            changes["dpieffect"] = (de_mode,
                                    de_br if de_br != de["brightness"] else None,
                                    de_sp if de_sp != de["speed"] else None)
        return changes

    def on_apply(self, *_):
        if self.config is None:
            return
        changes = self._collect_changes()
        if not changes:
            self.toasts.add_toast(Adw.Toast(title="No changes to apply"))
            return
        self.apply_btn.set_sensitive(False)
        self.banner.set_title("Applying…")
        self.banner.set_revealed(True)
        self._bg(lambda: self.backend.apply(changes), self._on_applied)

    def _on_applied(self, applied, err):
        if err is not None:
            self.toasts.add_toast(Adw.Toast(title=f"Error: {err}"))
            self.apply_btn.set_sensitive(True)
            self.banner.set_revealed(False)
            return
        self.toasts.add_toast(Adw.Toast(title=f"Applied {len(applied)} change(s)"))
        self.reload()  # re-read to confirm what stuck


class QuasarApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.hator.Quasar3")

    def do_activate(self):
        win = self.props.active_window or QuasarWindow(self)
        win.present()
