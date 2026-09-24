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
import math
import os
import platform
import queue
import re
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# Tk is not bundled with Python on Debian, Ubuntu or Mint, and without it this
# script cannot start at all. Unguarded, that arrives as a ModuleNotFoundError
# traceback: accurate, and useless to the operator it is aimed at. README.md and
# TESTING.md both say to install it beforehand -- this says so at the one moment
# the person who skipped that is actually looking.
try:
    import tkinter as tk
    from tkinter import messagebox, scrolledtext, ttk
except ImportError:
    sys.exit(
        "\nThis provisioner needs Tk, which Debian, Ubuntu and Mint do not bundle\n"
        "with Python. Install it and run this again:\n"
        "\n"
        "    sudo apt install python3-tk\n"
    )

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

#: Release this file belongs to, without the leading "v".
#:
#: Hand-maintained, and deliberately not derived from `git describe`: the
#: released artifact is a tarball with no .git alongside it, so anything
#: asking git would report "unknown" on precisely the copy an operator runs.
#: Bump it in the commit that precedes the tag, so a clone of main never
#: claims to be a release it is ahead of.
VERSION = "1.0.4"

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

#: Mesh -> GPX bridge. 120s is a compromise: each poll opens the serial port
#: for a few seconds, so a faster cadence leaves the radio busy a larger
#: fraction of the time, and mesh positions do not change fast enough to
#: justify it.
MESH_GPX_UNIT_NAME = UNIT_PREFIX + "-mesh-gpx.service"
MESH_GPX_INTERVAL  = 120
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

# ---------------------------------------------------------------------------
# QMapShack settings
#
# QMapShack stores its settings through QSettings, which on Linux is an INI
# file under the organisation name the application sets. QMapShack's main.cpp
# sets that to "QLandkarte", so the file is:
#
#     ~/.config/QLandkarte/QMapShack.conf
#
# NOT QLandkarteGT. That was the predecessor project, and a profile staged
# there is read by nothing -- which is how this provisioner registered its
# offline maps into a directory QMapShack never opens, while a verification
# row confirmed the file was present.
# ---------------------------------------------------------------------------
QMS_CONF_REL = Path(".config") / "QLandkarte" / "QMapShack.conf"
QMS_CONF_LEGACY_REL = Path(".config") / "QLandkarteGT" / "QMapShack.conf"

# QMapShack keys its saved views by a generated "key_<md5>" that cannot be
# predicted from here. It also carries a backward-compatibility path: a view
# group whose name does NOT start with "key_" is taken as a legacy canvas
# name, and that group's settings are loaded (CCanvas.cpp, storedKey branch).
# Seeding under a plain name is therefore stable across versions.
QMS_VIEW_GROUP = "View 1"

# QSettings percent-encodes the space when it WRITES a group name, so the
# view QMapShack saves is "View%201" -- while a literal "View 1" in the file
# reads back identically, because Qt unescapes on read. Both spellings work
# going in; only one comes back out.
#
# That asymmetry is why every check below has to know both. A check that
# knows only the literal form cannot see the group QMapShack itself wrote, so
# on the second run it concludes there is no saved view and seeds one --
# writing a second posFocus line over the operator's own. Seeding is supposed
# to happen exactly once, on a profile that has never been opened.
QMS_VIEW_GROUP_ENC = "View%201"


def qms_view_prefixes() -> tuple:
    """Both spellings of the view group prefix, the one Qt writes first."""
    return ("Views\\%s\\" % QMS_VIEW_GROUP_ENC,
            "Views\\%s\\" % QMS_VIEW_GROUP)

# Square (tile-aligned) scales, which is the table that matches a slippy tile
# pyramid: index i has MPIXEL / 2**(20 - i) metres per pixel, so
# zoomIndex = 20 - slippy_zoom over the 17 levels it defines (z4..z20).
QMS_SCALES_SQUARE = 1
QMS_ZOOM_BASE = 20
QMS_DEFAULT_VIEW_ZOOM = 12      # orientation level; the fetcher always has it


def _qt_ini_escape(raw: bytes) -> tuple:
    """Qt's QSettings INI escaping. Returns (escaped, needs_quotes).

    Reimplemented rather than shelled out to because a node has no Qt Python
    bindings. Verified against Qt's own reader over 3,005 coordinates spanning
    the poles, the antimeridian and the byte patterns that force quoting.
    """
    simple = {0x07: r"\a", 0x08: r"\b", 0x0C: r"\f",
              0x0A: r"\n", 0x0D: r"\r", 0x09: r"\t", 0x0B: r"\v"}
    out, escape_next_if_digit, needs_quotes = [], False, False
    for b in raw:
        if b == 0x00:
            out.append(r"\0")
            escape_next_if_digit = True
            continue
        if b in simple:
            out.append(simple[b])
            escape_next_if_digit = False
            continue
        if b == 0x22:
            out.append('\\"')
            escape_next_if_digit = False
            continue
        if b == 0x5C:
            out.append("\\\\")
            escape_next_if_digit = False
            continue
        if b <= 0x1F or b >= 0x7F:
            # Qt writes the hex unpadded and lowercase, which is exactly why
            # it then escapes a following hex digit: "\xe" + "f" would
            # otherwise read back as a single byte 0xef.
            out.append("\\x%x" % b)
            escape_next_if_digit = True
            continue
        ch = chr(b)
        if ch in ";,=":
            needs_quotes = True
        if escape_next_if_digit and ch in "0123456789abcdefABCDEF":
            out.append("\\x%x" % b)
            escape_next_if_digit = True
            continue
        out.append(ch)
        escape_next_if_digit = False
    return "".join(out), needs_quotes


def qsettings_qpointf(x: float, y: float) -> str:
    """The exact INI text QSettings writes for QPointF(x, y).

    Most of QMapShack's settings are plain text. The view centre is not: it is
    a QPointF serialised as a binary QVariant, type id 26, two big-endian
    doubles, escaped into the INI. There is no text form QMapShack will read
    instead -- QVariant::toPointF() on a string yields (0, 0).
    """
    blob = b"@Variant(" + struct.pack(">Idd", 26, x, y) + b")"
    escaped, needs_quotes = _qt_ini_escape(blob)
    return '"%s"' % escaped if needs_quotes else escaped


def utm_proj_for(lat: float, lon: float) -> str:
    """A proj4 string for the UTM zone containing (lat, lon).

    QMapShack has no MGRS or USNG support of any kind -- the strings appear
    nowhere in its source, and its grid takes a proj4 projection. US National
    Grid is MGRS on NAD83, so a UTM grid for the operating area's zone draws
    the same lines USNG does; what it cannot do is label them with USNG's
    100 km square letters. That is a QMapShack limitation, not a setting.
    """
    zone = min(60, max(1, int((lon + 180.0) / 6.0) + 1))
    south = " +south" if lat < 0 else ""
    return ("+proj=utm +zone=%d%s +datum=WGS84 +units=m +no_defs"
            % (zone, south))


def tile_zoom_range(layer_dir: Path) -> tuple:
    """(min_z, max_z) actually present on disk, or None when nothing is.

    A .tms that does not declare its range gets QMapShack's defaults of 1 and
    21, so the canvas asks for zoom levels the fetcher never downloaded. Every
    one of those is a miss that still costs a path lookup.
    """
    zooms = []
    try:
        for child in layer_dir.iterdir():
            if child.is_dir() and child.name.isdigit():
                zooms.append(int(child.name))
    except OSError:
        return None
    return (min(zooms), max(zooms)) if zooms else None


#: A .tms MinZoomLevel/MaxZoomLevel is NOT a slippy zoom. It is QMapShack's own
#: scale index, and it runs the other way: CMapTMS::draw() computes
#:
#:     zMax = 21 - layer.minZoomLevel      # highest slippy z the layer may use
#:     zMin = 21 - layer.maxZoomLevel      # lowest slippy z the layer may use
#:
#: so the element named "Min" bounds the zoomed-IN end. Writing the slippy
#: numbers straight in told QMapShack a z10-15 pyramid served z6-z11: it then
#: clamped the canvas to z11, asked for z6-z9 tiles that do not exist, and sat
#: on "22 tiles pending" over a blank canvas. Found on a node, not in a test.
TMS_ZOOM_BASE = 21


def tms_zoom_levels(zrange: tuple) -> tuple:
    """(MinZoomLevel, MaxZoomLevel) for a slippy (min_z, max_z) tile range."""
    min_z, max_z = zrange
    return TMS_ZOOM_BASE - max_z, TMS_ZOOM_BASE - min_z


def _qms_text_value(s: str) -> str:
    """A plain string as QSettings would write it into an INI."""
    escaped, needs_quotes = _qt_ini_escape(s.encode("utf-8"))
    return '"%s"' % escaped if needs_quotes else escaped


def qms_map_key(tms: Path) -> str:
    """QMapShack's identifier for a map file: MD5 of its first 4096 bytes.

    CMapItem::setFilename() hashes exactly that much and uses the hex digest
    as the key everything else in the config hangs off. A .tms is far smaller
    than 4 KiB, so in practice this is the hash of the whole file -- and it
    changes whenever the file does, which is why the map registration below is
    written in the same step that writes the .tms.
    """
    with tms.open("rb") as f:
        return hashlib.md5(f.read(4096)).hexdigest()


def _canvas_edit(lines: list, additions: list) -> list:
    """Insert key=value lines at the end of the [Canvas] section."""
    block = ["%s=%s" % (k, v) for k, v in additions]
    try:
        start = lines.index("[Canvas]")
    except ValueError:
        if lines and lines[-1].strip():
            lines.append("")
        return lines + ["[Canvas]"] + block
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("["):
            end = i
            break
    lines[end:end] = block
    return lines


def configure_qmapshack(conf: Path, maps_dir: Path, tms_files: list,
                        lat=None, lon=None,
                        zoom: int = QMS_DEFAULT_VIEW_ZOOM) -> dict:
    """Make QMapShack find, draw and open on the offline maps.

    Three separate things have to be true before a downloaded tile pyramid is
    visible, and none of them follows from the others:

      1. mapPath has to name the directory, or QMapShack never lists the .tms
         files at all. This used to be written only when a group supplied a
         QMapShack.conf to stage -- and configs/ ships empty, so on a stock
         clone it was never written and the maps were invisible.
      2. A map QMapShack meets for the first time is added with status Unused
         (CMapDraw::loadMapList), which means listed but NOT drawn. Registering
         it under map2/keysKnownMaps with isActive=true is what makes it draw
         on first launch instead of after the operator finds and clicks it.
      3. The view has to be over the area the tiles cover.

    Every part is a seed, never an overwrite: an operator who has run
    QMapShack once has their own answer to all three in this file, and
    QMapShack rewrites it on exit.

    tms_files is in priority order: the first becomes the active map, the rest
    are left for the operator to switch to. Two active raster layers stack, and
    the upper one simply hides the lower, which reads as the lower one being
    broken.
    """
    # surrogateescape, not "replace": this is Qt's file, and a byte this
    # provisioner cannot decode must come back out unchanged rather than as a
    # replacement character written over somebody's staged profile.
    lines = (conf.read_text(errors="surrogateescape").splitlines()
             if conf.is_file() else [])
    done = {}

    # Hand-edited rather than run through configparser: this file is Qt's, not
    # ours. configparser would rewrite every line it did not understand, and
    # the keys here carry backslashes and the values carry Qt's own escaping.
    def has(key):
        return any(l.startswith(key + "=") for l in lines)

    def has_view(suffix):
        """True when EITHER spelling of the view group carries this key."""
        return any(has(p + suffix) for p in qms_view_prefixes())

    # --- 1. the directory the .tms files live in --------------------------
    want_path = str(maps_dir)
    for i, line in enumerate(lines):
        if line.startswith("mapPath="):
            if want_path in line:
                done["mapPath"] = "already listed"
            else:
                # Qt writes a QStringList comma-separated, so this appends
                # rather than replacing whatever the operator already had.
                lines[i] = line + ", " + want_path
                done["mapPath"] = "appended"
            break
    else:
        lines = _canvas_edit(lines, [("mapPath", _qms_text_value(want_path))])
        done["mapPath"] = "added"

    # --- 2. the maps themselves, so the first one draws -------------------
    # Two schemas, because which one the installed QMapShack reads depends on
    # its version and the field build is older than the rename:
    #
    #   map2/keysKnownMaps + map2/<key>/isActive   current QMapShack
    #   map/active = <key>, ...                    what came before it
    #
    # A build that predates map2 ignores those keys completely -- the maps
    # still appear in the Maps tab, because loadMapList() scans mapPath and
    # adds whatever it finds as Unused, but nothing is activated and the
    # canvas stays empty until the operator clicks one. That is exactly the
    # symptom this function exists to prevent, so both are written.
    #
    # Current QMapShack reads map/active too: loadMapList() applies it after
    # map2, unconditionally, calling activate() on each key. The cost is that
    # nothing ever deletes that legacy group there, so it re-activates on
    # every launch. On the build this targets it is the native key and gets
    # rewritten on exit, which is the trade being made.
    vprefix = qms_view_prefixes()[0]
    prefix = vprefix + "map2\\"
    present = [f for f in tms_files if f.is_file()]
    if not present:
        done["maps"] = "no .tms files to register"
    elif has_view("map2\\keysKnownMaps") or has_view("map\\active"):
        done["maps"] = "kept"
    else:
        keys = [qms_map_key(f) for f in present]
        adds = [(prefix + "keysKnownMaps", ", ".join(keys[:1]))]
        # Only the first is activated; see the docstring on stacking.
        adds.append((prefix + keys[0] + "\\isActive", "true"))
        adds.append((prefix + keys[0] + "\\filename",
                     _qms_text_value(str(present[0]))))
        adds.append((vprefix + "map\\active", keys[0]))
        lines = _canvas_edit(lines, adds)
        done["maps"] = "registered %s as the active map" % present[0].name

    # --- 3. the view over the area ----------------------------------------
    if lat is None or lon is None:
        done["view"] = "no operating area"
    elif has_view("posFocus"):
        done["view"] = "kept"
    else:
        lines = _canvas_edit(lines, [
            (vprefix + "posFocus",
             qsettings_qpointf(math.radians(lon), math.radians(lat))),
            (vprefix + "map2\\zoomIndex", str(QMS_ZOOM_BASE - zoom)),
            # Same split as the map list above: a build predating map2 reads
            # the zoom index from map/, and ignoring that opened the canvas
            # at whatever QMapShack defaulted to rather than over the area.
            (vprefix + "map\\zoomIndex", str(QMS_ZOOM_BASE - zoom)),
            (vprefix + "scales", str(QMS_SCALES_SQUARE)),
            (vprefix + "grid\\proj", _qms_text_value(utm_proj_for(lat, lon))),
        ])
        done["view"] = "seeded"

    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("\n".join(lines) + "\n", errors="surrogateescape")
    return done


def area_centre(spec: dict) -> tuple:
    """(lat, lon) for an operating area given either way, or None."""
    centre = spec.get("center") or {}
    if centre.get("lat") is not None and centre.get("lon") is not None:
        try:
            return float(centre["lat"]), float(centre["lon"])
        except (TypeError, ValueError):
            return None
    try:
        box = {k: float(spec[k]) for k in ("north", "south", "east", "west")}
    except (KeyError, TypeError, ValueError):
        return None
    return ((box["north"] + box["south"]) / 2.0,
            (box["east"] + box["west"]) / 2.0)

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
            "%s Field Node provisioner v%s -- run log" % (OPERATOR_PREFIX, VERSION),
            "=" * 72,
            "Started    : %s" % self.started.strftime("%Y-%m-%d %H:%M:%S %Z").strip(),
            # Which build produced this transcript. The summary screen shows
            # it too, but the screen closes and this file is what gets
            # attached to a problem report.
            "Version    : v%s" % VERSION,
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
    # `make install` succeeded, so /usr/bin/satdump and
    # /usr/share/applications/satdump.desktop exist. Gates anything needing
    # those paths -- specifically the desktop shortcut in finalize().
    #
    # The name is this precise because one flag once conflated "a binary
    # exists somewhere" with "it was installed to /usr", and a failed
    # `make install` still added a shortcut to a binary that was not there.
    # A build-tree SatDump is usable and reads ~/.config/satdump the same
    # way; it just has nothing under /usr to point a shortcut at.
    satdump_system_installed: bool = False

    def __post_init__(self):
        self.data_dir = self.home / DATA_DIR_NAME
        # The child ctx.run() is currently waiting on, so Cancel can reach it.
        # Written from the worker thread and read from the Tk thread, hence
        # the lock: without it the reader can see a process object that the
        # worker has already finished with.
        self._active_child = None
        self._child_lock = threading.Lock()
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
    def run(self, cmd, check=True, interruptible=True, **kw):
        """Run a child and wait for it. Interruptible by Cancel, by default.

        Popen rather than subprocess.run so the process is nameable while it
        runs -- subprocess.run gives no handle until it is over, which is the
        reason Cancel used to be checkpoint-only. start_new_session puts the
        child in its own process group so terminate_active_child() can signal
        the whole tree: killing `make` alone leaves its compilers running.

        interruptible=False for anything that must not be stopped part-way.
        Nothing in this file passes it yet; ctx.sudo() is the blunt version of
        the same idea, and is never interruptible -- see there.
        """
        self.cancel_check()
        kw.setdefault("stdin", subprocess.DEVNULL)
        kw.setdefault("stdout", subprocess.PIPE)
        kw.setdefault("stderr", subprocess.STDOUT)
        kw.setdefault("text", True)
        if not interruptible:
            return subprocess.run(cmd, check=check, **kw)

        proc = subprocess.Popen(cmd, start_new_session=True, **kw)
        with self._child_lock:
            self._active_child = proc
        try:
            out, _ = proc.communicate()
        finally:
            with self._child_lock:
                self._active_child = None
        # A terminated child surfaces as an ordinary non-zero exit. Ask whether
        # Cancel was pressed before reporting it as a command failure, so a
        # deliberate stop does not read as a broken step.
        self.cancel_check()
        if check and proc.returncode != 0:
            raise subprocess.CalledProcessError(proc.returncode, cmd, output=out)
        return subprocess.CompletedProcess(cmd, proc.returncode, out, None)

    def terminate_active_child(self):
        """Signal the child ctx.run() is waiting on, if any. Safe to call from
        another thread, and a no-op when nothing is running.

        SIGTERM to the process group, not the process: `make -j` and
        `fetch_map_tiles.py` both have children of their own, and signalling
        only the leader leaves those orphaned and still working.
        """
        with self._child_lock:
            proc = self._active_child
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            # Already gone, or not ours to signal. The cancel event still
            # stops the run at the next checkpoint either way.
            pass

    def sudo(self, *args, check=True, **kw):
        """Deliberately NOT interruptible, unlike ctx.run().

        Almost everything that goes through sudo here is a package
        transaction -- apt, dpkg, debconf -- and killing one part-way leaves
        dpkg needing `dpkg --configure -a` before anything else can install.
        A cancelled run that also breaks the package manager is a worse
        outcome than one that takes another thirty seconds to stop.

        `make install` is the other sudo caller, and writing half a SatDump
        into /usr is the same class of problem.
        """
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

echo "[1/5] Restacking Time & Positioning Services..."
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

echo "[2/5] Spawning Station Log Window..."
if [ -f "$HOME/scripts/blotter.sh" ]; then
    x-terminal-emulator -e "$HOME/scripts/blotter.sh" &
fi
sleep 1

# ADS-B is deliberately NOT launched here. dump1090 and SatDump both drive the
# RTL-SDR dongle and only one process can hold it, so starting ADS-B at dock
# would silently cost the operator satellite imagery. It is a "here if you want
# it" install, started by hand -- see EMCOMM_Data/ADSB/dump1090_setup.md.
#
# This slot previously announced "Starting ADS-B Radar Engine..." and then
# launched only if ~/dump1090 existed, which nothing ever created: the line
# claimed a thing it had not done, on every dock event.

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

echo "[3/5] Launching JS8Call..."
if command -v js8call &> /dev/null; then
    js8call &
fi
sleep 2

echo "[4/5] Initializing ion2G HF ALE ..."
ION2G_EXE_PATH="$(cat "$HOME/@STATE_DIR@/ion2g_exe_path" 2>/dev/null)"
if [ -n "$ION2G_EXE_PATH" ] && [ -f "$ION2G_EXE_PATH" ]; then
    (cd "$(dirname "$ION2G_EXE_PATH")" && wine "$ION2G_EXE_PATH" &)
fi
sleep 2

echo "[5/5] Deploying QLog & QMapShack..."
if command -v qlog &> /dev/null; then
    qlog &
fi
if command -v qmapshack &> /dev/null; then
    qmapshack &
fi

echo "=== [$(date)] @OPERATOR_PREFIX@ Operational Stack Deployed ==="
"""

DONGLE_ARBITRATION_MD = """# The RTL-SDR Dongle: Who Has It

Staged by the provisioner. Nothing here is applied automatically.

One dongle, several programs that want it, and **only one can hold it at a
time**. This is the reference for handing it back and forth deliberately
rather than discovering the contention during a pass.

## Four layers, only one of which you control

Most of what decides "who has the dongle" is not configurable. Knowing which
layer you are fighting saves a lot of time.

### 0. The kernel may take it first

Plugging in an RTL-SDR can auto-load `dvb_usb_rtl28xxu`, the driver that treats
it as a DVB-T television tuner. If that happens, no userspace SDR program can
open the device at all -- not dump1090, not SatDump, not `rtl_test`.

```bash
lsmod | grep -i rtl28xxu          # any output means the kernel has it
sudo rmmod dvb_usb_rtl28xxu       # release it for this session
```

**The provisioner blacklists it** -- see **The blacklist** below. No package
does: `librtlsdr2` creates `/etc/modprobe.d/` but ships no file in it, and
neither does `rtl-sdr` or `dump1090-mutability`, so without this step the
kernel wins the race on a freshly imaged node.

### 1. Permissions decide who *may* open it

The device node is `root:plugdev 0660`, from
`/usr/lib/udev/rules.d/60-librtlsdr2.rules`. Anything opening the dongle must be
in the `plugdev` group.

Your own account is. So is the `dump1090` service user -- the provisioner adds
it, because the daemon runs as its own user (`--chuid` in the init script) and
`adduser --system` would otherwise leave it in `nogroup` only. Without that it
starts, fails to open the device, and exits, leaving an empty aircraft map and
no obvious error.

```bash
id                                            # you: expect plugdev
id "$(awk -F= '$1=="DUMP1090_USER"{gsub(/"/,"",$2); print $2}' \
      /etc/default/dump1090-mutability)"      # the service user: expect plugdev
```

### 2. Exclusivity is absolute

libusb claims the USB interface. First process to open it wins; everyone else
fails. There is no sharing, no priority, and no setting that changes this. This
is the layer people expect to configure, and it is the one that cannot be.

### 3. Who starts, and when -- the only layer you control

| | dump1090 | SatDump |
| --- | --- | --- |
| at boot | runlevel links, **removed by the provisioner** | never starts itself |
| on demand | `START_DUMP1090="yes"` in `/etc/default/dump1090-mutability`, then `service ... start` | you launch it |

dump1090 has **two** switches and both must be right: `START_DUMP1090` decides
whether it can start *at all* (the init script tests it on every start, not just
at boot), and the runlevel links decide whether it starts *at boot*. The
provisioner sets the first to `yes` and removes the second, which is what makes
it startable on demand while leaving the dongle free at boot.

## Handing it over

Work out who has it first:

```bash
sudo fuser -v /dev/bus/usb/*/*
ps aux | grep -E '[d]ump1090|[s]atdump'
```

**To ADS-B:**

```bash
sudo service dump1090-mutability start
```

**To SatDump:**

```bash
sudo service dump1090-mutability stop && sudo pkill -x dump1090-mutability
satdump-ui
```

Both halves matter. `service ... stop` only reaches the daemon the init script
started -- `start-stop-daemon` matches on the service user -- so a copy you
launched by hand from a terminal survives it untouched.

**To confirm nothing holds it:**

```bash
rtl_test -t
```

`rtl_test` succeeding means the dongle is free and openable by you. It failing
with a busy or permission error means something above still has it, or you are
fighting layer 0 or 1.

## The blacklist

The provisioner writes:

```
/etc/modprobe.d/emcomm-rtlsdr.conf
    blacklist dvb_usb_rtl28xxu
```

Only that one module is named. It is the driver that binds the USB device; the
demodulator and tuner modules load as its dependencies rather than on their own,
so blacklisting the binder is enough.

No `update-initramfs` is needed. The blacklist is read by modprobe, and a USB
dongle is bound by udev calling modprobe long after the initramfs is out of the
picture -- `dvb_usb_rtl28xxu` is never in an initramfs, because no root
filesystem needs it.

If the module was already loaded when the provisioner ran, it is also unloaded
there and then, so the dongle is free on that run rather than after the next
reboot.

**To use the dongle as a DVB-T receiver instead**, delete that file and reboot.
That is the trade: this node treats the RTL-SDR as an SDR, and television
reception is the thing given up. Any other SDR hardware needs its own driver
work regardless, which none of this touches.

## Before a satellite pass

The cost of getting this wrong is asymmetric. A dump1090 left running does not
announce itself; SatDump simply fails to open the device, and a pass that
happens once is missed. Stopping ADS-B before a pass is cheap and reversible;
the pass is not.
"""


DUMP1090_SETUP_MD = """# ADS-B Reception (dump1090) — Setup Reference

Staged by the provisioner. Nothing here is applied automatically: the receiver
position is operator data, and the dongle is shared hardware.

## The dongle is single-user

**See `EMCOMM_Data/SDR/dongle_arbitration.md` for the full picture** — the
kernel driver, the permission layer, and handing the dongle between programs.
This section covers only the dump1090 side of it.

SatDump and dump1090 both drive the RTL-SDR dongle, and only one process can
hold it at a time. Whichever starts first wins; the other fails to open the
device. This is why the provisioner removes dump1090's boot entry, and why the
dock autostart sequence does not launch it -- a service that took the dongle at
boot would silently cost you satellite imagery.

Start it when you want ADS-B, and stop it before a satellite pass:

```bash
sudo service dump1090-mutability start
sudo service dump1090-mutability stop
```

## Two switches, not one

Worth knowing, because they fail in different ways and only one is obvious.

`START_DUMP1090` in `/etc/default/dump1090-mutability` is checked by the init
script on **every** start, not just at boot. Set to `"no"`, the start command
above prints nothing and does nothing, `/run/dump1090-mutability/` is never
created, and the bundled map loads but its data fetch answers 404 --
"Problem fetching data from dump1090". The provisioner leaves it `"yes"`.

The runlevel links are what control boot, and the package installs them
regardless of that setting. The provisioner removes them. To check both:

```bash
grep START_DUMP1090 /etc/default/dump1090-mutability   # expect "yes"
systemctl is-enabled dump1090-mutability               # expect disabled
```

Running the binary straight from a terminal is a third thing again: it decodes
to your screen and holds the dongle, but writes no JSON, so the map stays empty
however long you leave it. Use the service.

## The service user needs the dongle

The daemon does not run as you. The init script starts it with
`--chuid "$DUMP1090_USER"`, and the package creates that user with
`adduser --system`, which puts it in `nogroup` and nothing else.

The dongle is not readable by `nogroup`. `librtlsdr2` ships
`/usr/lib/udev/rules.d/60-librtlsdr2.rules` with `GROUP="plugdev"` and
`MODE="0660"`, so the device node is `root:plugdev 0660`. Without membership
the daemon starts, fails to open the device, and exits:

```
usb_open error -3
Error opening the RTLSDR device: Permission denied
```

`service ... start` still returns 0, and the map stays empty. Your own account
is in `plugdev`, so running the binary by hand works perfectly -- which makes
this look like anything except a permissions problem.

The provisioner adds the service user to `plugdev`. To check, or to repair by
hand:

```bash
id "$(awk -F= '$1=="DUMP1090_USER"{gsub(/"/,"",$2); print $2}' \
      /etc/default/dump1090-mutability)"      # expect plugdev in the list
sudo adduser dump1090 plugdev
sudo service dump1090-mutability restart
```

Group membership applies to new processes, so restarting the service is enough
-- no logout needed.

Confirm which process holds the dongle:

```bash
sudo fuser -v /dev/bus/usb/*/* 2>/dev/null
rtl_test -t                      # fails if something else has it
```

Confirm which process holds the dongle:

```bash
rtl_test -t                      # fails if something else has it
```

## Receiver position is not set

`decode-lat` and `decode-lon` are deliberately left empty. They are your
station's position -- the same class of data as a grid square -- and setting
them is your call, not the provisioner's.

Without them dump1090 still decodes aircraft and shows them on the map. What
you lose is range rings, distance-from-station, and correct handling of
surface-position messages.

To set them:

```bash
sudo dpkg-reconfigure dump1090-mutability
```

## Viewing

The package installs a lighttpd site serving the aircraft map at
`http://<this-node>/dump1090/`. Unlike the document server, which is bound to
loopback, this arrives on the distribution default. Check it before deploying:

```bash
sudo ss -ltnp | grep lighttpd
```

Raw output for other tools is on TCP 30003 (Basestation format) and 30005
(Beast binary).

## Antenna

1090 MHz wants its own antenna. The stock dongle whip will show aircraft
overhead and little else; a proper 1090 MHz collinear or a filtered ADS-B
antenna is the difference between 20 nm and 200 nm.
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

## WARNING: `meshtastic --info` prints your private key

Its output includes `security.privateKey` in plaintext, along with the
node's position and every peer it has heard. It is a useful liveness check
and a fine thing to read; it is not a thing to paste into a bug report, an
issue, a forum post or a screenshot. If it has already gone somewhere
public, regenerate the node's keypair.

To share evidence of a problem, send the specific lines that matter, or
the service journal:

    journalctl --user -u emcomm-mesh-gpx -n 50

## 5. Backup / restore config

Back up a known-good node and clone it to the rest of the fleet:

    meshtastic --export-config > node_backup.yaml
    meshtastic --configure node_backup.yaml

## The USB port: what else on this machine may claim it

Read this before troubleshooting a node that "is not detected". A serial
port is an exclusive open -- first process wins, and the loser reports a
broken radio rather than a conflict.

On a stock Mint node with a Meshtastic board attached, **measured rather
than assumed, nothing claims the port**. The detail is worth keeping,
because the reasoning that predicts otherwise is plausible and wrong.

**gpsd does not claim by bridge chip on Debian or Mint.** Its hotplug
rules live at `/usr/lib/udev/rules.d/60-gpsd.rules`, and the generic
USB-serial bridge entries in it -- PL2303, FTDI, CP210x -- are all
**commented out**, each marked:

    # !!! rule disabled in Debian as it matches too many other devices

What remains enabled matches specific GPS receivers by exact VID:PID:
u-blox 5 through 9, Garmin GPSmap, Delorme, ATEN UC-232A, a MediaTek
HOLUX, a Telit module. A Meshtastic board is not in that list and will
not be auto-attached, whatever bridge chip it uses and whatever the
Serial Module is set to.

Upstream gpsd does ship the broad bridge rules, so this is a
Debian-specific narrowing. On another distribution, check the file
before assuming the same.

**ModemManager flags the device but does not take it.** udev marks a
Meshtastic board `ID_MM_CANDIDATE=1`, so MM is entitled to probe it.
Observed on a Seeed Wio Tracker L1 Pro (`2886:1668`, native CDC-ACM):
`mmcli -L` reports no modems, the journal records no probe, and
`meshtastic --info` connects first try with no delay. The flag is an
invitation ModemManager declines.

**brltty** claims CH340/CH341 adapters as braille displays, and is the
one worth knowing about because the device node does not go busy -- it
does not appear at all, so the symptom is "nothing enumerated" rather
than "something has it". Not exercised here: it was inactive on the
node tested, and the board tested is native CDC rather than CH340.

Working out which, if a node ever does go quiet:

    ls -l /dev/serial/by-id/          # is it there at all -- if not, suspect brltty
    sudo lsof /dev/ttyACM0            # who holds it (ttyUSB0 for bridge-chip boards)
    systemctl is-active ModemManager gpsd.socket brltty
    mmcli -L                          # has ModemManager taken it as a modem
    udevadm info -q property -n /dev/ttyACM0 | grep -E 'ID_VENDOR_ID|ID_MODEL_ID|ID_MM'

If something is holding it, the durable fix is to exclude the node's own
VID:PID from that service -- `ENV{ID_MM_DEVICE_IGNORE}="1"` in a udev
rule for ModemManager, the same shape as `/etc/modprobe.d/` keeping the
kernel DVB-T driver off the RTL-SDR. Stopping a service works for one
session and comes back on the next boot or dock event; the dock autostart
restarts `gpsd.socket` every time it runs.

## NMEA position output - LEAVE THIS OFF

The Serial Module can emit NMEA 0183: a $GNGGA sentence for the node's
own position, plus $GPWPL waypoint sentences for every mesh peer
reporting a valid position, at 38400 8N1.

**Do not enable it unless you have a specific need and have read the
whole of this section.** It is off by default, which is the correct
state for this node.

The reason is not contention. An earlier version of this document said
enabling NMEA would let gpsd take the port; on Debian and Mint it will
not, because gpsd auto-attaches only to a listed GPS receiver and a
Meshtastic board is not one -- see the section above.

The reason is that **the integration it exists for does not work**, so
turning it on buys nothing and costs a setting you then have to remember
is set:

* **gpsd cannot carry the peer waypoints.** Its client protocol reports
  TPV and SKY objects -- time/position/velocity and satellite data for
  the *host's own* fix. There is no waypoint object in that protocol, so
  $GPWPL sentences have nowhere to go regardless of whether gpsd parses
  them.
* **QMapShack does not read gpsd.** Its realtime sources are OpenSky
  flight data, AIS vessel positions, and GPS location data over a TCP
  NMEA connection -- not the gpsd daemon. A serial-to-TCP bridge would
  be needed, and that still only moves the node's own position.

Note this is independent of the radio's own GPS. `position.gps_enabled`
puts the node's location into mesh packets over LoRa and presents
nothing to the host; leave it on if you want position in the mesh. It is
the Serial Module that puts data on the USB port.

If you want mesh peer positions on the map, the path that actually
works is a file, not a live feed: read the node list and convert it to
GPX, which QMapShack imports directly. The provisioner installs that
bridge when QMapShack is also present -- a user service that polls the
node database and rewrites

    ~/EMCOMM_Data/Meshtastic/mesh_nodes.gpx

Import that file into a QMapShack project. It is refreshed on a timer;
QMapShack is not believed to re-read a file that changes underneath it,
so a refreshed map means importing again. Each waypoint carries its own
last-heard time, so a peer that has gone quiet keeps its last known
position and says how old it is rather than vanishing.

The bridge never holds the serial port: it opens, reads, closes, and
skips a cycle if something else has the radio. Run
`emcomm-mesh-gpx --once --dump` to see what it would write, or read the
raw list yourself with

    meshtastic --nodes

To turn it on anyway, knowing the above:

    meshtastic --set serial.enabled true
    meshtastic --set serial.mode NMEA

VERIFIED on a Seeed Wio Tracker L1 Pro against a provisioned node: the
board enumerates as native CDC-ACM, no service claims the port, and
`meshtastic --info` connects first try.

VERIFIED 2026-09-17 against a live mesh -- a Wio Tracker L1 Pro on a
9-node mesh, peers 0 to 7 hops out. Four of nine nodes had positions and
exactly those four reached the GPX. Three matched `meshtastic --info` to
seven decimal places on position, altitude and last-heard time; the fourth
was the local node, whose GPS had refreshed in the 71 seconds between the
two commands. `meshtastic --info` connected normally while the service was
running, so the bridge does not hold the port.

The import was exercised on QMapShack 1.17.1: the project loads and every
positioned peer appears by short name. Load it with

    qmapshack "$HOME/EMCOMM_Data/Meshtastic/mesh_nodes.gpx"

or drag the file onto the workspace -- the menus move between versions,
those two do not.

QMapShack does NOT notice the file changing on disk -- tested, not
assumed. With a waypoint moved half a degree and the file swapped in by
rename, the same way the bridge writes it, the open project did not
redraw. An import is a snapshot. Every load also creates a NEW project
rather than updating one, so a refreshed map means deleting the stale
project and importing again.

An empty-looking map is not a failed import. QMapShack logs "Empty
filename passed to function", repeatedly and on every pan, when its
registered map sources point at tile directories that hold nothing -- the
state of a node whose map step has not run, or one operating outside the
fetched area. The waypoints are still there.

A NOTE ON PRECISION. A peer's position may be a mesh broadcast rather than
a GPS reading, and broadcasts are quantized by the channel's
positionPrecision. On the mesh above, at precision 13, two nodes two
kilometres apart reported identical coordinates to seven decimal places.
Treat a peer waypoint as "roughly here", not as a survey point -- the
node's own position is the only one that arrives at full precision.

UNVERIFIED: that gpsd ignores $GPWPL and that QMapShack cannot read gpsd
are read from their documentation, not observed.

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

    # rtl-sdr for the command-line tools, not the library: librtlsdr2 arrives as
    # a dependency of dump1090 and SatDump and brings the udev rules, but
    # rtl_test and friends live in this separate package. The staged ADS-B
    # reference tells the operator to run `rtl_test -t` to prove the dongle
    # contention, which was "command not found" on every node built here.
    #
    # Also installed by each SDR step via _ensure_rtl_sdr_tools(), because this
    # step is independently selectable and a node that took SatDump and dump1090
    # without it had no rtl_test at all. Kept here as well so a node running
    # neither SDR step still gets the tools with the rest of the base system;
    # apt is idempotent, so the overlap costs nothing.
    packages = [
        "git", "curl", "wget", "build-essential",
        "gpsd", "gpsd-clients", "chrony", "tmux",
        "python3-pip", "python3-venv",
        "qmapshack", "kiwix", "kiwix-tools", "chirp", "js8call", "dirmngr",
        "rtl-sdr",
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
    for d in (ctx.home / QMS_CONF_REL.parent, ctx.home / ".config" / "qlog",
              ctx.home / ".local" / "share" / "CHIRP"):
        d.mkdir(parents=True, exist_ok=True)

    # QMapShack profile carries HOME_PLACEHOLDER tokens instead of absolute paths,
    # so it does not assume the operator's username.
    qms_conf = ctx.home / QMS_CONF_REL
    legacy_conf = ctx.home / QMS_CONF_LEGACY_REL
    if legacy_conf.is_file():
        ctx.log(f"[!] {legacy_conf} exists and QMapShack does not read it — earlier "
                f"runs of this provisioner staged there. Anything you customised in "
                f"it needs moving to {qms_conf} by hand; nothing is copied "
                f"automatically, because a stale profile would overwrite a good one.",
                "warn")
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
    #
    # ServerUrl, not Script. QMapShack resolves a tile path by calling
    # CMapTMS::createUrl() for EVERY tile on EVERY redraw, and for a <Script>
    # layer that means constructing a fresh QJSEngine and re-evaluating the
    # JavaScript each time, under a mutex. A <ServerUrl> is a QString::arg
    # substitution and starts no engine at all.
    #
    # %1/%2/%3, NOT {z}/{x}/{y}. createUrl() ends in
    #
    #     return layer.strUrl.arg(z).arg(x).arg(y);
    #
    # so the URL must already carry Qt's own place markers. QMapShack does
    # rewrite the brace form into them -- but only since v1.20.0, changelog
    # entry QMS-920. On 1.17.1, which is what Mint 22.3 ships, the braces
    # survive untouched, .arg() finds nothing to replace, and every request
    # asks for a file literally named "{z}/{x}/{y}.png". Nothing exists at
    # that path, so nothing decodes, nothing caches, and the canvas sits on
    # "N tiles pending" forever. Observed on a node, 2026-09-22.
    #
    # %1/%2/%3 is what QMapShack's own bundled .tms files use, and it works on
    # every version. A home directory containing a literal "%1" would break
    # it, which is not a path anyone has.
    #
    # MinZoomLevel/MaxZoomLevel default to 1 and 21 -- QMapShack's own scale
    # indices, meaning slippy z0-z20 -- so without them the canvas asks for
    # levels the fetcher never downloaded. They are read from what is on disk
    # rather than assumed, so a node that fetched a different range still
    # describes itself correctly, and they are written in QMapShack's
    # inverted numbering rather than in slippy zooms. See TMS_ZOOM_BASE.
    tiles_root = ctx.data_dir / "Offline_Maps" / "Offline_Tiles"
    for layer, tms_name, title in MAP_LAYERS:
        zrange = tile_zoom_range(tiles_root / layer)
        if zrange is None:
            zoom_lines = ""
            ctx.log(f"[!] No tiles under {tiles_root / layer} — {tms_name} written "
                    f"without a zoom range. Run the map step, then re-run this one.",
                    "warn")
        else:
            # Inverted on purpose -- see TMS_ZOOM_BASE.
            zoom_lines = ("  <MinZoomLevel>%d</MinZoomLevel>\n"
                          "  <MaxZoomLevel>%d</MaxZoomLevel>\n"
                          % tms_zoom_levels(zrange))
        (ctx.data_dir / "Offline_Maps" / tms_name).write_text(f"""<TMS>
<Layer idx="0">
  <Title>{title}</Title>
{zoom_lines}  <ServerUrl>file://{ctx.data_dir}/Offline_Maps/Offline_Tiles/{layer}/%1/%2/%3.png</ServerUrl>
</Layer>
</TMS>
""")

    # Everything QMapShack needs before a downloaded tile pyramid is visible.
    # Deliberately NOT guarded on a staged profile existing: configs/ ships
    # empty, so the old guard meant that on a stock clone -- which is every
    # node unless a group supplies a profile -- mapPath was never written and
    # the maps this provisioner had just spent an hour downloading were
    # invisible to the application that exists to draw them.
    centres = []
    for area_path in sorted(AREA_DIR.glob("*.json")):
        try:
            spec = json.loads(area_path.read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        if is_unmodified_sample(spec):
            continue
        centre = area_centre(spec)
        if centre:
            centres.append(centre)
    # Several areas: the midpoint of them all, which puts the first view
    # somewhere every area is reachable from rather than favouring one.
    lat = sum(c[0] for c in centres) / len(centres) if centres else None
    lon = sum(c[1] for c in centres) / len(centres) if centres else None

    result = configure_qmapshack(
        qms_conf, ctx.data_dir / "Offline_Maps",
        [ctx.data_dir / "Offline_Maps" / tms for _l, tms, _t in MAP_LAYERS],
        lat, lon)

    ctx.log(f"[+] QMapShack map path {result['mapPath']}: "
            f"{ctx.data_dir}/Offline_Maps", "ok")

    if result["maps"] == "kept":
        ctx.log("[+] QMapShack already knows these map sources — left as they are.", "ok")
    elif result["maps"].startswith("registered"):
        ctx.log(f"[+] QMapShack map sources {result['maps']}. A map QMapShack meets "
                f"for the first time is listed but not drawn, so without this the "
                f"Maps tab shows the sources and the canvas stays empty.", "ok")
        others = [tms for _l, tms, _t in MAP_LAYERS][1:]
        if others:
            ctx.log(f"[*] {', '.join(others)} stay off — two raster layers stack and "
                    f"the upper hides the lower. Click one in the Maps tab to switch.",
                    "info")
    else:
        ctx.log(f"[!] {result['maps']} — run the map step first, then re-run this one.",
                "warn")

    if result["view"] == "no operating area":
        ctx.log("[!] No operating area, so QMapShack keeps its built-in view centre "
                "(12E 49N, central Europe) and will open on empty canvas. Define an "
                "area and re-run this step.", "warn")
    elif result["view"] == "kept":
        ctx.log("[+] QMapShack already has a saved view centre — left as it is.", "ok")
    else:
        ctx.log(f"[+] QMapShack first view centred on {lat:.4f}, {lon:.4f} at zoom "
                f"{QMS_DEFAULT_VIEW_ZOOM}, grid set to the area's UTM zone.", "ok")
        ctx.log("[*] QMapShack overwrites this file when it closes, so all of this is "
                "a starting point, not a lock — move the view or switch layers and it "
                "stays moved.", "info")

    # "Every application will start unconfigured" stopped being true when the
    # QMapShack map configuration moved out of the profile path: on a stock
    # clone this line fired directly under eight lines reporting that map path,
    # active source and view had all been written. A summary that contradicts
    # the log above it teaches people to distrust the log.
    maps_note = ("" if result.get("mapPath") is None else
                 " QMapShack's offline maps are configured separately, above, and"
                 " are not affected by this.")
    if staged and not missing:
        ctx.log(f"[+] Config profiles staged: {', '.join(staged)}.", "ok")
    elif staged:
        ctx.log(f"[!] Staged {', '.join(staged)} — NOT staged: {', '.join(missing)}. "
                f"Those applications will start on their own defaults.", "warn")
    else:
        ctx.log(f"[!] No config profiles staged — configs/ holds none of them, which "
                f"is how it ships. {', '.join(missing)} will start on their own "
                f"defaults.{maps_note}", "err")
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

    # Four files land and each reports success, which is the whole of what this
    # step can demonstrate. The chain they form -- dock insertion, udev rule,
    # dispatcher, user unit, launcher -- has never been observed to run, and
    # there is no dock on hand to try it against. README.md and TESTING.md both
    # say so; neither is open at the moment the operator watches four green
    # lines go by, which is when the claim is actually being made.
    ctx.log("[!] Dock-trigger automation is a FUTURE FEATURE — installed, not proven.", "warn")
    ctx.log("    It is being designed for a Panasonic Toughbook CF-30 in a Havis "
            "DS-PAN-111 dock. The udev rule matches that dock's hub (05e3:0610) — a "
            "real ID, but one no dock event has ever been seen to match here.", "warn")
    ctx.log("    No dock insertion has ever been observed to fire this chain, on that "
            "hardware or any other. The files above are on disk; that is all that has "
            "been confirmed.", "warn")
    ctx.log("    Further development against the hardware is needed before a deployment "
            "relies on it — see 'Dock trigger: verifying it actually fires' in "
            "TESTING.md.", "warn")


GPS_CHRONY_DROPIN = "/etc/chrony/conf.d/10-%s-gps.conf" % PROJECT
GPS_DEFAULTS_FILE = "/etc/default/gpsd"

# The chrony side is safe to write on every node, with or without a receiver.
# chronyd parses a refclock line with no gpsd running and simply reports the
# source as unreachable -- checked against chrony 4.5 before shipping it.
GPS_CHRONY_CONF = """# EmComm GPS time source. Written by the provisioner.
#
# GPS time arrives through gpsd's NTP shared-memory segment 0. chronyd creates
# that segment while it is still root, then drops privileges; gpsd attaches to
# it as root. That is why gpsd.service carries After=chronyd.service, and why
# this provisioner restarts chrony BEFORE gpsd.
#
# offset: NMEA latency. The sentence describes an epoch it does not finish
# transmitting until ~200ms later, most of that being 70 bytes at 4800 baud.
# Measured at +206ms on a GlobalSat BU-353N; 0.2 left ~7ms residual, which is
# inside the sentence-order jitter and not worth chasing.
#
# delay: honest uncertainty for an NMEA-only source. There is no PPS on a USB
# puck, so this must not claim the sub-microsecond a PPS refclock would. It is
# why the source reports +/-101ms when its real agreement with NTP is ~7ms.
#
# prefer: policy, not accuracy. Without it chronyd selects on error bounds and
# the tighter NTP servers win -- and worse, when the network drops chronyd goes
# on steering from an UNREACHABLE server for hours, because a stale source's
# dispersion grows at only ~1ppm and takes that long to exceed 101ms. Measured
# on a node: reach 0, last contact 190s earlier, still selected over a live GPS
# at reach 377. A node built to outlive the network must not do that.
#
# NOT 'trust': prefer wins selection without telling chronyd to believe a GPS
# that has gone wrong. trust would let a bad refclock reject healthy NTP.
refclock SHM 0 refid GPS0 offset 0.2 delay 0.2 prefer
"""


def _read_gps_binding():
    """Return (device, baud) declared in configs/gps.conf, or (None, None).

    A declaration, never a probe. Opening a serial port asserts DTR, and some
    CAT interfaces key PTT on DTR or RTS -- a provisioner that swept ttyUSB*
    hunting for a GPS could put a radio on the air. The operator names the
    device; this step trusts them and touches nothing else.

    The file is per-unit hardware and is gitignored: a by-id path carries the
    receiver's serial number, which is machine data and does not belong in the
    repository. configs/gps.conf.sample ships instead.
    """
    src = Path("configs") / "gps.conf"
    if not src.is_file():
        return None, None
    device = baud = None
    try:
        for raw in src.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            key = key.strip()
            if key == "DEVICE":
                device = value or None
            elif key == "BAUD":
                baud = value or None
    except OSError as e:
        raise RuntimeError("could not read configs/gps.conf: %s" % e)
    return device, baud


def optional_gps_time(ctx: Ctx):
    """Give the node a time source that survives losing the network.

    Without this, chrony's only configured sources are internet NTP pools. A
    deployed node cannot reach them, never synchronises, and `chronyc tracking`
    reports "Not synchronised" indefinitely -- which matters most to JS8Call,
    whose timed transmit windows want the clock inside about a second.
    """
    ctx.log("[*] Configuring GPS time source (gpsd + chrony)...", "info")

    # Written unconditionally: harmless without a receiver, and it means a puck
    # attached later needs only the gpsd half rather than a re-provision.
    ctx.sudo("mkdir", "-p", "/etc/chrony/conf.d")
    ctx.sudo_write(GPS_CHRONY_DROPIN, GPS_CHRONY_CONF)
    ctx.log(f"[+] chrony refclock written to {GPS_CHRONY_DROPIN}.", "ok")

    # A hand-configured node may already carry a refclock on the same SHM unit
    # under a different filename, or a line left in chrony.conf itself. Two
    # sources on SHM 0 is not a configuration anyone intended, so say so rather
    # than quietly adding a duplicate.
    for other in sorted(Path("/etc/chrony/conf.d").glob("*.conf")):
        if other.name == Path(GPS_CHRONY_DROPIN).name:
            continue
        try:
            if "refclock SHM 0" in other.read_text():
                ctx.log(f"[!] {other} also declares refclock SHM 0. Two sources on "
                        f"the same segment is not intended — remove one before "
                        f"restarting chrony.", "warn")
        except OSError:
            pass
    try:
        main_cfg = Path("/etc/chrony/chrony.conf").read_text()
        if any(l.strip().startswith("refclock SHM 0") for l in main_cfg.splitlines()):
            ctx.log("[!] /etc/chrony/chrony.conf carries its own refclock SHM 0 line. "
                    "It will duplicate this one — comment it out; conf.d is where "
                    "this belongs.", "warn")
    except OSError:
        pass

    device, baud = _read_gps_binding()
    if not device:
        ctx.log("[!] No configs/gps.conf with a DEVICE line — gpsd left unbound. "
                "The chrony side is in place; declare the receiver's "
                "/dev/serial/by-id/... path and re-run this step. See "
                "configs/gps.conf.sample and checklist section 14.", "warn")
        return

    if not Path(device).exists():
        ctx.log(f"[!] configs/gps.conf names {device}, which is not present. "
                f"Writing the gpsd config anyway — it will bind when the "
                f"receiver is attached.", "warn")

    # -n: gpsd must poll without a client. chrony reads shared memory and never
    #     connects, so without this the segment stays empty and nothing errors.
    # -b: read-only. gpsd's probe writes lock up some receivers -- they locked
    #     up the BU-353N this was built against, which is exactly the case the
    #     flag exists for. A provisioner cannot know what puck gets attached,
    #     so the option that cannot break an unknown receiver is the default.
    opts = "-n -b" + (f" -s {baud}" if baud else "")
    backup = GPS_DEFAULTS_FILE + ".%s-backup" % PROJECT
    if Path(GPS_DEFAULTS_FILE).is_file() and not Path(backup).is_file():
        ctx.sudo("cp", GPS_DEFAULTS_FILE, backup)
    ctx.sudo_write(GPS_DEFAULTS_FILE,
                   'DEVICES="%s"\nGPSD_OPTIONS="%s"\nUSBAUTO="true"\n' % (device, opts))
    ctx.log(f"[+] gpsd bound to {device} ({opts}).", "ok")

    # gpsd.service is not enabled by default -- only gpsd.socket is, and socket
    # activation starts gpsd when a CLIENT connects. chrony is not a client, so
    # on a stock node gpsd never runs and the refclock never sees a sample.
    ctx.sudo("systemctl", "enable", "gpsd.service", check=False)

    # Order matters: chronyd owns the shared-memory segment, so restarting it
    # invalidates the attachment gpsd holds. chrony first, gpsd second.
    ctx.sudo("systemctl", "restart", "chrony", check=False)
    ctx.sudo("systemctl", "restart", "gpsd.service", check=False)
    ctx.log("[+] chrony restarted, then gpsd — in that order.", "ok")
    ctx.log("[*] Acquisition takes roughly a minute from cold. Confirm with "
            "`chronyc tracking` — Reference ID should read GPS0.", "info")


def _read_radio_binding():
    """(adevice, ptt_device, ptt_method) from configs/radio.conf, or Nones.

    A declaration, never a probe -- the same rule the GPS step follows, and
    for a sharper reason here. Opening a serial port asserts its control
    lines, and this interface's PTT is wired to one of them, so a provisioner
    that swept ttyUSB* looking for a radio could put one on the air.

    configs/radio.conf is gitignored: a by-id path carries the interface's
    serial number. configs/radio.conf.sample ships instead.
    """
    src = Path("configs") / "radio.conf"
    if not src.is_file():
        return None, None, None
    values = {}
    try:
        for line in src.read_text(errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            values[key.strip().upper()] = val.strip().strip('"').strip("'")
    except OSError as e:
        raise RuntimeError("could not read configs/radio.conf: %s" % e)
    return (values.get("ADEVICE") or None,
            values.get("PTT_DEVICE") or None,
            values.get("PTT_METHOD") or None)


def _alsa_card_ids() -> list:
    """Card ids from /proc/asound/cards. Reads a file; opens no device."""
    ids = []
    try:
        for line in Path("/proc/asound/cards").read_text(errors="replace").splitlines():
            m = re.match(r"\s*(\d+)\s+\[([^\]]+)\]", line)
            if m:
                ids.append((int(m.group(1)), m.group(2).strip()))
    except OSError:
        pass
    return ids


def direwolf_ptt_line(device, method) -> str:
    """The Direwolf PTT directive for a declared binding.

    CM108 is a method rather than a device: Direwolf takes the chip's GPIO
    with an optional /dev/hidraw* path. Everything else is a serial control
    line and needs the port named first.
    """
    method = (method or "RTS").strip()
    if method.upper() == "CM108":
        return "PTT CM108" + (" " + device if device else "")
    return "PTT %s %s" % (device, method)


#: udev rule that keeps ModemManager away from the declared radio interface.
RADIO_MM_RULE = "/etc/udev/rules.d/99-emcomm-radio-no-modemmanager.rules"


def modemmanager_id_serial(ptt_device):
    """ID_SERIAL for a /dev/serial/by-id/ path, or None if one cannot be read.

    udev builds a by-id name as usb-<ID_SERIAL>-if<NN>-port<N>, so the value a
    udev rule has to match on is already inside the path the operator
    declared. Deriving it from the declaration rather than from the device is
    the whole point: the rule can be written with the interface unplugged, and
    nothing opens a port that keys a transmitter in order to find out what to
    write.

    A numbered path carries no serial, so this returns None and the caller
    says why -- one more reason to declare the by-id form.
    """
    if not ptt_device:
        return None
    name = os.path.basename(ptt_device.strip())
    if not name.startswith("usb-"):
        return None
    name = name[len("usb-"):]
    # Most specific first: a serial number containing "-port" survives as long
    # as the interface suffix is the thing at the end.
    for pat in (r"-if[0-9a-fA-F]{2}-port\d+$", r"-if[0-9a-fA-F]{2}$", r"-port\d+$"):
        m = re.search(pat, name)
        if m:
            name = name[:m.start()]
            break
    return name or None


def radio_mm_rule_text(id_serial) -> str:
    """The udev rule body, as a function so the step and the test agree."""
    return (
        "# EmComm Field Node - keep ModemManager off the radio interface.\n"
        "#\n"
        "# ModemManager probes an unknown serial port by WRITING AT commands to\n"
        "# it. Writing opens the port, opening asserts RTS, and on this interface\n"
        "# RTS is what keys the transmitter -- so a probe can put a station on\n"
        "# the air that nobody asked to transmit, into an antenna that may not be\n"
        "# connected, under a callsign that may be a placeholder.\n"
        "#\n"
        "# ID_SERIAL is the string udev builds the /dev/serial/by-id/ path from,\n"
        "# so this match came out of the declaration in configs/radio.conf. The\n"
        "# bus was never scanned, and nothing was opened to write it.\n"
        "#\n"
        "# Re-run the Direwolf step after changing interfaces. A rule naming a\n"
        "# device you no longer use protects nothing and looks like it does.\n"
        'ACTION=="add|change", SUBSYSTEM=="tty", ENV{ID_SERIAL}=="%s", '
        'ENV{ID_MM_DEVICE_IGNORE}="1"\n' % id_serial
    )


def _udev_property(device, key):
    """One property from udev's database for a device node, or None.

    `udevadm info` reads the database and sysfs; it does not open the device.
    That distinction is the reason this is safe to call against an interface
    whose control lines key a transmitter, where reading the port is not.
    """
    try:
        out = subprocess.run(["udevadm", "info", "-q", "property", "-n", device],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True)
    except OSError:
        return None
    if out.returncode != 0:
        return None
    for line in out.stdout.splitlines():
        k, _, v = line.partition("=")
        if k == key:
            return v
    return None


def optional_direwolf(ctx: Ctx):
    with ctx.spin("Installing Direwolf...") as spin_result:
        proc = ctx.sudo("apt", "install", "-y", "direwolf", check=False)
        status = proc.returncode
        spin_result.ok = (status == 0)

    # The configuration is written whenever direwolf is actually present, not
    # only when this run installed it. apt failing on a node that already has
    # direwolf -- no network, a held lock, a partly-configured package -- used
    # to skip the whole config block, leaving no direwolf.conf behind a green
    # "Direwolf installed" verification row, because that row asks which() and
    # not apt. The install and the configuration are two things, and one
    # failing should not silently cancel the other.
    present = shutil.which("direwolf") is not None
    if status != 0:
        # Not "the install failed": a non-zero exit from apt says the
        # transaction ended badly, not that the package is absent or broken.
        # apt returns non-zero for a held lock, a failing post-invoke hook, or
        # a warning it was configured to treat as fatal, on a machine where
        # the package was already installed and perfectly usable. Report the
        # status and let the output below say what happened.
        ctx.log("[!] apt exited %d. That is the transaction's status, not a "
                "statement about the package — direwolf may be installed and "
                "fine. apt said:" % status, "warn")
        # ctx.sudo() captures stdout and stderr into a pipe, and this step used
        # to read only the return code and drop the rest -- then tell the
        # operator to check a log that had nothing in it. Say what apt said.
        output = (proc.stdout or "").strip()
        if output:
            for line in output.splitlines()[-12:]:
                ctx.log("    apt: " + line, "warn")
        else:
            ctx.log("    (apt produced no output)", "warn")

    if status == 0 or present:
        if status == 0:
            ctx.log("[+] Direwolf installed.", "ok")
        else:
            ctx.log("[*] direwolf is already on PATH, so its configuration is written "
                    "anyway — the failure above did not remove it.", "info")
        direwolf_config_dir = ctx.home / ".config" / "direwolf"
        direwolf_config_dir.mkdir(parents=True, exist_ok=True)

        adevice, ptt_device, ptt_method = _read_radio_binding()

        # Read-only reconnaissance, printed to help the operator fill the file
        # in. /proc/asound/cards is a file read and by-id is a directory
        # listing; neither opens a device, which matters because opening this
        # one keys a transmitter.
        cards = _alsa_card_ids()
        if cards:
            ctx.log("[*] Sound cards present: " + ", ".join(
                "%d [%s]" % (n, i) for n, i in cards), "info")
        by_id = sorted(Path("/dev/serial/by-id").glob("*")) \
            if Path("/dev/serial/by-id").is_dir() else []
        if by_id:
            ctx.log("[*] Serial interfaces present: " + ", ".join(
                p.name for p in by_id), "info")

        if adevice:
            audio_block = ("# Declared in configs/radio.conf.\nADEVICE %s" % adevice)
            card_ids = [i for _n, i in cards]
            m = re.search(r"CARD=([^,]+)", adevice)
            if m and card_ids and m.group(1) not in card_ids:
                ctx.log(f"[!] configs/radio.conf names sound card {m.group(1)!r}, which "
                        f"is not present ({', '.join(card_ids) or 'no cards'}). Writing it "
                        f"anyway — it will work once the interface is attached.", "warn")
        else:
            audio_block = ("# --- AUDIO DEVICE — UNVERIFIED PLACEHOLDER ---\n"
                           "# ADEVICE was not declared, so this is a GUESS. Run `arecord -l`,\n"
                           "# then set ADEVICE in configs/radio.conf.\n"
                           "ADEVICE plughw:1,0")

        if ptt_device or (ptt_method or "").upper() == "CM108":
            ptt_block = ("# Declared in configs/radio.conf.\n"
                         + direwolf_ptt_line(ptt_device, ptt_method))
            if ptt_device and not Path(ptt_device).exists():
                ctx.log(f"[!] configs/radio.conf names PTT device {ptt_device}, which is "
                        f"not present. Writing it anyway — it will bind when the "
                        f"interface is attached.", "warn")
        else:
            ptt_block = ("# --- PTT — UNVERIFIED PLACEHOLDER ---\n"
                         "# No configs/radio.conf, so this is a GUESS, and for a Digirig\n"
                         "# Mobile it is the wrong one: its PTT is an open-collector switch\n"
                         "# on the CP2102 serial port's RTS line, not a CM108 GPIO. See\n"
                         "# configs/radio.conf.sample.\n"
                         "PTT CM108")

        # ModemManager probes an unknown serial port by writing AT commands to
        # it, and on this interface writing asserts RTS, which is what keys the
        # transmitter. The remedy has been documented in the Meshtastic
        # troubleshooting notes since that step was written; it was never
        # installed for the one device where a stray probe transmits.
        mm_serial = modemmanager_id_serial(ptt_device)
        if mm_serial:
            ctx.sudo_write(RADIO_MM_RULE, radio_mm_rule_text(mm_serial))
            ctx.sudo("udevadm", "control", "--reload-rules", check=False)
            ctx.log("[+] ModemManager exclusion installed for %s." % mm_serial, "ok")
            ctx.log("[*] udev evaluates rules on the device event, so this applies the "
                    "next time the interface is attached — not to a port something is "
                    "already holding. Checklist section 4 says not to have a radio "
                    "connected while the node is first configured; that ordering is "
                    "what makes this land before anything can key.", "info")
        elif ptt_device:
            ctx.log("[!] %s is a numbered path, so no ModemManager exclusion was "
                    "written — the rule matches on the serial that only the "
                    "/dev/serial/by-id/ form carries. ModemManager probes unknown "
                    "serial ports by writing to them, and writing asserts RTS. "
                    "Declare the by-id path and re-run this step." % ptt_device, "warn")

        (direwolf_config_dir / "direwolf.conf").write_text(f"""# --- {OPERATOR_PREFIX} Direwolf Base Config ---
# Callsign pulled from Node ID set during provisioning.
# WARNING: transmitting AX.25/APRS traffic requires a valid, licensed
# amateur radio callsign. Do NOT transmit under this value unless it
# is a real, currently-licensed callsign.
MYCALL {ctx.node_id}

{audio_block}
CHANNEL 0
MODEM 1200

{ptt_block}

AGWPORT 8000
KISSPORT 8001
""")
        ctx.log(f"[+] Base Direwolf config written to {direwolf_config_dir}/direwolf.conf", "ok")
        home_symlink = ctx.home / "direwolf.conf"
        home_symlink.unlink(missing_ok=True)
        home_symlink.symlink_to(direwolf_config_dir / "direwolf.conf")
        ctx.log(f"[+] Symlinked to {ctx.home}/direwolf.conf for Direwolf's default search path.", "ok")

        if adevice and (ptt_device or (ptt_method or "").upper() == "CM108"):
            ctx.log(f"[+] Radio interface declared: audio {adevice}, "
                    f"{direwolf_ptt_line(ptt_device, ptt_method)}.", "ok")
            ctx.log("[!] Declared, not proven. Nothing here has keyed a radio — watch it "
                    "key before relying on the station to transmit.", "warn")
        elif Path("configs/radio.conf").is_file():
            ctx.log("[!] configs/radio.conf is present but does not declare both ADEVICE "
                    "and PTT_DEVICE, so those are still placeholders and Direwolf will "
                    "not work as shipped. Fill the empty values in and re-run this step. "
                    "See checklist sections 3 and 4.", "warn")
        else:
            ctx.log("[!] No configs/radio.conf — ADEVICE/PTT left as placeholders and "
                    "Direwolf will not work as shipped. Copy configs/radio.conf.sample, "
                    "fill it in, and re-run this step. See checklist sections 3 and 4.",
                    "warn")
    else:
        ctx.log("[!] direwolf is not on PATH either, so no configuration was written. "
                "Fix the install and re-run this step.", "err")


#: The mesh -> GPX bridge, written to the node by the Meshtastic step when
#: QMapShack is also installed. Embedded rather than shipped as a repo file
#: because the released artifact is a tarball of this tree and the script has
#: to land on the node whatever the operator cloned.
MESH_TO_GPX_PY = r'''#!/usr/bin/env python3
"""Meshtastic mesh peer positions -> GPX, for import into QMapShack.

Staged by the EMCOMM provisioner. Runs as a systemd --user service
(emcomm-mesh-gpx.service) and can also be run by hand:

    emcomm-mesh-gpx --once            one poll, write the file, exit
    emcomm-mesh-gpx --loop            poll forever (what the service runs)
    emcomm-mesh-gpx --once --dump     print the GPX instead of writing it

WHY A FILE AND NOT A LIVE FEED

The Serial Module's NMEA output cannot carry peer positions to a host:
gpsd's client protocol reports TPV and SKY objects only, so $GPWPL
waypoint sentences have no object to arrive in, and QMapShack's realtime
sources are OpenSky, AIS and GPS over TCP NMEA -- not gpsd. A file is the
path that works. See the Meshtastic setup reference for the whole argument.

WHAT THIS DOES NOT DO

It does not make QMapShack track the mesh live. QMapShack imports a GPX
into a project as a snapshot; it is not believed to re-read a file that
changes underneath it. This daemon keeps the file current so a re-import
is cheap -- it does not animate the map. That belief is UNVERIFIED: if
QMapShack does pick up changes on its own, this daemon is better than
advertised, not worse.

THE SERIAL PORT IS NOT HELD

Each poll opens the port, reads the node database, and closes it again,
even on failure. A daemon that held the port would block every
`meshtastic` command the operator typed, and the CLI would report a
broken radio rather than a busy one. If the port is busy or absent, the
poll is skipped with a warning and the previous file is left alone.
"""
import argparse
import os
import sys
import time
import xml.sax.saxutils as su
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_INTERVAL = 120
GPX_NS = "http://www.topografix.com/GPX/1/1"


def _log(msg: str) -> None:
    """One line to stderr, which systemd routes to the journal.

    Unit name is the journal tag, so `journalctl --user -u emcomm-mesh-gpx`
    finds these. Nothing calls logger(1), which without -t would tag them
    with the invoking user instead of anything searchable.
    """
    print(msg, file=sys.stderr, flush=True)


def _iso(ts) -> str:
    """Epoch seconds -> RFC3339 UTC, the only time format GPX accepts."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def node_position(node: dict):
    """(lat, lon, alt) for a node, or None when it has no usable fix.

    Meshtastic stores position as latitudeI/longitudeI in units of 1e-7
    degrees and the client library derives the float form from them, so a
    node that has never reported a position has no 'latitude' key at all
    -- and one whose fix was cleared reports exactly 0/0. The library
    itself treats 0/0 as unset (`if position.latitude_i != 0 and ...`),
    so this does too. A node sitting on Null Island is indistinguishable
    from one with no fix, which is the library's convention, not a
    judgement about the Gulf of Guinea.
    """
    pos = node.get("position") or {}
    lat = pos.get("latitude")
    lon = pos.get("longitude")
    if lat is None or lon is None:
        return None
    if lat == 0 and lon == 0:
        return None
    return float(lat), float(lon), pos.get("altitude")


def nodes_to_gpx(nodes: dict, generated=None) -> tuple:
    """Render the node database as GPX 1.1. Returns (xml, positioned, total).

    Pure: no hardware, no clock beyond `generated`, no I/O. Everything
    interesting about this script is testable through this function.
    """
    generated = generated or datetime.now(timezone.utc)
    nodes = nodes or {}
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="emcomm-mesh-gpx" xmlns="%s">' % GPX_NS,
        "  <metadata>",
        "    <name>Meshtastic mesh peers</name>",
        "    <desc>Peer positions as of the last poll. Each waypoint carries its "
        "own last-heard time: a stale peer keeps its old position until it is "
        "heard from again.</desc>",
        "    <time>%s</time>" % generated.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "  </metadata>",
    ]
    positioned = 0
    for node_id in sorted(nodes):
        node = nodes[node_id] or {}
        fix = node_position(node)
        if fix is None:
            continue
        positioned += 1
        lat, lon, alt = fix
        user = node.get("user") or {}
        name = user.get("shortName") or user.get("longName") or node_id
        long_name = user.get("longName") or ""

        detail = []
        if long_name and long_name != name:
            detail.append(long_name)
        detail.append("id %s" % node_id)
        if user.get("hwModel"):
            detail.append("hw %s" % user["hwModel"])
        if node.get("hopsAway") is not None:
            detail.append("%s hop(s)" % node["hopsAway"])
        if node.get("snr") is not None:
            detail.append("snr %s" % node["snr"])
        battery = (node.get("deviceMetrics") or {}).get("batteryLevel")
        if battery is not None:
            detail.append("battery %s%%" % battery)
        if node.get("lastHeard"):
            detail.append("heard %s" % _iso(node["lastHeard"]))

        out.append('  <wpt lat="%.7f" lon="%.7f">' % (lat, lon))
        if alt is not None:
            out.append("    <ele>%.1f</ele>" % float(alt))
        if node.get("lastHeard"):
            out.append("    <time>%s</time>" % _iso(node["lastHeard"]))
        out.append("    <name>%s</name>" % su.escape(str(name)))
        out.append("    <desc>%s</desc>" % su.escape(" | ".join(detail)))
        out.append("  </wpt>")
    out.append("</gpx>")
    return "\n".join(out) + "\n", positioned, len(nodes)


def write_atomic(path: Path, text: str) -> None:
    """Write via a temp file in the same directory, then rename.

    QMapShack may be reading the file at any moment. A partial GPX is not
    a smaller GPX, it is an XML parse error, so the reader must never see
    one: os.replace is atomic within a filesystem.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def poll_once(port=None, timeout=30):
    """Connect, read the node database, disconnect. None if unreachable.

    The import is deferred to here so --help and the GPX rendering work
    without the meshtastic package present, which is what makes this
    file testable off a provisioned node.
    """
    try:
        from meshtastic.serial_interface import SerialInterface
    except ImportError:
        _log("[!] the 'meshtastic' package is not importable by this interpreter "
             "(%s). The provisioner installs it into the operator's user site: "
             "python3 -m pip install --user --break-system-packages meshtastic"
             % sys.executable)
        return None

    iface = None
    try:
        iface = SerialInterface(devPath=port, timeout=timeout)
        return dict(iface.nodes or {})
    except Exception as exc:  # noqa: BLE001 — any failure means skip this poll
        _log("[!] poll skipped: could not read the node database (%s: %s). The port "
             "may be in use by an interactive meshtastic command, or no node is "
             "attached. The previous GPX is left as it was."
             % (type(exc).__name__, exc))
        return None
    finally:
        # Releasing the port matters more than a clean shutdown: an
        # unclosed interface holds /dev/ttyACM* until this process exits,
        # which is exactly the contention this daemon must not create.
        if iface is not None:
            try:
                iface.close()
            except Exception as exc:  # noqa: BLE001
                _log("[!] error closing the serial interface (%s) — the port may stay "
                     "held until this process exits." % type(exc).__name__)


def run_once(output: Path, port=None, dump=False) -> bool:
    nodes = poll_once(port)
    if nodes is None:
        return False
    xml, positioned, total = nodes_to_gpx(nodes)
    if dump:
        sys.stdout.write(xml)
    else:
        write_atomic(output, xml)
    if total == 0:
        _log("[!] the node database is empty — nothing has been heard yet. Wrote a "
             "GPX with no waypoints.")
    elif positioned == 0:
        _log("[!] %d node(s) known, none reporting a position — wrote a GPX with no "
             "waypoints. Peers only appear here once they send position, which "
             "requires position.gps_enabled on their node." % total)
    else:
        _log("[+] wrote %d of %d node(s) with positions to %s" % (positioned, total, output))
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Meshtastic mesh peer positions -> GPX for QMapShack.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="one poll, then exit")
    mode.add_argument("--loop", action="store_true", help="poll until stopped")
    ap.add_argument("--output", type=Path,
                    default=Path.home() / "EMCOMM_Data" / "Meshtastic" / "mesh_nodes.gpx")
    ap.add_argument("--interval", type=int, default=DEFAULT_INTERVAL,
                    help="seconds between polls in --loop (default %d)" % DEFAULT_INTERVAL)
    ap.add_argument("--port", default=None, help="serial device (default: autodetect)")
    ap.add_argument("--dump", action="store_true",
                    help="with --once, print the GPX instead of writing it")
    args = ap.parse_args(argv)

    if args.interval < 30:
        # Each poll opens the port for several seconds. Polling faster than
        # that leaves the radio busy more often than not, which is the
        # contention this daemon exists to avoid creating.
        _log("[!] --interval %d is below the 30s floor; using 30." % args.interval)
        args.interval = 30

    if not args.loop:
        return 0 if run_once(args.output, args.port, args.dump) else 1

    _log("[*] polling every %ds -> %s" % (args.interval, args.output))
    while True:
        run_once(args.output, args.port)
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
'''


#: Long-running user service. Not a timer: one unit owns the loop, so
#: `systemctl --user status` shows a single thing either running or not.
#: Restart=always covers the process dying; the poll loop already survives a
#: failed poll, so a restart means something worse than a busy port, and
#: RestartSec is generous for the same reason -- a node that is simply absent
#: should not produce a restart storm in the journal.
MESH_GPX_UNIT = """[Unit]
Description=EMCOMM Meshtastic to GPX bridge for QMapShack
Documentation=file:%(home)s/EMCOMM_Data/Meshtastic/meshtastic_setup.md
After=default.target

[Service]
Type=simple
ExecStart=%(python)s %(script)s --loop --interval %(interval)d --output %(output)s
Restart=always
RestartSec=30

[Install]
WantedBy=default.target
"""


def _install_mesh_gpx_bridge(ctx: Ctx, python3: str) -> bool:
    """Mesh -> GPX bridge, installed only when QMapShack is also present.

    Conditional because the bridge has exactly one consumer. On a node with
    no QMapShack there is nothing to import the file, so installing a service
    to write it would be a daemon polling a radio on a timer for no reader --
    cost with no benefit, and a serial port touched for nothing.

    The false branch is not silent, per rule 1: a positive-form guard whose
    else is silence is how a step comes to report success having installed
    half of what its name implies.
    """
    qmapshack = shutil.which("qmapshack")
    if not qmapshack:
        ctx.log("[!] QMapShack not found on PATH — the mesh-to-GPX bridge was NOT "
                "installed. It exists only to feed QMapShack, so there is nothing "
                "here for it to feed.", "warn")
        ctx.log("    QMapShack arrives with the system-packages step. Install it, "
                "re-run this step, and the bridge lands then.", "warn")
        return False

    script = ctx.home / APPS_DIR_NAME / "mesh_to_gpx.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(MESH_TO_GPX_PY)
    script.chmod(0o755)

    output = ctx.data_dir / "Meshtastic" / "mesh_nodes.gpx"
    output.parent.mkdir(parents=True, exist_ok=True)

    # On PATH so the operator can force a poll by hand -- the first thing to
    # do when the map looks wrong. It names the interpreter explicitly rather
    # than relying on a shebang, so it keeps working if ~/.local/bin is not
    # yet on PATH.
    local_bin = ctx.home / ".local" / "bin"
    local_bin.mkdir(parents=True, exist_ok=True)
    wrapper = local_bin / "emcomm-mesh-gpx"
    wrapper.write_text('#!/bin/bash\nexec "%s" "%s" "$@"\n' % (python3, script))
    wrapper.chmod(0o755)

    systemd_user = ctx.home / ".config" / "systemd" / "user"
    systemd_user.mkdir(parents=True, exist_ok=True)
    (systemd_user / MESH_GPX_UNIT_NAME).write_text(MESH_GPX_UNIT % {
        "home": ctx.home,
        "python": python3,
        "script": script,
        "interval": MESH_GPX_INTERVAL,
        "output": output,
    })
    ctx.run(["systemctl", "--user", "daemon-reload"], check=False)
    enabled = ctx.run(["systemctl", "--user", "enable", MESH_GPX_UNIT_NAME],
                      check=False).returncode == 0

    ctx.log(f"[+] Mesh-to-GPX bridge installed — {script}", "ok")
    ctx.log(f"[+] Writes {output} every {MESH_GPX_INTERVAL}s; import it into a "
            f"QMapShack project.", "ok")
    if enabled:
        # Enabled but deliberately NOT started. The same step may have just
        # added this user to 'dialout', which does not take effect until they
        # log in again -- a service started now would fail every poll on a
        # permission error until then and fill the journal with it. Enabling
        # means it comes up at the moment the group membership does.
        ctx.log("[!] The bridge starts at your NEXT LOGIN, not now — the same login "
                "that makes 'dialout' take effect. To start it sooner, log out and "
                "back in, then: systemctl --user start " + MESH_GPX_UNIT_NAME, "warn")
    else:
        ctx.log("[!] Could not enable " + MESH_GPX_UNIT_NAME + " — the bridge is on "
                "disk but will not start on its own. Enable it by hand: "
                "systemctl --user enable --now " + MESH_GPX_UNIT_NAME, "err")
    ctx.log("    A poll on demand, without the service: emcomm-mesh-gpx --once", "info")
    return True


def optional_meshtastic(ctx: Ctx):
    # Installed into the operator's user site rather than an isolated venv.
    # Noble enforces PEP 668, so this needs --break-system-packages to be
    # permitted at all; --user keeps it out of /usr/lib and confines it to
    # ~/.local, so "break system packages" overstates what actually happens
    # here. The package is 'meshtastic' -- there is no 'meshtastic-cli' on
    # PyPI -- and it is what provides the `meshtastic` console script.
    #
    # `python3 -m pip` rather than `pip3`: it pins the install to the
    # interpreter that will import it. The GPX bridge below runs under that
    # same interpreter, and a pip3 belonging to a different python is exactly
    # how a step comes to report a successful install of something the next
    # step cannot import.
    python3 = shutil.which("python3") or sys.executable
    local_bin = ctx.home / ".local" / "bin"
    meshtastic_bin = local_bin / "meshtastic"
    ok = False

    with ctx.spin("Installing Meshtastic Python CLI...") as spin_result:
        pip_status = ctx.run([python3, "-m", "pip", "install", "--user",
                              "--break-system-packages", "meshtastic"],
                             check=False).returncode
        # The console script landing on disk is the presence half; being
        # importable by the interpreter that will run the bridge is the half
        # that matters. Both, or this did not work.
        importable = ctx.run([python3, "-c", "import meshtastic"],
                             check=False).returncode == 0
        ok = (pip_status == 0) and os.access(meshtastic_bin, os.X_OK) and importable
        spin_result.ok = ok

    if not ok:
        ctx.log(f"[!] Meshtastic CLI install failed (pip exit {pip_status}, "
                f"console script {'present' if meshtastic_bin.is_file() else 'absent'}, "
                f"import {'ok' if importable else 'failed'}) — check log above.", "err")
        return

    ctx.log(f"[+] Meshtastic CLI installed to {meshtastic_bin}", "ok")

    # No wrapper is written. pip puts its own console script at exactly this
    # path, and the wrapper this step used to write would overwrite it --
    # leaving a script pointing at a venv that no longer exists.
    #
    # ~/.local/bin reaches PATH through ~/.profile, which adds it only `if [ -d
    # "$HOME/.local/bin" ]` -- evaluated at login. On a node where that
    # directory did not exist when the operator logged in, `meshtastic` is
    # installed and still "command not found" until the next login.
    if str(local_bin) not in os.environ.get("PATH", "").split(os.pathsep):
        ctx.log("[!] ~/.local/bin is not on this session's PATH — `meshtastic` will be "
                "'command not found' until you log out and back in. That is the same "
                "login 'dialout' needs below.", "warn")

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

    _install_mesh_gpx_bridge(ctx, python3)


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
#: Upstream tag the source build is pinned to.
#:
#: This was deliberately unpinned, on the reasoning that recording the built
#: commit was enough. It is not. Every provisioning run then clones whatever
#: master happens to be that day, so the same repository and the same steps
#: produce a node that builds or does not, depending on the date -- and on
#: 2026-09-13 it did not: upstream had moved the CLI entry point into
#: src-cli/legacy/main.cpp and renamed it main_old, leaving the satdump target
#: with "undefined reference to `main`" at link time. A provisioner cannot
#: absorb an upstream mid-refactor; pinning is what makes a run repeatable.
#:
#: Bumping this is a deliberate act: build the new tag on a node before
#: changing it here.
SATDUMP_VERSION = "1.2.2"

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


DVB_BLACKLIST_FILE = "/etc/modprobe.d/%s-rtlsdr.conf" % PROJECT


def _blacklist_dvb_driver(ctx: Ctx):
    """Stop the kernel claiming the RTL-SDR as a TV tuner.

    Plugging in an RTL-SDR matches dvb_usb_rtl28xxu, the DVB-T driver, which
    binds the device before any userspace program sees it. dump1090, SatDump
    and rtl_test then all fail to open it, and nothing says why.

    Only dvb_usb_rtl28xxu is named: it is the driver that binds the USB device,
    and the demodulator and tuner modules load as its dependencies rather than
    on their own. Blacklisting the one that binds is enough.

    This node's SDR hardware is an RTL-SDR used for ADS-B and weather imagery.
    DVB-T reception is not a thing it is for, so the driver has no use here and
    exactly one effect. Any other dongle needs its own driver work regardless,
    which this does not touch.

    No update-initramfs. The blacklist is read by modprobe, and a USB dongle is
    bound by udev calling modprobe well after the initramfs is out of the
    picture -- dvb_usb_rtl28xxu is never in an initramfs, because no root
    filesystem needs it.
    """
    content = (
        "# Written by the %s provisioner.\n"
        "#\n"
        "# An RTL-SDR dongle matches dvb_usb_rtl28xxu, the DVB-T television\n"
        "# driver, which binds the device before userspace can open it. This\n"
        "# node uses that dongle for ADS-B and weather satellite imagery, so\n"
        "# the TV driver has no purpose here and only takes the hardware away.\n"
        "#\n"
        "# Remove this file and reboot to use the dongle as a DVB-T receiver.\n"
        "blacklist dvb_usb_rtl28xxu\n" % OPERATOR_PREFIX
    )
    try:
        ctx.sudo_write(DVB_BLACKLIST_FILE, content, mode="0644")
    except subprocess.CalledProcessError as e:
        ctx.log(f"[!] Could not write {DVB_BLACKLIST_FILE} — the kernel may claim "
                f"the RTL-SDR dongle as a TV tuner, and no SDR program will be "
                f"able to open it. Write it by hand: "
                f"echo 'blacklist dvb_usb_rtl28xxu' | sudo tee {DVB_BLACKLIST_FILE}",
                "err")
        return
    ctx.log(f"[+] Kernel DVB-T driver blacklisted ({DVB_BLACKLIST_FILE}) — the "
            f"RTL-SDR stays available to SDR software.", "ok")

    # Blacklisting governs future loads, not the running kernel. If it is
    # already bound it still holds the dongle, and the operator would hit
    # exactly the failure this prevents -- on this run, not the next boot.
    loaded = subprocess.run(["lsmod"], stdin=subprocess.DEVNULL,
                            capture_output=True, text=True).stdout
    if any(line.split()[:1] == ["dvb_usb_rtl28xxu"] for line in loaded.splitlines()):
        if ctx.sudo("modprobe", "-r", "dvb_usb_rtl28xxu", check=False).returncode == 0:
            ctx.log("[+] Unloaded the running dvb_usb_rtl28xxu — the dongle is free now, "
                    "not just after the next reboot.", "ok")
        else:
            ctx.log("[!] dvb_usb_rtl28xxu is loaded and could not be unloaded — it is "
                    "holding the RTL-SDR until you reboot. The blacklist stops it "
                    "coming back.", "warn")


def _ensure_rtl_sdr_tools(ctx: Ctx):
    """Install the rtl-sdr command-line tools for whichever SDR step ran.

    Both SDR steps already pull the *library*: SatDump builds against
    librtlsdr-dev, and dump1090-mutability depends on librtlsdr2, which is
    also what brings the udev rules. Neither brings rtl_test, rtl_eeprom and
    friends -- those ship in the separate `rtl-sdr` package.

    That package was listed only in the system-packages step, which is
    independently selectable, so a node built with SatDump and dump1090 but
    without it had no rtl_test. The staged references and the checklist both
    tell the operator to run `rtl_test -t` to prove which process holds the
    dongle, and it is how the DVB-T blacklist is checked -- so the tool that
    proves the arbitration works was missing on exactly the nodes doing SDR.

    Declared here, at the two points of use, rather than left to a step the
    operator has to know to tick. apt is idempotent, so a node that also ran
    system packages pays nothing.
    """
    rc = ctx.sudo("apt", "install", "-y", "rtl-sdr", check=False).returncode
    if rc == 0:
        ctx.log("[+] rtl-sdr command-line tools installed (rtl_test, rtl_eeprom).", "ok")
    else:
        ctx.log(f"[!] rtl-sdr tools install exited {rc} — `rtl_test -t` will not be "
                f"available, and it is what the staged SDR references tell you to run "
                f"to prove the dongle is free. Install it by hand: "
                f"sudo apt install rtl-sdr", "warn")


def _stage_dongle_reference(ctx: Ctx):
    """Stage the shared-dongle reference, and warn when both claimants exist.

    dump1090 and SatDump are independent options, so a node can carry either,
    neither, or both. Only the last case has a problem, and it is a quiet one:
    whichever opens the RTL-SDR first wins, and the loser reports a device it
    cannot open rather than a conflict. Nothing on the node connects the two.

    Called from both steps, checking for the OTHER. In a run installing both
    that means the first step sees nothing and the second warns, so the notice
    appears once rather than twice -- and a run installing only one still warns
    correctly when the other was already there from an earlier run.
    """
    sdr_dir = ctx.data_dir / "SDR"
    sdr_dir.mkdir(parents=True, exist_ok=True)
    (sdr_dir / "dongle_arbitration.md").write_text(DONGLE_ARBITRATION_MD)
    ctx.log(f"[+] RTL-SDR dongle reference staged to {sdr_dir}/", "ok")

    has_dump1090 = shutil.which("dump1090-mutability") is not None
    # A build-tree SatDump takes the dongle exactly as a system-installed one
    # does, so presence here is not the same question as satdump_system_installed.
    has_satdump = (shutil.which("satdump") is not None
                   or (ctx.home / APPS_DIR_NAME / "SatDump" / "build" / "satdump").is_file())
    if has_dump1090 and has_satdump:
        ctx.log("[!] BOTH dump1090 and SatDump are installed on this node. They "
                "cannot both hold the RTL-SDR dongle — whichever opens it first "
                "wins and the other simply fails to see the device. Read "
                f"{sdr_dir}/dongle_arbitration.md before operating either.", "warn")


def optional_dump1090(ctx: Ctx):
    """ADS-B aircraft reception from the RTL-SDR dongle.

    Three properties of the Debian package shape this step, and none of them is
    the one the package's own naming suggests.

    It carries 41 debconf templates, so a bare install can stop and wait for an
    answer behind a pane showing only a spinner. That one is settled by
    preseeding before apt runs.

    debconf's `auto-start` does NOT mean "start at boot". postinst maps it to
    START_DUMP1090 in /etc/default/dump1090-mutability, and the init script
    tests that on EVERY start:

        if [ "x$START_DUMP1090" != "xyes" ]; then
            log_warning_msg "Not starting $NAME daemon, disabled via ..."
            return 2

    So seeding it false does not install "a service that does not start at
    boot" -- it installs a service that cannot be started at all. Boot
    behaviour is a separate switch: dh_installinit runs `update-rc.d
    dump1090-mutability defaults` regardless of debconf, so the runlevel links
    are what have to be removed.

    And the daemon does not run as the operator. The init script starts it with
    --chuid "$DUMP1090_USER", a user postinst creates with `adduser --system`
    into nogroup -- while the RTL-SDR device node is root:plugdev 0660 from
    librtlsdr2's udev rules. Without the group it starts, fails to open the
    dongle, and exits, leaving a map that never populates.

    The dock autostart sequence does not launch it either -- see
    AUTOSTART_SEQUENCE_SH.
    """
    # decode-lat / decode-lon are the station's own position, the same class of
    # data as a grid square, and are left empty on purpose. The staged
    # reference tells the operator how to set them.
    seed_path = "/tmp/%s-dump1090.seed" % PROJECT
    # auto-start true, then the boot links removed below: see the docstring.
    # Seeding it false is what makes the service unstartable.
    seed = ("dump1090-mutability dump1090-mutability/auto-start boolean true\n"
            "dump1090-mutability dump1090-mutability/decode-lat string \n"
            "dump1090-mutability dump1090-mutability/decode-lon string \n")

    with ctx.spin("Installing dump1090 (ADS-B)...") as spin_result:
        ctx.sudo_write(seed_path, seed)
        seeded = ctx.sudo("debconf-set-selections", seed_path, check=False).returncode == 0
        # noninteractive as well as the preseed: the seed answers the three
        # questions that matter, the frontend covers the other thirty-eight.
        install_rc = ctx.sudo("env", "DEBIAN_FRONTEND=noninteractive",
                              "apt", "install", "-y", "dump1090-mutability",
                              check=False).returncode
        ctx.sudo("rm", "-f", seed_path, check=False)
        spin_result.ok = (install_rc == 0)

    if not seeded:
        ctx.log("[!] debconf preseed failed — if the install stalled, it is waiting "
                "for an answer on a terminal you cannot see.", "err")
    if install_rc != 0:
        ctx.log(f"[!] dump1090-mutability install exited {install_rc} — ADS-B NOT "
                f"available.", "err")
        return

    binary = shutil.which("dump1090-mutability")
    if not binary:
        ctx.log("[!] dump1090-mutability installed but no binary on PATH — ADS-B NOT "
                "usable.", "err")
        return
    ctx.log(f"[+] dump1090 installed at {binary}.", "ok")

    # Without this the daemon starts, fails to open the dongle, and exits --
    # quietly, because the start command still returns 0. The operator's own
    # account IS in plugdev, so running the binary by hand works perfectly,
    # which makes it look like anything except a permissions problem.
    #
    # Read rather than hardcoded: DUMP1090_USER is debconf-configurable
    # (postinst: `subvar run-as-user DUMP1090_USER`), and granting dongle
    # access to the wrong account would be worse than not granting it.
    run_as = _dump1090_default("DUMP1090_USER")
    if not run_as:
        ctx.log(f"[!] No DUMP1090_USER in {DUMP1090_DEFAULTS} — cannot grant the "
                f"service user access to the RTL-SDR dongle. dump1090 will install "
                f"and start, but receive nothing.", "err")
    elif ctx.sudo("adduser", run_as, "plugdev", check=False).returncode == 0:
        ctx.log(f"[+] Service user '{run_as}' added to 'plugdev' — it can open the "
                f"RTL-SDR dongle.", "ok")
    else:
        ctx.log(f"[!] Could not add '{run_as}' to 'plugdev'. dump1090 will start and "
                f"then fail to open the dongle with 'usb_open error -3', leaving the "
                f"aircraft map empty. Fix with: sudo adduser {run_as} plugdev", "err")

    # This, not the preseed, is what keeps the dongle free at boot. The package
    # registers runlevel links unconditionally; removing them leaves the daemon
    # fully startable by hand.
    boot_off = ctx.sudo("systemctl", "disable", "dump1090-mutability",
                        check=False).returncode == 0
    ctx.sudo("service", "dump1090-mutability", "stop", check=False)
    if boot_off:
        ctx.log("[!] dump1090 does NOT start at boot and is not in the dock autostart "
                "sequence, deliberately — it and SatDump cannot both hold the RTL-SDR "
                "dongle. Start it when you want ADS-B with: "
                "sudo service dump1090-mutability start", "warn")
    else:
        ctx.log("[!] Could not disable dump1090 at boot — it may claim the RTL-SDR "
                "dongle on the next reboot and SatDump will not be able to open it. "
                "Disable it by hand: sudo systemctl disable dump1090-mutability", "err")

    adsb_dir = ctx.data_dir / "ADSB"
    adsb_dir.mkdir(parents=True, exist_ok=True)
    (adsb_dir / "dump1090_setup.md").write_text(DUMP1090_SETUP_MD)
    ctx.log(f"[+] ADS-B setup reference staged to {adsb_dir}/", "ok")
    _ensure_rtl_sdr_tools(ctx)
    _blacklist_dvb_driver(ctx)
    _stage_dongle_reference(ctx)
    ctx.log("[!] Receiver latitude/longitude are NOT set — that is operator position "
            "data. See the staged reference.", "warn")


def _find_curated_tle_set():
    """Return the group-supplied TLE set under configs/, or None.

    Used by the SatDump step and by verification, which must agree: the step
    decides whether to suppress SatDump's own TLE fetch, and the row reports
    which of the two arrangements the node ended up with. Two copies of this
    walk would let them disagree about the same node.
    """
    base_depth = len(Path("configs").resolve().parts)
    for dirpath, dirnames, filenames in os.walk("configs"):
        depth = len(Path(dirpath).resolve().parts) - base_depth
        if depth >= 2:
            dirnames[:] = []
        for fn in filenames:
            if re.search(r"tles.*\.txt$", fn, re.IGNORECASE):
                return Path(dirpath) / fn
    return None


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
        with ctx.spin(f"Cloning SatDump source at {SATDUMP_VERSION}...") as spin_result:
            # Shallow AND pinned: the full history is a large download for a
            # node provisioned over whatever connection is to hand, and master
            # is not a thing a provisioner can depend on -- see SATDUMP_VERSION.
            spin_result.ok = ctx.run(
                ["git", "clone", "--depth", "1", "--branch", SATDUMP_VERSION,
                 "https://github.com/SatDump/SatDump.git", str(satdump_dir)],
                check=False).returncode == 0
    else:
        # A clone left by an earlier run holds whatever that run checked out --
        # for any run before this change, master. Without moving it, the pin
        # above is silently ignored on every re-run, which is the quietest
        # possible way for a version pin to be untrue.
        with ctx.spin(f"Reusing SatDump source — checking out {SATDUMP_VERSION}...") as spin_result:
            ctx.run(["git", "-C", str(satdump_dir), "fetch", "--depth", "1",
                     "origin", "tag", SATDUMP_VERSION], check=False)
            spin_result.ok = ctx.run(
                ["git", "-C", str(satdump_dir), "checkout", "--force", SATDUMP_VERSION],
                check=False).returncode == 0
        if not spin_result.ok:
            ctx.log(f"[!] Could not move {satdump_dir} to {SATDUMP_VERSION} — building "
                    f"whatever that directory holds, which may not be a release. "
                    f"Delete it and re-run for a pinned build.", "warn")
    if not satdump_dir.is_dir():
        ctx.log("[!] SatDump source clone failed — SatDump NOT installed.", "err")
        return

    # Recorded as well as pinned. The pin says what should have been built; this
    # says what was, which still differ if the checkout above failed and warned.
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
        ctx.log(f"[!] SatDump build failed at {SATDUMP_VERSION} — SatDump NOT "
                f"installed. Retry manually:", "err")
        ctx.log(f"    cd {build_dir} && cmake .. && make -j{jobs}", "err")
        # Two failure modes, and the advice for one is useless for the other.
        # This used to offer the memory explanation unconditionally, which sent
        # an operator to `make -j1` after a linker error that -j1 cannot fix.
        # The captured output is already in the log above; point at what in it
        # tells them apart.
        ctx.log("    Read the output above before retrying. A compiler KILLED with no "
                "error ran out of memory — retry with make -j1. An error message, and "
                "particularly a linker one, is a real build failure and -j1 will not "
                "change it.", "err")
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

    # Reached only when the build succeeded, so the user-level TLE config
    # below is worth writing either way -- a build-tree binary reads
    # ~/.config/satdump exactly as an installed one does.
    _ensure_rtl_sdr_tools(ctx)
    _blacklist_dvb_driver(ctx)
    _stage_dongle_reference(ctx)
    # But only a successful install put anything under /usr, and the desktop
    # shortcut in finalize() needs /usr/share/applications/satdump.desktop.
    # Claiming it exists produced a "shortcut skipped" warning that read as a
    # finding of its own rather than a consequence of the line above.
    ctx.satdump_system_installed = installed

    _configure_satdump_tles(ctx)


def _configure_satdump_tles(ctx: Ctx):
    """Point SatDump at element sets, one of two ways.

    Its own module-level function, like the two helpers above it, because the
    build ahead of it cannot run on the bench: a stubbed clone returns before
    this is reached, so inside the step neither branch is observable without a
    real source build.
    """
    ctx.log("[*] Deploying SatDump TLE configuration...", "info")
    satdump_config_dir = ctx.home / ".config" / "satdump"
    satdump_config_dir.mkdir(parents=True, exist_ok=True)

    tle_src = _find_curated_tle_set()

    # Suppressing SatDump's TLE fetch only makes sense as protection for a
    # curated set: the fetch overwrites satdump_tles.txt on first launch, so
    # a staged file survives only if the fetch is off.
    #
    # With no curated set there is nothing to protect, and suppressing it
    # anyway leaves the node with no element sets at all and no way to get
    # any -- observed on a clean clone as SatDump logging "0 TLEs loaded!"
    # with a working recorder. configs/ ships empty here by design, so that
    # was the default outcome of every EmComm run, not an edge case. This
    # step was ported from a provisioner that *does* commit a curated set;
    # the suppression came with it and the empty case never got its own
    # answer.
    #
    # Leaving the fetch alone is what the checklist has always told the
    # operator to expect: "Open SatDump once, confirm the Tracking tab
    # lists satellites."
    if tle_src:
        shutil.copy(tle_src, satdump_config_dir / "satdump_tles.txt")
        ctx.log(f"[+] Synced {tle_src} -> {satdump_config_dir}/satdump_tles.txt", "ok")

        # Stop the auto-update by the setting built for it, and do not touch
        # tle_settings at all.
        #
        # Emptying the fetch lists -- what #26 concluded and what shipped --
        # looks like "never fetch" and is not. On launch SatDump loads
        # satdump_tles.txt into the registry, then calls autoUpdateTLE
        # (init.cpp L113-115), which calls updateTLEFile because the interval
        # has elapsed: a fresh node has no user.tles_last_updated and the
        # stock interval is "1 day". Inside, the fetch loops are plain
        # range-for over those lists, and what follows them is unconditional:
        #
        #     std::ofstream outfile(path, std::ios::trunc);   // tle.cpp L180
        #     for (TLE &tle : new_registry) ...               // empty
        #
        # So empty lists truncate satdump_tles.txt on first launch and log
        # "0 TLEs loaded!" -- deleting the very set this branch just staged.
        # Latent rather than shipped here, because configs/ carries no TLE
        # file and this branch never runs on a stock clone; a group that
        # supplies one would lose it.
        #
        # "Never" sets honor_setting = false (tle.cpp L198-199) so that clause
        # never fires. The remaining `|| registry.size() == 0` is a deliberate
        # fallback for a node whose staged file failed to load, and it is why
        # the fetch lists must keep SatDump's real defaults: left empty that
        # fallback truncates; left alone it fetches. See #26.
        satdump_global_cfg = Path("/usr/share/satdump/satdump_cfg.json")
        cfg_backup = Path(f"{satdump_global_cfg}.{PROJECT}-backup")
        if cfg_backup.is_file():
            # An earlier run commented the bulk URL and both NORAD IDs out of
            # the packaged config, which leaves exactly the empty lists above.
            ctx.sudo("cp", str(cfg_backup), str(satdump_global_cfg))
            ctx.sudo("rm", "-f", str(cfg_backup))
            ctx.log("[+] Restored SatDump's stock global config — the previous "
                    "arrangement would have emptied the curated TLE set.", "ok")

        satdump_settings = satdump_config_dir / "settings.json"
        never = {"satdump_general": {"tle_update_interval": {"value": "Never"}}}
        # settings.json is a sparse diff merged over the packaged config
        # (config.cpp L119, merge_json_diffs), so this one key is the whole
        # file -- the rest of the option object is inherited.
        superseded = (
            {"tle_settings": {"urls_to_fetch": [], "url_template": "", "tles_to_fetch": []}},
            {"tle_settings": {"urls_to_fetch": None, "url_template": None, "tles_to_fetch": None}},
        )
        if not satdump_settings.is_file():
            satdump_settings.write_text(json.dumps(never, indent=4) + "\n")
            ctx.log("[+] Pre-seeded settings.json: TLE auto-update set to Never.", "ok")
        else:
            try:
                existing = json.loads(satdump_settings.read_text())
            except (ValueError, OSError):
                existing = None
            # Replace only the exact files earlier runs wrote -- the empty-list
            # form, and the null form that preceded it. Anything else is the
            # operator's and is left alone.
            if existing in superseded:
                satdump_settings.write_text(json.dumps(never, indent=4) + "\n")
                ctx.log("[+] Replaced the old settings.json pre-seed, which would have "
                        "truncated the curated TLE set on first launch.", "ok")
            else:
                ctx.log("[+] settings.json already exists — leaving as-is.", "ok")
    else:
        ctx.log("[*] No curated TLE set under configs/ — leaving SatDump's own TLE "
                "fetch enabled, so first launch loads its full default set.", "info")

    # SatDump does its first-run setup and its TLE fetch when the GUI is
    # launched, not when it is installed -- its own config calls this "auto
    # update happens at launch only". A node that goes to the field having
    # never had SatDump opened therefore arrives with no element sets, and
    # discovers it during a pass rather than on the bench.
    #
    # Nothing automated can substitute for this: the fetch needs network,
    # and provisioning is the last point at which the node reliably has it.
    # Element sets also decay in days, so this is a pre-deployment action
    # and not a one-time install step -- a node imaged in March and
    # deployed in June needs it again. Said here because the operator is
    # looking at the screen now, and again in the checklist because that is
    # what gets worked before a node ships.
    ctx.log("[!] LAUNCH SATDUMP ONCE WHILE STILL ONLINE, before this node goes "
            "to the field. First launch is when it writes its runtime config and "
            "fetches TLEs; a node that has never had it opened has no element "
            "sets and cannot predict a pass. TLEs decay within days, so repeat "
            "this shortly before deployment.", "warn")


def finalize(ctx: Ctx):
    desktop_dir = ctx.home / "Desktop"
    desktop_dir.mkdir(parents=True, exist_ok=True)

    apps = ["qmapshack.desktop", "org.kiwix.desktop.desktop", "js8call.desktop", "chirp.desktop"]
    # Gated on the system install, not merely on SatDump being usable: the
    # .desktop file comes from `make install`, so a build-tree-only SatDump
    # has no shortcut to copy.
    if ctx.satdump_system_installed:
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
    Component("dock_trigger",
              "Dock-trigger autostart (Havis dock only) — FUTURE FEATURE, unproven",
              step_dock_trigger),
    Component("gps_time", "GPS time source (gpsd + chrony)", optional_gps_time),
    Component("direwolf", "Direwolf (AX.25 / APRS software TNC)", optional_direwolf),
    Component("meshtastic", "Meshtastic CLI (LoRa mesh node tooling)", optional_meshtastic),
    Component("dump1090", "dump1090 (ADS-B aircraft tracking, RTL-SDR)", optional_dump1090),
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


DUMP1090_DEFAULTS = Path("/etc/default/dump1090-mutability")


def _dump1090_default(key: str) -> Optional[str]:
    """One value out of /etc/default/dump1090-mutability.

    The init script sources this file, so what is in it is what actually
    decides behaviour -- and two of its values decide whether ADS-B works at
    all. START_DUMP1090 gates every start; DUMP1090_USER is who the daemon
    runs as, and therefore who needs access to the dongle. Both the install
    step and the verification pass need to read them, so the parsing lives in
    one place rather than being written twice and drifting.

    Built on _ini_value rather than repeating its parsing: `KEY=value` one per
    line, whole-name match, tolerant of spaces, and a commented line does not
    match. The one difference is that this file is shell, so values are quoted
    (DUMP1090_USER="dump1090") and the quotes come off here.

    Returns None when the file or the key is absent, which callers report
    rather than guessing a default: guessing the service user wrong would grant
    dongle access to an account that is not running it.
    """
    raw = _ini_value(DUMP1090_DEFAULTS, key)
    return None if raw is None else raw.strip('"').strip("'")


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

        # Gated on a profile actually existing to stage. configure_qmapshack()
        # creates this file whether or not one does, so an ungated existence
        # check passed on a run whose own log said "QMapShack left unconfigured"
        # -- and the placeholder row under it passed by having nothing to
        # substitute. Two green rows for work that did not happen.
        qms = home / QMS_CONF_REL
        if Path("configs/QMapShack.conf").is_file():
            if want(qms, "QMapShack profile staged"):
                left = _placeholder_count(qms)
                add("QMapShack placeholders substituted", "pass" if left == 0 else "fail",
                    "clean" if left == 0 else "%d unsubstituted token(s) remain" % left)
        else:
            add("QMapShack profile staged", "warn",
                "no configs/QMapShack.conf to stage — the settings file was written "
                "by the provisioner, not copied from a profile")

        # The profile is only useful where QMapShack looks for it. A file under
        # the predecessor project's directory reads as staged and is not.
        legacy = home / QMS_CONF_LEGACY_REL
        if legacy.is_file():
            add("QMapShack config in the directory QMapShack reads", "warn",
                "%s also exists and is ignored by QMapShack; move anything you "
                "need out of it" % legacy)

        # Three independent things, and the maps are invisible if any one of
        # them is missing. Checked separately so a failure names which.
        conf_text = qms.read_text(errors="replace") if qms.is_file() else ""
        maps_dir = str(ctx.data_dir / "Offline_Maps")
        registered = any(l.startswith("mapPath=") and maps_dir in l
                         for l in conf_text.splitlines())
        add("QMapShack knows where the offline maps are", "pass" if registered else "fail",
            "mapPath lists " + maps_dir if registered
            else "mapPath does not list " + maps_dir + " — QMapShack will not list "
                 "the .tms sources at all, whatever is in them")

        active = "keysKnownMaps=" in conf_text and "isActive=true" in conf_text
        add("An offline map is set to draw", "pass" if active else "warn",
            "a map source is registered active" if active
            else "no map registered as active — the sources appear in the Maps tab "
                 "but the canvas stays empty until one is clicked")

        has_view = "posFocus=" in conf_text
        add("QMapShack opens on the operating area", "pass" if has_view else "warn",
            "view centre set" if has_view
            else "no view centre — QMapShack will open on its built-in centre "
                 "in central Europe, where this node has no tiles")

        want(home / ".local" / "share" / "CHIRP" / "analog_channels.csv",
             "CHIRP channel list staged", hard=False)
        want(ctx.data_dir / "ion2G" / "ale_channels.zcp",
             "ALE channel plan staged", hard=False)

        # The .tms files and the tile directories must agree — they did not
        # once, and QMapShack then opened the sources onto nothing.
        for layer, tms_name, title in MAP_LAYERS:
            tms = ctx.data_dir / "Offline_Maps" / tms_name
            if want(tms, "Map source " + tms_name, hard=False):
                tms_text = tms.read_text(errors="replace")
                expect = "Offline_Tiles/" + layer + "/"
                add("Map source " + tms_name + " points at the fetched layer",
                    "pass" if expect in tms_text else "fail",
                    expect if expect in tms_text else "does not reference " + expect)
                # A <Script> layer costs a fresh JavaScript engine per tile per
                # redraw. This row exists so a .tms left over from an earlier
                # provisioning run is visible rather than quietly slow.
                add("Map source " + tms_name + " resolves tiles without JavaScript",
                    "pass" if "<ServerUrl>" in tms_text else "warn",
                    "ServerUrl" if "<ServerUrl>" in tms_text
                    else "still a <Script> layer from an earlier run — re-run the "
                         "config step to replace it")

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
        # Every row above is a file-presence check, and all of them pass on a
        # machine that has never seen a dock. TESTING.md has said so since the
        # section was written; the verification pass itself did not, and four
        # green rows read as a working feature unless something says otherwise.
        add("Dock trigger observed firing", "warn",
            "never — the rows above confirm files on disk, not that a dock event runs "
            "them. Future feature, designed for the CF-30 and DS-PAN-111 and not yet "
            "exercised against either; see 'Verifying it actually fires' in TESTING.md.")

    # --- optional installs ----------------------------------------------
    if "gps_time" in selected_ids:
        # Deliberately never opens the serial port. Opening it asserts DTR,
        # which resets a BU-353N-class receiver and costs ~10 seconds of time
        # source -- a check that knocks out the thing it is checking, and
        # reports on a device it just restarted. Ask chronyd instead, which
        # also has the advantage of proving the whole chain (gpsd reading the
        # puck, samples reaching shared memory, chronyd consuming them) rather
        # than just that a device node exists.
        want(Path(GPS_CHRONY_DROPIN), "chrony GPS refclock configured", hard=False)

        sources = subprocess.run(["chronyc", "-n", "sources"], stdin=subprocess.DEVNULL,
                                 capture_output=True, text=True)
        gps_line = ""
        for line in sources.stdout.splitlines():
            if "GPS0" in line:
                gps_line = line.strip()
                break
        if sources.returncode != 0:
            add("GPS time source active", "warn",
                "could not query chronyd — is chrony running?")
        elif not gps_line:
            add("GPS time source active", "warn",
                "chronyd is not carrying a GPS0 refclock; the drop-in may not "
                "have been read — restart chrony, then gpsd, in that order")
        else:
            # Field 3 of a chronyc sources row is the reach register, octal.
            fields = gps_line.split()
            reach = fields[3] if len(fields) > 3 else "0"
            if reach != "0":
                add("GPS time source active", "pass",
                    "chronyd has GPS0 with samples (reach %s) — %s" % (reach, gps_line))
            else:
                # Not a failure: most provisioning runs have no receiver
                # attached, and a cold receiver needs about a minute.
                add("GPS time source active", "warn",
                    "GPS0 configured but no samples yet (reach 0). Expected with "
                    "no receiver attached, or within the first minute after a "
                    "cold start. Re-check with `chronyc tracking`.")

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

        # Deliberately never opens the PTT device. This interface keys a
        # transmitter from a serial control line, so a check that opened the
        # port to confirm it works could put a radio on the air -- the same
        # reason the GPS row asks chronyd instead of the receiver.
        adevice, ptt_device, ptt_method = _read_radio_binding()
        if adevice:
            card_ids = [i for _n, i in _alsa_card_ids()]
            m = re.search(r"CARD=([^,]+)", adevice)
            present = (m.group(1) in card_ids) if (m and card_ids) else None
            add("Direwolf audio device declared", "pass" if present is not False else "warn",
                adevice if present is not False
                else "%s names a card that is not attached (%s)" % (adevice, ", ".join(card_ids)))
        else:
            add("Direwolf audio device declared", "warn",
                ("configs/radio.conf declares no ADEVICE" if Path("configs/radio.conf").is_file()
                 else "no configs/radio.conf")
                + " — ADEVICE is the shipped guess plughw:1,0 and is very likely wrong "
                  "for your interface")

        if ptt_device:
            here = Path(ptt_device).exists()
            # "declared, never observed" rather than a row of its own that can
            # only ever warn: a check that can never pass teaches people to
            # skim past warnings, which is the opposite of what this screen is
            # for. The caveat belongs on the row it qualifies.
            add("Direwolf PTT device declared", "pass" if here else "warn",
                (direwolf_ptt_line(ptt_device, ptt_method)
                 + " — declared and present; no node has observed it key a radio")
                if here
                else "%s is not present — attach the interface, or fix the path" % ptt_device)
            if ptt_device.startswith("/dev/tty"):
                add("Direwolf PTT device named by a stable path", "warn",
                    "%s is a numbered path; a GPS receiver and a radio interface both "
                    "enumerate as ttyUSB* and swap on plug order. Use /dev/serial/by-id/"
                    % ptt_device)
        elif (ptt_method or "").upper() == "CM108":
            add("Direwolf PTT device declared", "pass",
                "PTT CM108 — declared; no node has observed it key a radio")
        else:
            add("Direwolf PTT device declared", "warn",
                ("configs/radio.conf declares no PTT_DEVICE" if Path("configs/radio.conf").is_file()
                 else "no configs/radio.conf")
                + " — PTT is the shipped guess 'CM108', which does not key a Digirig "
                  "Mobile: its PTT is on the CP2102's RTS line")

        # ModemManager probes an unknown serial port by writing AT commands to
        # it, and on this interface writing asserts RTS, which keys the
        # transmitter. A row for the rule that stops it has to read what the
        # rule says: a file that exists but names an interface this node no
        # longer uses protects nothing, and looks like it does.
        #
        # Where the interface is attached, udev's own database is the better
        # answer than the file -- it says the rule matched, not merely that it
        # was written. Reading that database does not open the port.
        mm_serial = modemmanager_id_serial(ptt_device)
        if mm_serial:
            try:
                rule = Path(RADIO_MM_RULE).read_text(errors="replace")
            except OSError:
                rule = ""
            applied = (_udev_property(ptt_device, "ID_MM_DEVICE_IGNORE")
                       if Path(ptt_device).exists() else None)
            named = "ID_MM_DEVICE_IGNORE" in rule and mm_serial in rule
            if applied == "1":
                add("ModemManager excluded from the radio interface", "pass",
                    "udev reports ID_MM_DEVICE_IGNORE=1 on the attached interface")
            elif named and applied is None:
                add("ModemManager excluded from the radio interface", "pass",
                    "rule names %s; not attached, so udev has not applied it yet"
                    % mm_serial)
            elif named:
                add("ModemManager excluded from the radio interface", "warn",
                    "the rule names %s but udev has not applied it to the attached "
                    "interface — replug it or reboot" % mm_serial)
            elif rule:
                add("ModemManager excluded from the radio interface", "warn",
                    "%s does not name %s — re-run the Direwolf step after changing "
                    "interfaces" % (RADIO_MM_RULE, mm_serial))
            else:
                add("ModemManager excluded from the radio interface", "warn",
                    "no %s. ModemManager probes unknown serial ports by writing to "
                    "them, and writing asserts RTS on this one" % RADIO_MM_RULE)

    if "meshtastic" in selected_ids:
        want(home / ".local" / "bin" / "meshtastic", "Meshtastic wrapper on PATH")
        # A capability check, not a path check: the console script can be on
        # disk while the package is uninstallable by the interpreter that has
        # to import it, and the bridge is what would then fail -- silently,
        # two steps later.
        python3 = shutil.which("python3") or sys.executable
        importable = subprocess.run([python3, "-c", "import meshtastic"],
                                    stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True).returncode == 0
        add("Meshtastic package importable by python3",
            "pass" if importable else "fail",
            "%s can import meshtastic" % python3 if importable
            else "%s cannot import meshtastic — the CLI and the GPX bridge both "
                 "need it" % python3)

        # The import row proves the library loads under python3. It does not
        # prove the console script the operator actually types is runnable:
        # the wrapper carries its own shebang, and a wrapper written against an
        # interpreter that later moved is on disk, importable, and broken.
        #
        # --version is safe to run with no hardware attached. It is declared
        # action="version", so argparse prints it and exits during parsing,
        # before any device code is reached -- it cannot block on a port or
        # probe for a board. Confirmed on a machine with no serial devices at
        # all: exit 0, ~0.2s.
        cli = shutil.which("meshtastic") or str(home / ".local" / "bin" / "meshtastic")
        try:
            v = subprocess.run([cli, "--version"], stdin=subprocess.DEVNULL,
                               capture_output=True, text=True, timeout=30)
        except subprocess.TimeoutExpired:
            add("Meshtastic CLI runs", "fail",
                "%s --version did not return within 30s" % cli)
        except OSError as e:
            add("Meshtastic CLI runs", "fail", "%s could not be executed (%s)" % (cli, e))
        else:
            if v.returncode == 0:
                add("Meshtastic CLI runs", "pass",
                    "%s --version -> %s" % (cli, v.stdout.strip() or "(no output)"))
            else:
                # --version never reaches a device, so a failure here is the
                # install, not the hardware.
                tail = (v.stderr or v.stdout or "").strip().splitlines()
                add("Meshtastic CLI runs", "fail",
                    "%s --version exited %d%s" % (cli, v.returncode,
                                                  " — " + tail[-1] if tail else ""))

        # Device presence, by enumeration only. Nothing here opens a port.
        #
        # Deliberately NOT `meshtastic --info`, which would be the obvious
        # probe and is wrong three ways on this node:
        #   * it prints security.privateKey to stdout, so capturing it puts the
        #     node's private key in the log pane and in anything the operator
        #     saves or pastes -- the exposure the setup reference warns about;
        #   * with no board it falls back to a TCP connection on localhost,
        #     which a read-only verification pass has no business opening;
        #   * with more than one serial port it exits 1 demanding --port, and a
        #     second port is the *designed* configuration here -- gpsd is in
        #     the stack and Direwolf wants a radio interface on ttyUSB/ttyACM.
        #     A fully-equipped node would fail the row and a bare one pass it.
        #
        # So this row reports what is plugged in and never returns "fail":
        # no board attached during provisioning is the normal case, not a
        # defect. Whether the CLI can actually talk to a board is a question
        # this pass cannot answer honestly -- see the dialout note below.
        if importable:
            probe = subprocess.run(
                [python3, "-c",
                 "import json, meshtastic.util as u; print(json.dumps(u.findPorts(True)))"],
                stdin=subprocess.DEVNULL, capture_output=True, text=True)
            try:
                ports = json.loads(probe.stdout) if probe.returncode == 0 else None
            except (ValueError, TypeError):
                ports = None
            if ports is None:
                add("Meshtastic serial device detected", "warn",
                    "could not enumerate serial ports; the CLI's own detection failed "
                    "to run")
            elif not ports:
                add("Meshtastic serial device detected", "warn",
                    "no serial device present — nothing to reach. Not a defect: most "
                    "provisioning runs have no node plugged in.")
            elif len(ports) == 1:
                add("Meshtastic serial device detected", "pass",
                    "%s — note that 'dialout' does not take effect until the operator "
                    "logs out and back in, so this session still may not be able to "
                    "open it" % ports[0])
            else:
                # Not a fault, but the operator needs to know: bare `meshtastic`
                # commands refuse to guess between them.
                add("Meshtastic serial device detected", "pass",
                    "%d serial ports present (%s) — meshtastic will refuse to pick "
                    "one, so field commands need --port <path>. Expected on a node "
                    "that also carries a GPS puck or a radio interface."
                    % (len(ports), ", ".join(ports)))

        groups = subprocess.run(["id", "-nG", ctx.user], stdin=subprocess.DEVNULL,
                                capture_output=True, text=True).stdout.split()
        add("Operator in 'dialout' group", "pass" if "dialout" in groups else "warn",
            "present" if "dialout" in groups
            else "added, but requires log out / log in to take effect")
        want(ctx.data_dir / "Meshtastic" / "meshtastic_setup.md",
             "Mesh setup reference staged", hard=False)

        # The bridge is conditional on QMapShack, so its absence is a finding
        # only when QMapShack is there. Reporting a missing file on a node
        # that was never meant to have one is how a verification pass trains
        # an operator to skim past it.
        bridge = home / APPS_DIR_NAME / "mesh_to_gpx.py"
        if not shutil.which("qmapshack"):
            add("Mesh-to-GPX bridge", "warn",
                "not installed — QMapShack is not on PATH, and the bridge exists only "
                "to feed it. Not a defect on a node without QMapShack.")
        elif want(bridge, "Mesh-to-GPX bridge installed"):
            want(home / ".local" / "bin" / "emcomm-mesh-gpx",
                 "emcomm-mesh-gpx wrapper on PATH")
            unit = home / ".config" / "systemd" / "user" / MESH_GPX_UNIT_NAME
            if want(unit, "Mesh-to-GPX user unit installed"):
                # A unit file on disk is not an enabled unit, and this one is
                # deliberately enabled-but-not-started during provisioning, so
                # "enabled" is the strongest true claim available here.
                # Checking for "active" would fail on every correct install.
                enabled = subprocess.run(
                    ["systemctl", "--user", "is-enabled", MESH_GPX_UNIT_NAME],
                    stdin=subprocess.DEVNULL, capture_output=True,
                    text=True).stdout.strip()
                add("Mesh-to-GPX service enabled",
                    "pass" if enabled == "enabled" else "warn",
                    "%s — starts at next login; it is not started during provisioning "
                    "because 'dialout' does not take effect until then"
                    % (enabled or "unknown"))
            # The GPX itself is NOT checked: it cannot exist until the service
            # has run against a real node, and a row that fails on every fresh
            # install is a row nobody reads.

    if "dump1090" in selected_ids:
        d1090 = shutil.which("dump1090-mutability")
        add("dump1090 installed", "pass" if d1090 else "fail",
            d1090 or "no dump1090-mutability on PATH")
        # Three independent things have to be true, and checking only the first
        # reported "enabled — it will hold the dongle" on a node where the
        # daemon could not start at all: a false alarm about the dongle, and
        # silence about two real defects.
        enabled = subprocess.run(["systemctl", "is-enabled", "dump1090-mutability"],
                                 stdin=subprocess.DEVNULL, capture_output=True,
                                 text=True).stdout.strip()
        add("dump1090 not claiming the dongle at boot",
            "pass" if enabled != "enabled" else "warn",
            ("is-enabled -> " + (enabled or "not registered")) if enabled != "enabled"
            else "enabled — it will hold the RTL-SDR dongle at boot and SatDump "
                 "cannot open it")

        # START_DUMP1090 is what the init script tests on every start, so "no"
        # means the documented start command does nothing, the JSON directory is
        # never created, and the bundled map answers 404.
        startable = _dump1090_default("START_DUMP1090")
        if startable == "yes":
            add("dump1090 startable on demand", "pass", "START_DUMP1090=yes")
        elif startable is None:
            add("dump1090 startable on demand", "fail",
                "no START_DUMP1090 in " + str(DUMP1090_DEFAULTS))
        else:
            add("dump1090 startable on demand", "fail",
                "START_DUMP1090=%s — 'service dump1090-mutability start' will "
                "refuse, and the aircraft map will stay empty" % (startable or "(empty)"))

        # Installed, startable, not at boot -- and still unable to receive, if
        # the service user cannot open the dongle. The daemon exits seconds
        # after a successful start, so nothing upstream of this notices.
        run_as = _dump1090_default("DUMP1090_USER")
        if not run_as:
            add("dump1090 can open the dongle", "fail",
                "no DUMP1090_USER in " + str(DUMP1090_DEFAULTS))
        else:
            groups = subprocess.run(["id", "-nG", run_as], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True).stdout.split()
            if "plugdev" in groups:
                add("dump1090 can open the dongle", "pass",
                    "%s is in 'plugdev'" % run_as)
            else:
                add("dump1090 can open the dongle", "fail",
                    "%s is not in 'plugdev' — the RTL-SDR node is root:plugdev 0660, "
                    "so the daemon starts and then dies with 'usb_open error -3'"
                    % run_as)
        want(ctx.data_dir / "ADSB" / "dump1090_setup.md",
             "ADS-B setup reference staged", hard=False)

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
            # Which row applies depends on whether the group supplied a curated
            # set, because that is what decides the arrangement the node got.
            # With one, the file must be on disk now and its absence is a real
            # staging fault. Without one, nothing is staged on purpose and the
            # element sets arrive on first launch -- reporting "missing" there
            # would flag the default, correct outcome as a defect.
            #
            # Neither row proves the node can predict a pass: that needs the
            # first launch the checklist calls for, and the run log carries
            # that as its own warning.
            if _find_curated_tle_set():
                want(home / ".config" / "satdump" / "satdump_tles.txt",
                     "SatDump TLE set staged", hard=False)
            else:
                add("SatDump TLE source", "pass",
                    "no curated set; SatDump's own fetch left enabled, loads on first launch")
        else:
            add("SatDump available", "fail", "no satdump binary found (package or build)")

    # Staged by whichever SDR step ran, so it is checked once for either rather
    # than inside both blocks: add() appends unconditionally and does not key on
    # the label, so checking it in each would render the row twice on a node
    # carrying both.
    if {"dump1090", "satdump"} & selected_ids:
        want(ctx.data_dir / "SDR" / "dongle_arbitration.md",
             "RTL-SDR dongle reference staged", hard=False)
        # Hard: without it the kernel can take the dongle at plug-in and every
        # SDR program on the node fails to open a device that is plainly there.
        want(Path(DVB_BLACKLIST_FILE), "Kernel DVB-T driver blacklisted")
        # On PATH, not "the package is installed". A node built with SatDump and
        # dump1090 but without the system-packages step had librtlsdr and no
        # rtl_test: a package-presence check would have passed on it, while the
        # command the staged references tell the operator to run -- and the one
        # that proves the blacklist above actually works -- was absent.
        rtl_test = shutil.which("rtl_test")
        add("rtl_test available", "pass" if rtl_test else "warn",
            rtl_test or "not on PATH — install rtl-sdr; the staged SDR references "
                        "and checklist section 9 both call for it")

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
        # Set when a run starts; None before that, so Cancel or Close pressed
        # on an earlier screen has nothing to signal rather than an attribute
        # that does not exist yet.
        self.ctx: Optional[Ctx] = None
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

        # On this screen specifically, because this is where root is granted
        # and the risky operations are the privileged ones. Cancel is safe for
        # every step the operator picked; it is the package transactions
        # underneath them that are not, and that distinction is invisible from
        # the options screen.
        self.sudo_cancel_warning = ttk.Label(
            f,
            text=("\u26a0  Cancelling during a package install is the one unsafe stop.\n"
                  "     Downloads, map-tile fetches and builds stop cleanly \u2014 partial work "
                  "stays on disk and re-running the step resumes it. But apt and dpkg are "
                  "left to finish on purpose: interrupting one mid-transaction can leave "
                  "packages half-configured, which affects other software on this machine, "
                  "not just this run. Recovery is `sudo dpkg --configure -a`.\n"
                  "     So if you cancel while packages are installing, expect a wait rather "
                  "than an instant stop. That wait is deliberate."),
            wraplength=660, justify="left", foreground="#b8860b")
        self.sudo_cancel_warning.pack(anchor="w", pady=(4, 8))

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

        # Sits between the table and the buttons, so it is in the operator's
        # eye-line on the way to Continue rather than below the fold.
        #
        # Several things a run installs only work after a fresh login, and they
        # fail in ways that point away from the cause: 'meshtastic' is "command
        # not found" because ~/.local/bin joins PATH from ~/.profile only if it
        # existed at login; the CLI hits a permission error because 'dialout' is
        # not in effect yet; an enabled user service has not started because it
        # was enabled, not started. All of them are one logout away, and none
        # says so at the moment it fails.
        self.verify_relogin = ttk.Label(
            f,
            text=("\u26a0  Log out and back in \u2014 or reboot \u2014 before using this node.\n"
                  "     PATH, group membership and user services are all read at login. "
                  "Until then `meshtastic` reads as 'command not found', the CLI hits "
                  "permission errors on the serial port, and any service this run "
                  "enabled has not started yet. Nothing here is broken; it is waiting "
                  "for a login."),
            wraplength=760, justify="left", foreground="#b8860b")
        self.verify_relogin.pack(anchor="w", pady=(12, 0))

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

        # Scrolled, not bare. The problem list caps at 12 rows but failed_steps
        # does not, so a bad run can render past the pane -- and a Text without
        # a scrollbar clips with no scrollbar, no indication and no error. The
        # rows lost that way are the ones the operator most needs. Same wiring
        # as the verification table, so the two panes behave alike.
        body = ttk.Frame(f)
        body.pack(fill="both", expand=True)
        self.summary_body = tk.Text(body, height=16, wrap="word", relief="flat",
                                     background=self.cget("background"), state="disabled")
        sb = ttk.Scrollbar(body, orient="vertical", command=self.summary_body.yview)
        self.summary_body.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.summary_body.pack(side="left", fill="both", expand=True)
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
        # Held so Cancel can reach the child ctx.run() is waiting on. Read
        # from the Tk thread while the worker writes to it; Ctx does its own
        # locking around the process handle itself.
        self.ctx = ctx
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
        t.insert("end", "Node: ", "head"); t.insert("end", node_id + "\n")
        t.insert("end", "Provisioner: ", "head"); t.insert("end", "v%s\n\n" % VERSION)
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
        # Revisiting from a later screen re-renders; without this the pane keeps
        # wherever it was last scrolled to, which on a long summary is the middle
        # of the problem list rather than the result.
        t.yview_moveto(0.0)
        t.configure(state="disabled")

        # The summary is the last thing written. Close the file here rather
        # than at window teardown, so the transcript is complete and flushed
        # the moment the operator can read the path to it.
        if self.runlog:
            self.runlog.footer(title, len(ran), skipped, self.failed_steps,
                               passed, warned, failed)
            self.runlog.close()

    def _on_cancel(self):
        if messagebox.askyesno(
                "Cancel provisioning",
                "Stop the run?\n\n"
                "A download, a map-tile fetch or a build in progress is stopped "
                "now — whatever it had finished stays on disk, and re-running "
                "the step starts it again.\n\n"
                "A package install is allowed to finish first. Interrupting apt "
                "part-way leaves the package system needing repair, which is a "
                "worse outcome than waiting.\n\n"
                "Verification still runs, so you can see what landed."):
            # Order matters: arm the event first so nothing new starts, then
            # signal the child. Killing first would let the next iteration of
            # a retry loop begin before the event is seen.
            self.cancel_event.set()
            self.cancel_btn.configure(state="disabled")
            if self.ctx is not None:
                self.ctx.terminate_active_child()

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Provisioning in progress",
                                        "A run is still active. Quit anyway?"):
                return
            self.cancel_event.set()
            if self.ctx is not None:
                self.ctx.terminate_active_child()
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
