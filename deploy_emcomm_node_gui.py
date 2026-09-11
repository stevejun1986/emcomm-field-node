#!/usr/bin/env python3
#
# EMCOMM Field Node provisioner
# Copyright (C) 2026  WSNQ705
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
#
"""
EMCOMM Field Node — Automated Provisioner (GUI)

Builds a standardized, offline-capable emergency-communications
workstation on a Linux laptop: positioning and time sync, HF/VHF digital
modes, offline mapping, an offline reference library, and packet/mesh
tooling. Intended for ARES/RACES/CERT-style volunteer groups, EOC
support positions, and field deployments where internet and cellular
service cannot be assumed.

Every stage is an independently checkable step on the options screen,
all unchecked by default, so a run can install just what a given node
needs. "Select All" performs a full build.

REQUIREMENTS
    sudo apt install python3-tk python3-requests

python3-tk is not bundled with Python on Debian/Ubuntu/Mint and the GUI
will not start without it. python3-requests is optional (downloads fall
back to curl).

RUNNING
Run from the directory containing this script, as your normal user —
NOT with sudo. It refuses to start as root. You are asked for your sudo
password once, in the window; it drives a temporary mode-0700 "askpass"
helper for the duration of the run and is deleted when the run ends. It
is never written anywhere else and never leaves the machine.

    python3 deploy_emcomm_node_gui.py

WHAT THIS SCRIPT EXPECTS ALONGSIDE IT
Configuration profiles and reference material are NOT bundled — each
group supplies its own. Every step degrades gracefully with a visible
warning when a file is absent, so a partial set is fine:

    configs/JS8Call.ini          JS8Call profile (see placeholders below)
    configs/QMapShack.conf       QMapShack profile (see placeholders below)
    configs/analog_channels.csv  CHIRP channel list for analog radios
    configs/ale_channels.zcp     ion2G HF ALE channel plan
    configs/satdump_tles.txt     Curated TLE set for SatDump
    scripts/fetch_map_tiles.py   Offline map tile fetcher for your area
    docs/*.pdf                   Reference library, served over loopback
    scripts/Packages/            Locally cached .deb packages

PROFILE PLACEHOLDERS
Shipped profiles must not carry a real callsign or an absolute home
path — a profile captured from a working install will carry both, and
every node built from it then transmits under someone else's identity.
Use these tokens instead; they are substituted at provisioning time:

    MYCALL_PLACEHOLDER  ->  the callsign entered at the prompt
    HOME_PLACEHOLDER    ->  the provisioning user's $HOME

LICENSING
Transmitting with JS8Call, ion2G HF ALE, Direwolf/APRS or on amateur
DMR requires a valid amateur radio licence. Receive-only use does not.
GMRS requires a GMRS licence; FRS, MURS and Meshtastic (915 MHz ISM) do
not. The callsign entered at provisioning is written into the JS8Call
profile — set a real, licensed callsign before transmitting.

KNOWN LIMITATIONS
  - Cancel takes effect BETWEEN steps, not mid-command. An apt-get or a
    SatDump build already running finishes (or fails) first.
  - Steps are not dependency-checked. Running "QLog / ion2G HF ALE"
    without "System packages" assumes wine and unzip are already
    installed. This is deliberate, so a single step can be re-run.
  - Long steps show only a spinner; their output is captured and shown
    in full only if the step fails.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Callable, Optional

# ===========================================================================
# Naming
#
# Every path, unit and file name in this provisioner derives from the
# constants below, so a group deploying under its own name edits this block
# and nothing else.
#
# Four namespaces, four conventions, each matching whatever reads it:
#
#   OPERATOR_PREFIX  $HOME directories an operator browses, possibly under
#                    stress. CamelCase with an underscore, which sorts them
#                    above Desktop/Documents in a file manager — deliberate:
#                    field data belongs at the top of the home folder, not
#                    buried under ~/.local/share.
#   STATE_DIR_NAME   hidden per-user state the operator never opens.
#   SYSTEM_DIR       system configuration. FHS-conventional lowercase.
#   UNIT_PREFIX      systemd units, udev-invoked scripts, the sudoers
#                    drop-in — anything read by system tooling. Lowercase
#                    with hyphens, which is systemd's own convention and the
#                    usual shape for an executable in /usr/local/bin.
# ===========================================================================

PROJECT         = "emcomm"
OPERATOR_PREFIX = "EMCOMM"
STATE_DIR_NAME  = "." + PROJECT
SYSTEM_DIR      = "/etc/" + PROJECT
UNIT_PREFIX     = PROJECT

DATA_DIR_NAME = OPERATOR_PREFIX + "_Data"
APPS_DIR_NAME = OPERATOR_PREFIX + "_Apps"

NODE_CONF        = SYSTEM_DIR + "/node.conf"
SUDOERS_FILE     = "/etc/sudoers.d/" + UNIT_PREFIX + "-automation"
AUTOSTART_UNIT   = UNIT_PREFIX + "-autostart.service"
DOCS_SERVER_UNIT = UNIT_PREFIX + "-docs-server.service"
AUTOSTART_SCRIPT = UNIT_PREFIX + "-autostart-sequence.sh"
DOCK_EVENT_SH    = "/usr/local/bin/" + UNIT_PREFIX + "-dock-event.sh"

try:
    import requests
except ImportError:
    requests = None


# ===========================================================================
# Low-level helpers (same behavior as the CLI port; logging goes through
# a callback instead of print(), and every "sudo" call routes through the
# askpass session so it works with no controlling terminal).
# ===========================================================================

class ProvisioningCancelled(Exception):
    """Raised between steps when the user hits Cancel."""


class ProvisioningError(Exception):
    """Raised when a step fails in a way the run cannot continue from."""


class SpinResult:
    """Mutable outcome flag yielded by Ctx.spin() — defaults to success;
    a check=False call that fails without raising should set .ok = False
    explicitly so the spinner resolves to the right glyph."""
    __slots__ = ("ok",)

    def __init__(self):
        self.ok = True


class AskpassSession:
    """
    Sets up a `sudo -A` helper so every `sudo` call in this script works
    from a GUI with no TTY, using a password collected once at startup.

    The password is written into a private (mode 0700) temp script that
    just echoes it back to sudo when sudo invokes it as $SUDO_ASKPASS.
    The file is removed as soon as the session ends (success, failure,
    or cancel — see the `with` usage in run_provisioning()).
    """

    def __init__(self, password: str):
        fd, path = tempfile.mkstemp(prefix=UNIT_PREFIX + "-askpass-", suffix=".sh")
        os.close(fd)
        self.path = Path(path)
        # Single-quote-safe embedding of the password.
        escaped = password.replace("'", "'\\''")
        self.path.write_text(f"#!/bin/sh\necho '{escaped}'\n")
        self.path.chmod(stat.S_IRWXU)  # 0700 — owner only

    def env(self) -> dict:
        e = os.environ.copy()
        e["SUDO_ASKPASS"] = str(self.path)
        return e

    def verify(self) -> bool:
        """Validate the password now, so bad input fails fast at startup
        rather than 30 seconds into package installation."""
        r = subprocess.run(["sudo", "-A", "-v"], env=self.env(),
                            capture_output=True, text=True)
        return r.returncode == 0

    def close(self):
        self.path.unlink(missing_ok=True)


@dataclass
class Ctx:
    """Shared state threaded through every step function."""
    home: Path
    user: str
    node_id: str
    askpass: AskpassSession
    log: Callable[[str, str], None]          # log(message, level)
    cancel_check: Callable[[], None]          # raises ProvisioningCancelled
    spin_start: Callable[[str], None]         # begin an animated "in progress" log line
    spin_stop: Callable[[bool], None]         # resolve it to a check mark / cross
    spin_progress: Callable[[str], None]      # update the trailing text of the active spin line
    data_dir: Path = field(init=False)
    satdump_installed: bool = False

    def __post_init__(self):
        self.data_dir = self.home / DATA_DIR_NAME
        # Nothing here has a terminal to prompt into (unzip's "replace
        # file?", apt's debconf dialogs, needrestart's service-restart
        # prompt). Without a way to answer, an interactive prompt is a
        # silent, uncancellable hang — see stdin=DEVNULL below for the
        # other half of this fix.
        os.environ["DEBIAN_FRONTEND"] = "noninteractive"

    @contextmanager
    def spin(self, label: str):
        """Wrap a long-running block with an animated log line:

            with ctx.spin("Installing core packages..."):
                ctx.sudo("apt", "install", "-y", *packages)

        Resolves to a check mark on normal exit, a cross on any raised
        exception. For a check=False call whose success is judged by
        return code rather than an exception, set `result.ok` yourself:

            with ctx.spin("Building SatDump...") as result:
                build_status = ctx.run([...], check=False).returncode
                result.ok = (build_status == 0)
        """
        self.cancel_check()
        self.spin_start(label)
        result = SpinResult()
        try:
            yield result
        except BaseException:
            self.spin_stop(False)
            raise
        else:
            self.spin_stop(result.ok)

    # -- process helpers -----------------------------------------------
    # All of these capture stdout+stderr (merged) instead of letting the
    # child inherit the launching terminal — nothing from apt/wine/git/
    # cmake/make prints to the console behind the GUI. On success the
    # captured text is simply discarded (the GUI's own curated ctx.log
    # messages are what's shown). On a hard failure (check=True raising
    # CalledProcessError), the captured output travels with the
    # exception and _run_provisioning() dumps it into the GUI log —
    # so failures get MORE diagnostic detail than before, not less.
    #
    # stdin is likewise always DEVNULL: any child that tries to prompt
    # interactively (unzip on a file conflict, a stray debconf dialog)
    # gets immediate EOF instead of blocking on input nothing can ever
    # supply — a GUI child has no terminal to prompt into, so a hang
    # here would otherwise be silent AND uncancellable, since Cancel
    # only checks between steps, not mid-command.
    def run(self, cmd, check=True, **kw):
        self.cancel_check()
        kw.setdefault("stdin", subprocess.DEVNULL)
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.STDOUT)
        kw.setdefault("text", True)
        return subprocess.run(cmd, check=check, **kw)

    def sudo(self, *args, check=True, **kw):
        self.cancel_check()
        kw.setdefault("stdin", subprocess.DEVNULL)
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.STDOUT)
        kw.setdefault("text", True)
        return subprocess.run(["sudo", "-A", *args], env=self.askpass.env(),
                               check=check, **kw)

    def sudo_write(self, path: str, content: str, mode: Optional[str] = None):
        self.cancel_check()
        result = subprocess.run(["sudo", "-A", "tee", path], input=content, text=True,
                                 env=self.askpass.env(), check=False,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise subprocess.CalledProcessError(result.returncode, result.args,
                                                 output=result.stderr)
        if mode:
            self.sudo("chmod", mode, path)

    def verify_checksum(self, file: Path, expected: str) -> bool:
        actual = hashlib.sha256(file.read_bytes()).hexdigest()
        if actual != expected:
            self.log(f"CHECKSUM MISMATCH for {file}", "err")
            self.log(f"  expected: {expected}", "err")
            self.log(f"  got:      {actual}", "err")
            file.unlink(missing_ok=True)
            return False
        self.log(f"Checksum verified: {file.name}", "ok")
        return True

    def download(self, url: str, dest: Path):
        """Download with progress. Call this from inside a `with ctx.spin(...)`
        block — progress is reported via spin_progress(), updating the
        active spin line's trailing text in place ("... 42%") rather than
        logging a new line per percent. Outside a spin block spin_progress
        is a harmless no-op (see ProvisionerGUI._spin_progress), so this
        still works, just silently — always wrap it in ctx.spin()."""
        self.cancel_check()
        if requests is None:
            self.run(["curl", "-L", "--progress-bar", url, "-o", str(dest)])
            return
        with requests.get(url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length", 0))
            done = 0
            last_pct = -1
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    self.cancel_check()
                    f.write(chunk)
                    done += len(chunk)
                    if total:
                        pct = done * 100 // total
                        if pct != last_pct:
                            self.spin_progress(f"{pct}%")
                            last_pct = pct


# --- literal payloads: written verbatim to disk, never interpreted here ---

AUTOSTART_SEQUENCE_SH = r"""#!/bin/bash
export DISPLAY=:0
export XAUTHORITY=$HOME/.Xauthority

mkdir -p "$HOME/@STATE_DIR@"
exec >> "$HOME/@STATE_DIR@/autostart.log" 2>&1

LOCKFILE="/tmp/@UNIT_PREFIX@-autostart.lock"
if [ -f "$LOCKFILE" ] && kill -0 "$(cat "$LOCKFILE")" 2>/dev/null; then
    echo "[!] Autostart already running (PID $(cat "$LOCKFILE")) — exiting."
    exit 0
fi
echo $$ > "$LOCKFILE"
trap 'rm -f "$LOCKFILE"' EXIT

echo "=== [$(date)] Dock Event Triggered - Starting Sequenced Launch ==="

echo "[1/6] Restacking Time & Positioning Services..."
sudo systemctl restart gpsd.socket
sudo systemctl restart chrony

COUNTER=0
SYNCED=false
while [ $COUNTER -lt 15 ]; do
    if chronyc tracking | grep "Leap status" | grep -q "Normal"; then
        SYNCED=true
        break
    fi
    sleep 1
    ((COUNTER++))
done
if [ "$SYNCED" = true ]; then
    echo "[+] Time engine synced after ${COUNTER}s."
else
    echo "[!] Time engine did NOT sync after ${COUNTER}s — continuing anyway."
fi

echo "[2/6] Spawning Station Log Window..."
if [ -f "$HOME/scripts/blotter.sh" ]; then
    x-terminal-emulator -e "$HOME/scripts/blotter.sh" &
fi
sleep 1

echo "[3/6] Starting ADS-B Radar Engine..."
if [ -d "$HOME/dump1090" ]; then
    tmux new-session -d -s @UNIT_PREFIX@-adsb "cd $HOME/dump1090 && ./dump1090 --interactive"
fi
sleep 1

echo "[*] Waiting for radio interface (ttyUSB/ttyACM)..."
RADIO_COUNTER=0
RADIO_FOUND=false
while [ $RADIO_COUNTER -lt 10 ]; do
    if ls /dev/ttyUSB* /dev/ttyACM* &> /dev/null; then
        RADIO_FOUND=true
        break
    fi
    sleep 1
    ((RADIO_COUNTER++))
done
if [ "$RADIO_FOUND" = true ]; then
    echo "[+] Radio interface detected after ${RADIO_COUNTER}s."
else
    echo "[!] No radio interface detected after ${RADIO_COUNTER}s — apps may launch without hardware."
fi

echo "[4/6] Launching JS8Call..."
if command -v js8call &> /dev/null; then
    js8call &
fi
sleep 2

echo "[5/6] Initializing ion2G HF ALE ..."
ION2G_EXE_PATH="$(cat "$HOME/@STATE_DIR@/ion2g_exe_path" 2>/dev/null)"
if [ -n "$ION2G_EXE_PATH" ] && [ -f "$ION2G_EXE_PATH" ]; then
    (cd "$(dirname "$ION2G_EXE_PATH")" && wine "$ION2G_EXE_PATH" &)
fi
sleep 2

echo "[6/6] Deploying QLog & QMapShack..."
if command -v qlog &> /dev/null; then
    qlog &
fi
if command -v qmapshack &> /dev/null; then
    qmapshack &
fi

echo "=== [$(date)] @OPERATOR_PREFIX@ Operational Stack Deployed ==="
"""

MESHTASTIC_SETUP_MD = """# Meshtastic Node Setup Reference

Run with the node connected via USB. Confirm the port first:

    ls /dev/serial/by-id/*
    meshtastic --info

## 1. Set region (REQUIRED before the node will transmit)

    meshtastic --set lora.region US

A node with no region set will not transmit at all. This is the single
most common reason a new node appears dead.

## 2. Channel and preset

Agree these with your group BEFORE deployment. Every node on the net
must match region, preset and channel settings or they will not hear
each other.

    Band    : 915 MHz ISM (US) - no amateur licence required
    Preset  : LONG_FAST (default; longest range, lowest data rate)
    Channel : your group's agreed channel name

    meshtastic --set lora.modem_preset LONG_FAST
    meshtastic --ch-set name YOUR_NET_NAME --ch-index 0

## 3. Position reporting

For emergency management this is usually wanted - it lets net control
see where field teams are:

    meshtastic --set position.gps_mode ENABLED

Disable it for any node where broadcasting a location is inappropriate
(a shelter, a private residence, a member who has not consented):

    meshtastic --set position.gps_mode DISABLED

## 4. Verify

    meshtastic --info
    meshtastic --nodes

## 5. Backup / restore config

Back up a known-good node and clone it to the rest of the fleet:

    meshtastic --export-config > node_backup.yaml
    meshtastic --configure node_backup.yaml

## NMEA position output (optional - for mapping integration)

The Serial Module can emit NMEA 0183: a $GNGGA sentence for the node's
own position, plus $GPWPL waypoint sentences for every mesh peer
reporting a valid position. Read at 38400 8N1.

    meshtastic --set serial.enabled true
    meshtastic --set serial.mode NMEA

UNVERIFIED: whether gpsd ingests $GPWPL peer waypoints usefully, and
whether QMapShack renders them. Test before relying on it for
situational awareness.

## Operating note: this is an open net

Anyone within range running a node configured for the same channel can
read the traffic on it. Treat the mesh as a public net: no personal
identifying information, no patient or medical details, and nothing
that would matter if it were overheard. Use it for coordination, not
for anything confidential.
"""


# ===========================================================================
# Provisioning steps — one function per independently-selectable component.
# ===========================================================================

def step_sudoers_and_node_id(ctx: Ctx):
    ctx.log("[*] Configuring passwordless sudo for core RF daemons...", "warn")
    sudoers_content = (
        f"{ctx.user} ALL=(ALL) NOPASSWD: /bin/systemctl restart gpsd.socket\n"
        f"{ctx.user} ALL=(ALL) NOPASSWD: /bin/systemctl restart gpsd\n"
        f"{ctx.user} ALL=(ALL) NOPASSWD: /bin/systemctl restart chrony\n"
        f"{ctx.user} ALL=(ALL) NOPASSWD: /bin/systemctl status gpsd.socket\n"
        f"{ctx.user} ALL=(ALL) NOPASSWD: /bin/systemctl status chrony\n"
    )
    ctx.sudo_write(SUDOERS_FILE, sudoers_content, mode="0440")
    ctx.log(f"[+] Service execution privileges set for {ctx.user}.", "ok")

    ctx.log(f"[+] Configuring Node ID: {ctx.node_id}", "ok")
    ctx.sudo("mkdir", "-p", SYSTEM_DIR)
    ctx.sudo_write(NODE_CONF, f"NODE_ID={ctx.node_id}\n")


def step_system_packages(ctx: Ctx):
    ctx.sudo("dpkg", "--add-architecture", "i386")

    with ctx.spin("Updating package lists...") as spin_result:
        updated = ctx.sudo("apt", "update", check=False).returncode == 0
        spin_result.ok = updated
    if updated:
        with ctx.spin("Upgrading installed packages..."):
            ctx.sudo("apt", "upgrade", "-y", check=False)

    packages = [
        "git", "curl", "wget", "build-essential",
        "gpsd", "gpsd-clients", "chrony", "tmux",
        "python3-pip", "python3-venv",
        "qmapshack", "kiwix", "kiwix-tools", "chirp", "js8call", "dirmngr",
        "wine", "wine32", "wine64", "winetricks", "subversion",
        "gnupg", "libdbus-1-dev", "python3-requests",
    ]
    with ctx.spin("Installing core packages (this can take a while)..."):
        ctx.sudo("apt", "install", "-y", *packages)

    os.environ["WINEPREFIX"] = str(ctx.home / ".wine")
    os.environ["WINEARCH"] = "win64"
    with ctx.spin("Initializing Wine prefix..."):
        ctx.run(["wineboot", "--init"], check=False)


def optional_slim_appliance(ctx: Ctx):
    """OPT-IN. Turns a general-purpose laptop into a single-purpose appliance.

    Deliberately does NOT remove an office suite: ICS forms (214, 309, 205)
    are routinely handled as documents, so LibreOffice earns its disk space
    on an emergency management node. Only chat/torrent/scanner extras that
    have no role here are removed.

    Disabling unattended updates suits a node that is imaged once and then
    deployed offline. It is the wrong choice for a volunteer's daily-driver
    laptop, which is exactly why this is a separate, unchecked step.
    """
    with ctx.spin("Removing preinstalled apps not used on this node..."):
        ctx.sudo("apt", "purge", "-y",
                 "hexchat", "transmission-*", "drawing", "simple-scan", check=False)
        ctx.sudo("apt", "autoremove", "-y", check=False)

    with ctx.spin("Disabling unattended update services..."):
        ctx.sudo("systemctl", "stop",
                 "mintupdate-automation-upgrade.timer",
                 "mintupdate-automation-upgrade.service", check=False)
        ctx.sudo("systemctl", "disable",
                 "mintupdate-automation-upgrade.timer",
                 "mintupdate-automation-upgrade.service", check=False)
        ctx.sudo("rm", "-f", "/etc/xdg/autostart/mintupdate.desktop", check=False)
    ctx.log("[!] Unattended updates disabled — patch this node manually before each deployment.", "warn")


def step_qlog_ion2g(ctx: Ctx):
    has_qlog = shutil.which("qlog") is not None
    flatpak_has_qlog = False
    if shutil.which("flatpak"):
        r = subprocess.run(["flatpak", "list"], capture_output=True, text=True)
        flatpak_has_qlog = "qlog" in r.stdout

    if not has_qlog and not flatpak_has_qlog:
        with ctx.spin("Installing QLog via Flatpak..."):
            if not shutil.which("flatpak"):
                ctx.sudo("apt", "install", "-y", "flatpak")
                ctx.sudo("flatpak", "remote-add", "--if-not-exists",
                         "flathub", "https://flathub.org/repo/flathub.flatpakrepo")
            ctx.run(["flatpak", "install", "-y", "flathub", "io.github.foldynl.QLog"])

    ion2g_url = "https://ion2g.app/software/ion2g-0.9.8.8-win64.zip"
    ion2g_sha256 = "0a358f0124d038b4ee50e124a52801b2067e9515eed7f2b65985e9425e4965af"
    temp_zip = Path("/tmp/ion2g.zip")
    ion2g_install_dir = ctx.home / APPS_DIR_NAME / "ion2G"

    with ctx.spin("Downloading ion2G package..."):
        ctx.download(ion2g_url, temp_zip)

    if not ctx.verify_checksum(temp_zip, ion2g_sha256):
        ctx.log("[!] Aborting ion2G install — integrity check failed.", "err")
    else:
        ion2g_install_dir.mkdir(parents=True, exist_ok=True)
        with ctx.spin("Extracting ion2G package..."):
            # -o: overwrite without prompting — safe to re-run this step
            # (e.g. while testing) against a directory from a prior run.
            ctx.run(["unzip", "-q", "-o", str(temp_zip), "-d", str(ion2g_install_dir)])
        (ctx.home / STATE_DIR_NAME).mkdir(parents=True, exist_ok=True)
        ion2g_exe = next(
            (p for p in ion2g_install_dir.rglob("*")
             if p.is_file() and p.name.lower() == "ion2g.exe"),
            None,
        )
        if ion2g_exe:
            (ctx.home / STATE_DIR_NAME / "ion2g_exe_path").write_text(str(ion2g_exe) + "\n")
            ctx.log(f"[+] ion2G executable located at: {ion2g_exe}", "ok")
        else:
            ctx.log("[!] Warning: ion2g.exe not found after extraction!", "err")
        ctx.log(f"[+] ion2G extracted to {ion2g_install_dir}", "ok")

    temp_zip.unlink(missing_ok=True)


def step_maps_fetch(ctx: Ctx):
    (ctx.data_dir / "Offline_Maps").mkdir(parents=True, exist_ok=True)
    fetch_script = Path("scripts/fetch_map_tiles.py")
    if fetch_script.is_file():
        with ctx.spin("Executing regional tile fetch..."):
            ctx.run([sys.executable, str(fetch_script)], check=False)
    else:
        ctx.log("[!] Error: scripts/fetch_map_tiles.py not found in repo!", "err")


def step_kiwix_zim(ctx: Ctx):
    (ctx.data_dir / "Kiwix_ZIM").mkdir(parents=True, exist_ok=True)
    kiwix_med_url = "https://download.kiwix.org/zim/zimit/medlineplus.gov_en_all_2025-01.zim"
    kiwix_med_sha256 = "3ce8933e8b249cf685f5f108e8f10d9a7e3becb2ee4e91f3aedb5b0cf4d2e596"
    zim_dest = ctx.data_dir / "Kiwix_ZIM" / Path(kiwix_med_url).name

    kiwix_zim_ok = True
    with ctx.spin("Downloading Kiwix ZIM package..."):
        ctx.download(kiwix_med_url, zim_dest)
    if not ctx.verify_checksum(zim_dest, kiwix_med_sha256):
        ctx.log("[!] ZIM package failed integrity check — removed.", "err")
        kiwix_zim_ok = False

    kiwix_lib = ctx.home / ".local" / "share" / "kiwix" / "library.xml"
    kiwix_lib.parent.mkdir(parents=True, exist_ok=True)

    if not kiwix_zim_ok:
        ctx.log("[!] Skipping Kiwix registration — ZIM failed integrity check.", "warn")
    elif shutil.which("kiwix-manage"):
        ctx.run(["kiwix-manage", str(kiwix_lib), "add", str(zim_dest)])
        ctx.log(f"[+] Registered ZIM with Kiwix library: {kiwix_lib}", "ok")
    else:
        ctx.log("[!] kiwix-manage not found — ZIM not registered. Add manually via the app.", "warn")

    ctx.log("[+] Data acquisition pipeline executed.", "ok")
    (ctx.data_dir / "Kiwix_ZIM" / "README.txt").write_text(
        "Place offline .zim files (e.g., Wikipedia, Medical, Survival) into this folder.\n"
    )


def step_docs_server(ctx: Ctx):
    (ctx.data_dir / "PDF_Manuals").mkdir(parents=True, exist_ok=True)
    docs_dir = Path("docs")
    if docs_dir.is_dir():
        for pdf in docs_dir.glob("*.pdf"):
            shutil.copy(pdf, ctx.data_dir / "PDF_Manuals" / pdf.name)
        ctx.log("[+] Field manuals copied from local repo.", "ok")
    else:
        ctx.log("[!] Error: docs/ directory not found — running outside the cloned repo?", "err")

    docs_server_unit = ctx.home / ".config" / "systemd" / "user" / DOCS_SERVER_UNIT
    docs_server_unit.parent.mkdir(parents=True, exist_ok=True)
    docs_server_unit.write_text(f"""[Unit]
Description={OPERATOR_PREFIX} Local Document Server (PDF Manuals, loopback only)
After=network.target

[Service]
Type=simple
WorkingDirectory={ctx.data_dir}/PDF_Manuals
ExecStart=/usr/bin/python3 -m http.server 8085 --bind 127.0.0.1
Restart=on-failure

[Install]
WantedBy=default.target
""")
    ctx.run(["systemctl", "--user", "daemon-reload"])
    ctx.run(["systemctl", "--user", "enable", DOCS_SERVER_UNIT])
    ctx.run(["systemctl", "--user", "start", DOCS_SERVER_UNIT])
    ctx.log("[+] Document server enabled — http://127.0.0.1:8085", "ok")


def step_config_profiles(ctx: Ctx):
    (ctx.data_dir / "Offline_Maps").mkdir(parents=True, exist_ok=True)
    for d in (ctx.home / ".config" / "QLandkarteGT", ctx.home / ".config" / "qlog",
              ctx.home / ".local" / "share" / "CHIRP"):
        d.mkdir(parents=True, exist_ok=True)

    # QMapShack profile carries HOME_PLACEHOLDER tokens instead of absolute paths,
    # so it does not assume the operator's username.
    qms_conf = ctx.home / ".config" / "QLandkarteGT" / "QMapShack.conf"
    qms_src = Path("configs/QMapShack.conf")
    if qms_src.is_file():
        shutil.copy(qms_src, qms_conf)
        qms_conf.write_text(qms_conf.read_text().replace("HOME_PLACEHOLDER", str(ctx.home)))
    else:
        ctx.log(f"[!] Warning: {qms_src} not found — QMapShack left unconfigured.", "warn")

    # JS8Call stores its config as ~/.config/JS8Call.ini (Qt app name, case-sensitive).
    # The shipped profile carries MYCALL_PLACEHOLDER and HOME_PLACEHOLDER tokens so no
    # real callsign or absolute home path is baked into the repo.
    js8call_ini = ctx.home / ".config" / "JS8Call.ini"
    js8call_src = Path("configs/JS8Call.ini")
    if js8call_src.is_file():
        shutil.copy(js8call_src, js8call_ini)
        text = js8call_ini.read_text()
        text = text.replace("MYCALL_PLACEHOLDER", ctx.node_id)
        text = text.replace("HOME_PLACEHOLDER", str(ctx.home))
        js8call_ini.write_text(text)
        ctx.log(f"[+] JS8Call profile staged to {js8call_ini} (MyCall={ctx.node_id}).", "ok")
    else:
        ctx.log(f"[!] Warning: {js8call_src} not found — JS8Call left unconfigured.", "warn")

    chirp_csv = Path("configs/analog_channels.csv")
    if chirp_csv.is_file():
        chirp_dir = ctx.home / ".local" / "share" / "CHIRP"
        if not chirp_dir.is_dir():
            chirp_dir.mkdir(parents=True, exist_ok=True)
            ctx.log("[*] CHIRP directory did not exist — created it.", "warn")
        shutil.copy(chirp_csv, chirp_dir / chirp_csv.name)
        ctx.log("[+] UV-5RM codeplug staged to CHIRP runtime path.", "ok")

    # --- STAGE ALE CHANNEL PLAN FOR ion2G ---
    ale_src = Path("configs/ale_channels.zcp")
    if ale_src.is_file():
        dest_dir = ctx.data_dir / "ion2G"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(ale_src, dest_dir / "ale_channels.zcp")
        ctx.log(f"[+] ALE channel plan staged to {dest_dir}/", "ok")
    else:
        ctx.log(f"[!] Warning: {ale_src} not found — ALE channel plan not staged.", "warn")

    (ctx.data_dir / "Offline_Maps" / "Topo_Offline.tms").write_text(f"""<TMS>
<Layer idx="0">
  <Title>Topographic (Offline)</Title>
  <Script><![CDATA[(
  function createPath(z, x, y) {{
      return "file://{ctx.data_dir}/Offline_Maps/Offline_Tiles/Topo/" + z + "/" + x + "/" + y + ".png";
  }}
  )]]></Script>
</Layer>
</TMS>
""")

    (ctx.data_dir / "Offline_Maps" / "Satellite_Offline.tms").write_text(f"""<TMS>
<Layer idx="0">
  <Title>Satellite Imagery (Offline)</Title>
  <Script><![CDATA[(
  function createPath(z, x, y) {{
      return "file://{ctx.data_dir}/Offline_Maps/Offline_Tiles/Satellite/" + z + "/" + x + "/" + y + ".png";
  }}
  )]]></Script>
</Layer>
</TMS>
""")

    if qms_conf.is_file() and re.search(r"^mapPath=", qms_conf.read_text(), re.MULTILINE):
        text = qms_conf.read_text()
        if str(ctx.data_dir / "Offline_Maps") not in text:
            text = re.sub(r"^(mapPath=.*)$",
                           lambda m: f"{m.group(1)}, {ctx.data_dir}/Offline_Maps",
                           text, count=1, flags=re.MULTILINE)
            qms_conf.write_text(text)
            ctx.log(f"[+] Registered {ctx.data_dir}/Offline_Maps with QMapShack mapPath.", "ok")
        else:
            ctx.log(f"[+] {ctx.data_dir}/Offline_Maps already registered with QMapShack mapPath.", "ok")
    else:
        ctx.log("[!] QMapShack.conf mapPath line not found — .tms files may need manual import.", "warn")

    ctx.log("[+] Base config profiles successfully staged.", "ok")
    ctx.sudo("systemctl", "enable", "gpsd.socket")
    ctx.sudo("systemctl", "enable", "chrony")


def step_dock_trigger(ctx: Ctx):
    local_bin = ctx.home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    autostart_script = local_bin / AUTOSTART_SCRIPT
    autostart_script.write_text(
        AUTOSTART_SEQUENCE_SH
        .replace("@STATE_DIR@", STATE_DIR_NAME)
        .replace("@UNIT_PREFIX@", UNIT_PREFIX)
        .replace("@OPERATOR_PREFIX@", OPERATOR_PREFIX))
    autostart_script.chmod(0o755)
    ctx.log("[+] Autostart launcher installed.", "ok")

    systemd_user = ctx.home / ".config" / "systemd" / "user"
    systemd_user.mkdir(parents=True, exist_ok=True)
    (systemd_user / AUTOSTART_UNIT).write_text(f"""[Unit]
Description={OPERATOR_PREFIX} Sequenced Launch on Dock Event
After=graphical-session.target network.target

[Service]
Type=oneshot
ExecStart={ctx.home}/.local/bin/{AUTOSTART_SCRIPT}
Environment=DISPLAY=:0
Environment=XAUTHORITY={ctx.home}/.Xauthority

[Install]
WantedBy=default.target
""")
    ctx.run(["systemctl", "--user", "daemon-reload"])
    ctx.run(["systemctl", "--user", "enable", AUTOSTART_UNIT])
    ctx.log("[+] systemd user service installed and enabled.", "ok")

    dispatcher = f"""#!/bin/bash
ACTUAL_USER="{ctx.user}"
USER_UID=$(id -u "$ACTUAL_USER" 2>/dev/null)

if [ -z "$USER_UID" ]; then
    logger "{OPERATOR_PREFIX}: could not resolve UID for $ACTUAL_USER, aborting dock event"
    exit 1
fi

runuser -l "$ACTUAL_USER" -c "XDG_RUNTIME_DIR=/run/user/$USER_UID systemctl --user start {AUTOSTART_UNIT}"
logger "{OPERATOR_PREFIX}: dock event dispatched to user session (uid $USER_UID)"
"""
    ctx.sudo_write(DOCK_EVENT_SH, dispatcher, mode="+x")
    ctx.log("[+] Dock-event dispatcher installed.", "ok")

    udev_rule = (
        'ACTION=="add", SUBSYSTEM=="usb", ATTR{idVendor}=="05e3", '
        'ATTR{idProduct}=="0610", RUN+="' + DOCK_EVENT_SH + '"\n'
    )
    ctx.sudo_write("/etc/udev/rules.d/99-dock-trigger.rules", udev_rule)
    ctx.sudo("udevadm", "control", "--reload-rules")
    ctx.log("[+] udev dock-detection rule installed and reloaded.", "ok")

    ctx.sudo("loginctl", "enable-linger", ctx.user)
    ctx.log(f"[+] Linger enabled for {ctx.user}.", "ok")


def optional_direwolf(ctx: Ctx):
    with ctx.spin("Installing Direwolf...") as spin_result:
        status = ctx.sudo("apt", "install", "-y", "direwolf", check=False).returncode
        spin_result.ok = (status == 0)

    if status == 0:
        ctx.log("[+] Direwolf installed.", "ok")
        direwolf_config_dir = ctx.home / ".config" / "direwolf"
        direwolf_config_dir.mkdir(parents=True, exist_ok=True)

        (direwolf_config_dir / "direwolf.conf").write_text(f"""# --- {OPERATOR_PREFIX} Direwolf Base Config ---
# Callsign pulled from Node ID set during provisioning.
# WARNING: transmitting AX.25/APRS traffic requires a valid, licensed
# amateur radio callsign. Do NOT transmit under this value unless it
# is a real, currently-licensed callsign.
MYCALL {ctx.node_id}

# --- AUDIO DEVICE — UNVERIFIED PLACEHOLDER ---
# Card index (plughw:1,0) is a GUESS. Verify with `arecord -l` against
# your actual DigiRig before relying on this.
ADEVICE plughw:1,0
CHANNEL 0
MODEM 1200

# --- PTT — UNVERIFIED, KNOWN LINUX COMPATIBILITY ISSUES ---
# DigiRig's CM108-style PTT via /dev/hidraw* has documented reliability
# issues on Linux. Verify PTT actually keys the radio before field use.
PTT CM108

AGWPORT 8000
KISSPORT 8001
""")
        ctx.log(f"[+] Base Direwolf config written to {direwolf_config_dir}/direwolf.conf", "ok")
        home_symlink = ctx.home / "direwolf.conf"
        home_symlink.unlink(missing_ok=True)
        home_symlink.symlink_to(direwolf_config_dir / "direwolf.conf")
        ctx.log(f"[+] Symlinked to {ctx.home}/direwolf.conf for Direwolf's default search path.", "ok")
        ctx.log("[!] ADEVICE/PTT are UNVERIFIED placeholders — confirm against real hardware.", "warn")
    else:
        ctx.log("[!] Direwolf install failed — check log above.", "err")


def optional_meshtastic(ctx: Ctx):
    # Noble enforces PEP 668 (externally-managed environment), so a bare
    # 'pip install' is refused. Use an isolated venv rather than
    # --break-system-packages, to avoid touching system Python packages.
    meshtastic_venv = ctx.home / APPS_DIR_NAME / "meshtastic-venv"
    meshtastic_bin = meshtastic_venv / "bin" / "meshtastic"
    ok = False

    with ctx.spin("Installing Meshtastic Python CLI...") as spin_result:
        ctx.sudo("apt", "install", "-y", "python3-venv", "python3-full", check=False)
        ctx.run([sys.executable, "-m", "venv", str(meshtastic_venv)], check=False)
        ctx.run([str(meshtastic_venv / "bin" / "pip"), "install", "--upgrade", "pip"], check=False)
        pip_status = ctx.run([str(meshtastic_venv / "bin" / "pip"), "install", "meshtastic"],
                              check=False).returncode
        ok = (pip_status == 0) and os.access(meshtastic_bin, os.X_OK)
        spin_result.ok = ok

    if not ok:
        ctx.log("[!] Meshtastic CLI install failed — check log above.", "err")
        return

    ctx.log(f"[+] Meshtastic CLI installed to {meshtastic_venv}", "ok")

    # Wrapper on PATH so the operator can just run 'meshtastic'
    local_bin = ctx.home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    wrapper = local_bin / "meshtastic"
    wrapper.write_text(f'#!/bin/bash\nexec "{meshtastic_venv}/bin/meshtastic" "$@"\n')
    wrapper.chmod(0o755)
    ctx.log("[+] Wrapper installed at ~/.local/bin/meshtastic", "ok")

    # Serial port access — Meshtastic nodes enumerate as ttyUSB*/ttyACM*.
    # Without dialout membership the CLI fails with a permission error.
    groups = subprocess.run(["id", "-nG", ctx.user], capture_output=True, text=True).stdout.split()
    if "dialout" not in groups:
        ctx.sudo("usermod", "-aG", "dialout", ctx.user)
        ctx.log(f"[!] Added {ctx.user} to 'dialout' group — LOG OUT AND BACK IN before using the CLI.", "warn")
    else:
        ctx.log(f"[+] {ctx.user} already in 'dialout' group.", "ok")

    # --- STAGE MESHTASTIC SETUP REFERENCE ---
    # Reference only. NOT auto-applied: applying config requires the
    # node physically connected, and channel settings must match whatever
    # the group agreed — not something a provisioner should decide blind.
    meshtastic_dir = ctx.data_dir / "Meshtastic"
    meshtastic_dir.mkdir(parents=True, exist_ok=True)
    (meshtastic_dir / "meshtastic_setup.md").write_text(MESHTASTIC_SETUP_MD)
    ctx.log(f"[+] Meshtastic setup reference staged to {meshtastic_dir}/", "ok")
    ctx.log("[!] Meshtastic node config is NOT applied automatically — see the staged reference.", "warn")


def optional_satdump(ctx: Ctx):
    satdump_version = "1.2.2"
    satdump_deb_local = Path(f"scripts/Packages/satdump_{satdump_version}_ubuntu_24.04_amd64.deb")
    satdump_deb_sha256 = "68672f0d1bb76d5646d02ad2cbbeaa7549bed5ecedf4ee27d0d8467e72cf3221"

    if satdump_deb_local.is_file():
        satdump_deb_local.chmod(0o644)
        checksum_ok = ctx.verify_checksum(satdump_deb_local, satdump_deb_sha256)
        deb_install_ok = False
        if checksum_ok:
            with ctx.spin(f"Installing SatDump {satdump_version} from local repo cache...") as spin_result:
                deb_install_ok = ctx.sudo("apt", "install", "-y", str(satdump_deb_local), check=False).returncode == 0
                spin_result.ok = deb_install_ok
        if deb_install_ok:
            ctx.log("[+] SatDump installed via local .deb.", "ok")
            ctx.satdump_installed = True
        else:
            ctx.log("[!] Local .deb install/verification failed — falling back to source build...", "warn")
    else:
        ctx.log(f"[!] No local .deb found at {satdump_deb_local} — falling back to source build...", "warn")

    if not ctx.satdump_installed:
        with ctx.spin("Installing SatDump build dependencies..."):
            ctx.sudo("apt", "install", "-y",
                     "git", "build-essential", "cmake", "g++", "pkgconf", "libfftw3-dev", "libpng-dev",
                     "libtiff-dev", "libjemalloc-dev", "libcurl4-openssl-dev", "libsqlite3-dev",
                     "librtlsdr-dev", "libhackrf-dev", "libairspy-dev", "libairspyhf-dev",
                     "libdbus-1-dev", "libgl1-mesa-dev", "libpulse-dev", "libusb-1.0-0-dev",
                     "freeglut3-dev", "libglfw3-dev", "libzen-dev", "libmediainfo-dev", check=False)

            for pkg in ("libvolk-dev", "libvolk2-dev", "libvolk1-dev"):
                if ctx.sudo("apt", "install", "-y", pkg, check=False).returncode == 0:
                    break
            ctx.sudo("apt", "install", "-y", "libnng-dev", check=False)

        satdump_dir = ctx.home / APPS_DIR_NAME / "SatDump"
        if not satdump_dir.is_dir():
            with ctx.spin("Cloning SatDump source..."):
                ctx.run(["git", "clone", "https://github.com/SatDump/SatDump.git", str(satdump_dir)])

        build_dir = satdump_dir / "build"
        build_dir.mkdir(parents=True, exist_ok=True)
        build_status = 1
        with ctx.spin("Building SatDump (this can take several minutes)...") as spin_result:
            if ctx.run(["cmake", ".."], cwd=build_dir, check=False).returncode == 0:
                nproc = str(os.cpu_count() or 1)
                build_status = ctx.run(["make", f"-j{nproc}"], cwd=build_dir, check=False).returncode
            spin_result.ok = (build_status == 0)

        if build_status == 0:
            ctx.log(f"[+] SatDump built successfully at {build_dir}", "ok")
            ctx.satdump_installed = True
        else:
            ctx.log("[!] SatDump build failed — retry manually:", "err")
            ctx.log(f"    cd {build_dir} && cmake .. && make -j$(nproc)", "err")

    if ctx.satdump_installed:
        ctx.log("[*] Deploying SatDump TLE configuration...", "warn")
        satdump_config_dir = ctx.home / ".config" / "satdump"
        satdump_config_dir.mkdir(parents=True, exist_ok=True)

        tle_src = None
        base_depth = len(Path("configs").resolve().parts)
        for dirpath, dirnames, filenames in os.walk("configs"):
            depth = len(Path(dirpath).resolve().parts) - base_depth
            if depth >= 2:
                dirnames[:] = []
            for fn in filenames:
                if re.search(r"tles.*\.txt$", fn, re.IGNORECASE):
                    tle_src = Path(dirpath) / fn
                    break
            if tle_src:
                break

        if tle_src:
            shutil.copy(tle_src, satdump_config_dir / "satdump_tles.txt")
            ctx.log(f"[+] Synced {tle_src} -> {satdump_config_dir}/satdump_tles.txt", "ok")
        else:
            ctx.log("[!] Warning: No TLE .txt file found under configs/ directory.", "warn")

        satdump_global_cfg = Path("/usr/share/satdump/satdump_cfg.json")
        if satdump_global_cfg.is_file():
            ctx.sudo("cp", str(satdump_global_cfg), f"{satdump_global_cfg}.{PROJECT}-backup")
            text = satdump_global_cfg.read_text()
            text = re.sub(
                r'^( *)("http://celestrak\.org/NORAD/elements/gp\.php\?GROUP=active&FORMAT=tle")',
                r"\1// \1\2", text, flags=re.MULTILINE)
            text = re.sub(r"^( *)29499,", r"\1// 29499,", text, flags=re.MULTILINE)
            text = re.sub(r"^( *)35865 ", r"\1// 35865 ", text, flags=re.MULTILINE)
            ctx.sudo_write(str(satdump_global_cfg), text)
            ctx.log(f"[+] Disabled SatDump global bulk TLE fetch (backup: {satdump_global_cfg}.{PROJECT}-backup).", "ok")
        else:
            ctx.log(f"[!] SatDump global config not found at {satdump_global_cfg} — bulk TLE fetch NOT disabled.", "warn")

        satdump_settings = satdump_config_dir / "settings.json"
        if not satdump_settings.is_file():
            satdump_settings.write_text(json.dumps({
                "tle_settings": {"urls_to_fetch": None, "url_template": None, "tles_to_fetch": None}
            }, indent=4) + "\n")
            ctx.log("[+] Pre-seeded settings.json to disable user-level TLE auto-fetch.", "ok")
        else:
            ctx.log("[+] settings.json already exists — leaving as-is.", "ok")


def finalize(ctx: Ctx):
    desktop_dir = ctx.home / "Desktop"
    desktop_dir.mkdir(parents=True, exist_ok=True)

    apps = ["qmapshack.desktop", "org.kiwix.desktop.desktop", "js8call.desktop", "chirp.desktop"]
    if ctx.satdump_installed:
        apps.append("satdump.desktop")

    for app in apps:
        src = Path("/usr/share/applications") / app
        if src.is_file():
            dest = desktop_dir / app
            shutil.copy(src, dest)
            dest.chmod(0o755)
        else:
            ctx.log(f"[!] {src} not found — shortcut skipped.", "warn")

    qlog_desktop = Path("/var/lib/flatpak/exports/share/applications/io.github.foldynl.QLog.desktop")
    if qlog_desktop.is_file():
        dest = desktop_dir / qlog_desktop.name
        shutil.copy(qlog_desktop, dest)
        dest.chmod(0o755)
    else:
        ctx.log("[!] QLog Flatpak desktop file not found — shortcut skipped.", "warn")

    ion2g_exe_path_file = ctx.home / STATE_DIR_NAME / "ion2g_exe_path"
    ion2g_exe_path = ion2g_exe_path_file.read_text().strip() if ion2g_exe_path_file.is_file() else ""
    if ion2g_exe_path:
        ion2g_desktop = desktop_dir / "ion2G.desktop"
        ion2g_desktop.write_text(f"""[Desktop Entry]
Type=Application
Name=ion2G HF ALE
Exec=wine "{ion2g_exe_path}"
Path={Path(ion2g_exe_path).parent}
Icon=wine
Terminal=false
Categories=Network;
""")
        ion2g_desktop.chmod(0o755)
    else:
        ctx.log("[!] ion2g.exe path not recorded — desktop shortcut skipped.", "warn")

    for f in desktop_dir.glob("*.desktop"):
        ctx.run(["gio", "set", "--type=string", str(f), "metadata::trusted", "true"], check=False)
        checksum = hashlib.sha256(f.read_bytes()).hexdigest()
        ctx.run(["gio", "set", "--type=string", str(f), "metadata::xfce-exe-checksum", checksum], check=False)
        f.touch()


@dataclass(frozen=True)
class Component:
    id: str
    label: str
    fn: Callable[[Ctx], None]


# Every entry here is an independently-checkable box on the options screen,
# all unchecked by default — check only what you're testing. "Select All"
# reproduces a full, real run equivalent to deploy_emcomm_node.sh end to end.
COMPONENTS: list[Component] = [
    Component("sudoers_node_id", f"Sudo config + write callsign / node ID ({SYSTEM_DIR})", step_sudoers_and_node_id),
    Component("system_packages", "System packages (apt purge/update/upgrade + core list, Wine init) — SLOW", step_system_packages),
    Component("slim_appliance", "Remove preinstalled extras + disable auto-updates (appliance build)", optional_slim_appliance),
    Component("qlog_ion2g", "QLog station log + ion2G HF ALE (download & extract)", step_qlog_ion2g),
    Component("maps_fetch", "Offline map tile fetch (your operating area)", step_maps_fetch),
    Component("kiwix_zim", "Offline knowledgebase (Kiwix ZIM) download + registration", step_kiwix_zim),
    Component("docs_server", "Reference library sync + local document server", step_docs_server),
    Component("config_profiles", "App profiles (JS8Call / QMapShack / CHIRP) + ALE channel plan", step_config_profiles),
    Component("dock_trigger", "Dock-trigger autostart (systemd/udev) — optional hardware", step_dock_trigger),
    Component("direwolf", "Direwolf (AX.25 / APRS software TNC)", optional_direwolf),
    Component("meshtastic", "Meshtastic CLI (LoRa mesh node tooling)", optional_meshtastic),
    Component("satdump", "SatDump (weather satellite imagery via RTL-SDR) — SLOW", optional_satdump),
    Component("desktop_shortcuts", "Desktop shortcuts", finalize),
]


# ===========================================================================
# GUI
# ===========================================================================

class ProvisionerGUI(tk.Tk):
    LOG_TAGS = {
        "ok":   {"foreground": "#2e7d32"},
        "warn": {"foreground": "#b8860b"},
        "err":  {"foreground": "#c0392b"},
        "info": {"foreground": "#555555"},
        "spin": {"foreground": "#4a90d9"},
    }
    SPIN_GLYPHS = "|/-\\"
    SPIN_INTERVAL_MS = 120

    def __init__(self):
        super().__init__()
        self.title(f"{OPERATOR_PREFIX} Field Node Provisioner")
        self.geometry("820x700")
        self.minsize(700, 560)

        self.msg_queue: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.askpass: Optional[AskpassSession] = None
        self.component_vars: dict[str, tk.BooleanVar] = {}

        # Spinner state — all touched only from the main (Tk) thread, via
        # _drain_queue handling "spin_start"/"spin_stop" messages.
        self._spin_active = False
        self._spin_after_id: Optional[str] = None
        self._spin_line: Optional[int] = None
        self._spin_label: str = ""
        self._spin_suffix: str = ""
        self._spin_pos = 0

        self._build_options_screen()
        self.after(100, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- spinner: animated "in progress" log line ------------------------
    def _spin_start(self, label: str):
        self.log_widget.configure(state="normal")
        self._spin_line = int(self.log_widget.index("end-1c").split(".")[0])
        self._spin_label = label
        self._spin_suffix = ""
        self._spin_pos = 0
        self.log_widget.insert("end", f"  {self.SPIN_GLYPHS[0]} {label}\n", ("spin",))
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")
        self._spin_active = True
        self._spin_after_id = self.after(self.SPIN_INTERVAL_MS, self._spin_tick)

    def _spin_tick(self):
        if not self._spin_active:
            return
        self._spin_pos = (self._spin_pos + 1) % len(self.SPIN_GLYPHS)
        self._redraw_spin_line(self.SPIN_GLYPHS[self._spin_pos], "spin")
        self._spin_after_id = self.after(self.SPIN_INTERVAL_MS, self._spin_tick)

    def _spin_stop(self, ok: bool):
        self._spin_active = False
        if self._spin_after_id is not None:
            self.after_cancel(self._spin_after_id)
            self._spin_after_id = None
        self._spin_suffix = ""  # drop any trailing "... 87%" once resolved
        glyph = "✔" if ok else "✖"
        self._redraw_spin_line(glyph, "ok" if ok else "err")
        self._spin_line = None

    def _spin_progress(self, text: str):
        """Update the trailing text of the currently active spin line in
        place (e.g. "42%") — a no-op if no spin is active, so ctx.download()
        can call this unconditionally without checking state itself."""
        if self._spin_line is None:
            return
        self._spin_suffix = f" {text}"
        glyph = self.SPIN_GLYPHS[self._spin_pos] if self._spin_active else "✔"
        self._redraw_spin_line(glyph, "spin" if self._spin_active else "ok")

    def _redraw_spin_line(self, glyph: str, tag: str):
        if self._spin_line is None:
            return
        self.log_widget.configure(state="normal")
        self.log_widget.delete(f"{self._spin_line}.0", f"{self._spin_line}.end")
        self.log_widget.insert(f"{self._spin_line}.0",
                                f"  {glyph} {self._spin_label}{self._spin_suffix}", (tag,))
        self.log_widget.configure(state="disabled")

    def _queue_spin_start(self, label: str):
        self.msg_queue.put(("spin_start", label))

    def _queue_spin_stop(self, ok: bool):
        self.msg_queue.put(("spin_stop", ok))

    def _queue_spin_progress(self, text: str):
        self.msg_queue.put(("spin_progress", text))

    # -- screen 1: options ----------------------------------------------
    def _build_options_screen(self):
        self.options_frame = ttk.Frame(self, padding=16)
        self.options_frame.pack(fill="both", expand=True)

        ttk.Label(self.options_frame, text=f"{OPERATOR_PREFIX} Field Node Provisioner",
                  font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(self.options_frame,
                  text="Builds an offline-capable emergency communications workstation. "
                       "Every step below is optional — check only what this node needs, "
                       "or use Select All for a full build.",
                  foreground="#666666", wraplength=760, justify="left").pack(anchor="w", pady=(0, 12))

        form = ttk.Frame(self.options_frame)
        form.pack(fill="x", pady=4)

        ttk.Label(form, text="Node ID / Callsign:").grid(row=0, column=0, sticky="w", pady=4)
        self.node_id_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.node_id_var, width=30).grid(row=0, column=1, sticky="w", padx=8)

        ttk.Label(form, text="Sudo password:").grid(row=1, column=0, sticky="w", pady=4)
        self.sudo_pw_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.sudo_pw_var, show="*", width=30).grid(row=1, column=1, sticky="w", padx=8)

        comp_frame = ttk.LabelFrame(self.options_frame, text="Steps to run (all unchecked by default)")
        comp_frame.pack(fill="both", expand=True, pady=(14, 8))

        btn_row = ttk.Frame(comp_frame)
        btn_row.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Button(btn_row, text="Select All", command=self._select_all_components).pack(side="left")
        ttk.Button(btn_row, text="Select None", command=self._select_none_components).pack(side="left", padx=(6, 0))
        ttk.Label(btn_row, text="(\"Select All\" = a full node build)",
                  foreground="#888888").pack(side="left", padx=(10, 0))

        grid = ttk.Frame(comp_frame)
        grid.pack(fill="both", expand=True, padx=6, pady=(2, 6))
        for idx, comp in enumerate(COMPONENTS):
            var = tk.BooleanVar(value=False)
            self.component_vars[comp.id] = var
            row, col = divmod(idx, 2)
            ttk.Checkbutton(grid, text=comp.label, variable=var).grid(
                row=row, column=col, sticky="w", padx=6, pady=3)
        grid.columnconfigure(0, weight=1)
        grid.columnconfigure(1, weight=1)

        ttk.Label(self.options_frame,
                  text=("Run from the folder containing this script, as your normal user (not root). "
                        "The sudo password above stays in memory only, feeds a "
                        "private one-time askpass helper, and is wiped when the "
                        "run ends. Steps are independent and not dependency-checked, so "
                        "a single step can be re-run on its own — but e.g. the ion2G step "
                        "assumes wine and unzip are already installed."),
                  wraplength=760, foreground="#666666", justify="left").pack(anchor="w", pady=(4, 8))

        self.start_btn = ttk.Button(self.options_frame, text="Start Provisioning",
                                     command=self._on_start)
        self.start_btn.pack(anchor="e", pady=(4, 0))

    def _select_all_components(self):
        for var in self.component_vars.values():
            var.set(True)

    def _select_none_components(self):
        for var in self.component_vars.values():
            var.set(False)

    # -- screen 2: run ----------------------------------------------------
    def _build_run_screen(self):
        self.options_frame.destroy()

        self.run_frame = ttk.Frame(self, padding=16)
        self.run_frame.pack(fill="both", expand=True)

        self.step_label = ttk.Label(self.run_frame, text="Starting…", font=("TkDefaultFont", 11, "bold"))
        self.step_label.pack(anchor="w")

        self.progress = ttk.Progressbar(self.run_frame, maximum=len(COMPONENTS), mode="determinate")
        self.progress.pack(fill="x", pady=(4, 12))

        self.log_widget = scrolledtext.ScrolledText(self.run_frame, height=26, state="disabled",
                                                      background="#111111", foreground="#dddddd",
                                                      insertbackground="#dddddd", font=("Courier New", 9))
        self.log_widget.pack(fill="both", expand=True)
        for tag, cfg in self.LOG_TAGS.items():
            self.log_widget.tag_configure(tag, **cfg)

        btns = ttk.Frame(self.run_frame)
        btns.pack(fill="x", pady=(10, 0))
        self.cancel_btn = ttk.Button(btns, text="Cancel", command=self._on_cancel)
        self.cancel_btn.pack(side="left")
        self.close_btn = ttk.Button(btns, text="Close", command=self.destroy, state="disabled")
        self.close_btn.pack(side="right")

    # -- event handlers ---------------------------------------------------
    def _on_start(self):
        node_id = self.node_id_var.get().strip()
        password = self.sudo_pw_var.get()
        if not node_id:
            messagebox.showerror("Missing Node ID", "Node ID cannot be blank.")
            return
        if not password:
            messagebox.showerror("Missing password", "Sudo password is required.")
            return
        if os.geteuid() == 0:
            messagebox.showerror("Do not run as root",
                                  "Run this GUI as your normal user, not via sudo.")
            return

        selected_ids = {cid for cid, var in self.component_vars.items() if var.get()}
        if not selected_ids:
            if not messagebox.askyesno("No steps selected",
                                        "No steps are checked — this run will do nothing.\n"
                                        "Start anyway?"):
                return

        self.askpass = AskpassSession(password)
        self.sudo_pw_var.set("")  # drop the plaintext from the widget immediately
        if not self.askpass.verify():
            messagebox.showerror("Sudo authentication failed",
                                  "That password did not validate with sudo.")
            self.askpass.close()
            self.askpass = None
            return

        self._build_run_screen()

        ctx = Ctx(
            home=Path.home(),
            user=os.environ.get("USER") or os.getlogin(),
            node_id=node_id,
            askpass=self.askpass,
            log=self._queue_log,
            cancel_check=self._cancel_check,
            spin_start=self._queue_spin_start,
            spin_stop=self._queue_spin_stop,
            spin_progress=self._queue_spin_progress,
        )

        self.worker = threading.Thread(target=self._run_provisioning,
                                        args=(ctx, selected_ids), daemon=True)
        self.worker.start()

    def _on_cancel(self):
        if messagebox.askyesno("Cancel provisioning",
                                "Stop after the current step finishes?\n"
                                "(A command already running will not be killed mid-way.)"):
            self.cancel_event.set()
            self.cancel_btn.configure(state="disabled")

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Provisioning in progress",
                                        "A run is still active. Quit anyway?"):
                return
            self.cancel_event.set()
        if self.askpass:
            self.askpass.close()
        self.destroy()

    # -- worker thread ------------------------------------------------------
    def _cancel_check(self):
        if self.cancel_event.is_set():
            raise ProvisioningCancelled()

    def _queue_log(self, message: str, level: str = "info"):
        self.msg_queue.put(("log", message, level))

    def _run_provisioning(self, ctx: Ctx, selected_ids: set):
        try:
            for i, comp in enumerate(COMPONENTS, start=1):
                self._cancel_check()
                self.msg_queue.put(("step", i, comp.label))
                if comp.id not in selected_ids:
                    self._queue_log(f"===== {comp.label} — skipped (not selected) =====", "info")
                    continue
                self._queue_log(f"===== {comp.label} =====", "info")
                comp.fn(ctx)
            self.msg_queue.put(("done", True, ctx.node_id))
        except ProvisioningCancelled:
            self.msg_queue.put(("cancelled", None, None))
        except subprocess.CalledProcessError as e:
            cmd_str = " ".join(str(c) for c in e.cmd) if isinstance(e.cmd, (list, tuple)) else str(e.cmd)
            self._queue_log(f"[!] Command failed: {cmd_str} (exit {e.returncode})", "err")
            output = (e.output or "").rstrip()
            if output:
                lines = output.splitlines()
                if len(lines) > 40:
                    self._queue_log(f"  ... ({len(lines) - 40} earlier line(s) omitted) ...", "err")
                    lines = lines[-40:]
                for line in lines:
                    self._queue_log(f"  {line}", "err")
            self.msg_queue.put(("done", False, None))
        except Exception as e:  # noqa: BLE001 — surface anything unexpected to the user
            self._queue_log(f"[!] Unexpected error: {e!r}", "err")
            self.msg_queue.put(("done", False, None))
        finally:
            if ctx.askpass:
                ctx.askpass.close()

    # -- UI thread: drain queue --------------------------------------------
    def _drain_queue(self):
        try:
            while True:
                item = self.msg_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    _, message, level = item
                    self._append_log(message, level)
                elif kind == "spin_start":
                    _, label = item
                    self._spin_start(label)
                elif kind == "spin_stop":
                    _, ok = item
                    self._spin_stop(ok)
                elif kind == "spin_progress":
                    _, text = item
                    self._spin_progress(text)
                elif kind == "step":
                    _, i, name = item
                    self.progress["value"] = i - 1
                    self.step_label.configure(text=f"Step {i}/{len(COMPONENTS)}: {name}")
                elif kind == "done":
                    _, success, node_id = item
                    self.progress["value"] = len(COMPONENTS)
                    self.cancel_btn.configure(state="disabled")
                    self.close_btn.configure(state="normal")
                    if success:
                        self.step_label.configure(text=f"Provisioning complete — Node: {node_id}")
                        self._append_log(f"=== PROVISIONING COMPLETE for Node: {node_id} ===", "ok")
                    else:
                        self.step_label.configure(text="Provisioning failed — see log")
                elif kind == "cancelled":
                    self.step_label.configure(text="Cancelled")
                    self._append_log("=== Cancelled by user ===", "warn")
                    self.cancel_btn.configure(state="disabled")
                    self.close_btn.configure(state="normal")
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _append_log(self, message: str, level: str):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", message + "\n", level)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")


if __name__ == "__main__":
    if os.geteuid() == 0:
        print("Do not run this as root — run as your normal user.", file=sys.stderr)
        sys.exit(1)
    app = ProvisionerGUI()
    app.mainloop()
