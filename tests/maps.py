#!/usr/bin/env python3
"""Offline maps: what QMapShack is actually handed, and where.

    python3 tests/maps.py

Nothing here needs a display, a network or QMapShack itself. The assertions
are about the three things that were wrong and are cheap to get wrong again:
the settings file is the one QMapShack reads, the view opens on the operating
area rather than on its built-in centre in Europe, and a tile path is resolved
by string substitution rather than by starting a JavaScript engine per tile.

The QPointF encoder is the part with no margin for error -- QMapShack stores
the view centre as a binary QVariant, so a byte wrong is a view silently at
(0, 0). Its output was verified against Qt's own QSettings reader over 3,005
coordinates; this file re-checks the properties that do not need Qt present.
"""
import importlib.util
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
mod = {"struct": struct, "math": math, "Path": Path}
for name in ("QMS_CONF_REL", "QMS_CONF_LEGACY_REL", "QMS_VIEW_GROUP",
             "QMS_SCALES_SQUARE", "QMS_ZOOM_BASE", "QMS_DEFAULT_VIEW_ZOOM"):
    m = re.search(r"^%s = .*$" % name, SRC, re.M)
    assert m, "constant %s went missing" % name
    exec(compile(m.group(0), "<prov>", "exec"), mod)
for fn in ("_qt_ini_escape", "qsettings_qpointf", "utm_proj_for", "tile_zoom_range",
           "_qms_text_value", "seed_qmapshack_view", "area_centre"):
    m = re.search(r"^def %s\(.*?(?=\n\ndef |\n\n# |\n\nMAP_LAYERS)" % fn, SRC, re.S | re.M)
    assert m, "function %s went missing" % fn
    exec(compile(m.group(0), "<prov>", "exec"), mod)


# --- the settings file is the one QMapShack reads ------------------------
# QMapShack's main.cpp sets organisation "QLandkarte"; QLandkarteGT was the
# predecessor project and nothing reads it.
assert str(mod["QMS_CONF_REL"]) == ".config/QLandkarte/QMapShack.conf", mod["QMS_CONF_REL"]
assert "QLandkarteGT" in str(mod["QMS_CONF_LEGACY_REL"])
assert "QLandkarteGT" not in str(mod["QMS_CONF_REL"])
print("OK: the staged profile goes where QMapShack looks for it")


# --- the view centre encodes to what QSettings would write ---------------
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
print("OK: the view centre round-trips through Qt's own escaping, in radians")

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


# --- seeding is a first-launch nudge, never a takeover -------------------
with tempfile.TemporaryDirectory() as td:
    conf = Path(td) / "QMapShack.conf"
    assert mod["seed_qmapshack_view"](conf, 33.45, -112.07) == "seeded"
    text = conf.read_text()
    assert "[Canvas]" in text
    assert "Views\\View 1\\posFocus=" in text
    # zoomIndex = 20 - slippy zoom, on the square (tile-aligned) scale table
    assert "Views\\View 1\\map2\\zoomIndex=%d" % (20 - mod["QMS_DEFAULT_VIEW_ZOOM"]) in text
    assert "Views\\View 1\\scales=1" in text
    assert "+proj=utm +zone=12" in text
    x, y = decode(text.split("Views\\View 1\\posFocus=")[1].splitlines()[0])
    assert abs(math.degrees(y) - 33.45) < 1e-9 and abs(math.degrees(x) + 112.07) < 1e-9

    # A second run must not move an operator who has since saved their own view.
    before = conf.read_text()
    assert mod["seed_qmapshack_view"](conf, 0.0, 0.0) == "kept"
    assert conf.read_text() == before
print("OK: seeds an absent view, and leaves a saved one alone")

# Seeding into a file that already has a [Canvas] section must not cost the
# mapPath line -- that is what registers the offline maps in the first place.
with tempfile.TemporaryDirectory() as td:
    conf = Path(td) / "QMapShack.conf"
    conf.write_text("[Canvas]\nmapPath=/home/op/EMCOMM_Data/Offline_Maps\n"
                    "[MainWindow]\ngeometry=@Variant(x)\n")
    assert mod["seed_qmapshack_view"](conf, 33.45, -112.07) == "seeded"
    out = conf.read_text()
    assert "mapPath=/home/op/EMCOMM_Data/Offline_Maps" in out
    assert "[MainWindow]" in out and "geometry=@Variant(x)" in out
    assert out.index("posFocus") < out.index("[MainWindow]"), "keys landed outside [Canvas]"
print("OK: merges into an existing [Canvas] without disturbing mapPath")

# The staged profile is a group's file, not ours. A byte this provisioner
# cannot decode has to come back out unchanged rather than as U+FFFD written
# over somebody's settings.
with tempfile.TemporaryDirectory() as td:
    conf = Path(td) / "QMapShack.conf"
    conf.write_bytes(b"[Canvas]\nmapPath=/home/op/Maps\ntitle=caf\xe9\n[MainWindow]\nx=1\n")
    assert mod["seed_qmapshack_view"](conf, 33.45, -112.07) == "seeded"
    out = conf.read_bytes()
    assert b"title=caf\xe9" in out, "an undecodable byte was corrupted"
    assert b"mapPath=/home/op/Maps" in out and b"[MainWindow]" in out
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


# --- the .tms the provisioner writes -------------------------------------
# Rendered here the same way step_config_profiles renders it, so the shape
# under test is the shape that ships.
tms = """<TMS>
<Layer idx="0">
  <Title>Topographic (Offline)</Title>
  <MinZoomLevel>10</MinZoomLevel>
  <MaxZoomLevel>15</MaxZoomLevel>
  <ServerUrl>file:///home/op/EMCOMM_Data/Offline_Maps/Offline_Tiles/topo/{z}/{x}/{y}.png</ServerUrl>
</Layer>
</TMS>
"""
root = ET.fromstring(tms)
layer = root.find("Layer")
assert layer.find("Script") is None, "a <Script> layer starts a JS engine per tile"
url = layer.find("ServerUrl").text
assert url.startswith("file://") and url.endswith("/{z}/{x}/{y}.png"), url
assert int(layer.find("MinZoomLevel").text) == 10
assert int(layer.find("MaxZoomLevel").text) == 15

src = (REPO / "deploy_emcomm_node_gui.py").read_text()
gen = src.split("for layer, tms_name, title in MAP_LAYERS:")[1][:1200]
assert "<ServerUrl>" in gen, "the generator no longer writes a ServerUrl"
assert "<Script>" not in gen, "the generator still writes a Script layer"
print("OK: tile paths resolve by substitution, with the real zoom range declared")


# --- operating area centre, given either way -----------------------------
assert mod["area_centre"]({"center": {"lat": 33.45, "lon": -112.07}}) == (33.45, -112.07)
assert mod["area_centre"]({"north": 1.0, "south": -1.0, "east": 4.0, "west": 2.0}) == (0.0, 3.0)
assert mod["area_centre"]({"north": 1.0}) is None
assert mod["area_centre"]({}) is None
print("OK: an area centre is read from a centre or derived from a box")

print("\nMAPS: ALL ASSERTIONS PASSED")
