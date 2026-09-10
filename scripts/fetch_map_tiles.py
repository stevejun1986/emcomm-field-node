#!/usr/bin/env python3
"""
Offline map tile fetcher for an EMCOMM Field Node.

Downloads slippy-map tiles for YOUR operating area into the layout QMapShack
expects, so mapping keeps working with no network. Nothing about a region is
hardcoded: pass a bounding box, or put one in a small JSON file per operating
area and pass that.

    ./fetch_map_tiles.py --north 34.10 --south 33.70 --west -112.30 --east -111.80
    ./fetch_map_tiles.py --area configs/areas/county.json

Estimate before committing to a download (tile counts grow ~4x per zoom level):

    ./fetch_map_tiles.py --area configs/areas/county.json --estimate-only

Tiles land in:
    ~/EMCOMM_Data/Offline_Maps/Offline_Tiles/<layer>/<z>/<x>/<y>.png

Default source is the USGS National Map, which is US-government public domain
and needs no API key. Anything else: check the provider's terms before bulk
downloading, and set a contact address in --user-agent so you are reachable.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# name -> (url template, description)
SOURCES = {
    "topo": (
        "https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}",
        "USGS topographic basemap (public domain)",
    ),
    "imagery": (
        "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}",
        "USGS aerial imagery (public domain)",
    ),
    "relief": (
        "https://basemap.nationalmap.gov/arcgis/rest/services/USGSShadedReliefOnly/MapServer/tile/{z}/{y}/{x}",
        "USGS shaded relief (public domain)",
    ),
}

DEFAULT_UA = "EMCOMM-Field-Node-TileFetcher/1.0 (set --user-agent with a contact address)"


def deg2num(lat_deg: float, lon_deg: float, zoom: int) -> tuple[int, int]:
    """Lat/lon to slippy-map tile x/y (standard Web Mercator)."""
    lat_rad = math.radians(lat_deg)
    n = 2.0 ** zoom
    x = int((lon_deg + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


def tile_range(area: dict, zoom: int) -> tuple[int, int, int, int]:
    x_min, y_min = deg2num(area["north"], area["west"], zoom)
    x_max, y_max = deg2num(area["south"], area["east"], zoom)
    return min(x_min, x_max), max(x_min, x_max), min(y_min, y_max), max(y_min, y_max)


def count_tiles(area: dict, zooms: range) -> int:
    total = 0
    for z in zooms:
        x0, x1, y0, y1 = tile_range(area, z)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


def validate_area(area: dict) -> dict:
    for k in ("north", "south", "east", "west"):
        if k not in area:
            sys.exit(f"error: area is missing '{k}'")
        area[k] = float(area[k])
    if area["north"] <= area["south"]:
        sys.exit("error: north must be greater than south")
    if area["east"] <= area["west"]:
        sys.exit("error: east must be greater than west (use negative values west of Greenwich)")
    if not (-85 <= area["south"] < area["north"] <= 85):
        sys.exit("error: latitudes must be between -85 and 85 (Web Mercator limit)")
    if not (-180 <= area["west"] < area["east"] <= 180):
        sys.exit("error: longitudes must be between -180 and 180")
    return area


def fetch(url: str, dest: Path, ua: str, timeout: int) -> str:
    """Returns 'ok', 'skip' (already present) or 'fail'."""
    if dest.exists() and dest.stat().st_size > 0:
        return "skip"
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return "fail"
    if not data:
        return "fail"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)          # atomic: a killed run never leaves a truncated tile
    return "ok"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Download offline map tiles for an operating area.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Sources:\n" + "\n".join(f"  {k:9s} {v[1]}" for k, v in SOURCES.items()),
    )
    ap.add_argument("--area", type=Path, help="JSON file with north/south/east/west")
    ap.add_argument("--north", type=float)
    ap.add_argument("--south", type=float)
    ap.add_argument("--east", type=float)
    ap.add_argument("--west", type=float)
    ap.add_argument("--layer", choices=sorted(SOURCES), default="topo")
    ap.add_argument("--min-zoom", type=int, default=10)
    ap.add_argument("--max-zoom", type=int, default=15)
    ap.add_argument("--out", type=Path,
                    default=Path.home() / "EMCOMM_Data" / "Offline_Maps" / "Offline_Tiles")
    ap.add_argument("--delay", type=float, default=0.1,
                    help="seconds between requests; be polite to the tile server (default 0.1)")
    ap.add_argument("--timeout", type=int, default=30)
    ap.add_argument("--user-agent", default=DEFAULT_UA)
    ap.add_argument("--estimate-only", action="store_true",
                    help="report tile count and exit without downloading")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    args = ap.parse_args()

    if args.area:
        if not args.area.is_file():
            sys.exit(f"error: no such area file: {args.area}")
        area = validate_area(json.loads(args.area.read_text()))
    elif None not in (args.north, args.south, args.east, args.west):
        area = validate_area({"north": args.north, "south": args.south,
                              "east": args.east, "west": args.west})
    else:
        ap.error("supply --area FILE, or all four of --north --south --east --west")

    if args.min_zoom > args.max_zoom:
        sys.exit("error: --min-zoom cannot exceed --max-zoom")
    zooms = range(args.min_zoom, args.max_zoom + 1)

    total = count_tiles(area, zooms)
    approx_mb = total * 25 / 1024          # ~25 KB/tile is a reasonable rule of thumb
    print(f"area   : N{area['north']} S{area['south']} W{area['west']} E{area['east']}")
    print(f"layer  : {args.layer} — {SOURCES[args.layer][1]}")
    print(f"zooms  : {args.min_zoom}-{args.max_zoom}")
    print(f"tiles  : {total:,}  (~{approx_mb:,.0f} MB, ~{total * args.delay / 60:,.0f} min at {args.delay}s spacing)")
    print(f"out    : {args.out / args.layer}")

    if args.estimate_only:
        return 0
    if total > 50_000 and not args.yes:
        print("\nThat is a very large download. Narrow the area or lower --max-zoom,")
        print("or re-run with --yes if you really want it.")
        return 1
    if not args.yes:
        try:
            if input("\nProceed? [y/N]: ").strip().lower() not in ("y", "yes"):
                print("aborted")
                return 1
        except (EOFError, KeyboardInterrupt):
            print("\naborted")
            return 1

    url_tmpl = SOURCES[args.layer][0]
    ok = skip = fail = 0
    started = time.time()
    try:
        for z in zooms:
            x0, x1, y0, y1 = tile_range(area, z)
            for x in range(x0, x1 + 1):
                for y in range(y0, y1 + 1):
                    dest = args.out / args.layer / str(z) / str(x) / f"{y}.png"
                    result = fetch(url_tmpl.format(z=z, x=x, y=y), dest,
                                   args.user_agent, args.timeout)
                    if result == "ok":
                        ok += 1
                        time.sleep(args.delay)
                    elif result == "skip":
                        skip += 1
                    else:
                        fail += 1
                    done = ok + skip + fail
                    if done % 100 == 0 or done == total:
                        pct = done * 100 // max(total, 1)
                        print(f"\r  z{z}  {done:,}/{total:,} ({pct}%)  "
                              f"ok={ok:,} cached={skip:,} failed={fail:,}", end="", flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted — re-run the same command to resume (existing tiles are kept)")
        return 1

    print(f"\ndone in {time.time() - started:,.0f}s — {ok:,} fetched, {skip:,} already present, {fail:,} failed")
    if fail:
        print("Some tiles failed. Re-run to retry just those; existing tiles are skipped.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
