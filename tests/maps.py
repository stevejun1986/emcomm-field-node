#!/usr/bin/env python3
"""Offline maps: what QMapShack is actually handed, and where.

    python3 tests/maps.py

Nothing here needs a display, a network or QMapShack itself. The assertions
are about the three things that were wrong and are cheap to get wrong again:
the settings file is the one QMapShack reads, the view opens on the operating
area rather than on its built-in center in Europe, and a tile path is resolved
by string substitution rather than by starting a JavaScript engine per tile.

The QPointF encoder is the part with no margin for error -- QMapShack stores
the view center as a binary QVariant, so a byte wrong is a view silently at
(0, 0). Its output was verified against Qt's own QSettings reader over 3,005
coordinates; this file re-checks the properties that do not need Qt present.
"""
import importlib.util
import hashlib
import math
import os
import re
import struct
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)

# The provisioner builds a GUI at import, so pull out just what is under test.
SRC = (REPO / "deploy_emcomm_node_gui.py").read_text()
mod = {"struct": struct, "math": math, "Path": Path, "hashlib": hashlib}
for name in ("QMS_CONF_REL", "QMS_CONF_LEGACY_REL", "QMS_VIEW_GROUP",
             "QMS_VIEW_GROUP_ENC", "TMS_ZOOM_BASE",
             "QMS_SCALES_SQUARE", "QMS_ZOOM_BASE", "QMS_DEFAULT_VIEW_ZOOM"):
    m = re.search(r"^%s = .*$" % name, SRC, re.M)
    assert m, "constant %s went missing" % name
    exec(compile(m.group(0), "<prov>", "exec"), mod)
for fn in ("_qt_ini_escape", "qsettings_qpointf", "utm_proj_for", "tile_zoom_range",
           "tms_zoom_levels", "qms_view_prefixes",
           "_qms_text_value", "qms_map_key", "_canvas_edit", "configure_qmapshack",
           "area_center"):
    m = re.search(r"^def %s\(.*?(?=\n\ndef |\n\n# |\n\nMAP_LAYERS)" % fn, SRC, re.S | re.M)
    assert m, "function %s went missing" % fn
    exec(compile(m.group(0), "<prov>", "exec"), mod)


# --- the settings file is the one QMapShack reads ------------------------
# QMapShack's main.cpp sets organization "QLandkarte"; QLandkarteGT was the
# predecessor project and nothing reads it.
assert str(mod["QMS_CONF_REL"]) == ".config/QLandkarte/QMapShack.conf", mod["QMS_CONF_REL"]
assert "QLandkarteGT" in str(mod["QMS_CONF_LEGACY_REL"])
assert "QLandkarteGT" not in str(mod["QMS_CONF_REL"])
print("OK: the staged profile goes where QMapShack looks for it")


# --- the view center encodes to what QSettings would write ---------------
# Type id 26 is QVariant::PointF, then two big-endian doubles, x = longitude
# and y = latitude, both in RADIANS.
def decode(setting: str):
    body = setting[1:-1] if setting.startswith('"') else setting
    assert body.startswith("@Variant(") and body.endswith(")"), body
    esc = body[len("@Variant("):-1]
    simple = {"0": 0x00, "a": 0x07, "b": 0x08, "f": 0x0C,
              "n": 0x0A, "r": 0x0D, "t": 0x09, "v": 0x0B, '"': 0x22, "\\": 0x5C}
    out, i = bytearray(), 0
    while i < len(esc):
        ch = esc[i]
        if ch != "\\":
            out.append(ord(ch)); i += 1; continue
        nxt = esc[i + 1]
        if nxt == "x":
            j = i + 2
            while j < len(esc) and j < i + 4 and esc[j] in "0123456789abcdefABCDEF":
                j += 1
            out.append(int(esc[i + 2:j], 16)); i = j
        else:
            out.append(simple[nxt]); i += 2
    tid, x, y = struct.unpack(">Idd", bytes(out))
    assert tid == 26, tid
    return x, y

for lat, lon in [(33.45, -112.07), (0.0, 0.0), (85.0, 180.0), (-85.0, -180.0),
                 (49.0, 12.0), (-33.87, 151.21), (64.13, -21.90)]:
    x, y = math.radians(lon), math.radians(lat)
    got_x, got_y = decode(mod["qsettings_qpointf"](x, y))
    assert abs(got_x - x) < 1e-12 and abs(got_y - y) < 1e-12, (lat, lon, got_x, got_y)
print("OK: the view center round-trips through Qt's own escaping, in radians")

# A value whose bytes contain ';' ',' or '=' must be quoted, or QSettings
# reads it back as a string list and the view silently falls back to (0, 0).
quoted = [lat for lat in range(-85, 86)
          if mod["qsettings_qpointf"](0.5, math.radians(lat)).startswith('"')]
assert quoted, "no case exercised the quoting path -- the check below proves nothing"
for lat in quoted:
    s = mod["qsettings_qpointf"](0.5, math.radians(lat))
    assert s.startswith('"') and s.endswith('"')
    assert any(c in s for c in ";,=")
print("OK: %d of 171 latitudes need quoting, and get it" % len(quoted))


# --- UTM zone, which is the grid USNG is built on ------------------------
assert "+zone=12 " in mod["utm_proj_for"](33.45, -112.07)
assert "+zone=56 +south" in mod["utm_proj_for"](-33.87, 151.21)
assert "+zone=1 " in mod["utm_proj_for"](0.0, -180.0)
assert "+zone=60 " in mod["utm_proj_for"](0.0, 179.9)
assert "+south" not in mod["utm_proj_for"](33.45, -112.07)
print("OK: UTM zone and hemisphere follow the operating area")


# --- all three conditions, because maps are invisible without any one ----
# 1. mapPath names the directory, or QMapShack never lists the .tms files.
# 2. the map is registered active, or it is listed but not drawn.
# 3. the view is over the tiles.
def fixture(td, with_tms=True):
    maps = Path(td) / "Offline_Maps"
    maps.mkdir(parents=True)
    files = [maps / "Topo_Offline.tms", maps / "Satellite_Offline.tms"]
    if with_tms:
        for i, f in enumerate(files):
            f.write_text("<TMS><Layer idx=\"0\"><Title>L%d</Title></Layer></TMS>\n" % i)
    return maps, files

with tempfile.TemporaryDirectory() as td:
    maps, files = fixture(td)
    conf = Path(td) / "QMapShack.conf"
    r = mod["configure_qmapshack"](conf, maps, files, 33.45, -112.07)
    text = conf.read_text()

    assert r["mapPath"] == "added", r
    assert ("mapPath=%s" % maps) in text, text
    assert r["maps"].startswith("registered Topo_Offline.tms"), r
    assert r["view"] == "seeded", r

    # The key QMapShack will compute for that file, independently derived.
    key = hashlib.md5(files[0].read_bytes()[:4096]).hexdigest()
    assert mod["qms_map_key"](files[0]) == key
    assert "map2\\keysKnownMaps=%s" % key in text, text
    assert "map2\\%s\\isActive=true" % key in text, text
    assert "map2\\%s\\filename=%s" % (key, files[0]) in text, text

    # Only the first layer draws: two active raster layers stack, and the
    # upper one hides the lower.
    other = hashlib.md5(files[1].read_bytes()[:4096]).hexdigest()
    assert other not in text, "the second layer must not also be activated"
print("OK: map path, an active map source, and the view are all written")

# A stock clone has no staged profile, so there is no config file at all.
# That is the case that used to leave the maps invisible.
with tempfile.TemporaryDirectory() as td:
    maps, files = fixture(td)
    conf = Path(td) / "QMapShack.conf"
    assert not conf.exists()
    r = mod["configure_qmapshack"](conf, maps, files, 33.45, -112.07)
    assert conf.is_file(), "no config was written where none existed"
    assert r["mapPath"] == "added" and r["maps"].startswith("registered")
print("OK: a node with no staged profile still gets a usable QMapShack")

# Re-running must not take anything back from an operator who has since used
# QMapShack -- it writes its own answers to all three on exit.
with tempfile.TemporaryDirectory() as td:
    maps, files = fixture(td)
    conf = Path(td) / "QMapShack.conf"
    mod["configure_qmapshack"](conf, maps, files, 33.45, -112.07)
    before = conf.read_text()
    r = mod["configure_qmapshack"](conf, maps, files, 0.0, 0.0)
    assert r == {"mapPath": "already listed", "maps": "kept", "view": "kept"}, r
    assert conf.read_text() == before, "a re-run changed a saved configuration"
print("OK: a second run keeps every choice the operator has made")

# An existing mapPath from a group's profile is appended to, not replaced.
with tempfile.TemporaryDirectory() as td:
    maps, files = fixture(td)
    conf = Path(td) / "QMapShack.conf"
    conf.write_text("[Canvas]\nmapPath=/srv/group-maps\n[MainWindow]\nx=1\n")
    r = mod["configure_qmapshack"](conf, maps, files, 33.45, -112.07)
    out = conf.read_text()
    assert r["mapPath"] == "appended", r
    assert "mapPath=/srv/group-maps, %s" % maps in out, out
    assert "[MainWindow]" in out and "x=1" in out
    assert out.index("posFocus") < out.index("[MainWindow]"), "keys landed outside [Canvas]"
print("OK: an existing map path is appended to, never replaced")

# No tiles fetched yet: say so rather than register a map file that is not there.
with tempfile.TemporaryDirectory() as td:
    maps, files = fixture(td, with_tms=False)
    conf = Path(td) / "QMapShack.conf"
    r = mod["configure_qmapshack"](conf, maps, files, 33.45, -112.07)
    assert r["maps"] == "no .tms files to register", r
    assert r["mapPath"] == "added" and r["view"] == "seeded"
print("OK: absent map sources are reported, not invented")

# No operating area: the path and maps still get written, the view does not.
with tempfile.TemporaryDirectory() as td:
    maps, files = fixture(td)
    conf = Path(td) / "QMapShack.conf"
    r = mod["configure_qmapshack"](conf, maps, files, None, None)
    assert r["view"] == "no operating area", r
    assert r["mapPath"] == "added" and r["maps"].startswith("registered")
    assert "posFocus" not in conf.read_text()
print("OK: a missing area costs the view, not the maps")

with tempfile.TemporaryDirectory() as td:
    maps, files = fixture(td)
    conf = Path(td) / "QMapShack.conf"
    mod["configure_qmapshack"](conf, maps, files, 33.45, -112.07)
    text = conf.read_text()
    # The encoded spelling, because that is the one QSettings writes -- see
    # the view-seeding section below for why writing the other one matters.
    vp = mod["qms_view_prefixes"]()[0]
    zi = 20 - mod["QMS_DEFAULT_VIEW_ZOOM"]
    assert vp + "map2\\zoomIndex=%d" % zi in text
    # And under the pre-map2 group, which is what the field build reads.
    assert vp + "map\\zoomIndex=%d" % zi in text
    assert vp + "scales=1" in text
    assert "+proj=utm +zone=12" in text
    x, y = decode(text.split(vp + "posFocus=")[1].splitlines()[0])
    assert abs(math.degrees(y) - 33.45) < 1e-9 and abs(math.degrees(x) + 112.07) < 1e-9
print("OK: zoom index, scale table, grid projection and center are all correct")

# The staged profile is a group's file, not ours. A byte this provisioner
# cannot decode has to come back out unchanged.
with tempfile.TemporaryDirectory() as td:
    maps, files = fixture(td)
    conf = Path(td) / "QMapShack.conf"
    conf.write_bytes(b"[Canvas]\ntitle=caf\xe9\n[MainWindow]\nx=1\n")
    mod["configure_qmapshack"](conf, maps, files, 33.45, -112.07)
    out = conf.read_bytes()
    assert b"title=caf\xe9" in out, "an undecodable byte was corrupted"
    assert b"[MainWindow]" in out
print("OK: bytes this provisioner cannot decode survive the edit")


# --- the zoom range comes from the tiles that exist ----------------------
with tempfile.TemporaryDirectory() as td:
    layer = Path(td) / "topo"
    assert mod["tile_zoom_range"](layer) is None, "missing directory must not guess"
    layer.mkdir()
    assert mod["tile_zoom_range"](layer) is None, "empty directory must not guess"
    for z in (10, 11, 15):
        (layer / str(z)).mkdir()
    (layer / "notazoom").mkdir()
    assert mod["tile_zoom_range"](layer) == (10, 15)
print("OK: the declared zoom range is what is on disk, not an assumption")


# --- .tms zoom levels are QMapShack's scale indices, not slippy zooms ----
# CMapTMS::draw():   zMax = 21 - minZoomLevel   (highest slippy z allowed)
#                    zMin = 21 - maxZoomLevel   (lowest slippy z allowed)
# So the element named "Min" bounds the zoomed-IN end. Writing the slippy
# numbers straight in told QMapShack a z10-15 pyramid served z6-z11: it
# clamped the canvas to z11, requested z6-z9 tiles that do not exist, and
# showed "22 tiles pending" over a blank canvas on a real node.
assert mod["TMS_ZOOM_BASE"] == 21
for (lo, hi), expect in (((10, 15), (6, 11)), ((0, 20), (1, 21)), ((12, 12), (9, 9))):
    got = mod["tms_zoom_levels"]((lo, hi))
    assert got == expect, ((lo, hi), got, expect)
    # Round-trip through QMapShack's own arithmetic: what it will believe.
    zmax, zmin = 21 - got[0], 21 - got[1]
    assert (zmin, zmax) == (lo, hi), ("QMapShack would believe", zmin, zmax)
# The defaults QMapShack uses when the elements are absent mean slippy 0-20,
# which is why omitting them worked and writing slippy numbers did not.
assert (21 - 1, 21 - 21) == (20, 0)
print("OK: .tms zoom levels are written in QMapShack's inverted numbering")


# --- the .tms the provisioner writes -------------------------------------
# Rendered here the same way step_config_profiles renders it, so the shape
# under test is the shape that ships.
tms = """<TMS>
<Layer idx="0">
  <Title>Topographic (Offline)</Title>
  <MinZoomLevel>6</MinZoomLevel>
  <MaxZoomLevel>11</MaxZoomLevel>
  <ServerUrl>file:///home/op/EMCOMM_Data/Offline_Maps/Offline_Tiles/topo/%1/%2/%3.png</ServerUrl>
</Layer>
</TMS>
"""
root = ET.fromstring(tms)
layer = root.find("Layer")
assert layer.find("Script") is None, "a <Script> layer starts a JS engine per tile"
url = layer.find("ServerUrl").text
assert url.startswith("file://"), url
# 6 and 11 are slippy z15 and z10 in QMapShack's numbering.
assert 21 - int(layer.find("MinZoomLevel").text) == 15
assert 21 - int(layer.find("MaxZoomLevel").text) == 10

src = (REPO / "deploy_emcomm_node_gui.py").read_text()
gen = src.split("for layer, tms_name, title in MAP_LAYERS:")[1][:1200]
assert "<ServerUrl>" in gen, "the generator no longer writes a ServerUrl"
assert "<Script>" not in gen, "the generator still writes a Script layer"
assert "tms_zoom_levels(" in gen, "the generator writes raw slippy zooms again"

# createUrl() ends in strUrl.arg(z).arg(x).arg(y), so the URL has to carry Qt's
# own place markers. QMapShack rewrites {z}/{x}/{y} into them only from v1.20.0
# (QMS-920); Mint 22.3 ships 1.17.1, where the braces survive and every request
# asks for a file literally named "{z}/{x}/{y}.png". Cost a full day of testing.
assert "%1/%2/%3" in gen, "the ServerUrl must use Qt place markers"
assert "{z}" not in gen and "{{z}}" not in gen, \
    "brace templates are unsupported before QMapShack v1.20.0"
# %1 = z, %2 = x, %3 = y, matching the fetcher's <layer>/<z>/<x>/<y>.png layout.
assert url.endswith("/%1/%2/%3.png"), url
print("OK: tile paths resolve by substitution, with the real zoom range declared")


# --- operating area center, given either way -----------------------------
assert mod["area_center"]({"center": {"lat": 33.45, "lon": -112.07}}) == (33.45, -112.07)
assert mod["area_center"]({"north": 1.0, "south": -1.0, "east": 4.0, "west": 2.0}) == (0.0, 3.0)
assert mod["area_center"]({"north": 1.0}) is None
assert mod["area_center"]({}) is None
print("OK: an area center is read from a center or derived from a box")

# --- neither row may pass for work that did not happen -------------------
# configure_qmapshack() creates the settings file whether or not a profile
# exists to stage, so an ungated existence check reported "QMapShack profile
# staged -- PASS" on a run whose own log said "QMapShack left unconfigured".
# Observed on a node, 2026-09-22.
verif = SRC[SRC.index('if "config_profiles" in selected_ids:'):]
verif = verif[:verif.index('if "dock_trigger" in selected_ids:')]
i = verif.index('qms = home / QMS_CONF_REL')
gate = verif[i:i + 400]
assert 'Path("configs/QMapShack.conf").is_file()' in gate, \
    "the profile row must be gated on a profile existing to stage"
assert verif.index('_placeholder_count(qms)') > verif.index(
    'Path("configs/QMapShack.conf").is_file()'), \
    "the placeholder row must sit inside that gate -- it has nothing to " \
    "substitute in a file the provisioner generated"
assert "no configs/QMapShack.conf to stage" in verif, \
    "with no profile, the row must say so rather than pass"

# The step's closing summary fired "Every application will start unconfigured"
# directly beneath eight lines reporting that QMapShack's map path, active
# source and view had all been written. A summary that contradicts the log
# above it teaches people to distrust the log.
step = SRC[SRC.index("def step_config_profiles"):SRC.index("def step_dock_trigger")]
# Comments quote the old wording to explain why it went; only the code counts.
code = "\n".join(l for l in step.splitlines() if not l.lstrip().startswith("#"))
assert "Every application will start unconfigured" not in code
assert "configured separately, above" in code
print("OK: no verification row passes for work that did not happen")


# --- the shipped diagnostic must not drift from the provisioner ----------
# scripts/diagnose_qmapshack_maps.py is standalone on purpose -- a node may
# not have a Python with tkinter when someone needs to run it, so it cannot
# import the provisioner. That means it repeats these constants, and a repeated
# constant is a constant that drifts. This is the check that stops it.
DIAG = (REPO / "scripts" / "diagnose_qmapshack_maps.py").read_text()

def const(src, name):
    m = re.search(r"^%s = (.+?)(?:\s+#.*)?$" % name, src, re.M)
    assert m, "%s not found" % name
    return m.group(1).strip()

for name in ("QMS_CONF_REL", "QMS_CONF_LEGACY_REL", "QMS_VIEW_GROUP",
             "QMS_ZOOM_BASE", "TMS_ZOOM_BASE", "OPERATOR_PREFIX"):
    a, b = const(SRC, name), const(DIAG, name)
    assert a == b, "%s drifted: provisioner %s, diagnostic %s" % (name, a, b)
print("OK: the diagnostic's constants still match the provisioner's")

# It is a troubleshooting tool for a node that may be in a bad state, so it
# must not need anything the node might not have, and must not write.
assert "import tkinter" not in DIAG and "requests" not in DIAG, "stdlib only"
for forbidden in ("write_text(", "write_bytes(", "mkdir(", "unlink(", "rmtree"):
    assert forbidden not in DIAG, "the diagnostic is read-only: %s" % forbidden
assert DIAG.lstrip().startswith("#!/usr/bin/env python3"), "needs a shebang"
assert "GNU General Public License" in DIAG, "shipped script needs the GPL notice"
print("OK: the diagnostic is stdlib-only, read-only, and carries its notice")

# It ships in the release archive, which is where an operator will need it.
import subprocess
listed = subprocess.run(["git", "check-ignore", "scripts/diagnose_qmapshack_maps.py"],
                        capture_output=True, text=True).returncode
assert listed != 0, "the diagnostic is gitignored and would not ship"
attrs = (REPO / ".gitattributes").read_text()
assert "scripts/" not in attrs, "scripts/ must not be export-ignored"
print("OK: the diagnostic ships in the release archive")


# --- seeding a view happens once, not on every run -----------------------
# QSettings percent-encodes the space when it WRITES a group name, so what
# QMapShack saves is "View%201" -- while a literal "View 1" in the file reads
# back the same, because Qt unescapes on read. Both work going in; only one
# comes back out.
#
# So a check that knows only the literal spelling cannot see the group
# QMapShack itself wrote. It concludes the profile has no saved view, seeds
# one, and the operator's own view center is gone -- on the second run of a
# provisioner whose whole promise here is to seed and never overwrite.
prefixes = mod["qms_view_prefixes"]()
assert prefixes[0] == "Views\\View%201\\", prefixes[0]
assert prefixes[1] == "Views\\View 1\\", prefixes[1]
print("OK: both spellings of the view group are known, the written one first")


def staged(body: str) -> Path:
    conf = Path(tempfile.mkdtemp()) / "QMapShack.conf"
    conf.write_text(body)
    return conf


maps_dir = Path(tempfile.mkdtemp())
one_tms = maps_dir / "Topo.tms"
one_tms.write_text("<TMS></TMS>")
OWN = mod["qsettings_qpointf"](math.radians(-93.2), math.radians(44.9))

# The shape QMapShack leaves behind after it has been opened and closed once.
after_qmapshack = staged("[Canvas]\nmapPath=\n"
                         "Views\\View%%201\\posFocus=%s\n"
                         "Views\\View%%201\\map2\\keysKnownMaps=abc\n" % OWN)
done = mod["configure_qmapshack"](after_qmapshack, maps_dir, [one_tms],
                                  lat=33.5, lon=-112.05, zoom=12)
text = after_qmapshack.read_text()
assert done["view"] == "kept", "re-seeded over a saved view: %r" % done["view"]
assert done["maps"] == "kept", "re-registered known maps: %r" % done["maps"]
assert text.count("posFocus=") == 1, "%d posFocus lines" % text.count("posFocus=")
assert OWN in text, "the operator's own view center was overwritten"
print("OK: a profile QMapShack has saved is left alone on a re-run")

# The older literal spelling still counts as a saved view.
legacy = staged("[Canvas]\nmapPath=\nViews\\View 1\\posFocus=%s\n" % OWN)
done = mod["configure_qmapshack"](legacy, maps_dir, [one_tms],
                                  lat=33.5, lon=-112.05, zoom=12)
assert done["view"] == "kept", "literal 'View 1' no longer recognized"
print("OK: the literal spelling is recognized too")

# A profile that has never been opened still gets seeded, in Qt's spelling.
fresh = staged("[Canvas]\nmapPath=\n")
done = mod["configure_qmapshack"](fresh, maps_dir, [one_tms],
                                  lat=33.5, lon=-112.05, zoom=12)
assert done["view"] == "seeded", done["view"]
assert "Views\\View%201\\posFocus=" in fresh.read_text(), \
    "seeded under a spelling Qt does not write"
before = fresh.read_text()
mod["configure_qmapshack"](fresh, maps_dir, [one_tms], lat=33.5, lon=-112.05, zoom=12)
assert fresh.read_text() == before, "a second run changed what the first one seeded"
print("OK: a fresh profile is seeded once, and a re-run changes nothing")


# --- a map is activated under BOTH schemas -------------------------------
# QMapShack renamed the map-list group from "map" to "map2" and moved
# activation to a per-key isActive. A build older than that rename ignores
# every map2 key: the sources still appear in the Maps tab, because
# loadMapList() scans mapPath and adds what it finds as Unused, but none is
# active and the canvas stays empty until the operator clicks one.
#
# This was found on a node, not in a test -- the maps listed, the view was
# right, and the operator still had to click. So both spellings are written,
# and current QMapShack honors the old one: loadMapList() applies
# map/active after map2, unconditionally, calling activate() per key.
with tempfile.TemporaryDirectory() as td:
    maps, files = fixture(td)
    conf = Path(td) / "QMapShack.conf"
    mod["configure_qmapshack"](conf, maps, files, 33.45, -112.07)
    text = conf.read_text()
    vp = mod["qms_view_prefixes"]()[0]
    first = mod["qms_map_key"](files[0])
    assert vp + "map2\\keysKnownMaps=" + first in text, "current schema missing"
    assert vp + "map2\\" + first + "\\isActive=true" in text, "current schema missing"
    assert vp + "map\\active=" + first in text, \
        "nothing activates the map on a build older than the map2 rename"
    # Exactly one map is activated under either spelling; two raster layers
    # stack and the upper hides the lower.
    assert text.count(vp + "map\\active=") == 1
    for f in files[1:]:
        k = mod["qms_map_key"](f)
        assert vp + "map\\active=" + k not in text, "activated more than one map"
print("OK: the active map is registered under both the old and new schemas")


print("\nMAPS: ALL ASSERTIONS PASSED")
