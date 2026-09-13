#!/usr/bin/env python3
"""A failing step must not cost the operator the steps that would have worked.

    xvfb-run -a python3 tests/isolation.py

Observed: a QLog Flatpak install failed at step 4 of 13, the exception escaped
the component loop, and steps 5 through 13 never ran. Maps, profiles, the dock
trigger, Direwolf, Meshtastic and SatDump were all absent from a node whose
operator had selected them, and the verification screen reported 14 failures
that were consequences of the abort rather than faults of their own.
"""
import os, sys, dataclasses, subprocess, tempfile, types
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO); sys.path.insert(0, str(REPO))
# A run writes a transcript under $HOME. Point that somewhere disposable so the
# tests never leave files in the home directory of whoever ran them.
os.environ["HOME"] = tempfile.mkdtemp(prefix="isolation-home-")
import deploy_emcomm_node_gui as mod

# --- progress-bar condensing --------------------------------------------
flatpak_noise = (["Installing 7/8…  ███  %d%%  2.7 MB/s  00:%02d" % (p, 60 - p)
                  for p in range(10, 100)]
                 + ["Error: Flatpak system operation Deploy not allowed for user",
                    "error: Failed to install org.kde.Platform"])
out = mod.ProvisionerGUI._condense(flatpak_noise)
assert len(out) < 10, out
assert "Deploy not allowed for user" in "\n".join(out), out
assert "Failed to install org.kde.Platform" in "\n".join(out), out
print("OK: %d progress lines condensed to %d, both error lines survive"
      % (len(flatpak_noise), len(out)))
for line in out:
    print("     " + line[:74])

assert mod.ProvisionerGUI._condense([]) == []
assert mod.ProvisionerGUI._condense(["only one"]) == ["only one"]
distinct = ["alpha", "beta", "gamma"]
assert mod.ProvisionerGUI._condense(distinct) == distinct, "distinct lines must not collapse"
print("OK: empty, single and distinct inputs pass through unchanged")

# --- one failing step must not stop the others ---------------------------
mod.os.geteuid = lambda: 1000
mod.messagebox.showerror = lambda *a, **k: None
mod.AskpassSession.verify = lambda self: True

ran = []
def ok_step(cid):
    def fn(ctx): ran.append(cid)
    return fn
def boom_called(ctx):
    raise subprocess.CalledProcessError(1, ["flatpak", "install", "-y", "qlog"],
                                        output="\n".join(flatpak_noise))
def boom_other(ctx):
    raise RuntimeError("something unforeseen")

mod.COMPONENTS = [
    dataclasses.replace(c,
        fn=boom_called if c.id == "qlog_ion2g"
        else boom_other if c.id == "kiwix_zim"
        else ok_step(c.id))
    for c in mod.COMPONENTS]

app = mod.ProvisionerGUI()
app.node_id_var.set("N0CALL")
app._select_all_components()
app.component_vars["maps_fetch"].set(False)     # skip the area screen
app._on_continue_to_sudo()
app.sudo_pw_var.set("pw"); app._on_start()
app.worker.join(timeout=30)
for _ in range(6):
    app._drain_queue()

expected = [c.id for c in mod.COMPONENTS if c.id not in ("qlog_ion2g", "kiwix_zim", "maps_fetch")]
assert ran == expected, "steps did not all run:\n  ran      %r\n  expected %r" % (ran, expected)
print("\nOK: 2 steps failed, the other %d still ran" % len(ran))

text = app.log_widget.get("1.0", "end")
assert "FAILED — continuing with the remaining steps" in text, text[-600:]
assert "FAILED: RuntimeError" in text, text[-600:]
assert "2 step(s) failed" in text, text[-600:]
print("OK: both failures reported by name, and the run says how many failed")

assert app.run_outcome == "failed", app.run_outcome
assert "verify" in app.frames and app.check_results, "verification must still run"
print("OK: outcome is 'failed' and verification still ran (%d checks)" % len(app.check_results))

# the 588-line flatpak dump must not flood the log
assert text.count("2.7 MB/s") < 5, "progress spam reached the log %d times" % text.count("2.7 MB/s")
print("OK: progress spam did not flood the log pane")
app.destroy()

# the summary must name the steps that failed, not merely say "problems"
app2 = mod.ProvisionerGUI()
app2.node_id_var.set("N0CALL")
app2.failed_steps = ["QLog station log + ion2G HF ALE", "Offline knowledgebase (Kiwix ZIM)"]
app2.run_outcome = "failed"
app2.selected_ids = {c.id for c in mod.COMPONENTS}
app2._on_show_summary()
body = app2.summary_body.get("1.0", "end")
assert "2 step(s) failed" in body, body
assert "QLog station log" in body and "Kiwix ZIM" in body, body
print("OK: summary names the failed steps")
app2.destroy()
print("\nISOLATION: ALL ASSERTIONS PASSED")
