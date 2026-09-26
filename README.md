# EMCOMM Field Node — Provisioner

[![License: GPLv3 or later, helper scripts MIT](https://img.shields.io/badge/license-GPLv3%2B%20%7C%20scripts%20MIT-blue.svg)](#license)
[![Latest release](https://img.shields.io/github/v/release/stevejun1986/emcomm-field-node?include_prereleases&sort=semver)](https://github.com/stevejun1986/emcomm-field-node/releases)
[![Platform: Linux Mint 22.x XFCE](https://img.shields.io/badge/platform-Linux%20Mint%2022.x%20XFCE-lightgrey.svg)](#platform)

Builds a standardized, offline-capable emergency communications workstation on a
Linux laptop: positioning and time sync, HF/VHF digital modes, offline mapping, an
offline reference library, and packet/mesh tooling.

Intended for ARES/RACES/CERT-style volunteer groups, EOC support positions, and any
field deployment where internet and cellular service cannot be assumed. A node is
built once while online, then operates entirely offline.

---

## What a node does

All of this works with **no network** once the node is built:

* **Position and time** from the node's own GPS receiver — stratum 1 with no NTP
  server in reach, which is what keeps timed digital modes working
* **HF messaging** — JS8Call for weak-signal keyboard traffic, ion2G for
  automatic link establishment against a staged channel plan
* **Packet and APRS** on VHF/UHF through any sound-card interface
* **LoRa mesh** text and peer positions, via an external Meshtastic node
* **Offline mapping** — QMapShack over local tile pyramids, registered by the
  provisioner and drawn on first launch
* **Offline reference** — a Kiwix encyclopedia, plus a PDF library served on
  loopback only
* **Receive-only situational awareness** — ADS-B aircraft tracking and direct
  weather-satellite imagery
* **Station logging and radio programming** — QLog and CHIRP

It is **radio-agnostic**: any transceiver with a data or accessory port and
something that can key it. The provisioner supplies the software, the
configuration and the channel plan; you declare which audio device and which
keying line to use.

---

## Quick start

```bash
sudo apt install python3-tk python3-requests
python3 deploy_emcomm_node_gui.py
```

Run it from the folder containing the script, **as your normal user — not with
`sudo`.** It refuses to start as root.

You are asked for your sudo password once, inside the window. It drives a
temporary mode-0700 askpass helper for the duration of the run, which is deleted
when the run ends. The password is never written anywhere else and never leaves
the machine.

`python3-tk` is **not** bundled with Python on Debian/Ubuntu/Mint; the GUI will
not start without it. `python3-requests` is optional — downloads fall back to
`curl`.

Check the steps this node needs, or **Select All** for a full build. Steps are
independent and not dependency-checked, so any one can be re-run on its own —
though the ion2G step assumes `wine` and `unzip` are already installed.

**Read [`TESTING.md`](TESTING.md) before provisioning a node you intend to
deploy.** It covers what to watch for per step, the known rough edges, and where
a run writes its log.

---

## Documentation

| | |
| --- | --- |
| [**White paper**](EmComm_Field_Node_Whitepaper.pdf) | What a field node is for, who deploys one, and what a provisioned node can do — with an explicit account of what has been observed on hardware versus what has been written and checked but never exercised |
| [`TESTING.md`](TESTING.md) | Per-step expectations, the known rough edges, and how to report a failure |
| [`Pre-Deployment Config Checklist`](Pre-Deployment%20Config%20Checklist) | Every manual step before a node is deployed: audio device, PTT keying, GPS binding, map registration |
| [`configs/README.md`](configs/README.md) | The placeholder contract, operating areas, and the GPS and radio device bindings |
| [`docs/README.md`](docs/README.md) | What belongs in the reference library, and the licensing question to settle before adding it |

---

## Platform

This is vanilla Linux configuration and runs on **any x86_64 system** meeting the
requirements below. One optional step is tied to specific hardware; everything
else is not.

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

### The dock-trigger step is the one hardware-specific part

Everything else provisions normally on any supported machine. **The dock-trigger
step targets a Panasonic Toughbook CF-30 in a Havis DS-PAN-111 series dock** —
the reference build, and the only combination that automation is written for.

> **The dock-trigger automation is a future feature, and is stated as one
> deliberately.** It is *being designed for* the combination above — a CF-30 in a
> DS-PAN-111 dock — which is the design target, not a tested configuration.
>
> It installs and verifies: the udev rule, the dispatcher, the user unit and the
> launcher are all confirmed on disk. But **no dock insertion has ever fired the
> chain**, on any hardware, including the reference build, and there is no dock
> available to this project to readily test against. Verification checks that
> those files exist, which is not the same claim — so it now carries a fifth row
> saying the chain has never been seen to fire, and the run says the same thing
> at the end of the step.
>
> It stays in the provisioner and stays selectable, because the work belongs in
> the tree where it can be developed. Treat it as in development: select it if you
> have the hardware and intend to work on it, and do not build a deployment that
> depends on it firing. **Further development against the hardware is what would
> change this.** See **Verifying it actually fires** in `TESTING.md` for the five
> links in the chain and how to test each one.

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

---

## Software Stack

Installed and configured by the provisioner:

* **Positioning & time sync:** `gpsd`, `chrony`
* **HF digital:** JS8Call (weak-signal keyboard messaging), ion2G (HF ALE, via Wine)
* **Packet / APRS:** Direwolf (AX.25 software TNC for a sound-card interface)
* **Mesh:** Meshtastic CLI (manages an external 915 MHz LoRa node) — installed into
  the operator's user site with `python3 -m pip install --user
  --break-system-packages meshtastic`, so `meshtastic` lands on `~/.local/bin` and
  is importable by the system `python3` the GPX bridge runs under
* **Radio programming:** CHIRP
* **Station logging:** QLog (via Flatpak)
* **Mapping:** QMapShack with offline tile sources auto-registered
* **Offline knowledgebase:** Kiwix ZIM engine, plus a loopback document server on
  `127.0.0.1:8085` for the PDF reference library
* **Weather satellite imagery:** SatDump (via RTL-SDR)
* **SDR command-line tools:** `rtl-sdr` — `rtl_test`, `rtl_fm`, `rtl_power`;
  `rtl_test -t` is what the dongle-contention check calls for. Installed by
  either SDR step as well as by system packages, so a node that took SatDump or
  dump1090 alone still has it
* **ADS-B aircraft tracking:** dump1090 (via RTL-SDR) — installed but **not**
  started automatically; see the note below
* **Automation:** desktop shortcuts
* **Mesh-to-GPX bridge:** when **both** Meshtastic and QMapShack are installed, a
  user service (`emcomm-mesh-gpx.service`) polls the Meshtastic node database and
  rewrites `EMCOMM_Data/Meshtastic/mesh_nodes.gpx` for import into QMapShack.
  Skipped, with a message, on a node without QMapShack. It never holds the serial
  port — each poll opens, reads and closes, and skips a cycle if something else
  has the radio — so it cannot block an interactive `meshtastic` command. Peer
  positions reach the map as a **file, not a live feed**: QMapShack is not believed
  to re-read a GPX that changes on disk, so a refreshed map means importing again
* **Dock-triggered autostart (udev + systemd):** optional, installed by the
  provisioner, and **not a finished feature** — see [**Platform**](#platform) above

### ADS-B and SatDump share one dongle

Both drive the RTL-SDR, and only one process can hold it at a time. dump1090's
boot entry is therefore removed, and it is **not** launched by the dock autostart
sequence — a service that took the dongle at boot would silently cost you
satellite imagery, and the failure would surface later as SatDump being unable to
open the device.

It stays fully startable on demand, which is a separate switch from the boot one:
`START_DUMP1090` in `/etc/default/dump1090-mutability` is checked by the init
script on *every* start, and the provisioner leaves it `"yes"`. The service user
is also added to `plugdev`, without which the daemon starts, cannot open the
dongle, and exits — leaving an aircraft map that never populates.

Start ADS-B when you want it, and stop it before a satellite pass:

```bash
sudo service dump1090-mutability start
sudo service dump1090-mutability stop
```

Either SDR step also blacklists the kernel DVB-T driver
(`/etc/modprobe.d/emcomm-rtlsdr.conf`). An RTL-SDR matches `dvb_usb_rtl28xxu`,
which binds it as a television tuner before any SDR program can open it — no
package blacklists that, so without this step the kernel wins the race on a
freshly imaged node. Delete the file and reboot to use the dongle for DVB-T
instead.

If **both** are installed, the run says so explicitly and points at
`EMCOMM_Data/SDR/dongle_arbitration.md`, staged by either SDR step. It covers the
four layers that decide who holds the dongle — the kernel DVB-T driver, the
`plugdev` permission on the device node, libusb's exclusivity, and the start
switches that are the only part you control — and how to hand it between
programs.

Receiver latitude and longitude are deliberately left unset — that is operator
position data, in the same class as a grid square. `EMCOMM_Data/ADSB/dump1090_setup.md`
is staged on the node and covers setting them, viewing the aircraft map, and
antenna expectations at 1090 MHz.

Note that the map arrives on the distribution's lighttpd default rather than on
loopback, unlike the document server. Check what it is listening on before
deploying a node: `sudo ss -ltnp | grep lighttpd`.

Every one of these is an independently checkable step. Nothing is mandatory.

The provisioner runs as five screens — **options, administrator access, run,
verification, summary**. Sudo is collected after the steps are chosen, not as a
checkbox: provisioning writes to `/etc`, installs packages, enables systemd units
and adds a udev rule, so root is a requirement of the run. When the run ends a
read-only verification pass checks what actually landed on disk — not merely that
profiles were staged, but that placeholders were substituted and the map sources
point at the layers the fetcher wrote. It reports; it never repairs.

Every run writes a transcript to `~/.emcomm/logs/provision-<date>-<time>.log` —
its path is on the run screen and again on the summary. It holds everything the
log pane showed, the verification rows, the final result, and the **complete**
output of any command that failed, which is more than the pane displays: the pane
collapses runs of progress redraws so the error is not buried under them. If a
deployment goes wrong, that file is the thing to send. The newest ten are kept.

The callsign / node ID also becomes the machine's **system hostname**, with the
matching `/etc/hosts` entry, so a fleet built by cloning one master image does not
end up with every node answering to the same name.

If the map step is selected, a sixth screen — **Operating Area** — appears before
the credential prompt. It takes a center point in decimal degrees and a radius
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

The full contract — including how to check a profile before adding it — is in
[`configs/README.md`](configs/README.md).

---

## Verifying a release download

Releases are signed with this SSH key:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIgY7UVCUi3z4Un9Dxl83TF1iCjZSODEvNdetP7Omc1Z
```

Put that in a file called `allowed_signers`, prefixed with a label:

```text
release@emcomm-field-node ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIgY7UVCUi3z4Un9Dxl83TF1iCjZSODEvNdetP7Omc1Z
```

Then, alongside the downloaded tarball, its `.sig` and `SHA256SUMS`:

```bash
sha256sum -c SHA256SUMS
ssh-keygen -Y verify -f allowed_signers -I release@emcomm-field-node \
    -n file -s emcomm-field-node-<version>.tar.gz.sig \
    < emcomm-field-node-<version>.tar.gz
```

Both should pass; the second prints `Good "file" signature`. **If either fails, do not
run the provisioner** — report it on the issue tracker.

Key fingerprint, for checking a copy you got elsewhere:
`SHA256:Ut/j+m5SlkA1xGBz4wLK9S+9FKXON2Yoj9NYyanTgmw`

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

Transmitting requires the appropriate license — this is the operator's
responsibility, not the software's:

* **Amateur license required to transmit:** JS8Call, ion2G HF ALE, Direwolf/APRS,
  and amateur-band DMR. Receive-only use of any of these does not.
* **GMRS license required:** GMRS channels.
* **No license required:** FRS, MURS, Meshtastic (915 MHz ISM), SatDump reception,
  and receiving generally.

The callsign entered at provisioning is written into the JS8Call profile as
`MyCall`. **Set a real, licensed callsign before transmitting.** A placeholder is
not a license.

Amateur radio may not be used for communications in which the operator has a
pecuniary interest, and traffic must be sent in the clear so it can be understood
by anyone monitoring. Plan nets accordingly: assume everything you send is public.

---

## License

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

### MIT-licensed helper scripts

Two standalone scripts are licensed under the **MIT License** instead. The text
is in [`LICENSE-MIT`](LICENSE-MIT) and in each file's header:

* `scripts/fetch_map_tiles.py` — the offline map tile fetcher
* `scripts/diagnose_qmapshack_maps.py` — the QMapShack map diagnostic

Each is useful on its own, outside this provisioner, and permissive terms let it
be reused in tools that could not take on GPL-3.0. MIT is compatible with the
GPL, so the package as a whole still ships under GPL-3.0-or-later. The
provisioner, `deploy_emcomm_node_gui.py`, remains GPL.

### Alternative licensing

Some organizations — county IT departments, agency EOCs — work under policies
that do not permit GPL-3.0 software. If that applies to yours, **open a GitHub
issue** asking for separate license terms, naming the organization and the policy
that applies. Terms can be granted to a single organization; the public license
does not change.

Check what the policy actually restricts first. The GPL places no conditions on
running this software or on changing it for your own use; its conditions apply
when copies are passed on to others.

### What this license covers

The provisioner, the configuration templates and the documentation written for
this repository are GPL-3.0-or-later; the two helper scripts above are MIT.

It does **not** cover third-party material that a deployment pulls in or that is
later added to this tree. Reference manuals, map tiles, ZIM archives, `.deb`
packages and radio codeplugs each carry their own terms, set by whoever
published them. `docs/` and `scripts/Packages/` are intentionally empty here for
that reason. **Before committing a third-party document into this repository,
check that its license permits redistribution** — and record that license
alongside it.

Neither license is a license to transmit. See the regulatory section
above.
