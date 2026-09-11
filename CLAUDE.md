# Working in this repository

Provisioner for an EMCOMM field node — an offline-capable emergency
communications workstation on Linux Mint. Single variant, single branch (`main`),
no CI. See `README.md` for the software stack and `TESTING.md` for how to test.

---

## The failure mode this project keeps producing

**A step that reports success having done nothing.** It has shipped here more than
once: a profile copied from a filename that did not exist, a tile fetch invoked
with arguments the fetcher rejects, a green summary line sitting outside the
branch that earned it, `.tms` map sources pointing at a directory the fetcher
never writes to. Every one was invisible on the bench and would have been found in
the field, by an operator who needed the radio.

Three rules follow. They are not style preferences.

1. **An input that might be absent is always reported.** A positive-form guard
   (`if src.is_file():`) needs an `else` that warns; an early return
   (`if not src.is_file(): log(...); return`) already does the job. What is
   never acceptable is a guard whose false branch is silence. Negative-form
   guards that *create* something (`if not d.is_dir(): d.mkdir()`) are not this
   pattern and need nothing.

   To audit, walk the AST for `If` nodes whose test mentions `is_file`,
   `is_dir` or `exists`, and read every one that has no `orelse` — there are a
   handful of legitimate ones, so this is a read-and-judge check, not a
   pass/fail gate.
2. **A success line lives inside the branch that earned it.** If a step can
   partially succeed, say what worked and what did not — never summarise four
   warnings as "successfully staged".
3. **Substituting is not optional.** When a profile is copied, substitute the
   placeholder tokens, then verify the token is gone. A file that copies cleanly
   but substitutes nothing is used verbatim as a real value, which is worse than
   a missing setting.

Before committing a change to any step, run `python3 tests/dryrun.py` and read the
transcript for exactly this.

---

## Hardware scope

**The reference build is a Panasonic Toughbook CF-30 in a Havis DS-PAN-111 series
dock.** Two things are locked to it:

* The dock-trigger udev rule matches that dock's USB hub, `05e3:0610`. That is the
  real Havis ID, **not a placeholder** — do not describe it as one. On other
  hardware the files install and the rule never fires.
* The autostart sequence assumes the CF-30's peripheral layout and XFCE on `:0`.

Everything else is vanilla Linux configuration and runs on any x86_64 machine on
the supported OS.

**The CF-30's panel is 1024x768, and that is a hard constraint on the GUI.**
`pack()` clips silently rather than scrolling, so a screen that outgrows the
display hides its own controls with no error at all. `tests/flow.py` asserts every
screen fits; adding a paragraph to the options screen has already broken it once.
Keep step labels short — the detail belongs in `README.md`'s step table.

---

## Never commit operator or machine data

Shipped profiles use substitution tokens, filled in at provisioning time:

* `MYCALL_PLACEHOLDER` — the callsign entered at the prompt
* `HOME_PLACEHOLDER` — the provisioning user's `$HOME`

`configs/` and `docs/` ship **empty on purpose**. Each group runs its own
frequencies, its own operating area and its own document set, and a profile
captured from a working install carries that operator's callsign, grid square,
absolute home path and audio device names. The sibling repository had to be
rewritten with `git filter-repo` to remove exactly that. Do not repeat it.

Before committing anything under `configs/`: no real callsign, no grid square
(`MyGrid=` stays empty), no `alsa_output.*` device names, no absolute `/home/...`
path, no site or organisation name.

---

## One source of truth, twice

**Names.** Every path, unit and file name derives from the constants block at the
top of the provisioner — `PROJECT`, `OPERATOR_PREFIX`, `STATE_DIR_NAME`,
`SYSTEM_DIR`, `UNIT_PREFIX`. Renaming the project is a two-line edit. Do not
reintroduce a literal; if you need a new name, derive it there.

The four naming conventions are deliberate, and the block says why. In particular
`~/EMCOMM_Data` is CamelCase so it sorts to the top of a file manager — an
operator under stress should not be hunting for their maps in `~/.local/share`.

**Map layers.** `MAP_LAYERS` drives both the `--layer` value passed to the fetcher
and the directory each generated `.tms` file reads from. They disagreed once and
QMapShack opened the sources onto nothing. Never hardcode a layer directory.

---

## Testing

```bash
python3 tests/dryrun.py     # every step, subprocess and network stubbed
python3 tests/flow.py       # five screens, verification wiring, panel size
```

`tests/dryrun.py` is a **diagnostic harness, not an assertion suite** — it prints
what each step would do, for a human to read. That is the right shape here: the
bug class is "reported success, did nothing", which you catch by reading a
transcript. `tests/flow.py` does assert, and fails loudly.

Neither covers runtime behaviour — real `apt`, real downloads, real extraction.
A VM run is required before any deployment, and the hardware claims need the
actual dock.

---

## Keep the docs in step with the code

`TESTING.md`'s step table and `README.md`'s step list track the code and drift
silently when it changes. They already have.

**Change a step's label or behaviour → update `TESTING.md` and `README.md` in the
same commit.** Adding a step means adding its row. Changing what a step needs from
the operator — as the map step now needs an operating area — means saying so, or a
correct refusal reads as a failure.

---

## Relationship to the S.T.N.D. repository

This provisioner began as a public/EM adaptation of a separate private repository
and shares **no code** with it. Fixes have been ported by hand in one direction
(the five-screen flow, post-deployment verification, the hostname step, the
`os.getlogin()` crash). They do not flow automatically and the two will drift.

If you are told a fix exists "in the other repo", it is not in this one until
someone ports it. There are no distribution branches here — unlike the sibling
repository, `main` is the only branch and the clone is what people run.

---

## Licence

GPL-3.0-or-later. `LICENSE` is the FSF text verbatim; the provisioner and the tile
fetcher each carry the per-file notice the GPL asks for. The grant covers this
repository's own code, configuration templates and documentation — **not**
third-party material a deployment pulls in or that is later committed here.
Reference manuals, map tiles, ZIM archives, `.deb` packages and radio codeplugs
each carry their own terms. Check redistribution terms before committing any
third-party document, and record the licence alongside it.
