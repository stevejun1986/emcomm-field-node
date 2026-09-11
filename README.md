# EMCOMM Field Node — Provisioner

Builds a standardized, offline-capable emergency communications workstation on a
Linux laptop: positioning and time sync, HF/VHF digital modes, offline mapping, an
offline reference library, and packet/mesh tooling.

Intended for ARES/RACES/CERT-style volunteer groups, EOC support positions, and any
field deployment where internet and cellular service cannot be assumed. A node is
built once while online, then operates entirely offline.

---

## Target Platform

Hardware-agnostic vanilla Linux configuration — runs on any x86_64 system meeting
the requirements below.

* **OS:** Linux Mint 22.x, XFCE edition (Ubuntu 24.04 / "noble" base)
* **Typical hardware:** any serviceable laptop; rugged/ex-fleet machines (Panasonic
  Toughbook, Getac, Dell Latitude Rugged) are common because they are cheap
  secondhand, run from 12V, and tolerate field conditions
* **Interfaces:** USB / serial to radio hardware (sound-card interface, GPS puck,
  RTL-SDR, LoRa node)

Two components are environment-specific rather than hardware-specific:

* The dock-trigger udev rule is keyed to a **placeholder** USB vendor/product ID.
  If you use it, find your own dock's IDs with `udevadm monitor --environment --udev`
  and update `99-dock-trigger.rules`. Skip the step entirely if you have no dock.
* Wallpaper and desktop-shortcut trust use XFCE mechanisms (`xfconf`,
  `metadata::xfce-exe-checksum`). Another desktop environment needs different handling.

---

## Software Stack

Installed and configured by the provisioner:

* **Positioning & time sync:** `gpsd`, `chrony`
* **HF digital:** JS8Call (weak-signal keyboard messaging), ion2G (HF ALE, via Wine)
* **Packet / APRS:** Direwolf (AX.25 software TNC for a sound-card interface)
* **Mesh:** Meshtastic CLI (manages an external 915 MHz LoRa node)
* **Radio programming:** CHIRP
* **Station logging:** QLog (via Flatpak)
* **Mapping:** QMapShack with offline tile sources auto-registered
* **Offline knowledgebase:** Kiwix ZIM engine, plus a loopback document server on
  `127.0.0.1:8085` for the PDF reference library
* **Weather satellite imagery:** SatDump (via RTL-SDR)
* **Automation:** optional dock-triggered autostart (udev + systemd), desktop
  shortcuts, wallpaper

Every one of these is an independently checkable step. Nothing is mandatory.

---

## What You Supply

Configuration profiles and reference material are **not bundled** — each group runs
its own frequencies, its own operating area, and its own document set. The
provisioner references these paths and warns visibly when one is absent, so a
partial set works fine:

```text
configs/JS8Call.ini          JS8Call profile          (placeholders — see below)
configs/QMapShack.conf       QMapShack profile        (placeholders — see below)
configs/analog_channels.csv  CHIRP channel list for analog radios
configs/ale_channels.zcp     ion2G HF ALE channel plan
configs/satdump_tles.txt     Curated TLE set for SatDump
scripts/fetch_map_tiles.py   Offline map tile fetcher for YOUR operating area
docs/*.pdf                   Reference library, served over loopback
scripts/Packages/            Locally cached .deb packages, wallpaper image
```

### Profile placeholders — read this before shipping a profile

A profile captured from a working installation carries that operator's **callsign**
and their **absolute home path**. Ship it unchanged and every node built from it
transmits under someone else's identity, and breaks for any user whose username
differs. Use these tokens instead — they are substituted at provisioning time:

| Token | Replaced with |
| --- | --- |
| `MYCALL_PLACEHOLDER` | the callsign entered at the prompt |
| `HOME_PLACEHOLDER` | the provisioning user's `$HOME` |

Before distributing a profile, check it for a real callsign, a grid square, absolute
`/home/<someone>/` paths, and hardcoded audio device names. All four are easy to miss
and all four are wrong on someone else's machine.

---

## Requirements

```bash
sudo apt install python3-tk python3-requests
```

`python3-tk` is **not** bundled with Python on Debian/Ubuntu/Mint — the GUI will not
start without it. `python3-requests` is optional (downloads fall back to `curl`).

## Running

From the folder containing the script, **as your normal user — not with `sudo`**:

```bash
python3 deploy_emcomm_node_gui.py
```

It refuses to start as root. You are asked for your sudo password once, in the
window; it drives a temporary mode-0700 askpass helper for the duration of the run
and is deleted when the run ends. It is never written anywhere else and never
leaves the machine.

Check the steps this node needs, or **Select All** for a full build. Steps are
independent and not dependency-checked, so any one can be re-run on its own — but
the ion2G step assumes `wine` and `unzip` are already installed.

---

## Fleet Deployment

1. Build one physical node fully and verify it on the air
2. Image that disk — from real hardware, not a VM, to avoid driver mismatch
3. Clone the image to the remaining machines
4. Re-run the callsign/node-ID step per unit; the callsign is baked into the image
   (`/etc/emcomm/node.conf`, the JS8Call profile, the Direwolf config)

In this model the provisioner builds the golden image rather than running on every
node.

---

## Licensing

Transmitting requires the appropriate licence — this is the operator's
responsibility, not the software's:

* **Amateur licence required to transmit:** JS8Call, ion2G HF ALE, Direwolf/APRS,
  and amateur-band DMR. Receive-only use of any of these does not.
* **GMRS licence required:** GMRS channels.
* **No licence required:** FRS, MURS, Meshtastic (915 MHz ISM), SatDump reception,
  and receiving generally.

The callsign entered at provisioning is written into the JS8Call profile as
`MyCall`. **Set a real, licensed callsign before transmitting.** A placeholder is
not a licence.

Amateur radio may not be used for communications in which the operator has a
pecuniary interest, and traffic must be sent in the clear so it can be understood
by anyone monitoring. Plan nets accordingly: assume everything you send is public.

---

## Licence

This package is free software, licensed under the **GNU General Public License,
version 3 or (at your option) any later version**. The full text is in
[`LICENSE`](LICENSE).

    Copyright (C) 2026  WSNQ705

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

In practice, for an emergency-communications tool: use it, run it on as many
nodes as you like, change it to suit your group. If you distribute a modified
version, pass on the source under the same terms so the next group gets the same
freedom — including whatever you fixed in the field.

### What this licence covers

The provisioner, the tile fetcher, the configuration templates and the
documentation written for this repository.

It does **not** cover third-party material that a deployment pulls in or that is
later added to this tree. Reference manuals, map tiles, ZIM archives, `.deb`
packages and radio codeplugs each carry their own terms, set by whoever
published them. `docs/` and `scripts/Packages/` are intentionally empty here for
that reason. **Before committing a third-party document into this repository,
check that its licence permits redistribution** — and record that licence
alongside it.

Nothing in this licence is a licence to transmit. See the regulatory section
above.
