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

import datetime
import getpass
import hashlib
import importlib.util
import json
import os
import platform
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
LOG_DIR_NAME    = STATE_DIR_NAME + "/logs"
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

# Offline map layers: (fetcher --layer value, QMapShack .tms filename, title).
#
# The fetcher writes tiles to Offline_Tiles/<layer>/, and the .tms files below
# are generated from the same tuple, so the two can no longer disagree. They
# did: the .tms files pointed at Offline_Tiles/Topo/ and .../Satellite/ while
# the fetcher wrote .../topo/ and .../imagery/, so QMapShack found nothing
# even when tiles were present.
TILE_FETCHER = Path("scripts/fetch_map_tiles.py")
AREA_DIR = Path("configs/areas")


def load_tile_fetcher():
    """Import the tile fetcher so the estimate shown to the operator and the
    tiering below use the SAME maths the download itself uses. A second
    implementation of the tile arithmetic would drift from the first, and the
    operator would be shown a number that is not what happens.

    Returns None if the script is missing or unloadable; callers degrade rather
    than crash.
    """
    try:
        spec = importlib.util.spec_from_file_location("fetch_map_tiles", TILE_FETCHER)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:      # noqa: BLE001 — a broken fetcher must not stop the GUI
        return None


SAMPLE_AREA = AREA_DIR / "example-area.json.sample"


def _bounds(spec: dict):
    """(north, south, east, west) as floats, or None if the spec lacks them."""
    try:
        return tuple(round(float(spec[k]), 6) for k in ("north", "south", "east", "west"))
    except (KeyError, TypeError, ValueError):
        return None


def is_unmodified_sample(spec: dict) -> bool:
    """True if `spec` has the shipped sample's exact bounds.

    The sample ships as .json.sample so it is never read as a live area, but a
    clone that predates that rename leaves a real example-area.json on disk.
    configs/areas/*.json is gitignored, so git never mentions it and the file
    is fetched in silence — someone else's city, at full detail, before the
    operator's own area.

    Compared by bounds rather than by filename: an operator who edited the
    sample in place has a legitimate area and must not have it skipped.
    """
    mine = _bounds(spec)
    if mine is None or not SAMPLE_AREA.is_file():
        return False
    try:
        theirs = _bounds(json.loads(SAMPLE_AREA.read_text(errors="replace")))
    except (OSError, ValueError):
        return False
    return theirs is not None and mine == theirs


def fetch_passes(spec: dict, tf) -> list:
    """[(description, bbox, (min_zoom, max_zoom))] for one operating area.

    An area given as a centre and radius is fetched in two passes: full street
    detail in the inner ring where a node actually navigates, and orientation
    zoom out to the full radius. Fetching the whole radius at street zoom is
    what turns a 150-mile area into a multi-hour, multi-gigabyte download —
    roughly 350,000 tiles against a public USGS endpoint.

    A hand-written rectangle has no centre, so it gets a single pass.
    """
    centre = spec.get("center") or {}
    lat, lon, radius = centre.get("lat"), centre.get("lon"), spec.get("radius_miles")
    if tf and lat is not None and lon is not None and radius:
        detail_r = min(float(spec.get("detail_radius_miles", tf.DETAIL_RADIUS_MILES)),
                       float(radius))
        passes = [(f"detail {detail_r:g} mi",
                   tf.box_from_center(float(lat), float(lon), detail_r), tf.DETAIL_ZOOMS)]
        if float(radius) > detail_r:
            passes.append((f"overview {float(radius):g} mi",
                           tf.box_from_center(float(lat), float(lon), float(radius)),
                           tf.OVERVIEW_ZOOMS))
        return passes
    try:
        box = {k: float(spec[k]) for k in ("north", "south", "east", "west")}
    except (KeyError, TypeError, ValueError):
        return []
    return [("area", box, tf.DETAIL_ZOOMS if tf else (10, 15))]


MAP_LAYERS = (
    ("topo",    "Topo_Offline.tms",      "Topographic (Offline)"),
    ("imagery", "Satellite_Offline.tms", "Satellite Imagery (Offline)"),
)

try:
    import requests
except ImportError:
    requests = None


# ===========================================================================
# Low-level helpers (same behavior as the CLI port; logging goes through
# a callback instead of print(), and every "sudo" call routes through the
# askpass session so it works with no controlling terminal).
# ===========================================================================

def _current_user() -> str:
    """The invoking user's login name.

    os.getlogin() reads the controlling terminal, which a GUI launched from a
    desktop session may not have — it raises OSError there, which would kill
    the run at Ctx construction before any step began.
    """
    return os.environ.get("USER") or getpass.getuser()


def hostname_from_node_id(node_id: str) -> str:
    """An RFC 1123 hostname label derived from a callsign / node ID.

    Node IDs are free text; a hostname may hold only letters, digits and
    hyphens, may not begin or end with one, and stops at 63 characters.
    Returns "" when nothing usable survives, in which case the caller must
    leave the system name alone rather than guess at one.
    """
    h = re.sub(r"[^A-Za-z0-9-]", "-", node_id)
    h = re.sub(r"-{2,}", "-", h).strip("-")
    return h[:63].strip("-")


def rewrite_hosts(lines: list, hostname: str) -> list:
    """/etc/hosts with its 127.0.1.1 entry pointed at `hostname`.

    Pure and idempotent: every other line survives byte for byte, duplicate
    127.0.1.1 entries collapse to one, and a file without such an entry gets
    one directly after the loopback line, which is where Debian puts it.
    """
    out, replaced = [], False
    for line in lines:
        if line.split()[:1] == ["127.0.1.1"]:
            if not replaced:
                out.append("127.0.1.1\t" + hostname)
                replaced = True
            continue
        out.append(line)
    if not replaced:
        idx = next((i for i, l in enumerate(out) if l.split()[:1] == ["127.0.0.1"]), -1)
        out.insert(idx + 1, "127.0.1.1\t" + hostname)
    return out


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


class RunLog:
    """A plain-text transcript of one provisioning run, written as it happens.

    The GUI log pane is ephemeral. It dies with the window, and its failure
    output is deliberately condensed so that 588 progress redraws cannot bury
    the one line that says what actually went wrong. The file is the copy that
    survives, and it is the UNCONDENSED one: every line the pane shows, plus
    the complete captured output of every command that failed, plus the
    verification rows and the final result.

    A run that goes wrong on a field laptop has to be reportable as one path.
    Reconstructing it from screenshots of a scrolled pane loses exactly the
    part that matters, every time.

    Nothing here raises. A transcript that cannot be written is reported once,
    in the pane, and the deployment carries on. The log is diagnostic; losing
    it must never cost the operator the node.
    """

    #: Level tag written into the file. Fixed width, so the message column
    #: lines up and `grep FAIL` finds every failure in one pass.
    TAGS = {"info": "INFO", "ok": " OK ", "warn": "WARN", "err": "FAIL",
            "spin": " >> ", "head": "----"}
    KEEP = 10          # run logs retained; older ones are pruned on open

    def __init__(self, directory: Path):
        self.path: Optional[Path] = None
        self.error: Optional[str] = None
        self.started = datetime.datetime.now()
        self._fh = None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / self.started.strftime("provision-%Y%m%d-%H%M%S.log")
            # Line buffered. A run killed part-way through -- power loss on
            # battery, a hard reboot, the lid closed on a laptop mid-install --
            # still leaves everything up to the last line on disk, and that is
            # precisely the run whose log is worth having.
            self._fh = path.open("w", encoding="utf-8", errors="replace", buffering=1)
            self.path = path
        except Exception as e:      # noqa: BLE001 -- see the class docstring
            self.error = str(e)
            return
        self._prune(directory)

    def _prune(self, directory: Path):
        """Keep the newest KEEP logs. A node reprovisioned repeatedly should
        not accumulate transcripts in a hidden directory nobody opens."""
        try:
            logs = sorted(directory.glob("provision-*.log"))
            for old in logs[:-self.KEEP]:
                old.unlink(missing_ok=True)
        except Exception:      # noqa: BLE001
            pass                    # tidying is optional; logging is not

    # -- writing --------------------------------------------------------
    def write(self, message: str, level: str = "info"):
        """One timestamped, tagged entry. Multi-line messages get a timestamp
        on every line, so the file stays sortable and greppable by time."""
        tag = self.TAGS.get(level, "INFO")
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        self._emit("".join("%s [%s] %s\n" % (stamp, tag, line)
                           for line in message.split("\n")))

    def raw(self, text: str):
        """A verbatim block -- captured output of a command, kept exactly as
        the command emitted it. Indented into a gutter so it is visibly not a
        line the provisioner wrote, but otherwise untouched: no condensing, no
        truncation. This is the whole point of the file."""
        self._emit("".join("             | %s\n" % line
                           for line in text.rstrip("\n").split("\n")))

    def banner(self, text: str):
        """Header/footer block, written without timestamps."""
        self._emit(text if text.endswith("\n") else text + "\n")

    def _emit(self, text: str):
        if self._fh is None:
            return
        try:
            self._fh.write(text)
        except Exception as e:      # noqa: BLE001 -- see the class docstring
            # Out of disk is a real field condition, and it is not the only
            # way a handle goes bad. Whatever it was, record why the transcript
            # stops and stop trying: this class exists to describe a failing
            # run, so it must not become one.
            self.error = str(e)
            self.close()

    def close(self):
        if self._fh is None:
            return
        try:
            self._fh.close()
        except Exception:      # noqa: BLE001 -- closing must not raise either
            pass
        self._fh = None

    # -- header / footer ------------------------------------------------
    def header(self, node_id: str, user: str, selected: list, skipped: list):
        try:
            pretty = next((l.split("=", 1)[1].strip().strip('"')
                           for l in Path("/etc/os-release").read_text().splitlines()
                           if l.startswith("PRETTY_NAME=")), "unknown")
        except OSError:
            pretty = "unknown"
        uname = os.uname()
        lines = [
            "=" * 72,
            "%s Field Node provisioner -- run log" % OPERATOR_PREFIX,
            "=" * 72,
            "Started    : %s" % self.started.strftime("%Y-%m-%d %H:%M:%S %Z").strip(),
            "Node ID    : %s" % node_id,
            "User       : %s" % user,
            "Host       : %s -- %s" % (uname.nodename, pretty),
            "Kernel     : %s %s" % (uname.sysname, uname.release),
            "Python     : %s (%s)" % (platform.python_version(), sys.executable),
            "Script     : %s" % Path(__file__).resolve(),
            # The provisioner reads configs/, docs/ and scripts/ by RELATIVE
            # path, so a run launched from the wrong directory finds none of
            # them and warns its way to a hollow node. Record where it ran.
            "Working dir: %s" % os.getcwd(),
            "",
            "Steps selected (%d of %d):" % (len(selected), len(selected) + len(skipped)),
        ]
        lines += ["    %s" % label for label in selected] or ["    (none)"]
        lines += ["", "Steps skipped (%d):" % len(skipped)]
        lines += ["    %s" % label for label in skipped] or ["    (none)"]
        lines += ["=" * 72, ""]
        self.banner("\n".join(lines))

    def footer(self, title: str, ran: int, skipped: int, failed_steps: list,
               passed: int, warned: int, failed: int):
        end = datetime.datetime.now()
        lines = ["", "=" * 72, "RESULT: %s" % title,
                 "    %d step(s) run, %d skipped" % (ran, skipped)]
        if failed_steps:
            lines.append("    %d step(s) failed:" % len(failed_steps))
            lines += ["        %s" % label for label in failed_steps]
        lines.append("Verification: %d passed, %d warning(s), %d failed"
                     % (passed, warned, failed))
        lines.append("Finished: %s  (elapsed %s)"
                     % (end.strftime("%Y-%m-%d %H:%M:%S"),
                        str(end - self.started).split(".")[0]))
        lines += ["=" * 72, ""]
        self.banner("\n".join(lines))


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
    ctx.log("[*] Configuring passwordless sudo for core RF daemons...", "info")
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

    _set_system_hostname(ctx)


def _set_system_hostname(ctx: Ctx):
    """Make the callsign / node ID the machine's name on the network.

    Two writes, and both are required. hostnamectl alone leaves /etc/hosts
    pointing 127.0.1.1 at the OLD name, and every later sudo call then stalls
    on "unable to resolve host" — so the loopback entry moves with it.

    This matters most for a fleet: nodes are built by imaging one master and
    cloning it, and without this every unit answers to the master's name.
    """
    hostname = hostname_from_node_id(ctx.node_id)
    if not hostname:
        ctx.log(f"[!] {ctx.node_id!r} has no characters valid in a hostname — "
                f"system name left unchanged.", "warn")
        return
    if hostname != ctx.node_id:
        ctx.log(f"[!] {ctx.node_id!r} is not a valid hostname; using {hostname!r} "
                f"as the system name.", "warn")

    # One-time backup, so a bad /etc/hosts is recoverable without a live USB.
    if not Path("/etc/hosts.%s.bak" % PROJECT).exists():
        ctx.sudo("cp", "/etc/hosts", "/etc/hosts.%s.bak" % PROJECT)

    ctx.sudo("hostnamectl", "set-hostname", hostname)

    try:
        existing = Path("/etc/hosts").read_text(errors="replace").splitlines()
    except OSError as e:
        ctx.log(f"[!] Could not read /etc/hosts ({e}) — 127.0.1.1 not updated. "
                f"sudo may stall on 'unable to resolve host'.", "err")
        return
    ctx.sudo_write("/etc/hosts", "\n".join(rewrite_hosts(existing, hostname)) + "\n")
    ctx.log(f"[+] System hostname set to {hostname} "
            f"(open terminals keep the old prompt until relaunched).", "ok")


def step_system_packages(ctx: Ctx):
    ctx.sudo("dpkg", "--add-architecture", "i386")

    with ctx.spin("Updating package lists...") as spin_result:
        updated = ctx.sudo("apt", "update", check=False).returncode == 0
        spin_result.ok = updated
    if updated:
        with ctx.spin("Upgrading installed packages...") as spin_result:
            upgraded = ctx.sudo("apt", "upgrade", "-y", check=False).returncode == 0
            spin_result.ok = upgraded
        if not upgraded:
            ctx.log("[!] apt upgrade did not complete — this node is being built on "
                    "packages that are not fully up to date.", "warn")

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
    with ctx.spin("Initializing Wine prefix...") as spin_result:
        wine_ok = ctx.run(["wineboot", "--init"], check=False).returncode == 0
        spin_result.ok = wine_ok
    if not wine_ok:
        ctx.log("[!] wineboot --init failed — the Wine prefix is not initialised, and "
                "ion2G will not run until it is.", "warn")


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
    with ctx.spin("Removing preinstalled apps not used on this node...") as spin_result:
        # apt exits 0 both for a package that is not installed and for a glob
        # that matches nothing, so a non-zero code here is a genuine failure
        # rather than "this machine never had it".
        purge_rc = ctx.sudo("apt", "purge", "-y",
                            "hexchat", "transmission-*", "drawing", "simple-scan",
                            check=False).returncode
        autoremove_rc = ctx.sudo("apt", "autoremove", "-y", check=False).returncode
        spin_result.ok = (purge_rc == 0 and autoremove_rc == 0)
    if purge_rc != 0:
        ctx.log(f"[!] Package removal exited {purge_rc} — some extras may remain.", "warn")
    if autoremove_rc != 0:
        ctx.log(f"[!] apt autoremove exited {autoremove_rc}.", "warn")

    with ctx.spin("Disabling unattended update services..."):
        ctx.sudo("systemctl", "stop",
                 "mintupdate-automation-upgrade.timer",
                 "mintupdate-automation-upgrade.service", check=False)
        disable_rc = ctx.sudo("systemctl", "disable",
                              "mintupdate-automation-upgrade.timer",
                              "mintupdate-automation-upgrade.service",
                              check=False).returncode
        ctx.sudo("rm", "-f", "/etc/xdg/autostart/mintupdate.desktop", check=False)

    # The claim below used to be made unconditionally. mintupdate is Mint's, so
    # on anything else these units do not exist, nothing is disabled, and an
    # operator was still told to patch manually because automatic updates were
    # off -- a success line outside the branch that earned it, and one that
    # leaves a node updating itself in the field when it says it will not.
    # The spinner is deliberately not tied to this: an absent unit is the
    # expected case off Mint, not a failure.
    if disable_rc == 0:
        ctx.log("[!] Unattended updates disabled — patch this node manually before each deployment.", "warn")
    else:
        ctx.log("[*] mintupdate automation units not present — nothing to disable. "
                "Expected on anything but Linux Mint; confirm this system's own "
                "update service is handled before deployment.", "info")


def step_qlog_ion2g(ctx: Ctx):
    has_qlog = shutil.which("qlog") is not None
    flatpak_has_qlog = False
    if shutil.which("flatpak"):
        r = subprocess.run(["flatpak", "list"], capture_output=True, text=True)
        flatpak_has_qlog = "qlog" in r.stdout

    if not has_qlog and not flatpak_has_qlog:
        rc = 1
        with ctx.spin("Installing QLog via Flatpak...") as spin_result:
            if not shutil.which("flatpak"):
                ctx.sudo("apt", "install", "-y", "flatpak")
            # Unconditional, and idempotent via --if-not-exists. Previously this
            # sat inside the "flatpak is missing" branch, so on a node that
            # already had flatpak the remote was never added and the install
            # below could only fail.
            ctx.sudo("flatpak", "remote-add", "--if-not-exists",
                     "flathub", "https://flathub.org/repo/flathub.flatpakrepo")
            # Through sudo. The remote above is a system installation, and a
            # user-invoked install against it needs a polkit authorisation that
            # a GUI with no agent cannot obtain — the failure reads "Flatpak
            # system operation Deploy not allowed for user". finalize() also
            # looks for the exported .desktop under /var/lib/flatpak, which
            # only exists for a system install.
            rc = ctx.sudo("flatpak", "install", "-y", "flathub",
                          "io.github.foldynl.QLog", check=False).returncode
            spin_result.ok = (rc == 0)
        if rc == 0:
            ctx.log("[+] QLog installed via Flatpak.", "ok")
        else:
            # Not fatal to this step: ion2G below is the operational piece, and
            # QLog is a station log that nothing else depends on.
            ctx.log(f"[!] QLog Flatpak install failed (exit {rc}) — continuing to "
                    f"ion2G. QLog is a station log; nothing else depends on it.", "warn")

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
            ctx.log(f"[+] ion2G extracted to {ion2g_install_dir}", "ok")
        else:
            ctx.log(f"[!] ion2g.exe not found under {ion2g_install_dir} after extraction "
                    f"— the archive layout may have changed. ion2G is NOT usable.", "err")

    temp_zip.unlink(missing_ok=True)


def step_maps_fetch(ctx: Ctx):
    tiles_dir = ctx.data_dir / "Offline_Maps" / "Offline_Tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)
    if not TILE_FETCHER.is_file():
        ctx.log(f"[!] Error: {TILE_FETCHER} not found in repo!", "err")
        return

    # The fetcher refuses to guess an area: with no bounds it exits 2 before
    # downloading anything. Invoked bare it therefore fetched nothing while the
    # spinner still resolved green, so the area is resolved here and a missing
    # one is reported rather than silently producing an empty map.
    areas = sorted(AREA_DIR.glob("*.json"))
    if not areas:
        ctx.log(f"[!] No operating area defined. Copy "
                f"{AREA_DIR}/example-area.json.sample to {AREA_DIR}/<your-area>.json, "
                f"set your own bounds, and re-run this step — no tiles fetched.", "warn")
        return

    ctx.log("[*] Operating area(s) found: " + ", ".join(p.stem for p in areas), "info")

    tf = load_tile_fetcher()
    if tf is None:
        ctx.log(f"[!] Could not load {TILE_FETCHER} as a module — falling back to a "
                f"single full-detail pass per area. A large radius will be slow.", "warn")

    ok_count, fail_count = 0, 0
    for area_path in areas:
        try:
            spec = json.loads(area_path.read_text(errors="replace"))
        except (OSError, ValueError) as e:
            ctx.log(f"[!] {area_path} is not readable JSON ({e}) — skipped.", "err")
            fail_count += 1
            continue

        if is_unmodified_sample(spec):
            ctx.log(f"[!] {area_path.name} holds the shipped sample's bounds unchanged "
                    f"— skipped. It is a leftover from a clone that predates "
                    f"{SAMPLE_AREA.name}; delete it, or set your own bounds in it.", "warn")
            continue

        passes = fetch_passes(spec, tf)
        if not passes:
            ctx.log(f"[!] {area_path} has neither a centre/radius nor all four of "
                    f"north/south/east/west — skipped.", "err")
            fail_count += 1
            continue

        for layer, _tms, _title in MAP_LAYERS:
            for desc, box, (zmin, zmax) in passes:
                rc = 1
                proc = None
                with ctx.spin(f"Fetching {layer} tiles, {desc}, for {area_path.stem}...") as spin_result:
                    # Explicit bounds rather than re-deriving them in the child,
                    # and --yes because stdin is DEVNULL: the size confirmation
                    # would otherwise raise EOFError.
                    proc = ctx.run([sys.executable, str(TILE_FETCHER),
                                    "--north", str(box["north"]), "--south", str(box["south"]),
                                    "--east", str(box["east"]), "--west", str(box["west"]),
                                    "--layer", layer,
                                    "--min-zoom", str(zmin), "--max-zoom", str(zmax),
                                    "--out", str(tiles_dir), "--yes"],
                                   check=False)
                    rc = proc.returncode
                    spin_result.ok = (rc == 0)
                if rc == 0:
                    ok_count += 1
                else:
                    fail_count += 1
                    ctx.log(f"[!] Tile fetch failed: {area_path.stem} / {layer} / "
                            f"{desc} (exit {rc}).", "err")
                    # The fetcher validates its own input and exits saying exactly
                    # what is wrong -- "north must be greater than south",
                    # "latitudes must be between -85 and 85". Keeping only the
                    # return code threw that away and left the operator an exit
                    # status against a multi-hour download. A fatal input error is
                    # one "error:" line; anything else is progress, so show the
                    # diagnosis when there is one and the tail when there is not.
                    out = (proc.stdout or "").strip()
                    if out:
                        lines = out.splitlines()
                        diag = [l for l in lines if l.lstrip().lower().startswith("error:")]
                        for line in (diag or lines[-10:]):
                            ctx.log(f"    {line}", "err")

    if ok_count and not fail_count:
        ctx.log(f"[+] Tiles fetched — {ok_count} pass(es) completed.", "ok")
    elif ok_count:
        ctx.log(f"[!] {ok_count} pass(es) completed, {fail_count} failed — "
                f"see errors above.", "warn")
    else:
        ctx.log("[!] No tiles fetched — every pass failed.", "err")


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

    if kiwix_zim_ok:
        ctx.log("[+] Offline knowledgebase staged.", "ok")
    else:
        ctx.log("[!] Offline knowledgebase NOT staged — the ZIM failed its integrity "
                "check and was discarded.", "err")
    (ctx.data_dir / "Kiwix_ZIM" / "README.txt").write_text(
        "Place offline .zim files (e.g., Wikipedia, Medical, Survival) into this folder.\n"
    )


def step_docs_server(ctx: Ctx):
    (ctx.data_dir / "PDF_Manuals").mkdir(parents=True, exist_ok=True)
    docs_dir = Path("docs")
    if docs_dir.is_dir():
        pdfs = sorted(docs_dir.glob("*.pdf"))
        for pdf in pdfs:
            shutil.copy(pdf, ctx.data_dir / "PDF_Manuals" / pdf.name)
        if pdfs:
            ctx.log(f"[+] {len(pdfs)} field manual(s) copied from local repo.", "ok")
        else:
            ctx.log("[!] No PDFs in docs/ — the document server will serve an empty "
                    "library. Add your reference material there before deployment.", "warn")
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
    # A profile that is absent must not be summarised as "staged" — that is
    # exactly how a node reaches the field on application defaults.
    staged, missing = [], []

    qms_src = Path("configs/QMapShack.conf")
    if qms_src.is_file():
        shutil.copy(qms_src, qms_conf)
        qms_conf.write_text(qms_conf.read_text().replace("HOME_PLACEHOLDER", str(ctx.home)))
        ctx.log(f"[+] QMapShack profile staged to {qms_conf}.", "ok")
        staged.append("QMapShack")
    else:
        ctx.log(f"[!] Warning: {qms_src} not found — QMapShack left unconfigured.", "warn")
        missing.append("QMapShack")

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
        staged.append("JS8Call")
    else:
        ctx.log(f"[!] Warning: {js8call_src} not found — JS8Call left unconfigured.", "warn")
        missing.append("JS8Call")

    chirp_csv = Path("configs/analog_channels.csv")
    if chirp_csv.is_file():
        chirp_dir = ctx.home / ".local" / "share" / "CHIRP"
        if not chirp_dir.is_dir():
            chirp_dir.mkdir(parents=True, exist_ok=True)
            ctx.log("[*] CHIRP directory did not exist — created it.", "info")
        shutil.copy(chirp_csv, chirp_dir / chirp_csv.name)
        ctx.log(f"[+] CHIRP channel list staged to {chirp_dir / chirp_csv.name}.", "ok")
        staged.append("CHIRP")
    else:
        ctx.log(f"[!] Warning: {chirp_csv} not found — CHIRP left unconfigured.", "warn")
        missing.append("CHIRP")

    # --- STAGE ALE CHANNEL PLAN FOR ion2G ---
    ale_src = Path("configs/ale_channels.zcp")
    if ale_src.is_file():
        dest_dir = ctx.data_dir / "ion2G"
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(ale_src, dest_dir / "ale_channels.zcp")
        ctx.log(f"[+] ALE channel plan staged to {dest_dir}/", "ok")
        staged.append("ALE plan")
    else:
        ctx.log(f"[!] Warning: {ale_src} not found — ALE channel plan not staged.", "warn")
        missing.append("ALE plan")

    # Generated from MAP_LAYERS so the directory a .tms reads from is always
    # the directory the fetcher wrote to.
    for layer, tms_name, title in MAP_LAYERS:
        (ctx.data_dir / "Offline_Maps" / tms_name).write_text(f"""<TMS>
<Layer idx="0">
  <Title>{title}</Title>
  <Script><![CDATA[(
  function createPath(z, x, y) {{
      return "file://{ctx.data_dir}/Offline_Maps/Offline_Tiles/{layer}/" + z + "/" + x + "/" + y + ".png";
  }}
  )]]></Script>
</Layer>
</TMS>
""")

    # No else on the outer guard, deliberately: when the profile was never
    # staged, that is already reported above, and a second warning about the
    # contents of a file that does not exist describes a consequence rather
    # than a fault of its own. Only a staged profile that genuinely lacks the
    # line has something new to say.
    if qms_conf.is_file():
        text = qms_conf.read_text()
        if re.search(r"^mapPath=", text, re.MULTILINE):
            if str(ctx.data_dir / "Offline_Maps") not in text:
                text = re.sub(r"^(mapPath=.*)$",
                               lambda m: f"{m.group(1)}, {ctx.data_dir}/Offline_Maps",
                               text, count=1, flags=re.MULTILINE)
                qms_conf.write_text(text)
                ctx.log(f"[+] Registered {ctx.data_dir}/Offline_Maps with QMapShack mapPath.", "ok")
            else:
                ctx.log(f"[+] {ctx.data_dir}/Offline_Maps already registered with QMapShack mapPath.", "ok")
        else:
            ctx.log("[!] QMapShack.conf has no mapPath line — .tms files may need "
                    "manual import.", "warn")

    if staged and not missing:
        ctx.log(f"[+] Config profiles staged: {', '.join(staged)}.", "ok")
    elif staged:
        ctx.log(f"[!] Staged {', '.join(staged)} — NOT staged: {', '.join(missing)}. "
                f"Those applications will start on their own defaults.", "warn")
    else:
        ctx.log("[!] No config profiles staged — configs/ is empty or this is not "
                "being run from the repository root. Every application will start "
                "unconfigured.", "err")
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


def _build_jobs() -> int:
    """Parallel make jobs, capped by RAM as well as by core count.

    SatDump is heavy C++: one translation unit can take well over a gigabyte,
    and the reference node is a 4 GB dual-core also running a desktop session
    and this GUI. -j2 there can exhaust memory, and an OOM-killed compiler
    reports as an ordinary build failure with nothing pointing at the cause.
    One job per 2 GB is the usual rule of thumb.
    """
    cpus = os.cpu_count() or 1
    # MemAvailable, not total: the desktop session, X and this GUI are already
    # resident and the build competes with them. On a 4 GB node that is the
    # difference between budgeting 4 GB and the ~2.5 GB actually free, and so
    # between -j2 and -j1.
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                free_gb = int(line.split()[1]) / 1024 ** 2
                break
        else:
            free_gb = (os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")) / 1024 ** 3
    except (OSError, ValueError, IndexError, AttributeError):
        return cpus
    return max(1, min(cpus, int(free_gb // 2)))


# The build shells out to these three. They arrive with build-essential and
# cmake in SATDUMP_TOOLCHAIN, so an absent one means that install did not
# complete.
SATDUMP_BUILD_TOOLS = ("cmake", "make", "g++")

# Installed as two apt calls, not one. apt resolves a package list as a single
# transaction: if any member is unsatisfiable, nothing on the list is installed.
# That is not hypothetical here -- libmediainfo-dev depends on
# libcurl4-gnutls-dev, which Conflicts with the libcurl4-openssl-dev also on the
# list, so the whole set was unsatisfiable and the compiler never landed.
# Keeping the toolchain in its own transaction means a bad library name costs
# the library, not the ability to build at all.
SATDUMP_TOOLCHAIN = ("git", "build-essential", "cmake", "g++", "pkgconf")

# libzen-dev and libmediainfo-dev were on this list and are not SatDump
# dependencies -- upstream's CMakeLists references neither. They were also the
# two that made the set unsatisfiable. Removed.
SATDUMP_LIBS = (
    "libfftw3-dev", "libpng-dev", "libtiff-dev", "libjemalloc-dev",
    "libcurl4-openssl-dev", "libsqlite3-dev", "librtlsdr-dev", "libhackrf-dev",
    "libairspy-dev", "libairspyhf-dev", "libdbus-1-dev", "libgl1-mesa-dev",
    "libpulse-dev", "libusb-1.0-0-dev", "freeglut3-dev", "libglfw3-dev",
)


def optional_satdump(ctx: Ctx):
    # Built from source. Upstream ships .deb packages on GitHub releases but
    # runs no apt repository, so a packaged install meant carrying a .deb and
    # its SHA-256 in this repository and revising both on every release. The
    # source build needs neither.
    with ctx.spin("Installing SatDump build dependencies...") as spin_result:
        tools_rc = ctx.sudo("apt", "install", "-y", *SATDUMP_TOOLCHAIN,
                            check=False).returncode
        libs_rc = ctx.sudo("apt", "install", "-y", *SATDUMP_LIBS,
                           check=False).returncode

        # The VOLK package name varies across releases; take whichever exists.
        volk_pkg = None
        for pkg in ("libvolk-dev", "libvolk2-dev", "libvolk1-dev"):
            if ctx.sudo("apt", "install", "-y", pkg, check=False).returncode == 0:
                volk_pkg = pkg
                break
        ctx.sudo("apt", "install", "-y", "libnng-dev", check=False)
        spin_result.ok = (tools_rc == 0 and libs_rc == 0 and volk_pkg is not None)

    # Every call above is check=False and SpinResult defaults to success, so a
    # dependency install that installed nothing used to log exactly like one
    # that worked -- and the first sign of it was a bare FileNotFoundError from
    # cmake, thirty seconds of cloning later. Say which part failed instead.
    if tools_rc != 0:
        ctx.log(f"[!] Build toolchain install exited {tools_rc} — SatDump cannot "
                f"be built without it.", "err")
    if libs_rc != 0:
        ctx.log(f"[!] Build library install exited {libs_rc}. apt resolves a "
                f"package list as one transaction, so none of them landed; "
                f"cmake will report which it cannot find.", "err")
    if volk_pkg is None:
        ctx.log("[!] No VOLK development package could be installed (tried "
                "libvolk-dev, libvolk2-dev, libvolk1-dev).", "err")

    satdump_dir = ctx.home / APPS_DIR_NAME / "SatDump"
    if not satdump_dir.is_dir():
        with ctx.spin("Cloning SatDump source...") as spin_result:
            # Shallow: the full history is a large download for a node being
            # provisioned over whatever connection is to hand.
            spin_result.ok = ctx.run(
                ["git", "clone", "--depth", "1",
                 "https://github.com/SatDump/SatDump.git", str(satdump_dir)],
                check=False).returncode == 0
    if not satdump_dir.is_dir():
        ctx.log("[!] SatDump source clone failed — SatDump NOT installed.", "err")
        return

    # No version is pinned, so record what was built. Otherwise a node's
    # SatDump is whatever upstream HEAD was on the day it was imaged, with
    # nothing on the machine saying which.
    head = ctx.run(["git", "-C", str(satdump_dir), "rev-parse", "--short", "HEAD"], check=False)
    built_commit = (head.stdout or "").strip() or "unknown"

    # subprocess raises FileNotFoundError -- not CalledProcessError -- when the
    # executable itself is absent, so the check=False calls below do not catch
    # it and build_status never gets a value to report. The operator saw
    # "FileNotFoundError(2, 'No such file or directory')" with nothing naming
    # cmake. Say which tool is missing, and why it is missing.
    absent = [t for t in SATDUMP_BUILD_TOOLS if not shutil.which(t)]
    if absent:
        raise RuntimeError(
            "SatDump build tools not on PATH: %s. The dependency install above "
            "did not complete -- apt installs its package list all-or-nothing. "
            "Install them and re-run this step: sudo apt install -y "
            "build-essential cmake" % ", ".join(absent))

    build_dir = satdump_dir / "build"
    build_dir.mkdir(parents=True, exist_ok=True)

    # A configure that failed leaves CMakeCache.txt behind holding the results it
    # got, including every find_package that came back NOTFOUND. cmake reuses a
    # cached NOTFOUND rather than looking again, so a retry after the missing
    # dependencies are installed fails exactly as the first attempt did, for a
    # reason that is no longer true. The cache also pins the absolute source path
    # it was configured from, so a moved or re-cloned tree errors out.
    #
    # A build tree with a binary in it is a good one -- leave it so a re-run is an
    # incremental rebuild. A cache with no binary beside it is the wreckage of an
    # attempt that did not finish: discard it and configure clean.
    built_binary = build_dir / "satdump"
    cmake_cache = build_dir / "CMakeCache.txt"
    if cmake_cache.is_file() and not built_binary.is_file():
        cmake_cache.unlink()
        shutil.rmtree(build_dir / "CMakeFiles", ignore_errors=True)
        ctx.log("[*] Discarded the CMake cache left by an earlier failed configure "
                "— it would have reused that run's NOTFOUND results.", "warn")

    jobs = _build_jobs()
    if jobs < (os.cpu_count() or 1):
        ctx.log(f"[*] Building with -j{jobs} rather than -j{os.cpu_count()}: memory, "
                f"not cores, is the limit here. Slower, but it will not be "
                f"killed part-way.", "info")
    build_failure = None
    with ctx.spin(f"Building SatDump {built_commit} with -j{jobs} "
                  f"(several minutes; much longer on a slow node)...") as spin_result:
        try:
            # check=True so a failure arrives as CalledProcessError carrying the
            # command's captured output. Isolation routes that through
            # _report_command_failure, which writes every byte to the run log and
            # a condensed tail to the pane. Under check=False the CompletedProcess
            # was discarded and cmake's "Could NOT find ..." lines went with it,
            # so a configure failure reported as three lines of retry advice that
            # never said what was missing -- the run log promises the complete
            # output of anything that failed, and this step routed around it.
            ctx.run(["cmake", ".."], cwd=build_dir)
            ctx.run(["make", f"-j{jobs}"], cwd=build_dir)
        except subprocess.CalledProcessError as e:
            build_failure = e
            spin_result.ok = False

    if build_failure is not None:
        ctx.log("[!] SatDump build failed — SatDump NOT installed. Retry manually:", "err")
        ctx.log(f"    cd {build_dir} && cmake .. && make -j{jobs}", "err")
        ctx.log("    If the compiler was killed rather than reporting an error, it ran "
                "out of memory — retry with make -j1.", "err")
        # Re-raise the original rather than a fresh error: it carries the output,
        # and isolation adds a step to the failed list only when it raises. A
        # bare return logged at err level and still closed the run with
        # "0 step(s) failed".
        raise build_failure

    ctx.log(f"[+] SatDump {built_commit} built in {build_dir}", "ok")

    # Install so `satdump` is on PATH and a desktop entry exists. Without this
    # the binary stays in the build tree, the shortcut step can never find a
    # .desktop file, and the operator has no satdump command.
    installed = False
    with ctx.spin("Installing SatDump system-wide...") as spin_result:
        installed = ctx.sudo("make", "install", cwd=build_dir, check=False).returncode == 0
        spin_result.ok = installed
    if installed:
        ctx.sudo("ldconfig", check=False)
        ctx.log("[+] SatDump installed system-wide.", "ok")
    else:
        ctx.log(f"[!] 'make install' failed — the binary works at {build_dir}/satdump "
                f"but is not on PATH and has no desktop entry.", "warn")
    ctx.satdump_installed = True

    if ctx.satdump_installed:
        ctx.log("[*] Deploying SatDump TLE configuration...", "info")
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
    Component("sudoers_node_id", "Callsign / node ID + system hostname", step_sudoers_and_node_id),
    Component("system_packages", "System packages + Wine init — SLOWEST", step_system_packages),
    Component("slim_appliance", "Slim appliance build (remove extras)", optional_slim_appliance),
    Component("qlog_ion2g", "QLog station log + ion2G HF ALE", step_qlog_ion2g),
    Component("maps_fetch", "Offline map tiles (your operating area)", step_maps_fetch),
    Component("kiwix_zim", "Offline knowledgebase (Kiwix ZIM)", step_kiwix_zim),
    Component("docs_server", "Reference library + document server", step_docs_server),
    Component("config_profiles", "App profiles + ALE channel plan", step_config_profiles),
    Component("dock_trigger", "Dock-trigger autostart (Havis dock only)", step_dock_trigger),
    Component("direwolf", "Direwolf (AX.25 / APRS software TNC)", optional_direwolf),
    Component("meshtastic", "Meshtastic CLI (LoRa mesh node tooling)", optional_meshtastic),
    Component("satdump", "SatDump (weather imagery, RTL-SDR) — SLOW", optional_satdump),
    Component("desktop_shortcuts", "Desktop shortcuts", finalize),
]


# ===========================================================================
# Post-deployment verification
#
# Runs after the component loop, including after a failure or a cancel — what
# did land before the stop is the useful part. Every check is gated on the
# step that would have produced it, so an unselected step is never reported as
# a failure. The checks are read-only: they report, they never repair.
# ===========================================================================

@dataclass(frozen=True)
class CheckResult:
    label: str
    status: str          # "pass" | "warn" | "fail"
    detail: str = ""


# (display name, apt package, executables it may install)
#
# A mapping rather than a flat list because a package's name is not a promise
# about its executable: apt's "chirp" installs /usr/bin/chirpw.
CORE_APPS: tuple = (
    ("JS8Call",   "js8call",   ("js8call",)),
    ("QMapShack", "qmapshack", ("qmapshack",)),
    ("CHIRP",     "chirp",     ("chirpw", "chirp")),
    ("chrony",    "chrony",    ("chronyc",)),
    ("Wine",      "wine",      ("wine",)),
)


def _package_installed(pkg: str) -> bool:
    """True only if dpkg reports the package fully installed. A package removed
    but not purged reports "deinstall ok config-files", which is not it."""
    try:
        out = subprocess.run(["dpkg-query", "-W", "-f=${Status}", pkg],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True)
    except OSError:
        return False
    return out.returncode == 0 and out.stdout.split()[-1:] == ["installed"]


def _root_exists(ctx: Ctx, path: Path) -> bool:
    """Presence check for a path a normal user cannot stat. /etc/sudoers.d is
    mode 0750, so Path.exists() there answers False whether or not the file is
    present — a check that could only ever report a false failure."""
    if getattr(ctx, "askpass", None) is None:
        return path.exists()
    try:
        return subprocess.run(["sudo", "-A", "test", "-e", str(path)],
                              env=ctx.askpass.env(), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL).returncode == 0
    except OSError:
        return path.exists()


def _placeholder_count(path: Path) -> int:
    """Unsubstituted tokens left in a staged profile. A surviving token is worse
    than a missing setting: it is used verbatim as a real value."""
    try:
        return path.read_text(errors="replace").count("PLACEHOLDER")
    except OSError:
        return 0


def _ini_value(path: Path, key: str) -> Optional[str]:
    """First `key = value` in a flat INI-style file, or None.

    Spaces around the separator are tolerated. Qt writes `MyCall=W1AW` with
    none, but an operator who opens the profile in an editor may well leave
    `MyCall = W1AW` -- and matching on `key + "="` read that as absent, so
    verification reported a correctly configured node as `MyCall=None`. A
    commented-out line (`; MyCall=...`) still does not match, because the name
    is compared whole rather than as a prefix.
    """
    try:
        for line in path.read_text(errors="replace").splitlines():
            name, sep, value = line.partition("=")
            if sep and name.strip() == key:
                return value.strip()
    except OSError:
        pass
    return None


def verify_deployment(ctx: Ctx, selected_ids: set) -> list:
    out: list = []

    def add(label: str, status: str, detail: str = "") -> None:
        out.append(CheckResult(label, status, detail))

    def want(path: Path, label: str, *, hard: bool = True) -> bool:
        if path.exists():
            add(label, "pass", str(path))
            return True
        add(label, "fail" if hard else "warn", "missing: " + str(path))
        return False

    home = ctx.home

    # --- identity -------------------------------------------------------
    if "sudoers_node_id" in selected_ids:
        node_conf = Path(NODE_CONF)
        if want(node_conf, "Node ID file written"):
            recorded = _ini_value(node_conf, "NODE_ID")
            if recorded == ctx.node_id:
                add("Node ID matches this run", "pass", "NODE_ID=" + str(recorded))
            else:
                add("Node ID matches this run", "fail",
                    "file says %r, expected %r" % (recorded, ctx.node_id))

        want_host = hostname_from_node_id(ctx.node_id)
        if want_host:
            actual = subprocess.run(["hostname"], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True).stdout.strip()
            if actual.lower() == want_host.lower():
                add("System hostname is the Node ID", "pass", "hostname = " + actual)
            else:
                add("System hostname is the Node ID", "fail",
                    "hostname is %r, expected %r" % (actual, want_host))
            try:
                mapped = any(l.split()[:2] == ["127.0.1.1", want_host]
                             for l in Path("/etc/hosts").read_text(errors="replace").splitlines())
            except OSError:
                mapped = False
            add("Loopback name resolution updated", "pass" if mapped else "fail",
                "127.0.1.1 -> " + want_host if mapped
                else "/etc/hosts has no 127.0.1.1 entry for " + want_host
                     + " — sudo will stall on 'unable to resolve host'")

        sudoers = Path(SUDOERS_FILE)
        if _root_exists(ctx, sudoers):
            add("Passwordless sudo rule for RF daemons", "pass", str(sudoers))
        else:
            add("Passwordless sudo rule for RF daemons", "fail", "missing: " + str(sudoers))

    # --- base packages --------------------------------------------------
    if "system_packages" in selected_ids:
        for label, pkg, binaries in CORE_APPS:
            found = None
            for candidate in binaries:
                found = shutil.which(candidate)
                if found:
                    break
            if found:
                add(label + " installed", "pass", found)
            elif _package_installed(pkg):
                add(label + " installed", "warn",
                    "package %s is installed, but no %s on PATH" % (pkg, " or ".join(binaries)))
            else:
                add(label + " installed", "fail",
                    "no %s on PATH and package %s is not installed" % (" or ".join(binaries), pkg))

    # --- QLog / ion2G ---------------------------------------------------
    if "qlog_ion2g" in selected_ids:
        exe_ptr = home / STATE_DIR_NAME / "ion2g_exe_path"
        if want(exe_ptr, "ion2G executable path recorded"):
            target = Path(exe_ptr.read_text(errors="replace").strip())
            if target.is_file():
                add("ion2G executable present", "pass", str(target))
            else:
                add("ion2G executable present", "fail",
                    "recorded path does not exist: " + str(target))

    # --- offline maps ---------------------------------------------------
    if "maps_fetch" in selected_ids:
        tiles = ctx.data_dir / "Offline_Maps" / "Offline_Tiles"
        for layer, _tms, title in MAP_LAYERS:
            d = tiles / layer
            n = sum(1 for _ in d.rglob("*.png")) if d.is_dir() else 0
            add("Map tiles: " + title, "pass" if n else "warn",
                "%d tile(s) in %s" % (n, d) if n
                else "no tiles in " + str(d) + " — define an operating area and re-run")

    # --- offline knowledgebase ------------------------------------------
    if "kiwix_zim" in selected_ids:
        zim_dir = ctx.data_dir / "Kiwix_ZIM"
        zims = sorted(zim_dir.glob("*.zim")) if zim_dir.is_dir() else []
        if zims:
            add("Kiwix ZIM present", "pass", ", ".join(z.name for z in zims))
        else:
            add("Kiwix ZIM present", "fail", "no .zim in " + str(zim_dir))
        want(home / ".local" / "share" / "kiwix" / "library.xml",
             "Kiwix library registered", hard=False)

    # --- reference library + doc server ---------------------------------
    if "docs_server" in selected_ids:
        pdfs = ctx.data_dir / "PDF_Manuals"
        n = len(list(pdfs.glob("*.pdf"))) if pdfs.is_dir() else 0
        add("Reference PDFs staged", "pass" if n else "warn",
            ("%d PDF(s) in %s" % (n, pdfs)) if n
            else "no PDFs in " + str(pdfs) + " — the library will serve empty")
        want(home / ".config" / "systemd" / "user" / DOCS_SERVER_UNIT,
             "Document server unit installed")
        active = subprocess.run(["systemctl", "--user", "is-active", DOCS_SERVER_UNIT],
                                stdin=subprocess.DEVNULL, capture_output=True,
                                text=True).stdout.strip()
        add("Document server running", "pass" if active == "active" else "warn",
            "systemctl --user is-active -> " + (active or "unknown"))

    # --- application profiles (the ones that bite) ----------------------
    if "config_profiles" in selected_ids:
        js8 = home / ".config" / "JS8Call.ini"
        if want(js8, "JS8Call profile staged"):
            call = _ini_value(js8, "MyCall")
            if call == ctx.node_id:
                add("JS8Call MyCall set to this node", "pass", "MyCall=" + str(call))
            else:
                add("JS8Call MyCall set to this node", "fail",
                    "MyCall=%r, expected %r" % (call, ctx.node_id))
            left = _placeholder_count(js8)
            add("JS8Call placeholders substituted", "pass" if left == 0 else "fail",
                "clean" if left == 0 else "%d unsubstituted token(s) remain" % left)
            grid = _ini_value(js8, "MyGrid")
            add("JS8Call grid square", "pass" if not grid else "warn",
                "empty — set per operator" if not grid
                else "MyGrid=" + grid + " (set by profile, confirm correct)")

        qms = home / ".config" / "QLandkarteGT" / "QMapShack.conf"
        if want(qms, "QMapShack profile staged"):
            left = _placeholder_count(qms)
            add("QMapShack placeholders substituted", "pass" if left == 0 else "fail",
                "clean" if left == 0 else "%d unsubstituted token(s) remain" % left)

        want(home / ".local" / "share" / "CHIRP" / "analog_channels.csv",
             "CHIRP channel list staged", hard=False)
        want(ctx.data_dir / "ion2G" / "ale_channels.zcp",
             "ALE channel plan staged", hard=False)

        # The .tms files and the tile directories must agree — they did not
        # once, and QMapShack then opened the sources onto nothing.
        for layer, tms_name, title in MAP_LAYERS:
            tms = ctx.data_dir / "Offline_Maps" / tms_name
            if want(tms, "Map source " + tms_name, hard=False):
                expect = "Offline_Tiles/" + layer + "/"
                add("Map source " + tms_name + " points at the fetched layer",
                    "pass" if expect in tms.read_text(errors="replace") else "fail",
                    expect if expect in tms.read_text(errors="replace")
                    else "does not reference " + expect)

    # --- dock trigger ---------------------------------------------------
    if "dock_trigger" in selected_ids:
        launcher = home / ".local" / "bin" / AUTOSTART_SCRIPT
        if want(launcher, "Autostart launcher installed"):
            add("Autostart launcher executable",
                "pass" if os.access(launcher, os.X_OK) else "fail",
                "mode %o" % (launcher.stat().st_mode & 0o777))
        want(home / ".config" / "systemd" / "user" / AUTOSTART_UNIT,
             "Autostart user unit installed")
        want(Path(DOCK_EVENT_SH), "Dock-event dispatcher installed")
        want(Path("/etc/udev/rules.d/99-dock-trigger.rules"), "udev dock rule installed")

    # --- optional installs ----------------------------------------------
    if "direwolf" in selected_ids:
        dw = shutil.which("direwolf")
        add("Direwolf installed", "pass" if dw else "fail",
            dw or "direwolf not on PATH")
        conf = home / ".config" / "direwolf" / "direwolf.conf"
        if want(conf, "Direwolf config written"):
            mycall = None
            for line in conf.read_text(errors="replace").splitlines():
                if line.startswith("MYCALL "):
                    mycall = line.split(None, 1)[1].strip()
                    break
            if mycall == ctx.node_id:
                add("Direwolf MYCALL set to this node", "pass", "MYCALL=" + str(mycall))
            else:
                add("Direwolf MYCALL set to this node", "fail",
                    "MYCALL=%r, expected %r" % (mycall, ctx.node_id))
        want(home / "direwolf.conf", "Direwolf config discoverable from $HOME", hard=False)

    if "meshtastic" in selected_ids:
        want(home / ".local" / "bin" / "meshtastic", "Meshtastic wrapper on PATH")
        want(home / APPS_DIR_NAME / "meshtastic-venv" / "bin" / "meshtastic",
             "Meshtastic CLI installed in venv")
        groups = subprocess.run(["id", "-nG", ctx.user], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True).stdout.split()
        add("Operator in 'dialout' group", "pass" if "dialout" in groups else "warn",
            "present" if "dialout" in groups
            else "added, but requires log out / log in to take effect")
        want(ctx.data_dir / "Meshtastic" / "meshtastic_setup.md",
             "Mesh setup reference staged", hard=False)

    if "satdump" in selected_ids:
        built = home / APPS_DIR_NAME / "SatDump" / "build" / "satdump"
        binary = shutil.which("satdump") or (str(built) if built.is_file() else "")
        if binary:
            add("SatDump available", "pass", binary)
            # The TLE set is staged inside the install step, after the build
            # succeeds, so a failed build returns before reaching it and no step
            # ever attempts to write this file. Checked only once a binary
            # exists: otherwise the row reports a file nothing tried to create,
            # which is a consequence of the failure above rather than a finding
            # of its own.
            #
            # Both remaining cases still report. A failed `make install` leaves
            # the build-tree binary, and a successful build with no TLE source
            # under configs/ warns -- which on a node imaged from a populated
            # configs/ is a real staging fault, not an absent optional.
            want(home / ".config" / "satdump" / "satdump_tles.txt",
                 "SatDump TLE set staged", hard=False)
        else:
            add("SatDump available", "fail", "no satdump binary found (package or build)")

    # --- desktop -------------------------------------------------------
    if "desktop_shortcuts" in selected_ids:
        desktop = home / "Desktop"
        n = len(list(desktop.glob("*.desktop"))) if desktop.is_dir() else 0
        add("Desktop shortcuts created", "pass" if n else "warn",
            "%d shortcut(s)" % n if n else "none found in " + str(desktop))

    if not out:
        add("Nothing to verify", "warn", "no steps were selected for this run")
    return out


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
        # The reference panel is a CF-30 at 1024x768, so every screen has to
        # fit inside that with room for window decorations and a panel.
        self.geometry("900x700")
        self.minsize(700, 560)

        self.msg_queue: "queue.Queue[tuple]" = queue.Queue()
        self.cancel_event = threading.Event()
        self.worker: Optional[threading.Thread] = None
        self.askpass: Optional[AskpassSession] = None
        self.runlog: Optional[RunLog] = None
        self.component_vars: dict[str, tk.BooleanVar] = {}

        # Spinner state — all touched only from the main (Tk) thread, via
        # _drain_queue handling "spin_start"/"spin_stop" messages.
        self._spin_active = False
        self._spin_after_id: Optional[str] = None
        self._spin_line: Optional[int] = None
        self._spin_label: str = ""
        self._spin_suffix: str = ""
        self._spin_pos = 0

        # Screens are built once and shown/hidden, so going back to an earlier
        # one preserves whatever was already entered.
        self.frames: dict = {}
        self.selected_ids: set = set()
        self.check_results: list = []
        self.run_outcome: str = "unknown"     # completed | failed | cancelled
        self.failed_steps: list = []          # steps that raised, by label
        self.area_file = None                 # written by the area screen, if shown

        self._build_options_screen()
        self._show("options")
        self.after(100, self._drain_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _show(self, name: str):
        for frame in self.frames.values():
            frame.pack_forget()
        frame = self.frames[name]
        frame.pack(fill="both", expand=True)
        self._fit_minsize(frame)

    def _fit_minsize(self, frame):
        """Raise the window minimum so a screen can never be shrunk into
        clipping. pack() truncates silently rather than scrolling, so a minsize
        below a screen's requested size hides controls with no warning."""
        frame.update_idletasks()
        cur_w, cur_h = self.minsize()
        self.minsize(max(cur_w, frame.winfo_reqwidth()),
                     max(cur_h, frame.winfo_reqheight()))

    # -- spinner: animated "in progress" log line ------------------------
    def _spin_start(self, label: str):
        # The file gets a line when the work starts AND when it resolves. The
        # pane redraws one animated line in place; the file cannot, and the
        # start line is what localises a run that hung rather than failed.
        if self.runlog:
            self.runlog.write(label, "spin")
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
        if self.runlog and self._spin_label:
            self.runlog.write(self._spin_label, "ok" if ok else "err")
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
        self.frames["options"] = self.options_frame

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

        # Pack the bottom bar BEFORE the expanding step list: pack() hands out
        # space in call order and clips whatever came last, which must never be
        # the control that moves the run forward.
        self.continue_btn = ttk.Button(self.options_frame, text="Continue \u2192",
                                        command=self._on_continue_to_sudo)
        self.continue_btn.pack(side="bottom", anchor="e", pady=(4, 0))

        ttk.Label(self.options_frame,
                  text=("Run from the folder containing this script, as your normal user "
                        "(not root). Steps are independent and not dependency-checked, so "
                        "a single step can be re-run on its own \u2014 but e.g. the ion2G step "
                        "assumes wine and unzip are already installed."),
                  wraplength=660, foreground="#666666",
                  justify="left").pack(side="bottom", anchor="w", pady=(4, 8))

        hw = ttk.LabelFrame(self.options_frame, text="Hardware compatibility")
        hw.pack(side="bottom", fill="x", pady=(10, 4))
        ttk.Label(hw,
                  text=("This package targets a Panasonic Toughbook CF-30 in a Havis "
                        "DS-PAN-111 series dock. The dock-trigger step is keyed to that "
                        "dock's USB vendor/product ID, and the autostart sequence assumes "
                        "that hardware. Other docks and laptops are NOT supported \u2014 "
                        "the udev rule will not fire and the autostart sequence will not "
                        "run. Everything else provisions normally; see the README before "
                        "deploying on different hardware."),
                  wraplength=660, foreground="#8a5a00",
                  justify="left").pack(anchor="w", padx=8, pady=6)

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


    def _select_all_components(self):
        for var in self.component_vars.values():
            var.set(True)

    def _select_none_components(self):
        for var in self.component_vars.values():
            var.set(False)

    # -- screen 2 (conditional): operating area ---------------------------
    def _build_area_screen(self):
        f = ttk.Frame(self, padding=16)
        self.frames["area"] = f

        ttk.Label(f, text="Operating Area",
                  font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(f,
                  text=("Offline map tiles are fetched around a centre point. Full street "
                        "detail is kept within the inner ring where a node actually "
                        "navigates; the rest of the radius is covered at orientation zoom. "
                        "Fetching the whole radius at street zoom is what turns a wide area "
                        "into a multi-hour download."),
                  wraplength=660, foreground="#666666", justify="left").pack(anchor="w", pady=(0, 14))

        form = ttk.Frame(f)
        form.pack(fill="x")
        ttk.Label(form, text="Centre latitude:").grid(row=0, column=0, sticky="w", pady=4)
        self.lat_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.lat_var, width=14).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(form, text="decimal degrees, e.g. 39.00 (N positive)",
                  foreground="#888888").grid(row=0, column=2, sticky="w")

        ttk.Label(form, text="Centre longitude:").grid(row=1, column=0, sticky="w", pady=4)
        self.lon_var = tk.StringVar()
        ttk.Entry(form, textvariable=self.lon_var, width=14).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(form, text="decimal degrees, e.g. -77.00 (W negative)",
                  foreground="#888888").grid(row=1, column=2, sticky="w")

        radius_row = ttk.LabelFrame(f, text="Radius")
        radius_row.pack(fill="x", pady=(12, 8))
        self.radius_var = tk.IntVar(value=50)
        inner = ttk.Frame(radius_row)
        inner.pack(anchor="w", padx=8, pady=6)
        for miles in (50, 75, 150):
            ttk.Radiobutton(inner, text=f"{miles} miles", value=miles,
                            variable=self.radius_var).pack(side="left", padx=(0, 18))

        self.area_estimate = ttk.Label(f, text="", justify="left", wraplength=660,
                                        font=("Courier New", 9))
        self.area_estimate.pack(anchor="w", pady=(8, 0))

        self.area_error = ttk.Label(f, text="", foreground="#c0392b",
                                     wraplength=660, justify="left")
        self.area_error.pack(anchor="w", pady=(6, 0))

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="\u2190 Back", command=lambda: self._show("options")).pack(side="left")
        ttk.Button(btns, text="Continue \u2192",
                   command=self._on_area_continue).pack(side="right")

        # Trace every input rather than hanging the recompute off the
        # radiobuttons' command: a variable changed any other way would leave a
        # stale estimate on screen, which on this screen is a lie about a
        # multi-hundred-megabyte download.
        for var in (self.lat_var, self.lon_var, self.radius_var):
            var.trace_add("write", lambda *_a: self._update_area_estimate())

    def _parse_centre(self):
        """(lat, lon) rounded to 2 dp, or None with the reason in area_error.

        More precision is accepted and rounded rather than rejected — 2 dp is
        about 1.1 km, ample for a map centre, and refusing 39.123 would only
        annoy someone reading coordinates off a GPS.
        """
        try:
            lat = round(float(self.lat_var.get().strip()), 2)
            lon = round(float(self.lon_var.get().strip()), 2)
        except ValueError:
            return None
        if not -85.0 <= lat <= 85.0:
            self.area_error.configure(text="Latitude must be between -85 and 85 "
                                           "(the Web Mercator limit).")
            return None
        if not -180.0 <= lon <= 180.0:
            self.area_error.configure(text="Longitude must be between -180 and 180.")
            return None
        return lat, lon

    def _update_area_estimate(self, *_a):
        self.area_error.configure(text="")
        centre = self._parse_centre()
        if centre is None:
            self.area_estimate.configure(text="Enter a centre to see the download estimate.")
            return
        lat, lon = centre
        tf = load_tile_fetcher()
        if tf is None:
            self.area_estimate.configure(
                text=f"centre {lat:.2f}, {lon:.2f}   (estimate unavailable — "
                     f"{TILE_FETCHER} could not be loaded)")
            return
        radius = self.radius_var.get()
        lines, total = [], 0
        for desc, box, (zmin, zmax) in fetch_passes(
                {"center": {"lat": lat, "lon": lon}, "radius_miles": radius}, tf):
            n = tf.count_tiles(box, range(zmin, zmax + 1)) * len(MAP_LAYERS)
            total += n
            lines.append(f"  {desc:<16} z{zmin}-{zmax}   {n:>8,} tiles")
        mb = total * tf.KB_PER_TILE / 1024
        mins = tf.estimate_seconds(total) / 60
        # "total", not "both layers": each row above already counts every layer,
        # so labelling the sum that way reads as though the rows were per-layer.
        lines.append(f"  {'total':<16}          {total:>8,} tiles"
                     f"   ~{mb:,.0f} MB   ~{mins:,.0f} min")
        layers = " + ".join(layer for layer, _tms, _title in MAP_LAYERS)
        self.area_estimate.configure(
            text=f"centre {lat:.2f}, {lon:.2f} — radius {radius} mi   "
                 f"({layers}; every figure covers both)\n" + "\n".join(lines))

    def _on_area_continue(self):
        centre = self._parse_centre()
        if centre is None:
            if not self.area_error.cget("text"):
                self.area_error.configure(
                    text="Enter a centre latitude and longitude in decimal degrees.")
            return
        lat, lon = centre
        radius = self.radius_var.get()
        node = hostname_from_node_id(self.node_id_var.get().strip()) or "node"
        tf = load_tile_fetcher()
        detail_r = min(tf.DETAIL_RADIUS_MILES if tf else 25, radius)
        spec = {
            "_comment": ("Generated by the provisioner. Tiles are fetched at full detail "
                         "within detail_radius_miles and at orientation zoom to "
                         "radius_miles. The bounding box is the outer ring, kept so the "
                         "fetcher can be run directly with --area."),
            "center": {"lat": lat, "lon": lon},
            "radius_miles": radius,
            "detail_radius_miles": detail_r,
        }
        if tf:
            spec.update(tf.box_from_center(lat, lon, radius))
        try:
            AREA_DIR.mkdir(parents=True, exist_ok=True)
            dest = AREA_DIR / f"{node.lower()}-area.json"
            dest.write_text(json.dumps(spec, indent=2) + "\n")
        except OSError as e:
            self.area_error.configure(text=f"Could not write the area file: {e}")
            return
        self.area_file = dest
        self._show_sudo_screen()

    # -- screen 3: sudo credentials ---------------------------------------
    def _build_sudo_screen(self):
        f = ttk.Frame(self, padding=16)
        self.frames["sudo"] = f

        ttk.Label(f, text="Administrator Access Required",
                  font=("TkDefaultFont", 14, "bold")).pack(anchor="w")
        ttk.Label(f,
                  text=("Provisioning installs packages, writes to /etc, enables systemd "
                        "units and adds a udev rule. All of that needs root, so this is a "
                        "requirement of the run rather than something to opt into."),
                  wraplength=660, foreground="#666666", justify="left").pack(anchor="w", pady=(0, 14))

        self.sudo_summary = ttk.Label(f, text="", wraplength=660, justify="left")
        self.sudo_summary.pack(anchor="w", pady=(0, 14))

        form = ttk.Frame(f)
        form.pack(fill="x")
        ttk.Label(form, text="Sudo password:").grid(row=0, column=0, sticky="w", pady=4)
        self.sudo_pw_var = tk.StringVar()
        self.sudo_entry = ttk.Entry(form, textvariable=self.sudo_pw_var, show="*", width=32)
        self.sudo_entry.grid(row=0, column=1, sticky="w", padx=8)
        self.sudo_entry.bind("<Return>", lambda _e: self._on_start())

        self.sudo_error = ttk.Label(f, text="", foreground="#c0392b", wraplength=660, justify="left")
        self.sudo_error.pack(anchor="w", pady=(8, 0))

        ttk.Label(f,
                  text=("The password is held in memory only. It feeds a private, "
                        "single-use askpass helper readable by no one else, deleted the "
                        "moment the run ends \u2014 success, failure or cancel. It is never "
                        "written to the repository, a log, or anywhere else, and never "
                        "leaves this machine."),
                  wraplength=660, foreground="#666666", justify="left").pack(anchor="w", pady=(14, 8))

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="\u2190 Back", command=self._back_from_sudo).pack(side="left")
        self.start_btn = ttk.Button(btns, text="Start Provisioning", command=self._on_start)
        self.start_btn.pack(side="right")

    # -- screen 4: verification -------------------------------------------
    def _build_verify_screen(self):
        f = ttk.Frame(self, padding=16)
        self.frames["verify"] = f

        self.verify_title = ttk.Label(f, text="Verifying deployment\u2026",
                                       font=("TkDefaultFont", 14, "bold"))
        self.verify_title.pack(anchor="w")
        ttk.Label(f,
                  text=("Read-only checks against what actually landed on disk. Only steps "
                        "you selected are checked, so a skipped step is never reported as a "
                        "failure."),
                  wraplength=660, foreground="#666666", justify="left").pack(anchor="w", pady=(0, 12))

        table = ttk.Frame(f)
        table.pack(fill="both", expand=True)
        cols = ("status", "check", "detail")
        self.verify_tree = ttk.Treeview(table, columns=cols, show="headings", height=16)
        self.verify_tree.heading("status", text="")
        self.verify_tree.heading("check", text="Check")
        self.verify_tree.heading("detail", text="Detail")
        self.verify_tree.column("status", width=34, anchor="center", stretch=False)
        self.verify_tree.column("check", width=310, anchor="w")
        self.verify_tree.column("detail", width=440, anchor="w")
        self.verify_tree.tag_configure("pass", foreground="#2e7d32")
        self.verify_tree.tag_configure("warn", foreground="#b8860b")
        self.verify_tree.tag_configure("fail", foreground="#c0392b")
        sb = ttk.Scrollbar(table, orient="vertical", command=self.verify_tree.yview)
        self.verify_tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.verify_tree.pack(side="left", fill="both", expand=True)

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="\u2190 View log", command=lambda: self._show("run")).pack(side="left")
        self.verify_continue_btn = ttk.Button(btns, text="Continue \u2192",
                                               command=self._on_show_summary, state="disabled")
        self.verify_continue_btn.pack(side="right")

    # -- screen 5: summary -------------------------------------------------
    def _build_summary_screen(self):
        f = ttk.Frame(self, padding=16)
        self.frames["summary"] = f

        self.summary_title = ttk.Label(f, text="", font=("TkDefaultFont", 14, "bold"))
        self.summary_title.pack(anchor="w", pady=(0, 12))

        self.summary_body = tk.Text(f, height=16, wrap="word", relief="flat",
                                     background=self.cget("background"), state="disabled")
        self.summary_body.pack(fill="both", expand=True)
        self.summary_body.tag_configure("ok", foreground="#2e7d32")
        self.summary_body.tag_configure("warn", foreground="#b8860b")
        self.summary_body.tag_configure("err", foreground="#c0392b")
        self.summary_body.tag_configure("head", font=("TkDefaultFont", 10, "bold"))

        btns = ttk.Frame(f)
        btns.pack(fill="x", pady=(10, 0))
        ttk.Button(btns, text="\u2190 Verification details",
                   command=lambda: self._show("verify")).pack(side="left")
        ttk.Button(btns, text="View log",
                   command=lambda: self._show("run")).pack(side="left", padx=(8, 0))
        ttk.Button(btns, text="Close", command=self._on_close).pack(side="right")

    # -- screen 3: run ----------------------------------------------------
    def _build_run_screen(self):
        self.run_frame = ttk.Frame(self, padding=16)
        self.frames["run"] = self.run_frame

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
        # Re-labelled "Continue \u2192" once the run ends: from then on this screen
        # is the log archive, reachable from both later screens.
        self.close_btn = ttk.Button(btns, text="Close", command=self._on_run_screen_forward,
                                     state="disabled")
        self.close_btn.pack(side="right")

    # -- event handlers ---------------------------------------------------
    def _on_continue_to_sudo(self):
        """Options -> sudo. Validates everything that does not need a password,
        so a bad Node ID is caught before anyone types a credential."""
        node_id = self.node_id_var.get().strip()
        if not node_id:
            messagebox.showerror("Missing Node ID", "Node ID / callsign cannot be blank.")
            return
        if os.geteuid() == 0:
            messagebox.showerror("Do not run as root",
                                  "Run this GUI as your normal user, not via sudo.")
            return

        selected = {cid for cid, var in self.component_vars.items() if var.get()}
        if not selected:
            if not messagebox.askyesno("No steps selected",
                                        "No steps are checked — this run will do nothing.\n"
                                        "Continue anyway?"):
                return
        self.selected_ids = selected

        # The operating area is configuration, not privileged work, so it comes
        # before the credential prompt — and only when the map step is actually
        # selected. Skipping it otherwise keeps the flow at five screens for
        # everyone who is not fetching tiles.
        if "maps_fetch" in selected:
            if "area" not in self.frames:
                self._build_area_screen()
            self.area_error.configure(text="")
            self._update_area_estimate()
            self._show("area")
            return
        self._show_sudo_screen()

    def _show_sudo_screen(self):
        node_id = self.node_id_var.get().strip()
        selected = self.selected_ids
        if "sudo" not in self.frames:
            self._build_sudo_screen()
        labels = [c.label for c in COMPONENTS if c.id in selected]
        if labels:
            listing = "\n".join("    \u2022 " + l for l in labels)
            self.sudo_summary.configure(
                text="Node %s — %d step(s) selected:\n%s" % (node_id, len(labels), listing))
        else:
            self.sudo_summary.configure(text="Node %s — no steps selected." % node_id)
        self.sudo_error.configure(text="")
        self._show("sudo")
        self.sudo_entry.focus_set()

    def _back_from_sudo(self):
        """Back goes to wherever we came from — the area screen when the map
        step is selected, the options screen otherwise."""
        self._show("area" if "maps_fetch" in self.selected_ids and "area" in self.frames
                   else "options")

    def _on_start(self):
        """Sudo -> run. Validates the password before leaving this screen, so a
        typo is corrected here instead of surfacing mid-install."""
        password = self.sudo_pw_var.get()
        if not password:
            self.sudo_error.configure(text="Enter your sudo password to continue.",
                                      foreground="#c0392b")
            return

        self.start_btn.configure(state="disabled")
        self.sudo_error.configure(text="Checking credentials\u2026", foreground="#666666")
        self.update_idletasks()

        askpass = AskpassSession(password)
        self.sudo_pw_var.set("")           # drop the plaintext from the widget
        if not askpass.verify():
            askpass.close()
            self.sudo_error.configure(
                text="That password did not validate with sudo. Try again.",
                foreground="#c0392b")
            self.start_btn.configure(state="normal")
            self.sudo_entry.focus_set()
            return
        self.askpass = askpass

        if "run" not in self.frames:
            self._build_run_screen()
        self._show("run")

        node_id = self.node_id_var.get().strip()
        user = _current_user()
        self.runlog = RunLog(Path.home() / LOG_DIR_NAME)
        if self.runlog.path:
            selected = [c.label for c in COMPONENTS if c.id in self.selected_ids]
            skipped = [c.label for c in COMPONENTS if c.id not in self.selected_ids]
            self.runlog.header(node_id, user, selected, skipped)
            # Announced before the first step, not only on the summary: a run
            # that has to be killed part-way still tells the operator where the
            # transcript is.
            self._append_log("Run log: %s" % self.runlog.path, "info")
        else:
            self._append_log("[!] Could not open a run log (%s) \u2014 continuing; "
                             "this screen is the only record." % self.runlog.error, "warn")

        ctx = Ctx(
            home=Path.home(),
            user=user,
            node_id=node_id,
            askpass=self.askpass,
            log=self._queue_log,
            cancel_check=self._cancel_check,
            spin_start=self._queue_spin_start,
            spin_stop=self._queue_spin_stop,
            spin_progress=self._queue_spin_progress,
        )
        self.worker = threading.Thread(target=self._run_provisioning,
                                        args=(ctx, self.selected_ids), daemon=True)
        self.worker.start()

    def _on_run_screen_forward(self):
        """The run screen's right-hand button. Before verification exists it is
        a plain Close; afterwards it is the way back to the results."""
        if "verify" in self.frames:
            self._show("verify")
        else:
            self.destroy()

    def _on_show_summary(self):
        if "summary" not in self.frames:
            self._build_summary_screen()
        self._render_summary()
        self._show("summary")

    def _render_summary(self):
        node_id = self.node_id_var.get().strip()
        passed = sum(1 for r in self.check_results if r.status == "pass")
        warned = sum(1 for r in self.check_results if r.status == "warn")
        failed = sum(1 for r in self.check_results if r.status == "fail")
        ran = [c.label for c in COMPONENTS if c.id in self.selected_ids]
        skipped = len(COMPONENTS) - len(ran)

        if self.run_outcome == "cancelled":
            title, tag = "Provisioning cancelled", "warn"
        elif self.run_outcome == "failed" or failed:
            title, tag = "Provisioning finished with problems", "err"
        elif warned:
            title, tag = "Provisioning complete \u2014 review warnings", "warn"
        else:
            title, tag = "Provisioning complete", "ok"
        self.summary_title.configure(text=title)

        t = self.summary_body
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.insert("end", "Node: ", "head"); t.insert("end", node_id + "\n\n")
        t.insert("end", "Result\n", "head")
        t.insert("end", "    %s\n" % title, tag)
        t.insert("end", "    %d step(s) run, %d skipped\n" % (len(ran), skipped))
        if self.failed_steps:
            t.insert("end", "    %d step(s) failed:\n" % len(self.failed_steps), "err")
            for label in self.failed_steps:
                t.insert("end", "        %s\n" % label, "err")
        t.insert("end", "\n")

        t.insert("end", "Verification\n", "head")
        t.insert("end", "    %d passed\n" % passed, "ok")
        if warned:
            t.insert("end", "    %d warning(s)\n" % warned, "warn")
        if failed:
            t.insert("end", "    %d failed\n" % failed, "err")
        if not self.check_results:
            t.insert("end", "    nothing verified\n", "warn")
        t.insert("end", "\n")

        problems = [r for r in self.check_results if r.status in ("fail", "warn")]
        if problems:
            t.insert("end", "Needs attention\n", "head")
            for r in problems[:12]:
                t.insert("end", "    %s %s \u2014 %s\n"
                         % ("\u2716" if r.status == "fail" else "!", r.label, r.detail),
                         "err" if r.status == "fail" else "warn")
            if len(problems) > 12:
                t.insert("end", "    \u2026and %d more (see the previous screen)\n"
                         % (len(problems) - 12))
            t.insert("end", "\n")

        t.insert("end", "Run log\n", "head")
        if self.runlog and self.runlog.path:
            t.insert("end", "    %s\n" % self.runlog.path)
            t.insert("end", "    Full command output for anything that failed is in there,\n"
                            "    uncondensed. Attach it when reporting a problem.\n")
        else:
            t.insert("end", "    not written \u2014 %s\n"
                     % (self.runlog.error if self.runlog else "no run started"), "warn")
        t.insert("end", "\n")

        t.insert("end", "Next\n", "head")
        t.insert("end", "    Work through 'Pre-Deployment Config Checklist' in the repo root \u2014\n"
                        "    it covers what this tool cannot check without real hardware.\n")
        t.configure(state="disabled")

        # The summary is the last thing written. Close the file here rather
        # than at window teardown, so the transcript is complete and flushed
        # the moment the operator can read the path to it.
        if self.runlog:
            self.runlog.footer(title, len(ran), skipped, self.failed_steps,
                               passed, warned, failed)
            self.runlog.close()

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
        if self.runlog:
            # Quitting before the summary screen: whatever was written stays,
            # flushed and closed rather than left to the interpreter.
            self.runlog.write("=== Window closed before the summary screen ===", "warn")
            self.runlog.close()
        self.destroy()

    # -- worker thread ------------------------------------------------------
    def _cancel_check(self):
        if self.cancel_event.is_set():
            raise ProvisioningCancelled()

    def _queue_log(self, message: str, level: str = "info"):
        self.msg_queue.put(("log", message, level))

    def _run_provisioning(self, ctx: Ctx, selected_ids: set):
        failed = []
        try:
            for i, comp in enumerate(COMPONENTS, start=1):
                self._cancel_check()
                self.msg_queue.put(("step", i, comp.label))
                if comp.id not in selected_ids:
                    self._queue_log(f"===== {comp.label} — skipped (not selected) =====", "info")
                    continue
                self._queue_log(f"===== {comp.label} =====", "info")
                # Each step is isolated. The steps are independent by design —
                # that is the whole premise of the checklist — so one failing
                # must not cost the operator the twelve that would have worked.
                try:
                    comp.fn(ctx)
                except ProvisioningCancelled:
                    raise                       # a cancel is not a step failure
                except subprocess.CalledProcessError as e:
                    failed.append(comp.label)
                    self._report_command_failure(e)
                    self._queue_log(f"[!] {comp.label} FAILED — continuing with the "
                                    f"remaining steps.", "err")
                except Exception as e:      # noqa: BLE001 — surface it, keep going
                    failed.append(comp.label)
                    self._queue_log(f"[!] {comp.label} FAILED: {e!r} — continuing with "
                                    f"the remaining steps.", "err")

            if failed:
                self._queue_log(f"=== {len(failed)} step(s) failed: "
                                f"{', '.join(failed)} ===", "err")
            self.failed_steps = list(failed)
            self.msg_queue.put(("done", not failed, ctx.node_id))
            self._queue_verification(ctx, selected_ids)
        except ProvisioningCancelled:
            self.msg_queue.put(("cancelled", None, None))
            # Still verify: what did land before the stop is the useful part.
            self._queue_verification(ctx, selected_ids)
        except Exception as e:      # noqa: BLE001 — something outside any step
            self._queue_log(f"[!] Unexpected error outside a step: {e!r}", "err")
            self.msg_queue.put(("done", False, None))
            self._queue_verification(ctx, selected_ids)
        finally:
            if ctx.askpass:
                ctx.askpass.close()

    @staticmethod
    def _condense(lines: list) -> list:
        """Collapse runs of progress redraws into a single placeholder.

        flatpak and apt emit one line per percentage tick. A failing flatpak
        install produced 588 of them, burying the one line that said what was
        actually wrong. Lines are compared with digits and whitespace stripped,
        so successive ticks of the same bar collapse while genuinely different
        output — including the error at the end — survives.
        """
        out, last_key, run = [], None, 0
        for line in lines:
            key = re.sub(r"[\d.,:%/]+|\s+", "", line)[:60]
            if key and key == last_key:
                run += 1
                continue
            if run:
                out.append(f"  ... ({run} more progress line(s)) ...")
                run = 0
            out.append(line)
            last_key = key
        if run:
            out.append(f"  ... ({run} more progress line(s)) ...")
        return out

    def _report_command_failure(self, e: subprocess.CalledProcessError):
        cmd_str = " ".join(str(c) for c in e.cmd) if isinstance(e.cmd, (list, tuple)) else str(e.cmd)
        self._queue_log(f"[!] Command failed: {cmd_str} (exit {e.returncode})", "err")
        output = (e.output or "").rstrip()
        if not output:
            return
        # Two audiences, two treatments. The pane gets a condensed tail
        # because an operator reading it under stress needs the error, not
        # 588 progress redraws. The file gets every byte the command emitted,
        # because whoever diagnoses this afterwards needs the part the
        # condensing threw away. Queued rather than written here: the worker
        # thread must not touch the log file the UI thread is writing.
        raw = output.splitlines()
        self.msg_queue.put(("log_file",
                            "--- full captured output (%d line(s)) ---" % len(raw), output))
        lines = self._condense(raw)
        if len(lines) > 40:
            self._queue_log(f"  ... ({len(lines) - 40} earlier line(s) omitted \u2014 "
                            f"the run log has them all) ...", "err")
            lines = lines[-40:]
        for line in lines:
            self._queue_log(f"  {line}", "err")

    def _queue_verification(self, ctx: Ctx, selected_ids: set):
        """Run the read-only checks on the worker thread and stream each row to
        the UI. Never raises: a broken check must not mask the run's outcome."""
        try:
            results = verify_deployment(ctx, selected_ids)
        except Exception as e:  # noqa: BLE001
            results = [CheckResult("Verification itself failed", "fail", repr(e))]
        for r in results:
            self.msg_queue.put(("verify_row", r))
        self.msg_queue.put(("verify_done", len(results)))

    # -- UI thread: drain queue --------------------------------------------
    def _drain_queue(self):
        try:
            while True:
                item = self.msg_queue.get_nowait()
                kind = item[0]
                if kind == "log":
                    _, message, level = item
                    self._append_log(message, level)
                elif kind == "log_file":
                    # File only. The pane already has the condensed version.
                    _, heading, block = item
                    if self.runlog:
                        self.runlog.write(heading, "err")
                        self.runlog.raw(block)
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
                        self.run_outcome = "completed"
                        self.step_label.configure(text=f"Provisioning complete — Node: {node_id}")
                        self._append_log(f"=== PROVISIONING COMPLETE for Node: {node_id} ===", "ok")
                    else:
                        self.run_outcome = "failed"
                        self.step_label.configure(text="Provisioning failed — see log")
                    self._begin_verification()
                elif kind == "cancelled":
                    self.run_outcome = "cancelled"
                    self.step_label.configure(text="Cancelled")
                    self._append_log("=== Cancelled by user ===", "warn")
                    self.cancel_btn.configure(state="disabled")
                    self.close_btn.configure(state="normal")
                    self._begin_verification()
                elif kind == "verify_row":
                    _, result = item
                    self._add_verify_row(result)
                elif kind == "verify_done":
                    self._finish_verification()
        except queue.Empty:
            pass
        self.after(100, self._drain_queue)

    def _begin_verification(self):
        """Deployment finished (any outcome) — move to the verification screen."""
        if "verify" not in self.frames:
            self._build_verify_screen()
        self.check_results = []
        for row in self.verify_tree.get_children():
            self.verify_tree.delete(row)
        self.verify_title.configure(text="Verifying deployment\u2026")
        if self.runlog:
            self.runlog.write("===== Verification =====", "info")
        self.verify_continue_btn.configure(state="disabled")
        self.close_btn.configure(text="Continue \u2192")
        self._show("verify")

    def _add_verify_row(self, result):
        self.check_results.append(result)
        if self.runlog:
            self.runlog.write("%s \u2014 %s" % (result.label, result.detail),
                              {"pass": "ok", "warn": "warn"}.get(result.status, "err"))
        glyph = {"pass": "\u2714", "warn": "!", "fail": "\u2716"}.get(result.status, "?")
        self.verify_tree.insert("", "end", values=(glyph, result.label, result.detail),
                                 tags=(result.status,))
        self.verify_tree.yview_moveto(1.0)

    def _finish_verification(self):
        passed = sum(1 for r in self.check_results if r.status == "pass")
        warned = sum(1 for r in self.check_results if r.status == "warn")
        failed = sum(1 for r in self.check_results if r.status == "fail")
        parts = ["%d passed" % passed]
        if warned:
            parts.append("%d warning(s)" % warned)
        if failed:
            parts.append("%d failed" % failed)
        self.verify_title.configure(text="Verification \u2014 " + ", ".join(parts))
        self.verify_continue_btn.configure(state="normal")

    def _append_log(self, message: str, level: str):
        if self.runlog:
            self.runlog.write(message, level)
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
