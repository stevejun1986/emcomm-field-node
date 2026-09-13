#!/usr/bin/env python3
"""Every run leaves a transcript on disk, and the transcript is the complete one.

    xvfb-run -a python3 tests/runlog.py

The GUI log pane is a poor witness. It scrolls, it dies with the window, and
its failure output is condensed on purpose so that a wall of progress redraws
cannot bury the error. None of that is wrong for a pane an operator reads under
stress -- it is wrong for the copy somebody diagnoses a failed field run from.

So the pane and the file are checked against each other here: the pane must
stay condensed, and the same run's file must contain every line the command
emitted. A "log" that silently inherits the pane's truncation would look
entirely healthy and be useless on the day it is needed.
"""
import dataclasses
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))
import deploy_emcomm_node_gui as mod        # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="runlog-"))

# --- the file is written, tagged and timestamped -------------------------
log = mod.RunLog(TMP / "logs")
assert log.path is not None and log.path.exists(), log.error
assert log.path.parent == TMP / "logs", log.path
assert log.path.name.startswith("provision-") and log.path.name.endswith(".log"), log.path

log.header("N0CALL", "op", ["Core system packages", "Offline maps"], ["Kiwix ZIM"])
log.write("plain line")
log.write("all good", "ok")
log.write("careful", "warn")
log.write("broken", "err")
log.write("two\nlines", "info")
log.close()
text = log.path.read_text()

assert "N0CALL" in text and "Steps selected (2 of 3):" in text, text
assert "Core system packages" in text and "Kiwix ZIM" in text, text
# The provisioner reads configs/ and docs/ by relative path, so the directory
# it ran from is the difference between a real node and a hollow one.
assert os.getcwd() in text, "the header must record the working directory"
for tag, msg in (("INFO", "plain line"), (" OK ", "all good"),
                 ("WARN", "careful"), ("FAIL", "broken")):
    assert "[%s] %s" % (tag, msg) in text, (tag, text)
assert text.count("[INFO] two") == 1 and text.count("[INFO] lines") == 1, \
    "a multi-line message must be timestamped per line"
import re
assert re.search(r"^\d\d:\d\d:\d\d \[INFO\] plain line$", text, re.M), text
print("OK: header, levels and per-line timestamps")

# --- a log that cannot be opened degrades, it does not raise -------------
blocker = TMP / "not-a-dir"
blocker.write_text("")
dead = mod.RunLog(blocker / "logs")
assert dead.path is None and dead.error, (dead.path, dead.error)
dead.header("X", "y", [], [])       # must all be silent no-ops
dead.write("still fine")
dead.raw("output")
dead.footer("t", 0, 0, [], 0, 0, 0)
dead.close()
print("OK: an unopenable log reports an error and never raises")

# --- a write failing mid-run stops the log, not the deployment -----------
mid = mod.RunLog(TMP / "mid")
mid._fh.close()                     # e.g. the filesystem filled up
mid.write("this cannot be written")
assert mid.error and mid._fh is None, (mid.error, mid._fh)
mid.write("and neither can this")   # no second exception
print("OK: a mid-run write failure is recorded once and the log stands down")

# --- old transcripts are pruned ------------------------------------------
pdir = TMP / "prune"
pdir.mkdir()
for n in range(mod.RunLog.KEEP + 5):
    (pdir / ("provision-2020010%02d-000000.log" % n)).write_text("old")
(pdir / "keep-me.txt").write_text("not a run log")
mod.RunLog(pdir)
kept = sorted(p.name for p in pdir.glob("provision-*.log"))
assert len(kept) == mod.RunLog.KEEP, kept
assert kept[-1].startswith("provision-20"), kept
assert (pdir / "keep-me.txt").exists(), "pruning must only touch run logs"
print("OK: %d old logs pruned to the newest %d, nothing else touched"
      % (mod.RunLog.KEEP + 5, mod.RunLog.KEEP))

# --- a real run: pane condensed, file complete ---------------------------
HOME = Path(tempfile.mkdtemp(prefix="runlog-home-"))
os.environ["HOME"] = str(HOME)
assert Path.home() == HOME, Path.home()

noise = (["Installing 7/8…  ███  %d%%  2.7 MB/s  00:%02d" % (p, 100 - p)
          for p in range(10, 100)]
         + ["Error: Flatpak system operation Deploy not allowed for user",
            "error: Failed to install org.kde.Platform"])

mod.os.geteuid = lambda: 1000
mod.messagebox.showerror = lambda *a, **k: None
mod.messagebox.askyesno = lambda *a, **k: True
mod.AskpassSession.verify = lambda self: True

def boom(ctx):
    raise subprocess.CalledProcessError(1, ["flatpak", "install", "-y", "qlog"],
                                        output="\n".join(noise))

def fine(ctx):
    ctx.log("did something", "ok")
    with ctx.spin("Installing core packages..."):
        pass

mod.COMPONENTS = [dataclasses.replace(c, fn=boom if c.id == "qlog_ion2g" else fine)
                  for c in mod.COMPONENTS]

app = mod.ProvisionerGUI()
app.node_id_var.set("N0CALL")
app._select_all_components()
app.component_vars["maps_fetch"].set(False)      # skip the area screen
app._on_continue_to_sudo()
app.sudo_pw_var.set("pw")
app._on_start()
app.worker.join(timeout=60)
for _ in range(8):
    app._drain_queue()
app._on_show_summary()

pane = app.log_widget.get("1.0", "end")
assert app.runlog and app.runlog.path, "a run must open a log"
run_text = app.runlog.path.read_text()
assert str(app.runlog.path) in pane, "the log path must be announced in the pane"
assert str(app.runlog.path) in app.summary_body.get("1.0", "end"), \
    "the summary must say where the log is"

# The headline invariant. Same run, same failure, two different treatments:
# the file keeps every line the command emitted, the pane keeps almost none.
# A log that quietly inherited the pane's condensing would pass every other
# assertion in this file and be worthless on the day it is needed.
dropped_by_pane = [l for l in noise if l not in pane]
dropped_by_file = [l for l in noise if l not in run_text]
assert not dropped_by_file, \
    "the file dropped %d of %d output line(s), e.g. %r" % (
        len(dropped_by_file), len(noise), dropped_by_file[0])
assert len(dropped_by_pane) > 80, \
    "the pane kept %d lines — it is not condensing" % (len(noise) - len(dropped_by_pane))
print("\nOK: one failure, two treatments — of %d output lines the pane shows %d, "
      "the file keeps all %d" % (len(noise), len(noise) - len(dropped_by_pane), len(noise)))

for line in ("Deploy not allowed for user", "Failed to install org.kde.Platform"):
    assert line in run_text, line
assert "flatpak install -y qlog" in run_text, run_text[-800:]
assert "full captured output (92 line(s))" in run_text, run_text[-800:]
print("OK: the failing command, its exit status and its whole output are on disk")

assert "[ >> ] Installing core packages..." in run_text, \
    "a spin must be logged when it starts, so a hang is locatable"
assert "[ OK ] Installing core packages..." in run_text, run_text[-800:]
print("OK: long-running steps are logged at start and at resolution")

assert "===== Verification =====" in run_text, run_text[-1500:]
assert app.check_results, "verification must have run"
assert sum(run_text.count("[%s]" % t) for t in (" OK ", "WARN", "FAIL")) \
    >= len(app.check_results), "every verification row belongs in the log"
print("OK: %d verification rows recorded" % len(app.check_results))

assert "RESULT:" in run_text and "1 step(s) failed" in run_text, run_text[-600:]
assert "QLog station log" in run_text.split("RESULT:")[-1], run_text[-600:]
assert "elapsed" in run_text, run_text[-400:]
assert app.runlog._fh is None, "the log must be closed once the summary is rendered"
print("OK: the footer names the failed step and the file is closed")

app._on_show_summary()          # re-rendering must not reopen or duplicate
assert app.runlog.path.read_text().count("RESULT:") == 1, "footer written twice"
print("OK: revisiting the summary does not rewrite the footer")

app.destroy()
print("\nRUNLOG: ALL ASSERTIONS PASSED")
