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
| 9 | Direwolf / Meshtastic | Needs hardware to validate beyond "it installed" |
| 10 | SatDump | Always a source build; the longest step. Confirm `satdump` reaches `PATH` after `make install`. Toolchain and libraries install as two apt transactions, so a bad library name cannot cost the compiler; each resolves to its own cross and cmake's output reaches the run log. Retrying after a failed configure is safe — the stale `CMakeCache.txt` is discarded, since cmake would otherwise reuse its NOTFOUND results. The TLE row is only checked when a binary exists — TLE staging runs after the build, so with no binary its absence is a consequence, not a second fault |
| 11 | Dock-trigger autostart | **Havis DS-PAN-111 + CF-30 only.** The udev rule matches that dock's hub (`05e3:0610`) — it is the real ID, not a placeholder. On other hardware the files install and the rule simply never fires. See **Supported Hardware** in the README |
| 12 | Slim appliance build | Destructive — `apt purge`s preinstalled apps and disables mintupdate. Test last, on a VM you can roll back |

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

## Re-running steps

Steps are written to be safely repeatable: extraction overwrites rather than
prompting, directories are created with `exist_ok`, and downloads verify a
checksum. If a step is not safely re-runnable, that is a bug worth reporting.

## Known rough edges

* **Cancel is checkpoint-based**, not immediate. A running `apt-get` or a SatDump
  build finishes or fails first. To stop a genuinely stuck run, kill the process.
* **Long steps show only a spinner.** Output is captured and printed in full only
  if the step fails — deliberate, so the terminal behind the GUI stays quiet.
* **`python3-tk` is required** and is not bundled with Python on Debian/Ubuntu/
  Mint. Without it the GUI does not start at all.
* **The Kiwix URL is pinned** to a dated snapshot and will 404 once upstream
  rotates it. Pick a current file and update the URL and hash.
* **SatDump is built from source on every node**, and it is the longest step by
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
