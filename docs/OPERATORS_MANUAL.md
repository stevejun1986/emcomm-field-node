# EmComm Field Node — Operator's Manual

**Draft.** Describes the provisioner on `main`. The last tagged release is **1.0.2**;
`main` carries the GPS time source (Chapter 4) on top of it, which is not in that
release. Where the two differ, this manual follows `main` and says so.

---

## About this manual

This is the **operator's** manual: what to do with a node that has already been built.
It assumes the provisioner has run and the verification screen came back clean. If you
are building a node rather than using one, you want `README.md` and the
`Pre-Deployment Config Checklist` instead.

A copy lives on the node itself. See **Chapter 3 — The reference library**.

### What this node is

A laptop that does useful work with no network, no cell service and no infrastructure:
HF and VHF messaging, offline mapping, an offline encyclopedia, satellite weather
imagery, aircraft tracking, and a LoRa mesh for local peers. Every piece of it was
chosen so that it keeps working when the internet does not.

### What this node is not

It is not a turnkey appliance. Several tools need your radio, your frequencies and your
antennas before they do anything, and the manual says so where that applies. It is also
not a substitute for knowing how to operate the radios themselves — it is the computer
half of the station.

### Conventions

| | |
|---|---|
| `~` | your home directory |
| `~/EMCOMM_Data/` | operator data — maps, imagery, references, logs |
| `~/EMCOMM_Apps/` | applications built from source |
| `N0CALL-1` | stands in for your node ID throughout |
| **Proven** | confirmed working on real hardware, with the evidence recorded |
| **Unproven** | installed and configured, never confirmed working |
| **Future feature** | ships deliberately, not expected to work yet |

A command shown like this is typed in a terminal:

    rtl_test -t

### Figures, and what must never appear in one

Screenshots in this manual are **scrubbed before they are added**. A field node's screen
carries operator and machine identity in more places than people expect — window title
bars alone have leaked a callsign three times in this project's history.

Anything added here must be checked for:

* **no real callsign** — use `N0CALL-1`, including in window and tab titles, which often
  read `(on <hostname>)`
* **no populated grid square** — `MyGrid` stays empty or reads `AA00aa`
* **no `alsa_output.*` / `plughw:` device strings** from a working audio setup
* **no absolute `/home/<user>/` paths** — crop them, or show `~`
* **no site or installation name**, on screen or in a file path
* **image metadata stripped** before commit; PDFs also embed author and producer fields

Where a figure would genuinely help, this draft carries a placeholder naming what it
should show and what to scrub. Text comes first: a manual that only works with pictures
is a manual that stops working when it is printed in black and white.

---

## Status — what is proven and what is not

An operator manual that claims more than the software has demonstrated is worse than no
manual. This is the honest position for the provisioner as it stands on `main`.

### Proven on hardware

Confirmed on a real machine, with the result recorded in the project's issue history:

| | Evidence |
|---|---|
| SatDump builds under the provisioner | fresh VM, clean build |
| RTL-SDR appears in SatDump's recorder | live spectrum, dongle open |
| dump1090 opens the dongle and decodes | 61 aircraft, 198 messages/sec |
| DVB-T blacklist survives unplug/replug | device unclaimed and ready after hotplug |
| A reboot leaves the dongle usable | cold boot, bare metal, dongle attached from power-on |
| Meshtastic CLI runs and reaches a node | `--info` connected first try |
| The mesh-to-GPX bridge produces a valid GPX | live 9-node mesh, 4 peers with positions |
| QMapShack imports that GPX | peers drawn as waypoints |

### Unproven

Installed and configured, but never confirmed working end to end. Treat these as
"should work, verify before you rely on it":

| | Why it is unproven |
|---|---|
| **Dock-trigger autostart** | has never been observed to fire, on any hardware. Ships as a **future feature** by decision — see Chapter 20 |
| **Direwolf PTT** | `PTT CM108` is a starting guess. CM108-style PTT over `/dev/hidraw*` has documented reliability problems on Linux |
| **Direwolf audio device** | `ADEVICE plughw:1,0` is a placeholder and is very likely wrong for your interface |
| **JS8Call operation** | no station profile ships; nothing has been keyed on-air from a provisioned node |
| **GPS time source, on an EmComm node** | proven on the sibling project's command node — cold boot, no network, stratum 1 in about a minute, twice. The EmComm step is a hand-port of that one and has not been run on an EmComm node. See Chapter 4 |
| **SatDump TLE retention** | the setting that stops SatDump overwriting a curated element set is newly changed and unverified on a node |

If you confirm one of these on your own hardware, that is worth recording — the project
tracks what has been proven, not what is assumed.

---

# Part I — The node

## Chapter 1 — Layout: where everything lives

Two directories hold everything that is yours.

    ~/EMCOMM_Data/
        ADSB/            dump1090 reference and setup notes
        Kiwix_ZIM/       offline knowledgebase archives (.zim)
        Meshtastic/      mesh setup reference
        Offline_Maps/    map tiles for your operating area
        PDF_Manuals/     the reference library (Chapter 3)
        SDR/             RTL-SDR dongle reference
        ion2G/           HF ALE working directory

    ~/EMCOMM_Apps/
        SatDump/         built from source
        mesh_to_gpx.py   the mesh-to-GPX bridge

Three more locations matter:

* `/etc/emcomm/node.conf` — the node's identity, written at provisioning. It records the
  node ID you entered and which optional features were enabled.
* `~/.config/` — per-application settings, in each application's usual place. The
  provisioner writes some of these; most you will set yourself.
* `/etc/` — a few system files the provisioner owns, and does not expect you to
  edit: the RTL-SDR blacklist (Chapter 5) and, if you selected the GPS step, the
  chrony refclock and `gpsd` binding (Chapter 4).

One thing does **not** live on the node: `configs/` is part of the provisioner's own
directory, not of the finished node. It is where you put the profiles and the GPS
binding that a run consumes.

**Back up `~/EMCOMM_Data/`, `~/.config/`, and the provisioner's `configs/`.**
Everything else can be rebuilt by re-running the provisioner; those cannot. `configs/`
is on the list because it is gitignored by design — it holds your callsign, your
profiles and your receiver's serial number, so a fresh clone comes back without it and
a re-run then quietly provisions less than the last one did.

## Chapter 2 — First power-on

1. **Log in normally.** Nothing here runs as root, and none of the tools should be
   started with `sudo`.
2. **Check the desktop.** The provisioner places shortcuts for the graphical tools it
   installed — QMapShack, Kiwix, JS8Call, CHIRP, QLog, ion2G, and SatDump if it was
   selected.
3. **Confirm the node ID:**

        cat /etc/emcomm/node.conf

   This should match the callsign or node ID you provisioned with. Direwolf's `MYCALL`
   was written from it, so if this is wrong, Chapter 9 is wrong too.

4. **Open the reference library** — Chapter 3. Everything staged onto the node is
   reachable from there, including the notes each tool's chapter refers to.

### One thing that surprises people

`~/.local/bin` only joins your `PATH` at login, and only if it already exists. On a node
where the provisioner just created it, **commands like `meshtastic` will not be found
until you log out and back in.** The same login is what activates your `dialout` group
membership, which is what lets you open a serial port at all. If a command that should
exist reports "command not found" immediately after provisioning, log out and back in
before troubleshooting anything else.

## Chapter 3 — The reference library

The node runs a small document server so the staged manuals are readable in a browser
rather than hunted for on disk.

**Start it, and have it start at login:**

    systemctl --user start emcomm-docs-server.service
    systemctl --user enable emcomm-docs-server.service

**Read it:** open a browser to

    http://127.0.0.1:8085

It serves `~/EMCOMM_Data/PDF_Manuals/` and **binds to loopback only** — nothing off the
machine can reach it, by design.

To add material, drop PDFs into `~/EMCOMM_Data/PDF_Manuals/` and reload the page. No
restart needed.

> **The library ships empty.** The provisioner copies PDFs from the repository's `docs/`
> directory, and none are distributed — reference material is license-varied and large.
> A node provisioned from a stock clone has a working document server with nothing in it,
> and the provisioner says so in its log. Filling it is a deployment step.

## Chapter 4 — Time and position

**Read this before relying on any timed mode.**

A field node's clock is a problem the moment it goes offline, and it is a problem that
announces itself as something else entirely.

`chrony`'s stock configuration lists internet NTP pools. A deployed node cannot reach
them, so it never synchronises, and it says so only if you ask:

    $ chronyc tracking
    Leap status     : Not synchronised

The clock still runs — it is whatever the hardware clock said at boot, drifting at
whatever rate that clock drifts. For most of what this node does, that is fine.

**It is not fine for JS8Call.** JS8Call and similar weak-signal modes transmit and
receive in timed windows and expect the clock to be right within about a second. A node
that has been powered off for a week and comes up offline can easily be far enough out
that it hears nothing and nobody hears it — with no error message, because nothing is
broken. **If JS8Call decodes nothing, check the clock before you check the antenna.**

The fix is a GPS receiver. GPS carries time, not just position, and it carries it
without a network.

### What the provisioner does

Select **GPS time source (gpsd + chrony)** on the options screen. The step writes two
files and restarts two services:

| File | What it holds |
|---|---|
| `/etc/chrony/conf.d/10-emcomm-gps.conf` | a `refclock SHM 0 refid GPS0 … prefer` line, telling chrony to take time from `gpsd`'s shared-memory segment |
| `/etc/default/gpsd` | binds `gpsd` to the receiver you named, with `-n -b` and the baud rate if you set one |

It then enables `gpsd.service`, restarts `chrony`, and restarts `gpsd` — **in that
order**, because `chronyd` creates the shared-memory segment while it is still root and
`gpsd` attaches to it afterwards. Restarting chrony second would invalidate an
attachment gpsd already holds.

The existing `/etc/default/gpsd` is backed up to `/etc/default/gpsd.emcomm-backup`
before it is replaced, once — a re-run will not overwrite the backup.

### Your one job: name the receiver

The provisioner will not go looking for a GPS. Copy the sample and fill in one line:

    cp configs/gps.conf.sample configs/gps.conf
    ls -l /dev/serial/by-id/          # with the receiver attached
    # put that path in DEVICE=, and the speed in BAUD= if you know it

Two things about that, both of which have bitten:

**Use the `/dev/serial/by-id/` path, not `/dev/ttyUSB0`.** Device numbering moves the
instant another serial device is attached — plug in a radio CAT interface and it can
take `ttyUSB0` out from under the GPS. The by-id path is keyed on vendor, product and
serial number and survives that. (A by-id entry exists only if the device reports a
serial number. Most do.)

**`BAUD` is optional but worth setting.** Without it gpsd hunts for the speed, and on
the receiver this was built against it did not find 4800 inside the fifteen seconds it
was willing to spend. Most NMEA pucks are 4800 or 9600.

> **The provisioner never probes for a receiver, and neither should you.** Opening a
> serial port asserts DTR, and some CAT interfaces key PTT on DTR or RTS — a sweep of
> `ttyUSB*` hunting for a GPS could put a radio on the air. You name the device; the
> provisioner touches nothing else.

`configs/gps.conf` is gitignored, because a by-id path carries the receiver's serial
number and that is machine data. The `.sample` ships; your real file does not.

### Confirming it

Give it about a minute from cold, then:

    chronyc tracking | grep -E 'Reference ID|Stratum|Leap'

What you want:

    Reference ID    : 47505330 (GPS0)
    Stratum         : 1
    Leap status     : Normal

`Stratum : 1` is the whole point — the node is now its own time authority and needs
nothing upstream. Pull the network cable and it stays that way.

Before acquisition, `chronyc sources` shows `GPS0` with reach `0`. That is normal for
the first minute, not a fault. The verification screen says the same thing: a `GPS time
source active` row that **warns** at reach 0 rather than failing, because most
provisioning runs happen with no receiver attached at all.

> **Do not "test" the receiver by opening its serial port.** A USB puck typically resets
> when DTR drops, and the one this was built against needs about ten seconds to resume.
> Anything that opens and closes the port costs the node ten seconds of time source and
> then reports on a device it just restarted. Ask `chronyd` instead — `chronyc sources`
> proves the whole chain anyway: gpsd reading the puck, samples reaching shared memory,
> chronyd consuming them.

### When it does not work

Four things break this silently — each one leaves a node that looks configured, logs no
error, and never synchronises. The provisioner handles all four; they are listed because
a hand-configured node, or one someone has since edited, can lose any of them.

| Symptom | Cause |
|---|---|
| `gpsd` not running at all | `gpsd.service` is not enabled on a stock node — only `gpsd.socket` is, and socket activation starts gpsd when a *client* connects. chrony reads shared memory and never connects, so gpsd never starts |
| gpsd running, no samples | missing `-n`. Without it gpsd does not poll the receiver until a client connects. Same outcome, different cause |
| port open, correct speed, zero bytes | missing `-b`. gpsd's device probe *writes* to the receiver; that locked up the puck this was built against completely |
| GPS healthy but chrony ignores it | missing `prefer` on the refclock. chronyd will keep steering from an **unreachable** NTP server for hours after the network drops, because a stale source's dispersion grows at only about 1 ppm. Observed on a node: a server at reach `0`, last heard from 190 seconds earlier, still selected over a live GPS at reach `377` |

Useful commands, in escalating order of intrusiveness:

    chronyc sources                    # is GPS0 there, and what is its reach?
    systemctl status gpsd.service      # is gpsd actually running?
    sudo ntpshmmon -n 3                # are samples reaching shared memory? (needs root)
    sudo journalctl -u gpsd -n 50      # what did gpsd make of the device?

`ntpshmmon` without `sudo` reports a permission error rather than an empty result — that
is the tool, not the receiver.

If two things declare `refclock SHM 0`, they fight over one segment. The provisioner
checks for that and warns; if you have hand-edited `/etc/chrony/chrony.conf` or dropped
another file in `conf.d/`, that is the first place to look.

### If you have no GPS receiver

The step is optional and the node works without it. Until you have one:

* set the clock by hand from any known-good source before you need timed modes
* if the node sees a network at any point, let it sync then, and power-cycle as little
  as possible afterwards

### Position

`gpsd-clients` is installed, so with a receiver bound you can read position directly:

    cgps -s                            # live position, satellites, fix quality
    gpsmon                             # raw NMEA, for when you doubt the receiver

Be aware that **nothing else on the node consumes that position.** QMapShack does not
read gpsd (Chapter 12), and the mesh-to-GPX bridge takes peer positions from the
Meshtastic node database rather than from a local receiver (Chapter 11). The GPS is
there for time and for your own readout; it does not move a cursor on the map.

**Status: Proven on the sibling project's command node** — a GlobalSat BU-353N, cold
boot with no network reachable, `GPS0` selected at stratum 1 within about a minute, run
twice to be sure. The EmComm step is a hand-port of that one and has **not yet been run
on an EmComm node**. Checklist section 14 carries the same caveat.

## Chapter 5 — The RTL-SDR dongle: one radio, three programs

If you have an RTL-SDR, **three tools want it and only one can have it at a time**:
SatDump (Chapter 14), dump1090 (Chapter 15), and anything else you run by hand.

Symptoms of contention are unhelpful — usually "device not found" or "unable to open"
from whichever program lost, even though the dongle is plainly plugged in.

**The rule: stop one before starting the other.**

    # who has it right now
    sudo lsof /dev/bus/usb/*/* 2>/dev/null | grep -i rtl

    # stop the ADS-B receiver if it is holding the dongle
    sudo service dump1090-mutability stop

**Confirm the dongle is free and working:**

    rtl_test -t

This should report the tuner type and run without "device busy". If it cannot open the
device at all, see the note on the kernel DVB-T driver below.

### The kernel driver problem, already handled

Linux ships a DVB-T television driver that binds these dongles on sight and never lets
go. The provisioner blacklists it at `/etc/modprobe.d/emcomm-rtlsdr.conf`, which is read
when the device is plugged in, so the dongle comes up unclaimed and available.

**Status: Proven** — confirmed across an unplug/replug cycle and a cold boot with the
dongle attached from power-on.

The full explanation, including what to check if a dongle still will not open, is staged
at `~/EMCOMM_Data/SDR/dongle_arbitration.md` and readable from the document server.

---

# Part II — The tools

Each chapter follows the same shape: what the tool is for, what the provisioner already
did, how to start it, what you must set up yourself, and what is known to be shaky.

## Chapter 6 — JS8Call: HF keyboard messaging

**What it is for.** Sending short text messages over HF at signal levels where voice is
unusable. This is the tool that gets a message across a region when nothing else will.

**What the provisioner did.** Installed JS8Call and placed a desktop shortcut. If your
group supplied a `JS8Call.ini` profile, it was staged to `~/.config/JS8Call.ini` with
your node ID substituted in as `MyCall`. **On a stock clone no profile ships**, and the
provisioner logs a warning saying so — in that case JS8Call starts unconfigured and you
work through its own setup wizard.

**Starting it.** Desktop shortcut, or:

    js8call

**What you must set up yourself.**

1. **Audio devices.** Input and output must point at your radio interface, not the
   laptop's speakers and microphone. This is the single most common reason a new station
   hears nothing.
2. **Grid square.** Left empty deliberately in anything this project ships — fill in your
   own.
3. **PTT.** How the radio is keyed: CAT, a serial line, or VOX. This is radio-specific.
4. **The clock.** See Chapter 4, and read it before you conclude the radio is at fault.

**Files.** `~/.config/JS8Call.ini`.

**Status: Unproven.** Nothing has been keyed on-air from a provisioned node. The install
is a stock package install and there is no reason to expect trouble from it, but the
audio and PTT path is yours to confirm.

## Chapter 7 — ion2G: HF ALE

**What it is for.** Automatic Link Establishment — the radio scans a channel list,
sounds, and works out which frequency actually reaches a given station right now, rather
than you guessing by time of day.

**What the provisioner did.** Downloaded and extracted ion2G, set up Wine to run it, and
wrote a desktop entry that launches it under Wine. If your group supplied an
`ale_channels.zcp` channel plan, it was staged; none ships.

**Starting it.** Desktop shortcut, or:

    wine ~/EMCOMM_Data/ion2G/<the ion2G executable>

**What you must set up yourself.** The channel plan is the whole substance of ALE and it
is group- and region-specific. Without one, ion2G runs and does nothing useful.

**Files.** `~/EMCOMM_Data/ion2G/`.

**Worth knowing.** This is a Windows application under Wine. It is the least native thing
on the node, and if something behaves strangely that is the first place to look.

**Status: Unproven** beyond installing and launching.

## Chapter 8 — QLog: station log

**What it is for.** Logging contacts. In an exercise or an actual incident the log is
often the deliverable — who you reached, when, on what.

**What the provisioner did.** Installed QLog as a Flatpak and placed a desktop shortcut.

**Starting it.** Desktop shortcut, or:

    flatpak run io.github.foldynl.QLog

**What you must set up yourself.** Station callsign, grid and rig details, on first run.

**Worth knowing.** Being a Flatpak, QLog is sandboxed and does not see the filesystem the
way the other tools do. If you export a log and cannot find the file, that is why —
check the location QLog reports rather than assuming it landed in your home directory.

**Status: Installed.** Not exercised beyond launching.

## Chapter 9 — Direwolf: APRS and packet

**What it is for.** A software TNC. It turns your sound card into a packet modem, which
gets you APRS position beacons and AX.25 packet without buying a hardware TNC.

**What the provisioner did.** Installed Direwolf and **generated a working config** at
`~/.config/direwolf/direwolf.conf` with `MYCALL` set from your node ID. A copy is also
discoverable from `$HOME`, which is where Direwolf looks by default.

**Starting it.**

    direwolf

It prints what it is doing as it runs. Leave the terminal open — the output is the
diagnostic.

**What you must fix before it works.** Two lines in the config are placeholders, and
both are flagged in the file itself:

    ADEVICE plughw:1,0        # almost certainly not your interface
    PTT CM108                 # a guess, and a known-shaky one on Linux

1. **Find your real audio device:**

        arecord -l

   Then set `ADEVICE` to the matching `plughw:<card>,<device>`.

2. **Confirm PTT actually keys the radio.** CM108-style PTT over `/dev/hidraw*` has
   documented reliability problems on Linux. Watch the radio, not the screen — if it does
   not key, the alternatives are CAT control or a serial line, both of which Direwolf
   supports.

**Files.** `~/.config/direwolf/direwolf.conf`, and a copy at `~/direwolf.conf`.

**Status: Unproven.** The config is generated correctly and `MYCALL` is verified, but the
audio device is a placeholder and the PTT method is explicitly untested. **Do not assume
this station is transmitting until you have watched the radio key.**

## Chapter 10 — Meshtastic: the LoRa mesh

**What it is for.** Low-power, long-range text and position sharing among your own nodes.
No infrastructure, no licence, and it works where HF is overkill and cell is gone.

**What the provisioner did.** Installed the Meshtastic CLI into your user site (not a
virtual environment, so no activation step), added you to the `dialout` group so you can
open the radio's serial port, and staged a setup reference at
`~/EMCOMM_Data/Meshtastic/meshtastic_setup.md`.

**Starting it.** There is no GUI. The CLI talks to a board over USB:

    meshtastic --info          # who am I and what do I see
    meshtastic --nodes         # the peer table, as a readable table

**If `meshtastic` is not found**, log out and back in — see Chapter 2.

**Connecting a board.** Plug it in over USB. With exactly one serial device attached the
CLI finds it by itself.

> **With more than one serial device attached, it refuses to guess.** A node that also
> has a GPS puck or a radio interface plugged in will get:
>
>     Warning: Multiple serial ports were detected so one serial port must be specified with the '--port'.
>
> This is normal and not a fault. Name the port explicitly:
>
>     ls /dev/serial/by-id/*
>     meshtastic --port /dev/ttyACM0 --info

**⚠ `--info` prints your private key.** The output includes `security.privateKey` in
plaintext, along with the rest of the device config. Do not paste `--info` output into a
chat, an issue, or a support request without removing it. Use `--nodes` when you want to
see the mesh rather than the device.

**Status: Proven.** The CLI runs, connects and reports on real hardware.

## Chapter 11 — The mesh-to-GPX bridge

**What it is for.** Putting your mesh peers on the map. It reads the Meshtastic node
database on a timer and writes a GPX file that QMapShack can display.

**What the provisioner did.** Installed the bridge to `~/EMCOMM_Apps/mesh_to_gpx.py`,
added a `emcomm-mesh-gpx` wrapper on your `PATH`, and installed a user service. It is
only installed when **both** QMapShack and Meshtastic were selected — the bridge exists
solely to feed QMapShack, so on a node without it there is nothing to do.

**Running it once, by hand:**

    emcomm-mesh-gpx --once --dump

**Running it continuously:**

    systemctl --user start emcomm-mesh-gpx.service
    systemctl --user status emcomm-mesh-gpx.service

The service is **enabled but not started** by the provisioner, deliberately: your
`dialout` membership does not take effect until you log out and back in, so starting it
during provisioning would only produce a failure. It starts on its own at your next
login.

**Files.** The GPX is written to `~/EMCOMM_Data/Meshtastic/mesh_nodes.gpx`. Peers with
no position fix are skipped rather than placed at 0°/0°.

**Status: Proven** on a live 9-node mesh — four peers with positions, written to a valid
GPX and imported successfully. See Chapter 12 for the one surprise in displaying it.

## Chapter 12 — QMapShack: offline mapping

**What it is for.** The map. Terrain, routes, waypoints, and your mesh peers — all from
tiles stored on the disk, with no network.

**What the provisioner did.** Installed QMapShack, fetched map tiles for the operating
area defined in `configs/areas/<your-area>.json`, and staged a settings profile if your
group supplied one. Tiles land in `~/EMCOMM_Data/Offline_Maps/`.

**Starting it.** Desktop shortcut, or:

    qmapshack

**Loading a file** — a GPX from the mesh bridge, a track, a set of waypoints:

    qmapshack ~/EMCOMM_Data/Meshtastic/mesh_nodes.gpx

or drag the file onto the Workspace panel. Both work on any version; menu layouts differ
between releases, so prefer these.

### The thing that will confuse you

**QMapShack does not reload a file that changed on disk.** The mesh bridge rewrites the
GPX every couple of minutes, and QMapShack will keep showing the positions from whenever
you loaded it — indefinitely, with no indication that it is stale.

This is not a bug in either program. The bridge writes a new file and moves it into
place, which swaps the underlying file out from under anything holding the old one.

**To see current positions, remove the project from the Workspace and load the file
again.** If peers have not moved in a while, confirm you are looking at fresh data before
concluding the mesh is quiet — a stationary node and a stale file look identical.

**Status: Proven** — mesh peers imported and drawn from a bridge-generated GPX.

### If the map is blank

A blank map with warnings about empty filenames usually means a tile source is pointing
at a directory with no tiles in it — not that the file you loaded is wrong. Check that
`~/EMCOMM_Data/Offline_Maps/` actually contains tiles for the area you are looking at.

## Chapter 13 — Kiwix: the offline knowledgebase

**What it is for.** Wikipedia, medical references, repair manuals, survival material —
whatever you loaded — with no network. Of everything on the node this is the one most
likely to matter to someone who is not a radio operator.

**What the provisioner did.** Installed Kiwix and the `kiwix-tools`, downloaded the
configured ZIM archive into `~/EMCOMM_Data/Kiwix_ZIM/`, and registered it with the
library.

**Starting it.** Desktop shortcut, or:

    kiwix-desktop

**Adding more material.** Drop additional `.zim` files into `~/EMCOMM_Data/Kiwix_ZIM/`
and add them from within Kiwix. ZIM archives are large — a full Wikipedia with images is
tens of gigabytes — so check disk space before committing to one.

**Worth knowing.** The archive is a point-in-time snapshot. Note its date, because a
medical or technical reference several years stale is worth knowing about *before* you
rely on it in front of someone.

**Status: Installed and populated.**

## Chapter 14 — SatDump: weather satellite imagery

**What it is for.** Receiving weather satellites directly — your own imagery of your own
area, no internet, no third party.

**What the provisioner did.** Built SatDump 1.2.2 from source into `~/EMCOMM_Apps/`,
staged a curated orbital element set if your group supplied one, and **turned off
automatic element updates** so a deployed node never reaches out. See Chapter 19.

**Starting it.** Desktop shortcut, or:

    satdump

**Before a pass.**

1. **Free the dongle** — Chapter 5. If dump1090 has it, SatDump cannot.
2. **Check the element sets loaded.** SatDump reports this bottom-left at startup:

       8 TLEs loaded!

   A count of zero means it has no orbital data and cannot predict a pass. See
   Chapter 19.
3. **Check the Tracking tab lists satellites** with sensible pass times for your
   location. If passes look wrong, the elements are stale — again Chapter 19.

**Files.** `~/EMCOMM_Apps/SatDump/`, config and elements in `~/.config/satdump/`.

**Status: Proven** — builds under the provisioner, and the RTL-SDR appears in the
recorder with a live spectrum.

> **Element sets never refresh in the field.** This is deliberate — a deployed node
> should not reach out — but it means the elements are only as current as your last
> provisioning run. Chapter 19 is not optional reading if you plan to work satellites.

## Chapter 15 — dump1090: aircraft tracking

**What it is for.** ADS-B. Every aircraft in range, on a map, from the same dongle
SatDump uses. Useful for situational awareness and as a quick proof the SDR chain works
at all.

**What the provisioner did.** Installed `dump1090-mutability`, added the service user to
`plugdev` so it can open the dongle, and **deliberately left it out of the autostart
sequence**. It does not run until you start it.

**Starting it on demand:**

    sudo service dump1090-mutability start

**Stopping it** — do this before using SatDump:

    sudo service dump1090-mutability stop && sudo pkill -x dump1090-mutability

**Viewing.** The package installs a lighttpd site serving the aircraft map at

    http://<this-node>/dump1090/

Aircraft appear within seconds if any are in range.

> **Unlike the document server, this is not bound to loopback.** The reference library on
> port 8085 is reachable only from the machine itself; the ADS-B map is reachable from
> anything that can route to the node. On a shared or untrusted network, treat the fact
> that you are receiving as information you may not want to publish, and stop the service
> when you are not using it.
>
>     sudo ss -ltnp | grep lighttpd        # confirm what is listening, and on what

**Two switches, and both must be right.** `START_DUMP1090="yes"` in
`/etc/default/dump1090-mutability` decides whether the service may run at all; the
service command decides whether it is running now. A node where the first is `no` will
ignore a start request without an obvious error.

**Files.** Reference and setup notes at `~/EMCOMM_Data/ADSB/dump1090_setup.md`.

**Status: Proven** — 61 aircraft at 198 messages/sec on a provisioned node.

**Why it does not autostart.** It would hold the dongle from boot, and quietly deny it to
SatDump. Given one dongle and two consumers, the one that runs on demand is the one you
can choose not to run.

## Chapter 16 — CHIRP: programming the radios

**What it is for.** Loading channel plans into handhelds and mobiles. When a plan
changes, this is how the fleet gets it without anyone typing frequencies into a keypad.

**What the provisioner did.** Installed CHIRP and placed a desktop shortcut. If your
group supplied `analog_channels.csv`, it was staged; none ships.

**Starting it.** Desktop shortcut, or:

    chirp

**Worth knowing.**

* **Read from the radio before you write to it**, and save that read as a backup. A bad
  write can leave a radio needing a factory reset.
* Programming cables are serial devices, so the `dialout` group applies here too — see
  Chapter 2 if the radio is not seen.
* Cheap cables with counterfeit chipsets are a common failure and usually present as the
  radio simply not being detected.

**Status: Installed.** Not exercised against a radio from a provisioned node.

---

# Part III — Keeping the node running

## Chapter 17 — Re-running the provisioner

Re-running is safe and is the normal repair path. Every step is written to be run twice.

    cd ~/emcomm-field-node
    python3 deploy_emcomm_node_gui.py

**Never with `sudo`** — it refuses to start as root, on purpose. It asks for
administrator access on its own screen, after you have chosen what to run.

**Steps are independent.** Tick only what you need. If one step failed during the
original run, selecting just that step is enough — the ones that succeeded are
unaffected. A failing step no longer stops the steps after it.

**On cancelling.** Cancel stops a download, a map-tile fetch or a build immediately.
Package installation is deliberately allowed to finish: interrupting `apt` or `dpkg`
mid-transaction leaves the package system needing repair, which affects other software on
the machine and not just this run. The administrator-access screen says so before the run
starts.

## Chapter 18 — Reading the verification screen

After a run, the provisioner checks what actually landed on disk and shows a row per
check.

| | Means |
|---|---|
| green | the check passed |
| **warn** | something to look at, not necessarily wrong |
| **fail** | a real fault — act on it |

**Two things it does not do.** It never repairs anything, and **it checks the filesystem,
not behaviour.** A correctly-staged JS8Call profile says nothing about whether the radio
keys up. Nothing needing hardware in the loop is — or can be — covered there.

Warnings you should expect on a normal node, and which are not faults:

* **no serial device detected** — no Meshtastic board plugged in during provisioning,
  which is the usual case
* **`dialout` requires log out / log in** — correct, and expected until you do
* **mesh-to-GPX bridge not installed** — only a finding if you have QMapShack
* **document library empty** — expected until you add PDFs (Chapter 3)

## Chapter 19 — Refreshing SatDump's orbital elements

**Skip this chapter if you do not work satellites. Read all of it if you do.**

Automatic element updates are switched off on every node
(`tle_update_interval` = `Never` in `~/.config/satdump/settings.json`). This is
deliberate: a deployed node should not reach out to the internet on every launch.

**The consequence is that the elements never refresh in the field.** Whatever was staged
at provisioning time is what the node has, permanently, until it is provisioned again.
Re-provisioning is the refresh.

### How long a set stays usable

Not all objects age at the same rate:

| | Useful for | Why |
|---|---|---|
| Low-Earth satellites — the weather birds you actually want | **days; visibly wrong past about two weeks** | atmospheric drag |
| Geosynchronous and higher | months | negligible drag |

**A set is as stale as its low-Earth entries.** Refreshing only when the high-orbit
elements look old is refreshing far too late — and the low-orbit ones are exactly the
ones you are trying to receive.

### Checking what the node has

SatDump reports the count bottom-left at startup:

    8 TLEs loaded!

* **Zero** — no orbital data; it cannot predict a pass.
* **Fewer than you expect** — an entry failed to parse, or an object has decayed out of
  the catalogue. That is a curation problem, not a provisioning one.
* **Passes at implausible times** — the elements are stale.

### Refreshing

Element sets are refreshed on the machine that builds nodes, not in the field:

1. Replace `configs/satdump_tles.txt` in the repository with current elements for the
   objects your area actually works.
2. Sanity-check the count:

       grep -c '^1 ' configs/satdump_tles.txt

3. Re-provision the node, or re-run the SatDump step alone.

After the repository's set is updated, **any node still carrying the previous one reports
a warning on its next verification pass.** That is how you find which nodes are behind.

### If element sources are unreachable

The node still works — it flies the last set it was given, with accuracy degrading as
above. Capture a current set whenever the opportunity exists rather than when a refresh
is due. A file in hand is worth more than a source that may not answer.

## Chapter 20 — Known limitations

Stated plainly, because a manual that hides these is worse than one that does not exist.

**The dock-trigger autostart has never fired.** It is designed for a Panasonic Toughbook
CF-30 in a Havis dock and ships as a **future feature**. The provisioner installs it and
says so. Do not build an operating procedure around docking the laptop and expecting the
station to come up.

**Direwolf is not confirmed to transmit.** The audio device is a placeholder and the PTT
method is a guess with known reliability problems on Linux. See Chapter 9. **Watch the
radio key before you rely on this.**

**Offline time depends on hardware you supply.** A node with no GPS receiver has no
reachable time source and never synchronises — silently. The GPS step fixes it, but
only once you have a receiver and have named it in `configs/gps.conf`. See Chapter 4.
This matters most for JS8Call.

**Verification checks files, not behaviour.** See Chapter 18.

**The document library ships empty.** See Chapter 3.

**Nothing here covers operating the radios.** Frequencies, power, antennas, band
conditions and the rules that apply to you are outside this manual entirely.

---

## Appendix A — Command reference

    # identity
    cat /etc/emcomm/node.conf

    # reference library
    systemctl --user start emcomm-docs-server.service
    # then browse to http://127.0.0.1:8085

    # time
    chronyc tracking                   # Reference ID, Stratum, Leap status
    chronyc sources                    # is GPS0 there, and what is its reach?
    systemctl status gpsd.service      # is gpsd actually running?
    sudo ntpshmmon -n 3                # samples reaching shared memory (needs root)
    cgps -s                            # live position and fix quality

    # SDR dongle
    rtl_test -t
    sudo service dump1090-mutability start
    sudo service dump1090-mutability stop && sudo pkill -x dump1090-mutability

    # mesh
    meshtastic --nodes                 # peers; safe to share
    meshtastic --info                  # full config; CONTAINS YOUR PRIVATE KEY
    ls /dev/serial/by-id/*             # find a port when more than one is attached
    emcomm-mesh-gpx --once --dump
    systemctl --user start emcomm-mesh-gpx.service

    # applications
    js8call
    qmapshack ~/EMCOMM_Data/Meshtastic/mesh_nodes.gpx
    kiwix-desktop
    chirp
    satdump
    direwolf
    flatpak run io.github.foldynl.QLog

## Appendix B — When something does not work

Work down this list before deeper troubleshooting. Most faults are one of these.

1. **Command not found, right after provisioning** → log out and back in (Chapter 2).
2. **Cannot open a serial port** → same; `dialout` needs a fresh login.
3. **SDR "device not found" although it is plugged in** → something else has it
   (Chapter 5). Stop dump1090.
4. **Meshtastic will not pick a port** → more than one serial device attached; name it
   with `--port` (Chapter 10).
5. **JS8Call decodes nothing** → check the clock before the antenna (Chapter 4).
6. **`chronyc tracking` says `Not synchronised` with a GPS attached** → check
   `chronyc sources` for `GPS0`, then that `gpsd.service` is running. Reach `0` in
   the first minute after a cold start is normal (Chapter 4).
7. **Direwolf hears nothing, or does not key** → `ADEVICE` and `PTT` are placeholders
   (Chapter 9).
8. **Mesh peers have not moved in ages** → QMapShack is showing a stale file; reload it
   (Chapter 12).
9. **SatDump reports `0 TLEs loaded!`** → no orbital elements (Chapter 19).
10. **A provisioning step failed** → re-run the provisioner with only that step selected
   (Chapter 17).

---

## Appendix C — What this draft still needs

This is a **running draft**. It is kept in the repository and revised in place; the PDF
is rebuilt from it rather than edited. What follows is the working list, recorded here so
it survives between sessions.

### Not yet walked against a running node

No chapter has been followed start to finish on a provisioned machine with the hardware
attached. Paths, unit names and commands were read out of the provisioner and are
correct; the *procedures* around them are reasoned, not rehearsed.

Chapter 4 is the nearest thing to an exception: its procedure was worked out on real
hardware, but on the sibling project's node rather than an EmComm one.

### Kept in step with `main`

The manual tracks the provisioner, and the provisioner keeps moving. A change that
adds a step, renames a unit or moves a path invalidates a chapter, and a stale manual
is worse than a thin one. Revised so far:

| Landed on `main` | What it changed here |
|---|---|
| GPS time source (PR #54) | Chapter 4 rewritten from "not in this version" to a procedure; Chapter 1 gained the `/etc/` and `configs/` notes; Chapter 20, Appendix A and Appendix B updated; the Unproven table's time row restated |

### Chapters most likely to be wrong

Five chapters are written from outside — the software was installed and launched, but
never operated from a provisioned EmComm node by anyone who wrote this:

| Chapter | What needs an operator's eye |
|---|---|
| 4 — Time and position | the procedure is proven, but on the **sibling project's** node. Needs one run on an EmComm node to move from ported to confirmed |
| 6 — JS8Call | first-run setup order, and what a working audio configuration looks like |
| 7 — ion2G | how a channel plan is actually loaded, and what running ALE looks like |
| 8 — QLog | first-run fields, and where a Flatpak export really lands |
| 16 — CHIRP | the read-before-write procedure against a specific radio |

Corrections from someone who has used these on this hardware are worth more than anything
that can be inferred from the provisioner.

### Deliberately not covered

* **Operating the radios.** Frequencies, power, antennas, propagation and the rules that
  apply to you are outside this manual.
* **The slim-appliance step.** It removes software at build time rather than adding
  anything an operator uses. If a node is missing an application you expected, that step
  is where to look.
* **Building a node.** `README.md` and the `Pre-Deployment Config Checklist` own that.

### Figures

None yet. The rules for adding them are in the front matter, and they are not optional —
a field node's screen carries operator identity in more places than people expect, window
title bars included.

### How to revise this

    # edit the source
    docs/OPERATORS_MANUAL.md

    # rebuild the PDF
    python3 scripts/build_manual.py

The PDF is a build artifact and is not tracked. Every part, chapter and appendix starts
on its own page, so a chapter can be printed and handed over on its own.

---

*End of draft.*
