#!/usr/bin/env python3
"""Build parallelism: memory, not core count, is the binding constraint.

    python3 tests/build.py

SatDump is built from source on every node, and the reference hardware is a
4 GB dual-core also running a desktop session. An OOM-killed compiler reports
as an ordinary build failure with nothing pointing at the cause, so the job
count is derived from MemAvailable rather than from nproc.
"""
import os, sys
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO); sys.path.insert(0, str(REPO))
import deploy_emcomm_node_gui as g

real_path = g.Path
class FakeMemInfo:
    def __init__(self, kb): self.kb = kb
    def read_text(self): return "MemTotal: 999\nMemAvailable: %d kB\nCached: 1\n" % self.kb
def patch(kb, cpus):
    g.os.cpu_count = lambda: cpus
    g.Path = lambda p: FakeMemInfo(kb) if str(p) == "/proc/meminfo" else real_path(p)

print("  cpus  avail     -j   rationale")
for cpus, gb, why in ((2, 2.5, "CF-30, desktop running"), (2, 4.0, "CF-30, nothing else"),
                      (2, 0.9, "starved"), (4, 16.0, "workstation"),
                      (8, 3.0, "many cores, little RAM")):
    patch(int(gb * 1024**2), cpus)
    print("   %-4d %5.1f GB   %-3d  %s" % (cpus, gb, g._build_jobs(), why))
g.Path = real_path
g.os.cpu_count = os.cpu_count

# /proc/meminfo unreadable must not crash the build
class Boom:
    def read_text(self): raise OSError("no /proc")
g.Path = lambda p: Boom() if str(p) == "/proc/meminfo" else real_path(p)
n = g._build_jobs()
g.Path = real_path
assert n == (os.cpu_count() or 1), n
print("\n  OK: unreadable /proc/meminfo falls back to core count (-j%d), does not raise" % n)
assert g._build_jobs() >= 1
print("  OK: never returns zero jobs")
print("\nJOBS: ALL ASSERTIONS PASSED")
