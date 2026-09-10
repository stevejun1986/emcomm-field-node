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
| `satdump_tles.txt` | SatDump step | Curated TLE set |
| `areas/*.json` | Map tile step | Bounding box for an operating area |

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

`example-area.json` is a sample around Washington DC, deliberately chosen as a
neutral public reference. Replace it; do not commit your group's real area unless
you are content for it to be public.
