"""Five-screen flow, verification wiring, and the CF-30 panel constraint.

    python3 tests/flow.py                    # on a desktop session
    xvfb-run -a python3 tests/flow.py        # headless

Needs a Python with tkinter and a display (xvfb is fine). Exercises the real
GUI: no credential is prompted for before the options are valid, a bad password
is retryable in place, verification populates after the run, and every screen
fits the reference 1024x768 panel — pack() clips silently, so a screen that
outgrows the display hides its own controls with no error.
"""
import os, sys, dataclasses
from pathlib import Path

# Resolve the provisioner relative to this file, so the test runs the same
# from the repository root or from inside tests/.
REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)
sys.path.insert(0, str(REPO))
import deploy_emcomm_node_gui as mod
mod.os.geteuid = lambda: 1000
mod.messagebox.showerror = lambda *a, **k: None
app = mod.ProvisionerGUI()

assert not hasattr(app, "sudo_pw_var"), "password var must not exist before the sudo screen"
assert list(app.frames) == ["options"], list(app.frames)
print("OK: options screen carries no sudo field")

app.node_id_var.set("")
app._on_continue_to_sudo()
assert "sudo" not in app.frames
print("OK: blank Node ID blocked before any credential prompt")

app.node_id_var.set("N0CALL")
# deliberately NOT maps_fetch: that path goes via the operating-area screen
# and is covered by tests/area.py. This test covers the five-screen flow.
app.component_vars["config_profiles"].set(True)
app.component_vars["docs_server"].set(True)
app._on_continue_to_sudo()
assert "sudo" in app.frames
assert app.selected_ids == {"config_profiles", "docs_server"}, app.selected_ids
assert "2 step(s) selected" in app.sudo_summary.cget("text")
print("OK: options -> sudo, selections carried")

app._show("options")
assert app.component_vars["config_profiles"].get() is True and app.node_id_var.get() == "N0CALL"
print("OK: Back preserves state")

app._show("sudo"); app.sudo_pw_var.set(""); app._on_start()
assert "run" not in app.frames and "Enter your sudo password" in app.sudo_error.cget("text")
print("OK: empty password rejected inline")

mod.AskpassSession.verify = lambda self: False
app.sudo_pw_var.set("wrong"); app._on_start()
assert "run" not in app.frames and "did not validate" in app.sudo_error.cget("text")
assert str(app.start_btn.cget("state")) == "normal"
print("OK: bad password retryable")

mod.AskpassSession.verify = lambda self: True
mod.COMPONENTS = [dataclasses.replace(c, fn=lambda ctx: None) for c in mod.COMPONENTS]
app.sudo_pw_var.set("right"); app._on_start()
assert "run" in app.frames
app.worker.join(timeout=20)
for _ in range(4): app._drain_queue()
print("OK: run screen reached; worker done:", not app.worker.is_alive())

assert "verify" in app.frames
rows = app.verify_tree.get_children()
assert len(rows) == len(app.check_results) > 0, (len(rows), len(app.check_results))
assert str(app.verify_continue_btn.cget("state")) == "normal"
print("OK: verification populated — %d rows, %r" % (len(rows), app.verify_title.cget("text")))

app._on_show_summary()
body = app.summary_body.get("1.0", "end")
assert "N0CALL" in body and "2 step(s) run" in body, body
assert "Provisioner: v%s" % mod.VERSION in body, body
print("OK: summary rendered, reporting v%s" % mod.VERSION)

# A bad run renders past the pane: the problem list caps at 12 rows but
# failed_steps does not. Before the scrollbar this clipped silently -- no
# scrollbar, no indication, no error -- and the rows lost were the failures.
# Assert the overflow is reachable, not merely that it was written.
kept = (app.check_results, app.failed_steps, app.run_outcome)
app.check_results = [mod.CheckResult("Check %02d with a reasonably long label" % i,
                                      "fail", "a detail string of the usual length")
                     for i in range(20)]
app.failed_steps = ["Step %d" % i for i in range(13)]
app.run_outcome = "failed"
app.runlog = None                      # the footer is already written and closed
app._on_show_summary()
app.update_idletasks()
t = app.summary_body
assert t.cget("yscrollcommand"), "summary pane has no scrollbar wired"
assert t.yview()[1] < 1.0, "test case does not overflow; assertion proves nothing"
t.yview_moveto(1.0); app.update_idletasks()
last = t.index("end-2c").split(".")[0]
assert t.dlineinfo("%s.0" % last) is not None, "bottom of a long summary is unreachable"
app._on_show_summary(); app.update_idletasks()
assert app.summary_body.yview()[0] == 0.0, "re-render did not return the pane to the top"
print("OK: a long summary scrolls, and re-rendering returns to the top")
app.check_results, app.failed_steps, app.run_outcome = kept
app._on_show_summary()

geo_w, geo_h = (int(n) for n in app.geometry().split("+")[0].split("x"))
min_w, min_h = app.minsize()
bad = [(n, f.winfo_reqwidth(), f.winfo_reqheight()) for n, f in app.frames.items()
       if f.winfo_reqwidth() > max(min_w, geo_w) or f.winfo_reqheight() > max(min_h, geo_h)]
for n, f in app.frames.items():
    print("    %-8s %4d x %-4d" % (n, f.winfo_reqwidth(), f.winfo_reqheight()))
assert not bad, bad
print("OK: all %d screens fit (window %dx%d, minsize %dx%d)" % (len(app.frames), geo_w, geo_h, min_w, min_h))
print("\nEM FLOW: ALL ASSERTIONS PASSED")

# the reference panel is 1024x768 — nothing may exceed it
CF30_W, CF30_H = 1024, 768
over = [(n, f.winfo_reqwidth(), f.winfo_reqheight()) for n, f in app.frames.items()
        if f.winfo_reqwidth() > CF30_W - 40 or f.winfo_reqheight() > CF30_H - 90]
assert not over, "will not fit a CF-30 panel: %r" % over
assert min_w <= CF30_W - 40 and min_h <= CF30_H - 90, (min_w, min_h)
print("OK: every screen fits a 1024x768 CF-30 panel (minsize %dx%d)" % (min_w, min_h))
