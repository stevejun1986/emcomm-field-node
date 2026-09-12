# EMCOMM Field Node — Provisioner

Builds a standardized, offline-capable emergency communications workstation on a
Linux laptop: positioning and time sync, HF/VHF digital modes, offline mapping, an
offline reference library, and packet/mesh tooling.

Intended for ARES/RACES/CERT-style volunteer groups, EOC support positions, and any
field deployment where internet and cellular service cannot be assumed. A node is
built once while online, then operates entirely offline.

---

## Supported Hardware

**This package targets a Panasonic Toughbook CF-30 docked in a Havis DS-PAN-111
series dock.** That is the reference build, and it is the only combination the
dock-trigger automation is known to work on.

Two parts are tied to that hardware:

* **The dock-trigger udev rule** matches the DS-PAN-111 dock's USB hub by vendor
  and product ID (`05e3:0610`). A different dock enumerates differently, the rule
  never fires, and nothing autostarts on dock.
* **The autostart sequence** assumes the CF-30's peripheral layout — its serial
  and USB topology, and the display coming up on `:0` under XFCE.

**On other laptops and other docks, treat the dock-trigger step as unsupported.**
Leave it unchecked. Everything else — packages, profiles, mapping, the document
server, Direwolf, Meshtastic, SatDump — is vanilla Linux configuration and
provisions normally on any x86_64 machine running the supported OS. You lose
dock-triggered autostart, not the node.

To adapt the rule to your own dock, find its IDs with
`udevadm monitor --environment --udev` while docking, and edit the `idVendor` /
`idProduct` values in `99-dock-trigger.rules`. That is a port, not a
configuration option, and it is on you to verify.

---

## Target Platform

Apart from the dock automation above, this is vanilla Linux configuration and runs
on any x86_64 system meeting the requirements below.

* **OS:** Linux Mint 22.x, XFCE edition (Ubuntu 24.04 / "noble" base)
* **Typical hardware:** any serviceable laptop; rugged/ex-fleet machines (Panasonic
  Toughbook, Getac, Dell Latitude Rugged) are common because they are cheap
  secondhand, run from 12V, and tolerate field conditions
* **Interfaces:** USB / serial to radio hardware (sound-card interface, GPS puck,
  RTL-SDR, LoRa node)

One further component is environment-specific:

* Desktop-shortcut trust uses XFCE mechanisms (`xfconf`,
  `metadata::xfce-exe-checksum`). Another desktop environment needs different
  handling.

The dock-trigger rule is hardware-specific — see **Supported Hardware** above.

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
  shortcuts

Every one of these is an independently checkable step. Nothing is mandatory.

The provisioner runs as five screens — **options, administrator access, run,
verification, summary**. Sudo is collected after the steps are chosen, not as a
checkbox: provisioning writes to `/etc`, installs packages, enables systemd units
and adds a udev rule, so root is a requirement of the run. When the run ends a
read-only verification pass checks what actually landed on disk — not merely that
profiles were staged, but that placeholders were substituted and the map sources
point at the layers the fetcher wrote. It reports; it never repairs.

The callsign / node ID also becomes the machine's **system hostname**, with the
matching `/etc/hosts` entry, so a fleet built by cloning one master image does not
end up with every node answering to the same name.

If the map step is selected, a sixth screen — **Operating Area** — appears before
the credential prompt. It takes a centre point in decimal degrees and a radius
(50, 75 or 150 miles), shows the tile count, size and time **before** anything
downloads, and writes the area file. Tiles are tiered: full street detail within
25 miles, orientation zoom to the full radius. Coverage scales with the square of
the radius, so a flat 150-mile fetch at street zoom would be roughly 694,000 tiles
against a public USGS endpoint.

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
scripts/Packages/            Locally cached .deb packages
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
