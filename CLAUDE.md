# Working in this repository

Provisioner for an EMCOMM field node — an offline-capable emergency
communications workstation on Linux Mint. Single variant, no CI. See `README.md`
for the software stack and `TESTING.md` for how to test.

---

## Pull requests

**Every change goes through a pull request. Never push to `main`.**

Branch from current `main`, push the branch, open the PR against `main`, and say
plainly what it changes and what you verified. The repository owner reviews and
merges; do not merge your own pull request.

Use `claude/<short-description>` for branch names. One PR per coherent change —
a fix and an unrelated cleanup are two pull requests, because they are two review
decisions.

**That rule runs both ways, and the other direction is the one that gets missed.
Related changes to one subsystem ship together, not one PR per instruction.**

A behavior change and the documentation it creates or invalidates belong in the
same PR. So does the verification row that checks the behavior, the checklist
entry the operator now needs, and the sample config the change requires. If
landing the code leaves the repository describing something that is no longer
true — even for an hour — the pieces were not separable.

This matters because the history is the provenance record for software people
take into the field. A sequence of small reactive PRs reads as firefighting; one
PR reads as a considered change. Same diff, different story about how it was
built.

The rule bites hardest when work arrives as a sequence of requests. Each one in
isolation looks like its own PR; together they are one change. **Recognizing that
is the agent's job, not the owner's** — say "these are one change, I will land
them together" rather than executing each request separately and leaving the
owner to notice afterwards.

Worked example: the GPS time source landed as a single PR carrying the
provisioning step, the verification row, the checklist section, the sample config
and the `configs/README.md` entry. Splitting those would have meant a merged
provisioner whose checklist did not yet mention the step it had just added.

State what you did **not** verify as clearly as what you did. Nothing here can be
exercised end to end without a VM and, for the dock automation, the physical
hardware — so "tests pass" and "this works" are different claims and should not
be written as if they were the same one.

### Pull request bodies

Write the body as technical documentation. Three sections, in this order:

**WHAT** — the change, stated as fact.

**WHY** — the defect, constraint or requirement that made it necessary, with
evidence: the failing output, the measurement, the contradiction between two
files. A claim with no evidence under WHY is an assertion, and the next reader
cannot check it.

**THE FIX** — what was done, and any structural decision worth recording — why
a constant was centralized rather than corrected in place, why a rename was
chosen over an exclusion list.

Close with what was verified and what was not.

Omit second-person address, pleasantries, and narration of the author's process.
`Your screenshot showed the total mislabeled` is `The estimate labeled the sum
"both layers" while each row already counted every layer`. `I fixed it
structurally` is `Pacing defaults are defined once in the fetcher and imported
by the GUI`. The body is a record for whoever reads the log in a year with no
access to the conversation that produced it.

---

## The failure mode this project keeps producing

**A step that reports success having done nothing.** It has shipped here more than
once: a profile copied from a filename that did not exist, a tile fetch invoked
with arguments the fetcher rejects, a green summary line sitting outside the
branch that earned it, `.tms` map sources pointing at a directory the fetcher
never writes to. Every one was invisible on the bench and would have been found in
the field, by an operator who needed the radio.

Five rules follow. They are not style preferences.

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
   partially succeed, say what worked and what did not — never summarize four
   warnings as "successfully staged".
3. **Substituting is not optional.** When a profile is copied, substitute the
   placeholder tokens, then verify the token is gone. A file that copies cleanly
   but substitutes nothing is used verbatim as a real value, which is worse than
   a missing setting.
4. **A failing step is contained, never fatal to the run.** The steps are
   independent by design, so an exception escaping one of them and aborting the
   other twelve is a bug, not caution. Report it by name and continue. This has
   happened: a Flatpak install failed at step 4 of 13 and the operator lost
   maps, profiles, the dock trigger, Direwolf, Meshtastic and SatDump — none of
   which had anything to do with it.
5. **Condensing output is a display decision, and only a display decision.** The
   log pane collapses runs of progress redraws so the error is not buried under
   588 of them. The run log on disk never does: it keeps the complete captured
   output of every failed command. Route the file through the pane's condensing
   and the diagnosis loses exactly the lines it was condensed to hide — with no
   sign that anything is missing, which is this whole section's failure mode
   wearing a different hat. `tests/runlog.py` asserts the two diverge.

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
absolute home path and audio device names. A repository built this way had to
be rewritten with `git filter-repo` to remove exactly that. Do not repeat it.

Before committing anything under `configs/`: no real callsign, no grid square
(`MyGrid=` stays empty), no `alsa_output.*` device names, no absolute `/home/...`
path, no site or organization name.

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
python3 tests/area.py       # center/radius input, the estimate, the file it writes
python3 tests/throttle.py   # pacing, backoff, behavior when rate-limited
python3 tests/build.py      # build parallelism capped by memory, not cores
python3 tests/isolation.py  # one failing step must not stop the others
python3 tests/runlog.py     # the transcript on disk, and that it is the complete one
python3 tests/maps.py       # what QMapShack is handed: sources, activation, first view
python3 tests/radio.py      # the radio binding, the ModemManager rule, nothing opened
```

`tests/dryrun.py` is a **diagnostic harness, not an assertion suite** — it prints
what each step would do, for a human to read. That is the right shape here: the
bug class is "reported success, did nothing", which you catch by reading a
transcript. Every other suite asserts, and fails loudly. The ones that build a
window (`flow`, `area`, `isolation`, `runlog`) need Tk and a display — headless, prefix
them with `xvfb-run -a`.

Neither covers runtime behavior — real `apt`, real downloads, real extraction.
A VM run is required before any deployment, and the hardware claims need the
actual dock.

### Test the step, not its source

A test that greps the provisioner's source for a string proves the string is
there. It does not prove the step does anything. That is this project's failure
mode one level down: a check that passes for work that did not happen, with the
test as the thing reporting success.

It has gone wrong here in two ways:

- **The substring also appears somewhere else.** `shutil.which("direwolf")` and
  `proc.stdout` each appear more than once in the provisioner. A whole-file
  assertion kept passing after the Direwolf step's own copies were deleted,
  and only mutating those lines revealed it. A narrower slice can fall into the
  same trap: inside the Direwolf verification block, a bare `read_text` also
  matches the read of `direwolf.conf`.
- **The sentence never appears assembled.** A log message split across adjacent
  string literals is never in the source as one string. An assertion for the
  full sentence fails against correct code, and the easy "fix" is to loosen it
  until it matches something.

Three rules follow:

1. **Prefer driving the step.** Run the real function against a fixture: a
   temporary `HOME`, a `configs/` built for the case, and a context whose `sudo`
   and `sudo_write` record what they were given instead of running it. Then
   assert on what was written and what was logged. `DryCtx` in
   `tests/dryrun.py` already does this. That catches what a source assertion
   cannot: a rule written malformed, a branch that never runs, a message that
   never prints. If a test pulls functions out by `exec` instead of importing
   the module, it has to supply every name they reference (`Ctx`, `os`,
   `shutil`). A missing name fails the test, not the code.
2. **When a source assertion is the right tool, scope it and match it
   exactly.** Some claims are proofs of absence that no run can give, for
   example "nothing in this path opens the device". Those belong in source.
   Slice to the function's own region. Match the exact expression
   (`Path(RADIO_MM_RULE).read_text`, not `read_text`). Match only text that is
   contiguous in the file.
3. **Mutate every new assertion before trusting it.** Break the line the
   assertion guards, confirm the suite fails, then restore the line. An
   assertion that survives a mutation of its own subject is decoration.

`tests/radio.py` is still mostly source assertions. Converting it to drive
`optional_direwolf` against fixtures, the way `tests/dryrun.py` drives every
step, is outstanding.

---

## Keep the docs in step with the code

`TESTING.md`'s step table and `README.md`'s step list track the code and drift
silently when it changes. They already have.

**Change a step's label or behavior → update `TESTING.md` and `README.md` in the
same commit.** Adding a step means adding its row. Changing what a step needs from
the operator — as the map step now needs an operating area — means saying so, or a
correct refusal reads as a failure.

---

## Releasing

### What a version number is for

`VERSION` at the top of `deploy_emcomm_node_gui.py` is the one place the release
number lives. Nothing derives it — the shipped artifact is a tarball with no
`.git` beside it, so `git describe` would report "unknown" on exactly the copy an
operator runs.

It reaches two places an operator can read: the summary screen, and the header of
every run log. A stale one puts a wrong number on the transcript attached to a
problem report, which is worse than no number at all.

Versions are `vMAJOR.MINOR.PATCH`. The tag carries the `v`; the constant does not.

### A tag is a release event, not a commit event

**The scripts are the product. A version moves when a script changes.**

Two scripts ship and both count:

```
deploy_emcomm_node_gui.py     the provisioner
scripts/fetch_map_tiles.py    run directly by the operator
```

A change to either is a change to what someone runs, so either can earn a
version. Everything else in the archive — `README.md`, `TESTING.md`, the
checklist, `configs/`, `docs/` — is documentation and scaffolding around them.

Docs-only commits, repo hygiene, and agent guidance do **not** earn a tag.
Retagging every merge is wasted work: it moves a tag onto a tree that runs
identically to the one before it, and it invalidates any artifact already built
against that tag for no gain.

### The number moves whenever a script changes — pre-release included

A script change is a change to what someone runs, and the version is how they say
which one they ran. That holds before the release ships as much as after, so the
pre-release window is not exempt: `v1.0.0` was the first tag, `v1.0.1` the second,
and they continue from there.

Categorize by what the change does:

| Change | Bump |
| --- | --- |
| Fixes a defect in existing behavior | PATCH — `1.0.1` |
| Adds a step, a check, or a capability | MINOR — `1.1.0` |
| Breaks an already-provisioned node or an existing config | MAJOR — `2.0.0` |

Clearing the pre-release flag is not itself a version event. It changes how the
release is labeled, not what the scripts do, so the number moves only if a script
moved with it.

This replaces an earlier rule under which `v1.0.0` covered the entire pre-release
window and integers only began after shipping. It was dropped because a pre-release
that receives fixes is still something people are running, and leaving every one of
those trees stamped `1.0.0` puts one number on builds that behave differently —
which is the problem the constant exists to prevent.

### The order, when a version does move

**Bump `VERSION` in the commit that gets tagged, not after.** A tag points at a
commit; if the bump lands later, `v1.2.0` is a tree that calls itself `1.1.0`
forever.

    bump → merge to `main` → tag that commit → build the tarball from it

**Tags are annotated.** `git tag -a v1.2.0 -m "..."`, not a bare `git tag v1.2.0`.
An annotated tag is its own object carrying a tagger, a date and a message; a
lightweight one is a bare pointer with none of that, so when a tag is moved —
which has happened here — nothing records that it was, or when, or why.

Existing tags are mixed: `v1.0.0` is annotated, `v1.0.1` and `v1.0.2` are
lightweight. They are left as they are. Retagging them would move tags that
published releases point at, for no benefit to anyone who already has the
artifacts.

Build the tarball from the tagged commit, not from a working tree. Signing
attests to bytes; bytes that no tag points at cannot be checked by anyone else.

### Check the archive against the tag before signing it

**This is a step, not advice.** Run it between building the tarball and signing
it, every time:

```bash
TAG=v1.0.2                       # the tag being released
ARCHIVE=emcomm-field-node-${TAG#v}.tar.gz

git archive --format=tar.gz --prefix="emcomm-field-node-${TAG#v}/" "$TAG" > "$ARCHIVE"

# What the archive actually says about itself.
BUILT=$(tar -xzOf "$ARCHIVE" --wildcards '*/deploy_emcomm_node_gui.py' \
        | sed -n 's/^VERSION = "\(.*\)"/\1/p')

[ "$BUILT" = "${TAG#v}" ] \
  && echo "OK: archive reports $BUILT, tag is $TAG" \
  || { echo "STOP: archive reports '$BUILT', tag is '$TAG'"; false; }
```

Sign only if that prints OK. If it does not, the tag is on the wrong commit —
fix the tag, rebuild, re-check. Do not sign and fix afterwards: a signature over
the wrong bytes has to be withdrawn, and anyone who fetched it in the meantime
has no way to know.

Why it is written down rather than trusted to care: **a tag has pointed at a
tree carrying the wrong version twice here.** Once when `v1.0.0` was moved back
onto `17059d3`, undoing a retag that had already fixed it, and once when
`v1.0.1` first landed on `b8dca1a`, one commit before the bump. Both were
recoverable, and both cost a re-tag and a re-signed asset.

The ordering rule above did not prevent either, and cannot: it governs the
commit, and the fault appears in the artifact. This check reads the artifact,
which is the only place the fault is visible. It is three lines and it is the
last moment the mistake is still cheap.

---

## Where this code came from

This provisioner began as an emergency-communications adaptation of a separate
private repository and shares **no code** with it. Fixes have been ported by hand
in one direction (the five-screen flow, post-deployment verification, the hostname
step, the `os.getlogin()` crash). They do not flow automatically and the two will
drift.

If you are told a fix exists "in the other repo", it is not in this one until
someone ports it. There are no distribution branches here: `main` is the only
branch and the clone is what people run.

**Name neither that repository nor its project, anywhere in this one.** Not in
code comments, not in documentation, not in issue or commit text. The two are
developed in parallel because the workflows are alike; that is the whole of the
relationship, and this repository is going public while the other is not. Where a
fact was established on the other project's hardware and the provenance matters,
state it abstractly — "on a separate node running the hand-ported equivalent of
this step" — and never link to an issue or a branch that a reader cannot open.

---

## License

GPL-3.0-or-later. `LICENSE` is the FSF text verbatim; the provisioner and the tile
fetcher each carry the per-file notice the GPL asks for. The grant covers this
repository's own code, configuration templates and documentation — **not**
third-party material a deployment pulls in or that is later committed here.
Reference manuals, map tiles, ZIM archives, `.deb` packages and radio codeplugs
each carry their own terms. Check redistribution terms before committing any
third-party document, and record the license alongside it.
