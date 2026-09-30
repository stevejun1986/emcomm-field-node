# EmComm Field Node — Operator's Manual

**Draft.** Describes the provisioner on `main`, which is **v1.0.5**. Everything the
manual covers is in that release: the GPS time source (Chapter 4), the radio interface
binding and the ModemManager exclusion (Chapter 9), and the offline map fixes
(Chapter 12). Where the manual and a release differ, it follows `main`.

---

## About this manual

This is the **operator's** manual: what to do with a node that has already been built.
It assumes the provisioner has run and the verification screen came back clean. If you
are building a node rather than using one, you want `README.md` and the
`Pre-Deployment Config Checklist` instead.

A copy can live on the node itself, served offline by its own document server — build
the PDF into `docs/` before provisioning and it gets there. See **Chapter 3 — The
reference library**.

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
| **Offline maps draw with no network** | on a VM: 20,000-tile pyramid, topographic layer drawing once selected, view on the operating area, UTM grid reading in meters |

### Unproven

Installed and configured, but never confirmed working end to end. Treat these as
"should work, verify before you rely on it":

| | Why it is unproven |
|---|---|
| **Dock-trigger autostart** | has never been observed to fire, on any hardware. Ships as a **future feature** by decision — see Chapter 20 |
| **Direwolf PTT** | comes from `PTT_DEVICE` and `PTT_METHOD` in `configs/radio.conf`: declared and path-checked, never keyed. With no declaration the node ships `PTT CM108`, which does not key a Digirig Mobile. See Chapter 9 |
| **Direwolf audio device** | comes from `ADEVICE` in `configs/radio.conf`. With no declaration it is the placeholder `plughw:1,0`, very likely wrong for your interface |
| **ModemManager exclusion** | the udev rule is written from your declaration and verification reads it back. On the nodes it has run on, ModemManager flagged the interface and did not claim it, so the rule is insurance; nothing has been observed keying a radio, with or without it. See Chapter 9 |
| **JS8Call operation** | no station profile ships; nothing has been keyed on-air from a provisioned node |
| **GPS time source, on an EmComm node** | proven on a separate node running the hand-ported equivalent of this step — cold boot, no network, stratum 1 in about a minute, twice. It has not been run on an EmComm node. See Chapter 4 |
| **Map layer drawing on first launch, on an EmComm node** | proven on a separate node running the hand-ported equivalent of this step — QMapShack 1.17.1, logged out and back in, nothing clicked, and the topographic layer drew over the operating area. The EmComm session, on a VM, predates that fix, and there the layer drew only once it had been selected. See Chapter 12 |
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

### Before you connect a radio

**Power the node on, and dock it, with no radio connected to the interface.**

A radio interface whose PTT hangs off a serial control line — RTS or DTR, which is how
most of them work — can assert that line the moment something opens the port. Opening it
is not the same as deciding to transmit, and several things open it without being asked:
Direwolf, JS8Call, ion2G, or the dock autostart launching one of those (Chapter 20).

A node coming up with a radio already attached can therefore key it, with no operator
involved and nothing on screen to say so. Get the node configured and the interface
paths settled first, then connect the radio — into a dummy load — and key it
deliberately. Chapter 9 has the order.

This costs nothing and it is the only way this arrangement bites.

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

**This manual is the exception, and it is the one document worth putting there first.**
Its markdown source ships in the provisioner's `docs/` directory; no PDF ships, in the
tarball or on a release. If you want it on the node, build it from the provisioner's
directory *before* you run:

    sudo apt install python3-reportlab       # once
    python3 scripts/build_manual.py

That writes `docs/EmComm-Operators-Manual.pdf`, and the run carries it into the library
with everything else.

Timing is the part that catches people: the provisioner copies what is in `docs/` at
the moment that step runs and does not come back for more. Put the manual there
afterwards and it goes to `~/EMCOMM_Data/PDF_Manuals/` by hand, or at the next run.

## Chapter 4 — Time and position

**Read this before relying on any timed mode.**

A field node's clock is a problem the moment it goes offline, and it is a problem that
announces itself as something else entirely.

`chrony`'s stock configuration lists internet NTP pools. A deployed node cannot reach
them, so it never synchronizes, and it says so only if you ask:

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
error, and never synchronizes. The provisioner handles all four; they are listed because
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

**Status: Proven, but not here.** The procedure above was worked out and confirmed on a
separate node running the hand-ported equivalent of this step — a GlobalSat BU-353N,
cold boot with no network reachable, `GPS0` selected at stratum 1 within about a minute,
run twice to be sure. It has **not yet been run on an EmComm node**. Checklist section
14 carries the same caveat.

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

### Connect the radio last

**Do not have a radio connected while the node boots, docks, or is being configured for
the first time.**

An interface whose PTT hangs off a serial control line — RTS or DTR — can assert that
line the moment something opens the port, before any software decides to transmit. And
several things open it without being asked: Direwolf, JS8Call, ion2G, or the dock
autostart launching one of those when you dock the laptop (Chapter 2).

The result is a transmitter keyed with no operator involved and nothing on screen saying
so. Work in this order:

1. interface plugged into the laptop, **nothing plugged into the radio**
2. settle the audio device and the device paths, and re-run the provisioning step
3. connect the radio, into a dummy load
4. key it deliberately and watch it

That order costs nothing and removes the only way this arrangement can bite you.

### What you must set before it works

Two values decide whether Direwolf can hear and transmit, and the provisioner writes
whatever you declared in `configs/radio.conf` rather than guessing:

    ADEVICE=plughw:CARD=Device,DEV=0
    PTT_DEVICE=/dev/serial/by-id/usb-Silicon_Labs_CP2102_...-if00-port0
    PTT_METHOD=RTS

1. **Find your real audio device:**

        arecord -l
        cat /proc/asound/cards

   Prefer the card **id** — the name in square brackets — over the index. Indices move
   when another USB audio device is attached.

2. **Find the PTT device, and name it by id:**

        ls -l /dev/serial/by-id/

   Not `/dev/ttyUSB0`. A GPS receiver and a radio interface both enumerate as `ttyUSB*`,
   and which one gets `ttyUSB0` depends on the order they were plugged in — so a numbered
   path can hand your GPS daemon the radio interface, or hand Direwolf the GPS.

> **`PTT CM108` will not key a Digirig Mobile**, and it is what this node ships with if
> you declare nothing. The Mobile is a USB hub carrying two devices: a CM108-based sound
> card and a CP2102 serial bridge. Its PTT is an open-collector switch on the CP2102's
> **RTS** line — so the chip that makes `PTT CM108` look right is the audio chip, and the
> keying lives somewhere else. Use `PTT_METHOD=RTS` with the serial path.

3. **Confirm PTT actually keys the radio.** Watch the radio, not the screen. The
   provisioner never opens the PTT device and neither does the verification screen — both
   only check the path exists, precisely because opening it is what transmits. Nothing
   short of watching the radio tells you this works.

### ModemManager is kept off the interface

ModemManager probes an unknown serial port by **writing AT commands to it**. Writing
opens the port, opening asserts RTS, and on an RTS-keyed interface that is a
transmission nobody asked for.

The Direwolf step writes a udev rule telling ModemManager to leave the declared
interface alone:

    /etc/udev/rules.d/99-emcomm-radio-no-modemmanager.rules

It matches on the serial number inside the `/dev/serial/by-id/` path you declared, so
it is written without scanning the bus or opening anything. **A numbered `PTT_DEVICE`
carries no serial, so no rule can be written for one** — the step warns and says why.
One more reason to declare the by-id path.

* **It applies the next time the interface is attached**, not to a port something is
  already holding. Connecting the radio last is what makes it land before anything can
  key.
* **Re-run the Direwolf step after changing interfaces.** The rule names one device. A
  rule naming an interface you no longer use protects nothing, and looks like it does.
* **To check it took**, with the interface attached:

        udevadm info -q property -n /dev/serial/by-id/usb-... | grep ID_MM_DEVICE_IGNORE

  `ID_MM_DEVICE_IGNORE=1` means udev applied it. Reading udev's database does not open
  the port. The verification row **ModemManager excluded from the radio interface**
  makes the same check, and warns when the rule names a different interface from the
  one `configs/radio.conf` declares.

**Files.** `~/.config/direwolf/direwolf.conf`, a copy at `~/direwolf.conf`, and the
udev rule above.

**Status: Unproven.** The config is generated correctly and `MYCALL` is verified, and the
audio device and PTT path are now whatever you declared rather than a guess — but
**nothing in this project has ever keyed a radio.** Verification reads the filesystem; it
cannot observe RF. Do not assume this station is transmitting until you have watched the
radio key.

## Chapter 10 — Meshtastic: the LoRa mesh

**What it is for.** Low-power, long-range text and position sharing among your own nodes.
No infrastructure, no license, and it works where HF is overkill and cell is gone.

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
group supplied one. Tiles land in `~/EMCOMM_Data/Offline_Maps/`. It also registered that
directory with QMapShack, switched the topographic layer on, and pointed the first view
at your area — see below.

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

**Status: Proven** — mesh peers imported and drawn from a bridge-generated GPX, and
offline maps drawing from local tiles with no network, on a VM: a 20,000-tile pyramid,
the view on the operating area, and the UTM grid reading in meters. **The layer drawing
on first launch with nothing clicked is proven on a separate node running the
hand-ported equivalent of this step, not yet on an EmComm node** — the EmComm session
predates the fix described below, and there the layer drew only once it had been
selected.

### Where the settings live

    ~/.config/QLandkarte/QMapShack.conf

**Not `QLandkarteGT`.** That is the predecessor project, and earlier versions of this
provisioner staged there — a directory QMapShack never opens. If your node has one, it
has never been in effect. Move anything you customized across by hand; the provisioner
will not do it for you, because a stale profile would overwrite a good one.

QMapShack rewrites this file every time it closes. Anything you set by hand while it is
running is lost on exit, so change settings in the application, not in the file.

### Three things have to be true before a map draws

Downloading tiles is not the same as QMapShack being able to see them, and the failure
modes look alike from the outside. In order:

| | If it is missing |
|---|---|
| `mapPath` names `~/EMCOMM_Data/Offline_Maps` | the Maps tab is **empty** — the sources are not even listed |
| a source is registered active | the Maps tab **lists** the sources and the canvas stays empty |
| the view is over your tiles | everything is listed and active and the screen is still blank, because you are looking somewhere else |

The provisioner writes all three. If you are staring at an empty canvas, that table tells
you which one to check, and the verification screen carries a row for each.

**A node provisioned before v1.0.4 misses the second one.** Linux Mint 22 ships
QMapShack 1.17.1, which reads the active source from the older `map/active` key; those
provisioners wrote only the `map2/…` group that later versions read. The sources list in
the Maps tab and nothing draws until one is selected. Re-running **App profiles + ALE
channel plan** writes both; tiles already on disk are untouched.

**Only the topographic layer is switched on.** Two active raster layers stack and the
upper one hides the lower, which reads as the lower one being broken. Satellite imagery
is listed and one click away in the Maps tab.

### The first view, and the grid

QMapShack's built-in default view is 12°E 49°N — central Europe. On a node provisioned
for anywhere else the first launch therefore opens on blank canvas, and the obvious move
— pan and zoom until you find your area — is the slow one, because every step asks the
map source for tiles that do not exist.

The provisioner seeds the view instead: centered on your operating area, at zoom 12, with
a grid set to your area's UTM zone. It only seeds. Once you have moved the view and
closed the application, your position is the saved one and the provisioner leaves it
alone on later runs.

**The grid is UTM, not USNG.** QMapShack has no US National Grid or MGRS support — its
grid takes a projection, not a grid system. UTM is what USNG is built on, so the lines
fall exactly where USNG's do; the labels read as meters within the zone rather than as
USNG 100 km square letters. A position is transcribable to USNG but not readable as one
off the screen. If you need true USNG strings, convert them — the map will not do it.

The basemap is USGS Topo, which is the ICS reference basemap already.

### If the map is blank

**Run the diagnostic first.** It is read-only, safe with QMapShack open, and it names
which of the four conditions is missing instead of leaving you to tell four identical
blank screens apart:

    python3 scripts/diagnose_qmapshack_maps.py

It ships with the provisioner, so it is already on the node.

A blank map with warnings about empty filenames usually means a tile source is pointing
at a directory with no tiles in it — not that the file you loaded is wrong. Check that
`~/EMCOMM_Data/Offline_Maps/` actually contains tiles for the area you are looking at.

Check where you are looking, too. Tiles cover your operating area and nothing else, so
a view over open ocean is blank for the same reason an empty directory is — and the two
look identical. If the map is blank right after a fresh provisioning run, work the table
above before concluding the tiles are missing: an empty Maps tab, a populated Maps tab
with nothing drawing, and a view in the wrong place are three different faults that
produce the same white screen.

**If the Maps tab lists a source, it is active, and the canvas is still blank at every
zoom**, open `~/EMCOMM_Data/Offline_Maps/*.tms` and look at the `ServerUrl`. It must
read `%1/%2/%3`, not `{z}/{x}/{y}`:

    <ServerUrl>file:///home/you/EMCOMM_Data/Offline_Maps/Offline_Tiles/topo/%1/%2/%3.png</ServerUrl>

QMapShack only understands the `{z}/{x}/{y}` form from **v1.20.0**, and Linux Mint 22.3
ships **1.17.1**. On the older build the braces are never substituted, every request asks
for a file literally named `{z}/{x}/{y}.png`, and you get a blank canvas with a "tiles
pending" count that never clears. One command fixes a node that has it:

    sed -i 's|{z}/{x}/{y}|%1/%2/%3|' ~/EMCOMM_Data/Offline_Maps/*.tms

Close QMapShack first — it rewrites its settings on exit. The diagnostic reports this
one by name.

**If panning is heavy on the CPU**, look at the same files for a `<Script>` block. That
is from an older provisioning run: QMapShack starts a JavaScript engine for every tile
it draws through one. A current `.tms` uses `<ServerUrl>` and declares the zoom range on
disk. Re-running the config step replaces it.

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

**On canceling.** Cancel stops a download, a map-tile fetch or a build immediately.
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
not behavior.** A correctly-staged JS8Call profile says nothing about whether the radio
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
  the catalog. That is a curation problem, not a provisioning one.
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

**Direwolf is not confirmed to transmit.** Nothing in this project has ever keyed a
radio. The audio device and PTT path are declared by you rather than guessed, but
declaring them is not the same as proving them, and verification reads the filesystem —
it cannot observe RF. See Chapter 9. **Watch the radio key before you rely on this.**

**A radio connected at boot or dock can be keyed without you.** An RTS- or DTR-keyed
interface asserts when something opens its port, and the dock autostart opens ports.
Connect the radio last. See Chapter 2 and Chapter 9.

**Offline time depends on hardware you supply.** A node with no GPS receiver has no
reachable time source and never synchronizes — silently. The GPS step fixes it, but
only once you have a receiver and have named it in `configs/gps.conf`. See Chapter 4.
This matters most for JS8Call.

**Verification checks files, not behavior.** See Chapter 18.

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

    # radio interface — none of these open the port
    ls -l /dev/serial/by-id/           # the path to declare as PTT_DEVICE
    arecord -l                         # the card to declare as ADEVICE
    udevadm info -q property -n /dev/serial/by-id/usb-... | grep ID_MM_DEVICE_IGNORE

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
7. **Direwolf hears nothing, or does not key** → check what `configs/radio.conf`
   declares; with no declaration, `ADEVICE` and `PTT` are placeholders (Chapter 9).
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
hardware, but not on an EmComm node.

### Kept in step with `main`

The manual tracks the provisioner, and the provisioner keeps moving. A change that
adds a step, renames a unit or moves a path invalidates a chapter, and a stale manual
is worse than a thin one. Revised so far:

| Landed on `main` | What it changed here |
|---|---|
| GPS time source (PR #54) | Chapter 4 rewritten from "not in this version" to a procedure; Chapter 1 gained the `/etc/` and `configs/` notes; Chapter 20, Appendix A and Appendix B updated; the Unproven table's time row restated |
| Radio interface binding (PR #57) | Chapter 9 rewritten: the audio device and PTT come from `configs/radio.conf`, `PTT CM108` does not key a Digirig Mobile, and "connect the radio last". Chapter 2 gained the boot/dock rule, Chapter 20 both. **Merged; shipped in v1.0.4. No radio has been keyed** |
| Offline map fixes (PR #56) | Chapter 12 gained where the settings actually live, the seeded first view, the UTM-not-USNG grid, and what a blank canvas means. **Merged; shipped in v1.0.3** |
| Map activation on QMapShack 1.17.1 (PR #59) | Chapter 12 gained the note on nodes provisioned before v1.0.4 and the re-run that fixes them. The first-launch claim moved to the Unproven table with its provenance stated, and the maps row now says it was a VM session. **Merged; shipped in v1.0.4** |
| ModemManager exclusion (PR #63) | Chapter 9 gained the udev rule, how to check it took, and why a numbered path gets none; Appendix A gained the radio-interface commands; the Unproven table gained a row, and its Direwolf rows now read from `configs/radio.conf`. **Merged; shipped in v1.0.4** |
| US spelling (v1.0.5) | Converted throughout. `Not synchronised` stays where it quotes `chronyc tracking`, because that is what the program prints. The front matter now describes v1.0.5 |
| No PDF ships | Chapter 3, this appendix, checklist §15 and `docs/README.md`: the operator builds the PDF with `scripts/build_manual.py` if they want one. Nothing is attached to a release |

### Chapters most likely to be wrong

Four chapters are written from outside — the software was installed and launched, but
never operated from a provisioned EmComm node by anyone who wrote this:

| Chapter | What needs an operator's eye |
|---|---|
| 4 — Time and position | the procedure is proven, but on other hardware. Needs one run on an EmComm node to move from ported to confirmed |
| 6 — JS8Call | first-run setup order, and what a working audio configuration looks like |
| 7 — ion2G | how a channel plan is actually loaded, and what running ALE looks like |
| 8 — QLog | first-run fields, and where a Flatpak export really lands |

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

The PDF is a build artifact and is not tracked: `docs/*.pdf` is gitignored, and **no
PDF ships** — not in the tarball, not on a release. The markdown source and this script
ship in the tarball, and an operator who wants the PDF builds it (Chapter 3). There is
one copy of the manual, and it is the markdown — the two-copies drift this script
exists to avoid cannot start.

Every part, chapter and appendix starts on its own page, so a chapter can be printed
and handed over on its own.

---

*End of draft.*
