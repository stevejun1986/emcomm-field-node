# configs/

The provisioner reads these files. **None of them ship with this repository** —
each group runs its own frequencies, its own operating area, and its own radios.
Every step degrades gracefully with a visible warning when a file is absent, so a
partial set is fine while you are getting started.

`.gitignore` excludes this whole directory's contents by design. Keep your group's
real profiles in a private location; commit nothing here you would not publish.

| File | Used by | What it is |
| --- | --- | --- |
| `JS8Call.ini` | App profiles step | JS8Call station profile |
| `QMapShack.conf` | App profiles step | QMapShack settings profile |
| `analog_channels.csv` | App profiles step | CHIRP channel list for analog radios |
| `ale_channels.zcp` | App profiles step | ion2G HF ALE channel plan |
| `satdump_tles.txt` | SatDump step | Curated TLE set — **supplying one changes behavior**, see below |
| `areas/*.json` | Map tile step | Bounding box for an operating area |

---

## `satdump_tles.txt` is a switch, not just a file

Every other file here is additive: supply it and the step uses it, omit it and the
step says so. This one also decides where the node's element sets come from for the
rest of its life.

**Omit it** — the normal case. SatDump's own TLE fetch is left alone, and first
launch pulls its full default set, upwards of 16,000 objects. Nothing to maintain.

**Supply it** and the provisioner stages the file *and switches SatDump's fetch
off*. It has to: the fetch overwrites `satdump_tles.txt` on first launch, so a
staged set survives only if the fetch cannot run. From then on the node's element
sets come from your file and nowhere else — which means keeping it fresh is
entirely yours. TLEs decay within days and badly past two weeks, and a stale one
does not fail loudly, it points the dish at empty sky.

Supply one only if you actually want that trade: a small set for the birds you work,
refreshed on your own schedule, instead of everything, refreshed by SatDump.

---

## Placeholder contract — read before adding a profile

A profile exported from a working installation carries that operator's identity
and that machine's paths. Ship it unchanged and every node built from it
transmits under someone else's callsign, and breaks for any user whose Linux
username differs.

Substitute these tokens; the provisioner replaces them at build time:

| Token | Replaced with |
| --- | --- |
| `MYCALL_PLACEHOLDER` | the callsign entered at the prompt |
| `HOME_PLACEHOLDER` | the provisioning user's `$HOME` |

Leave these **empty** rather than tokenised — nothing substitutes them, and a
literal token would be used as a real value:

* `MyGrid` — a grid square is transmitted position data; let each operator set it
* `SoundInName` / `SoundOutName` — audio devices differ per machine and adapter

### Check a profile before you add it

```bash
grep -nE "MyCall|MyGrid|/home/|alsa_|plughw|pci-" configs/*.ini configs/*.conf
```

Any real callsign, grid square, absolute home path, or hardcoded audio device
name in that output needs fixing first. This is the single easiest mistake to
make with exported profiles, and the hardest to notice afterwards.

---

## Operating areas

`areas/*.json` gives the tile fetcher a bounding box:

```json
{ "north": 38.996, "south": 38.792, "west": -77.120, "east": -76.910 }
```

Longitudes west of Greenwich are negative. Estimate before downloading — tile
counts roughly quadruple per zoom level:

```bash
./scripts/fetch_map_tiles.py --area configs/areas/your-area.json --estimate-only
```

The map tile step reads **every `areas/*.json`** and fetches each layer for each
area. Define at least one or the step fetches nothing and says so — it will not
silently produce an empty map.

When the map step is selected, the provisioner shows an **Operating Area** screen
that takes a center point and a radius, shows the download estimate before
committing, and writes the area file for you. Hand-writing one is still supported.

### Center and radius, and why tiles are tiered

An area with a `center` and `radius_miles` is fetched in two passes: full street
detail within `detail_radius_miles` (25 by default), and orientation zoom out to
the full radius. That is not a cosmetic choice. Coverage scales with the square of
the radius, so fetching everything at street zoom costs roughly:

| Radius | Flat, street zoom | Tiered |
| --- | --- | --- |
| 50 mi | ~78,000 tiles | ~21,000 |
| 75 mi | ~174,000 tiles | ~23,000 |
| 150 mi | ~694,000 tiles | ~31,000 |

Both layers, mid-latitude. The flat 150-mile figure is a multi-hour download of
several gigabytes against a public USGS endpoint — which is why the radius alone
does not set the zoom.

```json
{
  "center": { "lat": 39.00, "lon": -77.00 },
  "radius_miles": 75,
  "detail_radius_miles": 25,
  "north": 40.086957, "south": 37.913043,
  "east": -75.604826, "west": -78.395174
}
```

The bounding box is the outer ring, kept so the fetcher can still be pointed at
the file directly with `--area`. A file with only `north/south/east/west` is a
plain rectangle and gets a single full-detail pass.

### Download size

The size figure the provisioner quotes assumes 25 KB per tile. That is measured,
not guessed: 25.5 KB across 19,774 USGS tiles at z10-15 over mixed urban and
mountain terrain — topo 24.6 KB, imagery 26.5 KB. The two layers are close
enough that one constant covers both. Flat rural terrain compresses better and
may come in under it.

For reference, a 50-mile area with both layers came to roughly 493 MB on disk.

### Being a good neighbor to the tile service

The tiles come from a public USGS endpoint. The fetcher paces **every** request
(`--delay`, default 0.15s), pauses between bursts (`--burst 100 --pause 5`), and
backs off exponentially on 429/503 — honoring `Retry-After` when the server
sends one. After ten consecutive throttle responses it **stops** and tells you,
rather than grinding out thousands of failures against a service that has
repeatedly said no.

Everything already fetched is kept, so re-running fills the gaps. If it keeps
happening, be gentler:

```bash
./scripts/fetch_map_tiles.py --area configs/areas/your-area.json \
    --delay 0.5 --burst 50 --pause 30
```

Every area found is named in the log before any download starts, so a file you
did not expect is visible before it costs you anything.

A `*.json` carrying the sample's bounds **unchanged** is skipped with a warning.
A clone predating the `.json.sample` rename leaves a real `example-area.json`
behind, and `areas/*.json` is gitignored so `git status` never mentions it —
without the check it is fetched in silence, ahead of your own area. Editing its
bounds makes it a legitimate area again; the check compares coordinates, not
filenames.

`example-area.json.sample` is a sample around Washington DC, deliberately chosen
as a neutral public reference. **Copy it to `<your-area>.json` and edit the copy** —
do not edit the sample in place. It carries the `.sample` suffix so it is never
read as a live area, which is what makes "copy it" the only workflow that works.

Do not commit your group's real area unless you are content for it to be public;
`.gitignore` keeps `areas/*.json` out of the repository for that reason.
