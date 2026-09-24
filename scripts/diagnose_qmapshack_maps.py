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
Why QMapShack is not showing the offline maps.

    python3 scripts/diagnose_qmapshack_maps.py

Read-only. Changes nothing, opens no device, and is safe to run with
QMapShack open.

Four things have to be true before a downloaded tile appears on screen, and
none of them follows from the others. All four fail the same way -- a blank
canvas -- so this prints each one separately and says which is missing:

    1. the settings file is the one QMapShack reads
    2. mapPath names the directory, or the sources are not even listed
    3. a source is registered active, or it is listed and not drawn
    4. the view is over ground the tiles cover, at a zoom they exist at

Stdlib only, and standalone in the same way scripts/fetch_map_tiles.py is: a
node may not have a Python with tkinter at the point someone needs to run
this, so it does not import the provisioner. The constants it repeats are
pinned against the provisioner's by tests/maps.py.
"""
import hashlib
import math
import re
import struct
from pathlib import Path

# Keep in step with the provisioner; tests/maps.py fails if these drift.
OPERATOR_PREFIX = "EMCOMM"
QMS_CONF_REL = Path(".config") / "QLandkarte" / "QMapShack.conf"
QMS_CONF_LEGACY_REL = Path(".config") / "QLandkarteGT" / "QMapShack.conf"
QMS_VIEW_GROUP = "View 1"
QMS_ZOOM_BASE = 20          # canvas zoomIndex = QMS_ZOOM_BASE - slippy zoom
TMS_ZOOM_BASE = 21          # .tms levels are inverted: slippy = 21 - declared

HOME = Path.home()
CONF = HOME / QMS_CONF_REL
LEGACY = HOME / QMS_CONF_LEGACY_REL
MAPS = HOME / (OPERATOR_PREFIX + "_Data") / "Offline_Maps"
TILES = MAPS / "Offline_Tiles"


def deg2num(lat, lon, z):
    """Lat/lon to slippy tile x/y -- the same arithmetic the fetcher uses."""
    n = 2.0 ** z
    r = math.radians(lat)
    return (int((lon + 180.0) / 360.0 * n),
            int((1.0 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2.0 * n))


def ini_get(text, key):
    m = re.search(r"^%s=(.*)$" % re.escape(key), text, re.M)
    return m.group(1) if m else None


def decode_pointf(value):
    """QSettings stores the view center as a binary QVariant QPointF, in
    radians. Returns (lat, lon) in degrees, or None if it is not one."""
    body = value[1:-1] if value.startswith('"') else value
    if not (body.startswith("@Variant(") and body.endswith(")")):
        return None
    esc = body[len("@Variant("):-1]
    simple = {"0": 0x00, "a": 0x07, "b": 0x08, "f": 0x0C, "n": 0x0A,
              "r": 0x0D, "t": 0x09, "v": 0x0B, '"': 0x22, "\\": 0x5C}
    out, i = bytearray(), 0
    while i < len(esc):
        ch = esc[i]
        if ch != "\\":
            out.append(ord(ch))
            i += 1
            continue
        nxt = esc[i + 1]
        if nxt == "x":
            j = i + 2
            while j < len(esc) and j < i + 4 and esc[j] in "0123456789abcdefABCDEF":
                j += 1
            out.append(int(esc[i + 2:j], 16))
            i = j
        elif nxt in simple:
            out.append(simple[nxt])
            i += 2
        else:
            return None
    if len(out) != 20:
        return None
    tid, x, y = struct.unpack(">Idd", bytes(out))
    return (math.degrees(y), math.degrees(x)) if tid == 26 else None


def tile_counts(layer_dir):
    """{zoom: tiles} on disk, without walking the whole tree twice."""
    counts = {}
    try:
        for child in sorted(layer_dir.iterdir()):
            if child.is_dir() and child.name.isdigit():
                counts[int(child.name)] = sum(1 for _ in child.rglob("*.png"))
    except OSError:
        pass
    return counts


def main():
    faults = []
    print("=" * 74)

    print("1. SETTINGS FILE")
    print("   %-56s %s" % (CONF, "present" if CONF.is_file() else "MISSING"))
    if LEGACY.is_file():
        print("   %-56s present, and QMapShack does not read it" % LEGACY)
    if not CONF.is_file():
        print("\n   Nothing has configured QMapShack. Run the provisioner's")
        print("   'App profiles + ALE channel plan' step, after the map step.")
        return 1
    text = CONF.read_text(errors="replace")

    print("\n2. MAP PATH")
    mp = ini_get(text, "mapPath")
    listed = mp is not None and str(MAPS) in mp
    print("   mapPath = %s" % mp)
    if listed:
        print("   lists %s" % MAPS)
    else:
        print("   does NOT list %s" % MAPS)
        print("   -> the Maps tab will be empty; the sources are not even listed")
        faults.append("mapPath does not name the offline maps directory")

    on_disk_range = [None, None]

    print("\n3. MAP SOURCES")
    keys = ini_get(text, "Views\\%s\\map2\\keysKnownMaps" % QMS_VIEW_GROUP) or ""
    sources = sorted(MAPS.glob("*.tms")) if MAPS.is_dir() else []
    if not sources:
        print("   no .tms files in %s" % MAPS)
        faults.append("no map sources written -- run the config step")
    active_any = False
    for tms in sources:
        body = tms.read_text(errors="replace")
        key = hashlib.md5(tms.read_bytes()[:4096]).hexdigest()
        is_active = (ini_get(text, "Views\\%s\\map2\\%s\\isActive"
                             % (QMS_VIEW_GROUP, key)) or "").lower() == "true"
        active_any = active_any or is_active
        mn = re.search(r"<MinZoomLevel>(\d+)", body)
        mx = re.search(r"<MaxZoomLevel>(\d+)", body)
        declared = ((TMS_ZOOM_BASE - int(mx.group(1)), TMS_ZOOM_BASE - int(mn.group(1)))
                    if mn and mx else (0, 20))
        m = re.search(r"Offline_Tiles/([^/]+)/", body)
        counts = tile_counts(TILES / m.group(1)) if m else {}

        print("\n   %s" % tms.name)
        print("     registered   %s" % ("yes" if key in keys else "no"))
        print("     active       %s" % ("yes" if is_active else
                                        "no -- listed in the Maps tab, not drawn"))
        print("     layer type   %s" % ("ServerUrl" if "<ServerUrl>" in body
                                        else "Script -- a JS engine per tile, per redraw"))
        # QMapShack substitutes Qt place markers; it only rewrites the {z}/{x}/{y}
        # form into them from v1.20.0 (QMS-920). Older builds leave the braces
        # alone, ask for a file literally named "{z}/{x}/{y}.png", and sit on
        # "N tiles pending" forever.
        if "{z}" in body or "{x}" in body or "{y}" in body:
            print("     BAD URL      uses {z}/{x}/{y}; QMapShack before v1.20.0")
            print("                  does not translate those. Needs %1/%2/%3.")
            faults.append("%s uses {z}/{x}/{y} placeholders -- replace with "
                          "%%1/%%2/%%3" % tms.name)
        elif "%1" not in body:
            print("     BAD URL      no %1/%2/%3 place markers in the ServerUrl")
            faults.append("%s has no %%1/%%2/%%3 place markers" % tms.name)
        print("     declares     slippy z%d-z%d%s"
              % (declared[0], declared[1],
                 "" if mn and mx else "  (no range given; assumed z0-z20)"))
        if counts:
            on_disk = (min(counts), max(counts))
            print("     on disk      z%d-z%d   %s"
                  % (on_disk[0], on_disk[1],
                     ", ".join("z%d:%d" % (z, n) for z, n in sorted(counts.items()))))
            lo, hi = on_disk_range
            on_disk_range = [min(x for x in (lo, on_disk[0]) if x is not None),
                             max(x for x in (hi, on_disk[1]) if x is not None)]
            if on_disk != declared:
                print("     MISMATCH     declared z%d-z%d, disk has z%d-z%d"
                      % (declared + on_disk))
                faults.append("%s declares a zoom range the tiles do not cover"
                              % tms.name)
        else:
            print("     on disk      NO TILES")
            faults.append("%s has no tiles -- run the map step" % tms.name)
    if sources and not active_any:
        faults.append("no source is active -- the canvas stays empty until one is")

    print("\n4. VIEW")
    pf = ini_get(text, "Views\\%s\\posFocus" % QMS_VIEW_GROUP)
    zi = ini_get(text, "Views\\%s\\map2\\zoomIndex" % QMS_VIEW_GROUP)
    center = decode_pointf(pf) if pf else None
    if center is None:
        print("   no view center -- QMapShack opens on its built-in center,")
        print("   12E 49N in central Europe, where this node has no tiles")
        faults.append("no view center")
    else:
        print("   center     lat %.5f  lon %.5f" % center)
    if zi is not None:
        z = QMS_ZOOM_BASE - int(zi)
        print("   zoom       index %s -> slippy z%d" % (zi, z))
        lo, hi = on_disk_range
        if lo is not None and not (lo <= z <= hi):
            where = "below" if z < lo else "above"
            print("   -> z%d is %s the tiles on disk (z%d-z%d). QMapShack draws"
                  % (z, where, lo, hi))
            print("      NOTHING outside that range, and the \"N tiles pending\"")
            print("      label freezes rather than clearing -- it skips the code")
            print("      that recomputes it. ZOOM IN; the map is there.")
            faults.append("ZOOM: the saved view is at z%d, %s the tiles on disk "
                          "(z%d-z%d)" % (z, where, lo, hi))
        if center:
            print("\n5. THE TILES QMAPSHACK WILL ASK FOR AT THAT VIEW")
            for layer_dir in sorted(p for p in (TILES.iterdir() if TILES.is_dir() else [])
                                    if p.is_dir()):
                x, y = deg2num(center[0], center[1], z)
                hits = sum(1 for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                           if (layer_dir / str(z) / str(x + dx) / ("%d.png" % (y + dy))).is_file())
                print("   %-10s z%-3d center tile %d/%d -> %d of the 9 around it exist"
                      % (layer_dir.name, z, x, y, hits))
                if hits == 0 and not any(f.startswith("ZOOM:") for f in faults):
                    # Suppressed when the zoom is already the known cause --
                    # one fault, reported once, with the fix that applies.
                    faults.append("%s has no tiles at the view's position and zoom"
                                  % layer_dir.name)

    print("\n" + "=" * 74)
    if faults:
        zoom_only = all(f.startswith("ZOOM:") for f in faults)
        print("FAULTS")
        for f in faults:
            print("  - %s" % f.replace("ZOOM: ", ""))
        if zoom_only:
            print("\nZOOM IN. Nothing is wrong with the maps and nothing needs")
            print("re-running -- the view is simply outside the tiles you fetched.")
        else:
            print("\nMost of these are fixed by re-running the provisioner's map step")
            print("and then its 'App profiles + ALE channel plan' step, in that order.")
            print("Tiles already on disk are skipped, so the map step costs seconds.")
        return 1
    print("Everything the filesystem can confirm looks right. If the canvas is still")
    print("blank, check you have not zoomed out past the fetched range -- below it")
    print("the layer draws nothing at all, with no error.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
