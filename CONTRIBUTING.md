# Contributing

Field reports are worth more to this project than patches. A node built by
someone else, on hardware nobody here owns, finding a step that does not work —
that is the thing this repository cannot generate for itself.

## Reporting a failure

**[`TESTING.md`](TESTING.md) has the procedure.** Read the "Reporting a failure"
section before opening an issue; it explains which log to send and — importantly
— what to strip out of it first.

That warning is not boilerplate. A run log records the callsign, the hostname,
the user, and absolute `/home/<user>/` paths, and captured command output can
carry more of the same. This repository has already had real operator data
scrubbed out of its history once. **Read a log before you paste it.**

## Sending a change

Branch from current `main`, open a pull request against `main`, and say plainly
what it changes and what you verified. A few things will be asked of any change,
so they are easier to do up front:

**No operator or machine data.** Shipped configuration uses substitution tokens
filled in at provisioning time — `MYCALL_PLACEHOLDER`, `HOME_PLACEHOLDER`. A
profile captured from a working install carries a real callsign, a grid square,
absolute home paths and audio device names. All four are wrong on someone else's
machine, and the first two are personal. `configs/README.md` has the full
contract.

**A check must read content, not paths.** The recurring defect in software of
this kind is a verification row that passes for work that did not happen: a
profile that copied cleanly and substituted nothing, a tracking file that existed
and held zero entries. If you add a check, make it read what is inside the file.

**Nothing may open a radio interface to see what it is.** Opening a serial port
asserts its control lines, and on a sound-card interface one of those lines keys
the transmitter. The provisioner never scans for hardware — the operator declares
it in `configs/gps.conf` and `configs/radio.conf`, and the provisioner
substitutes what it was told. Read-only reconnaissance (`/proc/asound/cards`, a
directory listing of `/dev/serial/by-id/`) is fine; opening a device is not.

**Confirm behavior against the version the target platform ships**, not against
a dependency's current source tree. Two separate faults here came from reading
the wrong tree — a config syntax that only exists in a later release, and a
settings group renamed after the version Linux Mint packages.

**Say what you did not verify.** A change that has not been run on hardware is
not a problem; a change that implies it has been is. The distinction between
"observed working" and "written and checked but never exercised" is maintained
deliberately throughout this repository, and it is the reason any of its claims
are worth anything.

## Testing

`tests/` runs without a display, a network, or a radio:

```bash
for t in tests/*.py; do python3 "$t" || break; done
```

Some need `tkinter`, so use a Python that has it — `xvfb-run -a python3.12` on a
headless machine. `TESTING.md` covers the dry-run path that needs no VM.

## Versions

`VERSION` in `deploy_emcomm_node_gui.py` is the one place the release number
lives. It moves when either shipping script changes — the provisioner or
`scripts/fetch_map_tiles.py`. Documentation-only changes do not earn a bump.
