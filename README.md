# Quasar3 — Linux app for the HATOR Quasar 3 / Quasar 3 Ultra 8K

A Linux (Ubuntu / Pop!_OS) configuration app for the **HATOR Quasar 3** family of
wireless gaming mice, replacing the Windows-only official software.

Supported devices (all Compx `379a`, same vendor HID protocol, reachable on Linux
**without root**):

| Model | Link | USB ID | USB product string |
|-------|------|--------|--------------------|
| Quasar 3 Wireless | 2.4 GHz dongle | `379a:0cf0` | `Quasar 3WL` |
| Quasar 3 Ultra 8K | 8K 2.4 GHz dongle | `379a:3308` | `Quasar 3UL` |
| Quasar 3 Ultra 8K | mouse on USB cable | `379a:3307` | `Quasar 3UL` |

When several are plugged in (e.g. the mouse charging on the cable while its
dongle stays in the dock) the app talks to whichever one actually answers,
preferring the cable.

> **Status: working core.** The protocol is fully decoded from the official
> HATOR MouseHUB and **verified byte-for-byte against the live devices** (read +
> write, on both the Quasar 3 Wireless dongle and the Quasar 3 Ultra 8K over
> cable). The CLI is a usable configurator today and the GTK4 GUI ships in the
> .deb. See `docs/PROTOCOL.md`.

## Feature status

- [x] DPI / CPI per-stage values + active stage
- [x] Polling rate
- [x] Per-stage RGB indicator colors
- [x] Debounce, Motion Sync, lift-off distance, sleep timer
- [x] Main backlight (`Light` zone): Off / Rainbow / Single Color Breath / Fixed Color / Neon / Rainbow Breath / Fixed Rainbow
- [x] DPI indicator effect: Off / Steady / Breathing + brightness/speed
- [ ] Button remapping + macros — layout known, encoding WIP
- [ ] Battery readout / onboard profiles — opcodes known, not yet ported
- [x] GTK4 / libadwaita GUI (native Pop!_OS look)

## Layout

```
src/quasar3/
  transport.py   verified hidraw access (stdlib only, no root)
  protocol.py    command codecs (built up as the protocol is decoded)
  device.py      high-level device API
  cli.py         command-line interface
  app/           GTK4 + libadwaita GUI
tools/probe.py   read-only reconnaissance probe (safe; never writes)
udev/            udev rule for guaranteed non-root access
docs/            RECON.md (hardware facts) + PROTOCOL.md (command map)
```

## Quick start (read-only, safe)

```bash
python3 tools/probe.py            # discover node, dump feature reports
python3 tools/probe.py --watch 5  # passively watch input reports
```

## Install

### Option A — Debian package (recommended)

```bash
bash packaging/build-deb.sh                 # -> dist/quasar3_<ver>_all.deb
sudo apt install ./dist/quasar3_*.deb       # pulls GTK4/libadwaita deps, installs udev rule
```

Then launch **HATOR Quasar 3** from your app menu, or run `quasar3 show` /
`quasar3-gui` from a terminal.

### Option B — run from the checkout (no .deb)

```bash
sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1   # GUI deps
bash packaging/install-local.sh                            # udev rule + ~/.local launchers
# or just:  PYTHONPATH=src python3 -m quasar3.cli show
```

### udev rule only

```bash
sudo cp udev/99-hator-quasar3.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

## CLI

```bash
quasar3 info                       # which devices are plugged in, which one answers, firmware
quasar3 show                       # decoded current settings
quasar3 battery                    # battery %, charging, voltage
quasar3 set polling 1000 --yes
quasar3 set dpi 1 1600 --yes       # stage 1 -> 1600 DPI
quasar3 set color 1 #ff0000 --yes  # stage 1 indicator -> red
quasar3 set backlight '#ff6a00' 7 9 fixed --yes   # colour, speed, brightness, mode (name or number)
quasar3 set dpieffect steady 5 --yes               # DPI indicator effect, brightness 1-10
```

> Only **Fixed Color** and **Single Color Breath** use the colour. Rainbow, Neon,
> Rainbow Breath and Fixed Rainbow cycle the spectrum and ignore it — so a mouse
> that "stays rainbow" after you picked a colour just needs its effect switched.

## Changelog

- **0.1.1** (2026-10-24) — real backlight mode names from the official language
  file (mode 1 is *Rainbow*, not "Static"; *Fixed Color* is 3), "Off" now toggles
  `LightState` like the official app, DPI indicator effect (Off/Steady/Breathing,
  brightness, speed) in CLI + GUI, colour-ignored hint in the GUI.

- **0.1.0** (2026-10-24) — Quasar 3 Ultra 8K support: new USB IDs `379a:3307`
  (cable) and `379a:3308` (8K dongle), auto-selection of the answering link,
  `quasar3 info` reports model/link/firmware, udev rule covers all three IDs.
  Verified on hardware: config read, battery, DPI colour write + revert,
  polling 8000 Hz write + revert.
- **0.0.1** (2026-05-31) — initial release for the Quasar 3 Wireless (`379a:0cf0`).

## Safety

Reads (`GET_FEATURE`, input reports) cannot change device state. Writes
(`SET_FEATURE`, output reports) can — the tool only issues writes for commands
that have been confirmed against hardware, and the first write experiments target
easily-reversible settings (e.g. a static RGB color).
