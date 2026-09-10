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

## Suggested order

Roughly cheapest and most reversible first, so a broken assumption surfaces
before an hour of package installation.

| # | Step | Watch for |
| --- | --- | --- |
| 1 | Sudo config + callsign | `/etc/emcomm/node.conf` written; sudoers file is mode 0440 |
| 2 | App profiles | Placeholders substituted — see the check below |
| 3 | Desktop shortcuts + wallpaper | Missing `.desktop` files warn rather than fail |
| 4 | Reference library + doc server | `curl -sI http://127.0.0.1:8085` returns 200 |
| 5 | Map tiles | Run `--estimate-only` first; the count is easy to get wrong |
| 6 | System packages | Slow. Confirm no interactive prompt stalls it |
| 7 | QLog + ion2G | Checksum must verify; re-run to confirm it is idempotent |
| 8 | Offline knowledgebase | Large download; the pinned URL will eventually rotate |
| 9 | Direwolf / Meshtastic | Needs hardware to validate beyond "it installed" |
| 10 | SatDump | Slowest by far if it falls back to a source build |
| 11 | Dock trigger | Only meaningful with a dock; the udev ID is a placeholder |
| 12 | Appliance build | Destructive. Test last, on a VM you can roll back |

## The check that matters most

After the app-profiles step, confirm no placeholder survived and no foreign
identity got baked in:

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
* **The SatDump hash is pinned** to one package version. A different `.deb` is
  refused until the expected hash is updated — correct behaviour, not a bug.

## Reporting a failure

Include the step name, the red `✖` line, and the captured output beneath it —
that block is the actual error. Note whether the step had been run before on that
machine, since several failures only appear on a second run.
