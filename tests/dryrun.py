"""Execute every provisioning step with the subprocess/network boundary stubbed.

    python3 tests/dryrun.py .                 # as if nothing is installed yet
    python3 tests/dryrun.py . wine,js8call    # as if those are already present

Run it from the repository root. Provisioning cannot be exercised in CI or a
container — it installs packages and writes to /etc — so this runs the real step
functions with subprocess, downloads and shutil.which replaced by recorders, and
prints what each step WOULD do. It catches wiring and reporting faults (a step
that reports success having done nothing, a path written in one place and read
from another) but not runtime ones. A real VM run is still required.

Nothing is installed and nothing outside a temp $HOME is touched, but the real
step functions run, so anything that would crash, skip silently, or write the
wrong path shows up in the transcript.
"""
import importlib.util, os, shutil, subprocess as real_subprocess, sys, tempfile, traceback, types
from pathlib import Path

REPO = Path(sys.argv[1]).resolve()
INSTALLED = set(sys.argv[2].split(",")) if len(sys.argv) > 2 and sys.argv[2] else set()

os.chdir(REPO)
spec = importlib.util.spec_from_file_location("g", REPO / "deploy_emcomm_node_gui.py")
g = importlib.util.module_from_spec(spec); sys.modules["g"] = g; spec.loader.exec_module(g)

HOME = Path(tempfile.mkdtemp(prefix="dryhome-"))
CMDS, WRITES, DOWNLOADS, LOGS, PROBLEMS = [], [], [], [], []

class FakeCompleted:
    def __init__(self, args, rc=0, out=""):
        self.args, self.returncode, self.stdout, self.stderr = args, rc, out, ""

def fake_run(cmd, **kw):
    CMDS.append(("direct", cmd))
    out = ""
    if isinstance(cmd, (list, tuple)):
        if cmd[:2] == ["id", "-nG"]:
            out = "op adm cdrom sudo dip plugdev\n"        # note: no dialout
        elif cmd[:2] == ["flatpak", "list"]:
            out = ""
    return FakeCompleted(cmd, 0, out)

g.subprocess = types.SimpleNamespace(
    run=fake_run, DEVNULL=real_subprocess.DEVNULL, PIPE=real_subprocess.PIPE,
    STDOUT=real_subprocess.STDOUT, CalledProcessError=real_subprocess.CalledProcessError)
g.shutil = types.SimpleNamespace(
    which=lambda b: ("/usr/bin/" + b) if b in INSTALLED else None,
    copy=shutil.copy, copytree=shutil.copytree, rmtree=shutil.rmtree, move=shutil.move)

class DryCtx(g.Ctx):
    def run(self, cmd, check=True, **kw):
        CMDS.append(("run", cmd)); return FakeCompleted(cmd)
    def sudo(self, *a, check=True, **kw):
        CMDS.append(("sudo", list(a))); return FakeCompleted(list(a))
    def sudo_write(self, path, content, mode=None):
        WRITES.append(("sudo", str(path), content, mode))
    def download(self, url, dest):
        DOWNLOADS.append((url, str(dest)))
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"DRYRUN")
    def verify_checksum(self, file, expected):
        CMDS.append(("checksum", "%s == %s" % (file, expected[:16] + "...")));  return True

def log(msg, level="info"):
    LOGS.append((level, msg))

ctx = DryCtx(home=HOME, user="op", node_id="N0CALL",
             askpass=types.SimpleNamespace(env=lambda: {}, close=lambda: None),
             log=log, cancel_check=lambda: None,
             spin_start=lambda label: LOGS.append(("spin", label)),
             spin_stop=lambda ok: None, spin_progress=lambda t: None)

print("dry run  repo=%s  home=%s  installed=%s\n" % (REPO, HOME, sorted(INSTALLED) or "nothing"))
for comp in g.COMPONENTS:
    before = (len(CMDS), len(WRITES), len(DOWNLOADS), len(LOGS))
    print("=" * 78); print("STEP  %s — %s" % (comp.id, comp.label)); print("=" * 78)
    try:
        comp.fn(ctx)
    except Exception as e:
        PROBLEMS.append((comp.id, e, traceback.format_exc()))
        print("  ** RAISED %s: %s" % (type(e).__name__, e))
        for line in traceback.format_exc().splitlines()[-6:]:
            print("     " + line)
    for lvl, msg in LOGS[before[3]:]:
        print("  [%-4s] %s" % (lvl, msg))
    for kind, c in CMDS[before[0]:]:
        print("  $ (%s) %s" % (kind, c if isinstance(c, str) else " ".join(map(str, c))))
    for u, d in DOWNLOADS[before[2]:]:
        print("  GET %s\n      -> %s" % (u, d))
    for kind, path, content, mode in WRITES[before[1]:]:
        print("  WRITE(%s) %s  mode=%s  %d bytes" % (kind, path, mode, len(content)))
    print()

print("=" * 78); print("FILES CREATED UNDER $HOME"); print("=" * 78)
for f in sorted(HOME.rglob("*")):
    if f.is_file():
        print("  %8d  %s" % (f.stat().st_size, f.relative_to(HOME)))
print()
print("=" * 78); print("SUMMARY"); print("=" * 78)
print("  steps run      : %d" % len(g.COMPONENTS))
print("  raised         : %d  %s" % (len(PROBLEMS), [p[0] for p in PROBLEMS]))
print("  commands       : %d" % len(CMDS))
print("  sudo writes    : %d" % len(WRITES))
print("  downloads      : %d" % len(DOWNLOADS))
warn = [m for l, m in LOGS if l == "warn"]
err  = [m for l, m in LOGS if l == "err"]
print("  warn lines     : %d" % len(warn))
print("  err lines      : %d" % len(err))
for m in err:
    print("     ERR  %s" % m)
