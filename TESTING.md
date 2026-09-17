# Testing Notes

The provisioner changes a real system: it installs packages, writes to `/etc`,
enables systemd units, and adds udev rules. Test it on a disposable VM or a
machine you are willing to reimage — not on a working node, and not on your
daily driver.

## Why every step is optional

Each stage is an independently checkable component, all unchecked by default.
That is a testing affordance first: check one step, run it, inspect the result,
re-run it after a fix, without sitting through a full build every cycle. **Select
All** performs a real end-to-end build.

Steps are deliberately **not** dependency-checked. Running "QLog + ion2G" without
"System packages" assumes `wine` and `unzip` are already installed. That is the
tradeoff for being able to re-run one step in isolation.

A step that fails is **contained**: it is reported by name, and the remaining
steps still run. A run ends with an honest tally rather than stopping at the
first problem, because the steps are independent and losing eleven of them to
one failure serves nobody. Check the summary screen for which steps failed.

## Suggested order

Roughly cheapest and most reversible first, so a broken assumption surfaces
before an hour of package installation.

| # | Step | Watch for |
| --- | --- | --- |
| 1 | Callsign / node ID + system hostname | `/etc/emcomm/node.conf` written; sudoers mode 0440; `hostname` returns the callsign; `sudo` stays instant afterwards (if it stalls ~10s, the `/etc/hosts` entry did not take) |
| 2 | App profiles + ALE channel plan | Placeholders substituted — see the check below |
| 3 | Desktop shortcuts | Missing `.desktop` files warn rather than fail |
| 4 | Reference library + doc server | `curl -sI http://127.0.0.1:8085` returns 200 |
| 5 | Offline map tiles | Selecting it adds the **Operating Area** screen before the sudo prompt — check the estimate there before committing. Without an area file the step correctly refuses. Run the fetcher with `--estimate-only` first; the count is easy to get wrong |
| 6 | System packages + Wine init | Slowest step. Confirm no interactive prompt stalls it |
| 7 | QLog station log + ion2G HF ALE | Checksum must verify; re-run to confirm it is idempotent |
| 8 | Offline knowledgebase | Large download; the pinned URL will eventually rotate |
| 9 | Direwolf / Meshtastic | Needs hardware to validate beyond "it installed". The Meshtastic step also installs the **mesh-to-GPX bridge** (`emcomm-mesh-gpx.service`) when QMapShack is on PATH — skipped with a message otherwise, since QMapShack is the only consumer. Enabled, not started: it comes up at the next login, the same one that makes `dialout` take effect. See *Mesh-to-GPX bridge: what has and has not been tested* below |
| 10 | dump1090 (ADS-B) | Preseeded install, so no debconf prompt should stall it. **Three separate things must hold, and the first alone is not enough**: not enabled at boot (`systemctl is-enabled` → disabled) and not in the autostart sequence, since it and SatDump cannot share the dongle; startable on demand (`grep START_DUMP1090 /etc/default/dump1090-mutability` → `"yes"`, the switch the init script tests on every start); and able to open the dongle (`id dump1090` lists `plugdev`, since the device node is `root:plugdev 0660`). With a dongle attached, `sudo service dump1090-mutability start` should populate `/run/dump1090-mutability/aircraft.json` within a second or two — an empty directory means it started and died. The lighttpd map arrives on the distribution default, not loopback: `ss -ltnp | grep lighttpd` |
| 10b | RTL-SDR dongle arbitration | Only meaningful with a dongle attached **and both** dump1090 and SatDump installed. The run should print a `BOTH … are installed` warning naming `EMCOMM_Data/SDR/dongle_arbitration.md`, and the verification should show **RTL-SDR dongle reference staged**. Confirm the contention is real: start dump1090, then `rtl_test -t` should fail; stop it (`service stop` **and** `pkill -x dump1090-mutability` — the first misses a hand-launched copy) and `rtl_test -t` should succeed. Also confirm the kernel is out of the way: `/etc/modprobe.d/emcomm-rtlsdr.conf` should exist and `lsmod \| grep -i rtl28xxu` should be empty. Unplug and replug the dongle — the driver must not come back. If `rtl_test` fails with nothing running at all, that blacklist is the first thing to check. Distinguish that from `rtl_test: command not found`, which is the `rtl-sdr` package being absent rather than any contention — verification's **rtl_test available** row reports it, and either SDR step now installs it |
| 11 | SatDump | Always a source build; the longest step. Confirm `satdump` reaches `PATH` after `make install`. Toolchain and libraries install as two apt transactions, so a bad library name cannot cost the compiler; each resolves to its own cross and cmake's output reaches the run log. Retrying after a failed configure is safe — the stale `CMakeCache.txt` is discarded, since cmake would otherwise reuse its NOTFOUND results. The TLE row is only checked when a binary exists — TLE staging runs after the build, so with no binary its absence is a consequence, not a second fault. TLE handling then depends on whether `configs/satdump_tles.txt` exists: with no curated set (the default, since `configs/` ships empty) SatDump's own fetch is left enabled and first launch loads its full default set; with one, it is staged and the fetch is switched off so it cannot overwrite it. The log says which arrangement was applied. Then open SatDump and confirm the Tracking tab lists satellites — `0 TLEs loaded!` means neither happened, and the node cannot predict a pass |
| 12 | Dock-trigger autostart | **FUTURE FEATURE — being designed for a Havis DS-PAN-111 + CF-30, never yet observed firing. See *Verifying it actually fires* below.** The udev rule matches that dock's hub (`05e3:0610`) — it is the real ID, not a placeholder. On other hardware the files install and the rule simply never fires. Verification confirms the four artifacts exist and now carries a fifth row saying the chain has never been seen to fire; it does not and cannot confirm a dock event does anything. See **Supported Hardware** in the README |
| 13 | Slim appliance build | Destructive — `apt purge`s preinstalled apps and disables mintupdate. Test last, on a VM you can roll back |

## The check that matters most

The verification screen now runs this automatically at the end of every run —
`JS8Call placeholders substituted`, `JS8Call MyCall set to this node`, and
`JS8Call grid square` are three of its rows. Check it there first.

To confirm by hand, or on a node provisioned before that screen existed:

```bash
grep -nE "PLACEHOLDER|MyCall|MyGrid" ~/.config/JS8Call.ini
grep -nE "PLACEHOLDER|/home/" ~/.config/QLandkarteGT/QMapShack.conf
```

`MyCall` must be the callsign you entered. `MyGrid` should be empty. No
`PLACEHOLDER` token should remain anywhere — a literal token that survives is
used as a real value, which is worse than a missing setting.

## One login gates three things

A run leaves three things waiting on a fresh login, and each fails in a way that
points away from the shared cause:

| Symptom | Actual cause |
| --- | --- |
| `meshtastic`: command not found | `~/.local/bin` joins PATH from `~/.profile` only `if [ -d "$HOME/.local/bin" ]`, evaluated at login. The provisioner may have just created it |
| Permission error on `/dev/ttyACM*` | `dialout` membership is not in effect yet |
| `emcomm-mesh-gpx.service` not running | enabled, deliberately not started — see below |

```bash
echo "$PATH" | tr ':' '\n' | grep -x "$HOME/.local/bin"
id -nG | tr ' ' '\n' | grep -x dialout
systemctl --user is-active emcomm-mesh-gpx.service
```

Log out and back in, or reboot, before judging any of it. The verification screen
says so on the way out, and the run log carries the same line.

## Mesh-to-GPX bridge: what has and has not been tested

Installed by the Meshtastic step **only when QMapShack is also on PATH**. On a node
without QMapShack it is skipped and the run says so — that is correct behavior, not
a failure, and verification reports it as a single explanatory warning rather than a
missing file.

**Verified end to end on 2026-09-17** — a Wio Tracker L1 Pro on a 9-node mesh,
peers 0 to 7 hops out. Four of nine nodes had positions and exactly those four
reached the GPX; three matched `meshtastic --info` to seven decimal places on
position, altitude and last-heard time, and the fourth was the local node whose
GPS refreshed between the two commands. Every claim below has now been tested on
hardware rather than inferred.

**CAUTION: `meshtastic --info` prints `security.privateKey` in plaintext**, along
with your position and every peer you have heard. Read it freely; do not paste it
into an issue, a forum post or a screenshot. If it has already gone somewhere
public, regenerate the node keypair. Share `journalctl --user -u emcomm-mesh-gpx`
instead.

What to confirm on hardware, in order:

```bash
systemctl --user is-enabled emcomm-mesh-gpx.service   # should say: enabled
```

It is deliberately **not started** during provisioning. The same step may have just
added you to `dialout`, which does not take effect until the next login, so a
service started now would fail every poll on a permission error. It comes up at
that login.

After a re-login, with a node attached:

```bash
emcomm-mesh-gpx --once --dump          # force one poll, print the GPX
ls -l ~/EMCOMM_Data/Meshtastic/mesh_nodes.gpx
journalctl --user -u emcomm-mesh-gpx -n 20
```

Peers appear only once they have **sent** a position. A peer with
`position.gps_enabled` off is known to the mesh and absent from the GPX — that is
the mesh's state, not a defect in the bridge, and the poll says so in the log.

What the hardware run settled:

1. **It does not fight you for the radio.** `meshtastic --info` connected normally
   while the service was running. Each poll opens the port, reads and closes it.
   **If that command ever fails only while the service runs, the port release has
   regressed** — capture `journalctl --user -u emcomm-mesh-gpx` and treat it as a
   defect.
2. **QMapShack does not notice the file changing.** Tested on 1.17.1: with the
   service stopped, a waypoint moved half a degree and the file swapped in by
   rename — the same way the bridge writes it — the open project did not redraw.
   An import is a snapshot, and every load creates a **new** project rather than
   updating one, so a refreshed map means deleting the stale project and importing
   again. Re-test if you upgrade QMapShack.
3. **The waypoints land where the radio says.** `<metadata><time>` is the poll time;
   each `<wpt><time>` is that peer's last-heard time, so a stale peer keeps its old
   position and says how old it is rather than disappearing.

Loading it, in a form that survives version changes:

```bash
qmapshack "$HOME/EMCOMM_Data/Meshtastic/mesh_nodes.gpx"
```

or drag the file onto the workspace. An empty-looking map is **not** a failed
import: QMapShack logs `Empty filename passed to function` repeatedly when its map
sources point at tile directories holding nothing, which is the state of a node
whose map step has not run. The waypoints load regardless.

**Peer positions may be approximate.** A position arriving over the air is
quantized by the channel's `positionPrecision`; on the mesh tested, at precision
13, two nodes two kilometres apart reported identical coordinates to seven decimal
places. Only the local node's own position arrives at full GPS precision.

## Dock trigger: verifying it actually fires

**This chain has never been observed running — on any hardware, including the
reference CF-30 and DS-PAN-111.** It is written, installed and verified present.
Whether a dock insertion actually launches anything is unknown.

Verification's five presence rows — `Autostart launcher installed`, `Autostart
launcher executable` (nested inside the first), `Autostart user unit installed`,
`Dock-event dispatcher installed`, `udev dock rule installed` — all pass on a
machine that has never seen a dock. They attest that files were written. That is
not the same claim, so a sixth row, `Dock trigger observed firing`, warns rather
than passing, and says why: the five above confirm files on disk, not that a dock
event runs them. No automated check here can make the stronger claim: `tests/dryrun.py` stubs the subprocesses by design,
and a VM cannot present a `05e3:0610` insertion to a live XFCE session.

Five links have to hold, and only the first is hardware-specific:

1. udev matches `05e3:0610` on insertion
2. `RUN+=` invokes `/usr/local/bin/emcomm-dock-event.sh`
3. the dispatcher crosses from udev's context into the user session
4. the unit runs with a usable `DISPLAY` and `XAUTHORITY`
5. the sequence launches applications in order, and its lockfile survives a bounce

Links 3 and 4 are the fragile ones. A udev `RUN+=` runs as root with a minimal
environment, no session, and a short timeout; reaching a live user session from there
is where this kind of thing usually breaks.

With the hardware, dock the machine and work down the chain — each command tells you
which link failed:

```bash
udevadm monitor --environment --udev | grep -i 05e3    # 1: does udev see it
journalctl -b --grep=EMCOMM                            # 2-3: dispatcher logged
systemctl --user status emcomm-autostart.service       # 4: did the unit run
cat ~/.emcomm/autostart.log                            # 5: what the sequence did
loginctl show-user "$USER" -p Linger                   # must be Linger=yes
```

`Linger=yes` matters: without it the user manager may not be running when udev fires,
and link 3 fails with nothing obviously wrong anywhere else.

The dispatcher calls `logger` without `-t`, so its lines carry the invoking user as
the syslog tag rather than `emcomm` — `journalctl -t emcomm` finds nothing even when
it did run. Match on the message text instead, as above.

To exercise it without a dock, run the dispatcher by hand as root. That skips link 1
and tests 2-5, which are the parts most likely to be wrong:

```bash
sudo /usr/local/bin/emcomm-dock-event.sh
```

If that works and a real docking does not, the problem is the udev match. If it fails,
the problem is in the session crossing and a dock would not have helped.

## Re-running steps

Steps are written to be safely repeatable: extraction overwrites rather than
prompting, directories are created with `exist_ok`, and downloads verify a
checksum. If a step is not safely re-runnable, that is a bug worth reporting.

## Known rough edges

* **Cancel stops a download or a build immediately; a package install finishes
  first.** Pressing Cancel signals whatever child is running — the map-tile
  fetch, a `git clone`, the SatDump compile — including its own children, so a
  `make -j` does not leave compilers behind. Whatever had completed stays on
  disk; re-running the step starts it again.

  `apt`, `dpkg` and `make install` run through the privileged path and are
  deliberately left alone. Interrupting a package transaction leaves dpkg
  needing `dpkg --configure -a` before anything else can install, which is a
  worse outcome than waiting for it. So Cancel during a long `apt` still waits
  for that command, and stops at the next checkpoint.

  Verification runs after a cancel either way — what landed before the stop is
  usually the thing you want to see.
* **Long steps show only a spinner.** Output is captured and printed in full only
  if the step fails — deliberate, so the terminal behind the GUI stays quiet.
* **`python3-tk` is required** and is not bundled with Python on Debian/Ubuntu/
  Mint. Without it the GUI does not start at all.
* **The Kiwix URL is pinned** to a dated snapshot and will 404 once upstream
  rotates it. Pick a current file and update the URL and hash.
* **A VirtualBox VM cannot be rebooted while a USB device is forwarded to it.**
  Rebooting with the RTL-SDR passed through faults the guest — it behaves like
  pulling the power cord rather than a clean shutdown. The VM itself is fine.
  Detach the dongle in VirtualBox first, then reboot. This matters because the
  one thing needing a reboot is whether dump1090 claims the dongle at boot, and
  that question splits cleanly in two:

  ```bash
  ls -l /etc/rc*.d/ | grep dump1090   # no S* links => it will not start at boot
  ```

  `update-rc.d ... disable` renames the start links to kill links, so `K01…` in
  `rc0.d`/`rc1.d`/`rc6.d` is expected and `S01…` in `rc2.d`–`rc5.d` is not. That
  is the same fact `systemctl is-enabled` reports, read off the filesystem. Then
  reboot with the dongle **detached**, confirm nothing is running
  (`ps aux | grep [d]ump1090`, `/run/dump1090-mutability/` absent), attach the
  dongle, and confirm `rtl_test -t` opens it cleanly. Testing "does it start at
  boot" and "can it open the device" separately avoids the fault and isolates a
  failure better than the combined test would.
* **SatDump is built from source on every node, pinned to an upstream tag.**
  It is not built from `master`: on 2026-09-13 upstream had moved the CLI entry
  point into `src-cli/legacy/main.cpp` and renamed it `main_old`, so the
  `satdump` target failed to link with ``undefined reference to `main` `` and
  the step failed on a node that had built fine days earlier. A provisioner
  cannot absorb an upstream mid-refactor. The tag lives in `SATDUMP_VERSION`;
  bumping it means building the new tag on a node first.

  A clone left by an earlier run is moved onto the tag rather than reused as-is,
  and warns if it cannot be — otherwise the pin is silently untrue on every
  re-run.
* The SatDump build is the longest step by
  far. Parallelism is capped by available memory rather than core count: on a
  4 GB node with a desktop running it builds with `-j1` — slow, but it will not
  be killed part-way. A compiler *killed* rather than erroring ran out of
  memory; retry with fewer jobs.

## Reporting a failure

**Send the run log.** Every run writes one:

```
~/.emcomm/logs/provision-<date>-<time>.log
```

Its path is printed as the first line of the run screen and again on the summary
screen. It holds every line the log pane showed, the verification rows, the final
result — and, for anything that failed, the **complete** captured output of the
command, not the condensed version the pane displays. That last part is the whole
reason the file exists: the pane collapses runs of progress redraws so the error
is not buried under them, and the lines it collapses are sometimes the ones that
explain the failure.

The newest ten logs are kept; older ones are pruned when a new run starts.

**Read one before you send it.** The header records the callsign, the hostname,
the user and absolute `/home/<user>/` paths, and the captured output can carry
more of the same. That is the data this repository has already had to scrub out
of its own history once — a log pasted into an issue puts it straight back.

If the log is unavailable, include the step name, the red `✖` line, and the
captured output beneath it. Either way, note whether the step had been run before
on that machine, since several failures only appear on a second run.

**Say which version it was.** The summary screen carries a `Provisioner: v…` line and
the run log repeats it in its header — copy whichever you have. Without it a report
cannot be matched to a tree, and an operator running a tarball from a release and
someone running a clone of `main` can hit the same symptom for different reasons.

---

## Dry run (no VM required)

```bash
python3 tests/dryrun.py     # every step, nothing installed
python3 tests/flow.py       # the five screens and verification wiring
python3 tests/area.py       # center/radius input, the estimate, the file it writes
python3 tests/throttle.py   # pacing, backoff, and behavior when rate-limited
python3 tests/build.py      # build parallelism capped by memory, not cores
python3 tests/isolation.py  # one failing step must not stop the others
python3 tests/runlog.py     # the transcript on disk, and that it is the complete one
```

`tests/flow.py`, `tests/area.py`, `tests/isolation.py` and `tests/runlog.py` need Tk
(`sudo apt install python3-tk`) and a display. On a desktop session they just
run; headless, prefix them with `xvfb-run -a`. All of them work from the
repository root or from inside `tests/`, and none writes outside a throwaway
`$HOME`.

Runs the real step functions with `subprocess`, downloads and `shutil.which`
replaced by recorders, against a throwaway `$HOME`. Nothing is installed and
nothing outside that directory is touched. It prints, per step, every command
that would run, every file that would be written, and every log line.

Read the transcript for **steps that report success having done nothing**. That
is the failure mode this project keeps producing: a profile copied from a path
that does not exist, a fetch invoked with arguments it rejects, a green summary
line sitting outside the branch that earned it. All of those are invisible on a
real machine until someone needs the radio.

It does not cover runtime behavior — real downloads, real archive extraction,
real `apt`. A VM run is still required before any deployment.

`tests/flow.py` additionally asserts that **every screen fits a 1024x768 panel** —
the reference CF-30's display. `pack()` clips silently rather than scrolling, so a
screen that outgrows the display hides its own controls with no error at all. That
has happened before; the assertion is there so it cannot happen quietly again.
