#!/usr/bin/env python3
"""Operating-area screen: centre/radius input, the estimate, and the file it writes.

    python3 tests/area.py            # on a desktop session
    xvfb-run -a python3 tests/area.py

The estimate an operator is shown must be the same arithmetic the download
performs, so it is computed by importing the fetcher rather than by a second
implementation. These assertions check that it stays that way.
"""
import os, sys, json, tempfile, dataclasses
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO); sys.path.insert(0, str(REPO))
import deploy_emcomm_node_gui as mod

tf = mod.load_tile_fetcher()
assert tf is not None, "the GUI could not import the tile fetcher"
print("OK: GUI imports the fetcher — estimate and download share one implementation")

# --- box maths -----------------------------------------------------------
b = tf.box_from_center(39.0, -77.0, 50)
assert b["north"] > 39.0 > b["south"] and b["east"] > -77.0 > b["west"], b
lat_span = (b["north"] - b["south"]) * tf.MILES_PER_DEG_LAT
assert 99 < lat_span < 101, lat_span
print("OK: 50 mi radius -> %.1f mi tall box" % lat_span)

# longitude must widen with latitude, not stay fixed
narrow = tf.box_from_center(10.0, 0.0, 50)["east"]
wide = tf.box_from_center(60.0, 0.0, 50)["east"]
assert wide > narrow, (narrow, wide)
print("OK: longitude span widens with latitude (%.3f deg at 10N, %.3f at 60N)" % (narrow, wide))

# poles and antimeridian must still yield a valid box
for lat, lon in ((84.9, 179.9), (-84.9, -179.9), (89.0, 0.0)):
    box = tf.box_from_center(lat, lon, 150)
    tf.validate_area(dict(box))          # exits the process if invalid
    assert -85 <= box["south"] < box["north"] <= 85, box
    assert -180 <= box["west"] < box["east"] <= 180, box
print("OK: extreme centres clamp to a valid box instead of an unusable one")

# --- tiering -------------------------------------------------------------
for radius in (50, 75, 150):
    passes = mod.fetch_passes({"center": {"lat": 39.0, "lon": -77.0},
                               "radius_miles": radius}, tf)
    assert len(passes) == 2, passes
    (d_desc, d_box, d_z), (o_desc, o_box, o_z) = passes
    assert d_z == tf.DETAIL_ZOOMS and o_z == tf.OVERVIEW_ZOOMS, passes
    assert d_box["north"] < o_box["north"], "detail ring must sit inside the radius"
    n = sum(tf.count_tiles(bx, range(z0, z1 + 1)) for _d, bx, (z0, z1) in passes) * 2
    flat = tf.count_tiles(tf.box_from_center(39.0, -77.0, radius),
                          range(*[tf.DETAIL_ZOOMS[0], tf.DETAIL_ZOOMS[1] + 1])) * 2
    print("  %3d mi: tiered %8s tiles vs %9s flat at street zoom  (%.0fx less)"
          % (radius, format(n, ","), format(flat, ","), flat / n))
    assert n < flat, (n, flat)
assert mod.fetch_passes({"center": {"lat": 39.0, "lon": -77.0}, "radius_miles": 25}, tf)[0]
assert len(mod.fetch_passes({"center": {"lat": 39.0, "lon": -77.0},
                             "radius_miles": 25}, tf)) == 1, "radius == detail needs one pass"
print("OK: tiering keeps every radius viable; radius == detail collapses to one pass")

# a hand-written rectangle still works, and junk is rejected rather than guessed
assert len(mod.fetch_passes({"north": 1, "south": 0, "east": 1, "west": 0}, tf)) == 1
assert mod.fetch_passes({"nonsense": True}, tf) == []
print("OK: hand-written rectangle supported; malformed area returns no passes")

# --- the screen ----------------------------------------------------------
mod.os.geteuid = lambda: 1000
mod.messagebox.showerror = lambda *a, **k: None
app = mod.ProvisionerGUI()
app.node_id_var.set("N0CALL-1")

app.component_vars["config_profiles"].set(True)
app._on_continue_to_sudo()
assert "area" not in app.frames, "area screen must not appear when maps are not selected"
assert app.frames["sudo"].winfo_manager(), "should have gone straight to sudo"
print("OK: area screen skipped entirely when the map step is unchecked")

app._show("options")
app.component_vars["maps_fetch"].set(True)
app._on_continue_to_sudo()
assert "area" in app.frames and app.frames["area"].winfo_manager(), "area screen expected"
print("OK: area screen appears when the map step is checked")

app._on_area_continue()
assert "Enter a centre" in app.area_error.cget("text"), app.area_error.cget("text")
assert not app.frames["sudo"].winfo_manager(), "must not advance without a centre"
print("OK: blank centre blocked")

app.lat_var.set("91.0"); app.lon_var.set("-77.0"); app._on_area_continue()
assert "between -85 and 85" in app.area_error.cget("text"), app.area_error.cget("text")
app.lat_var.set("39.0"); app.lon_var.set("181.0"); app._on_area_continue()
assert "between -180 and 180" in app.area_error.cget("text"), app.area_error.cget("text")
print("OK: out-of-range latitude and longitude rejected with a specific message")

app.lat_var.set("39.123456"); app.lon_var.set("-77.987654")
app.radius_var.set(75)
app.update_idletasks()
est = app.area_estimate.cget("text")
assert "39.12" in est and "-77.99" in est, est
assert "radius 75 mi" in est and "total" in est, est
# every row already counts all layers; the sum must not be labelled as if the
# rows above it were per-layer
assert "both layers" not in est, est
rows = [l for l in est.splitlines() if "tiles" in l and "total" not in l]
per_row = sum(int(l.split()[-2].replace(",", "")) for l in rows)
total_row = int([l for l in est.splitlines() if "total" in l][0].split()[1].replace(",", ""))
assert per_row == total_row, (per_row, total_row)
one_layer = sum(tf.count_tiles(b, range(z0, z1 + 1)) for _d, b, (z0, z1) in
                mod.fetch_passes({"center": {"lat": 39.12, "lon": -77.99},
                                  "radius_miles": 75}, tf))
assert total_row == one_layer * len(mod.MAP_LAYERS), (total_row, one_layer)
print("OK: estimate rounds to 2 dp and totals both layers")
print("   " + est.replace("\n", "\n   "))

with tempfile.TemporaryDirectory() as td:
    mod.AREA_DIR = Path(td) / "areas"
    app._on_area_continue()
    written = list(mod.AREA_DIR.glob("*.json"))
    assert len(written) == 1, written
    spec = json.loads(written[0].read_text())
    assert spec["center"] == {"lat": 39.12, "lon": -77.99}, spec
    assert spec["radius_miles"] == 75 and spec["detail_radius_miles"] == 25, spec
    for k in ("north", "south", "east", "west"):
        assert k in spec, spec
    tf.validate_area(dict(spec))
    assert app.frames["sudo"].winfo_manager(), "should advance to sudo after writing"
    print("OK: wrote %s — centre, radius, detail ring and a valid bbox" % written[0].name)
    assert len(mod.fetch_passes(spec, tf)) == 2, "written file must tier on re-read"
    print("OK: the written file tiers correctly when read back")

app._back_from_sudo()
assert app.frames["area"].winfo_manager(), "Back from sudo should return to the area screen"
print("OK: Back from sudo returns to the area screen, not past it")

CF30_W, CF30_H = 1024, 768
f = app.frames["area"]
assert f.winfo_reqwidth() <= CF30_W - 40 and f.winfo_reqheight() <= CF30_H - 90, \
    (f.winfo_reqwidth(), f.winfo_reqheight())
print("OK: area screen fits a CF-30 panel (%d x %d)" % (f.winfo_reqwidth(), f.winfo_reqheight()))
app.destroy()

# --- a stale copy of the shipped sample must not be fetched -------------
# A clone predating the .json.sample rename leaves a real example-area.json on
# disk. configs/areas/*.json is gitignored, so git never mentions it and it is
# fetched in silence: someone else's city, at full detail, ahead of the
# operator's own area. Observed on a live run.
sample_spec = json.loads((REPO / "configs" / "areas" / "example-area.json.sample").read_text())
assert mod.is_unmodified_sample(sample_spec), "the sample itself must be recognised"
print("OK: an unmodified sample copy is recognised by its bounds")

edited = dict(sample_spec); edited["north"] = round(edited["north"] + 0.5, 6)
assert not mod.is_unmodified_sample(edited), "an edited sample is a real area"
print("OK: editing the bounds makes it a legitimate area again")

assert not mod.is_unmodified_sample({"center": {"lat": 1, "lon": 2}, "radius_miles": 50})
assert not mod.is_unmodified_sample({"nonsense": True})
print("OK: centre/radius areas and malformed specs are not mistaken for it")

# end to end: a stale file present alongside a real one is skipped, not fetched
import types, shutil as _sh
with tempfile.TemporaryDirectory() as td:
    areas = Path(td) / "areas"; areas.mkdir()
    _sh.copy(REPO / "configs" / "areas" / "example-area.json.sample", areas / "example-area.json")
    (areas / "real-area.json").write_text(json.dumps(
        {"center": {"lat": 34.06, "lon": -117.55}, "radius_miles": 50}))
    old_dir = mod.AREA_DIR
    mod.AREA_DIR = areas
    logs, cmds = [], []
    ctx = types.SimpleNamespace(
        home=Path(td), user="op", node_id="N0CALL-1", data_dir=Path(td) / "data",
        log=lambda m, l="info": logs.append((l, m)),
        run=lambda cmd, **k: (cmds.append(cmd), types.SimpleNamespace(returncode=0))[1],
        spin=mod.Ctx.spin.__get__(types.SimpleNamespace(
            cancel_check=lambda: None, spin_start=lambda l: None,
            spin_stop=lambda ok: None), object))
    try:
        mod.step_maps_fetch(ctx)
    finally:
        mod.AREA_DIR = old_dir
    fetched = {c[c.index("--layer") - 1] if "--layer" in c else "" for c in cmds}
    joined = " ".join(" ".join(map(str, c)) for c in cmds)
    assert "example-area" not in joined, "the stale sample was fetched"
    assert any("real-area" in m or "real-area" in str(m) for _l, m in logs) or cmds, logs
    assert any("shipped sample's bounds unchanged" in m for _l, m in logs), logs
    assert any("Operating area(s) found" in m for _l, m in logs), logs
    assert len(cmds) == 4, "the real area should still run 2 passes x 2 layers: %d" % len(cmds)
    print("OK: stale sample skipped with a reason; the real area still fetched (%d passes)" % len(cmds))
    for lvl, m in logs:
        if lvl in ("warn", "info"):
            print("     [%s] %s" % (lvl, m[:96]))

print("\nAREA: ALL ASSERTIONS PASSED")
