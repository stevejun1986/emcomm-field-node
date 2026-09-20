#!/usr/bin/env python3
"""Tile fetcher: pacing, backoff, and what it does when told to slow down.

    python3 tests/throttle.py

Nothing here touches the network. urlopen and sleep are replaced with
recorders, so the assertions are about the client's manners: that it paces
every attempt, tells "no such tile" apart from "slow down", honours
Retry-After, and eventually stops rather than grinding.
"""
import sys, urllib.error, importlib.util, tempfile, io
from pathlib import Path
REPO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("f", REPO / "scripts" / "fetch_map_tiles.py")
f = importlib.util.module_from_spec(spec); sys.modules["f"] = f; spec.loader.exec_module(f)

SLEPT = []
f.time.sleep = lambda s: SLEPT.append(s)
f.random.uniform = lambda a, b: 0.0          # deterministic jitter

class Resp:
    def __init__(self, data=b"PNG"): self.data = data
    def read(self): return self.data
    def __enter__(self): return self
    def __exit__(self, *a): return False

def http_error(code, headers=None):
    return urllib.error.HTTPError("u", code, "msg", headers or {}, None)

def run(responses, delay=0.15):
    """responses: list of Resp or Exception, consumed per attempt."""
    SLEPT.clear()
    seq = list(responses)
    def fake_urlopen(req, timeout=None):
        r = seq.pop(0)
        if isinstance(r, Exception): raise r
        return r
    f.urllib.request.urlopen = fake_urlopen
    with tempfile.TemporaryDirectory() as td:
        return f.fetch("http://x/1.png", Path(td) / "1.png", "ua", 10, delay)

# --- the bug this replaced: pacing must apply to every attempt ----------
assert run([Resp()]) == "ok"
assert SLEPT == [0.15], SLEPT
print("OK: a successful fetch is paced once, before the request")

r = run([http_error(429, {"Retry-After": "7"}), Resp()])
assert r == "ok", r
assert SLEPT == [0.15, 7.0, 0.15], SLEPT
print("OK: 429 then success — Retry-After honoured, and the retry is paced too")
print("    slept: %s" % SLEPT)

# --- "no such tile" is not "slow down" ---------------------------------
assert run([http_error(404)]) == "missing"
assert SLEPT == [0.15], "a 404 must not be retried: %s" % SLEPT
print("OK: 404 returns 'missing' immediately — pyramid edges are normal")

# --- persistent throttling gives up and says so ------------------------
r = run([http_error(429) for _ in range(f.MAX_ATTEMPTS)])
assert r == "throttled", r
paced = [s for s in SLEPT if s == 0.15]
backoffs = [s for s in SLEPT if s != 0.15]
assert len(paced) == f.MAX_ATTEMPTS, SLEPT
assert backoffs == [1.0, 2.0, 4.0], backoffs
print("OK: persistent 429 -> 'throttled' after %d paced attempts" % f.MAX_ATTEMPTS)
print("    exponential backoff between them: %s" % backoffs)

# --- backoff is capped so a bad Retry-After cannot park the run --------
r = run([http_error(503, {"Retry-After": "99999"}), Resp()])
assert max(SLEPT) == f.MAX_BACKOFF, SLEPT
print("OK: an absurd Retry-After is capped at %gs rather than obeyed" % f.MAX_BACKOFF)

# --- transient network errors retry; permanent ones do not -------------
assert run([TimeoutError(), TimeoutError(), Resp()]) == "ok"
print("OK: transient timeouts retry and recover")
assert run([http_error(403)]) == "fail"
assert SLEPT == [0.15], "403 is not retryable: %s" % SLEPT
print("OK: 403 is permanent — not retried, not mistaken for throttling")

# --- the treat-everything-as-fail behaviour is gone --------------------
for code, expect in ((404, "missing"), (429, "throttled"), (503, "throttled"), (403, "fail")):
    got = run([http_error(code)] * f.MAX_ATTEMPTS)
    assert got == expect, (code, got, expect)
print("OK: every status maps to a distinct outcome, not a single 'fail'")

# --- URL casing, per the note carried in the fetcher -------------------
for name, (url, _desc) in f.SOURCES.items():
    assert "/ArcGIS/rest" in url, (name, url)
    assert "/arcgis/" not in url, (name, url)
print("OK: all %d sources use the case-sensitive /ArcGIS/ path" % len(f.SOURCES))


# --- the estimate must match the pacing it describes --------------------
assert f.estimate_seconds(0) == 0.0
assert f.estimate_seconds(1, 0.15, 0, 5) == 0.15
assert f.estimate_seconds(100, 0.15, 100, 5) == 100 * 0.15          # no pause yet
assert f.estimate_seconds(101, 0.15, 100, 5) == 101 * 0.15 + 5      # one pause
assert f.estimate_seconds(250, 0.15, 100, 5) == 250 * 0.15 + 10     # two pauses
assert f.estimate_seconds(1000, 0.15, 0, 5) == 150.0                 # bursting off
print("OK: estimate_seconds counts burst pauses, not just spacing")

# defaults are shared, so the estimate cannot drift from the download again
import inspect
src = inspect.getsource(f.main)
for name in ("DEFAULT_DELAY", "DEFAULT_BURST", "DEFAULT_PAUSE"):
    assert name in src, "argparse should default from %s, not a literal" % name
print("OK: argparse defaults come from the shared constants")
print("\nTHROTTLE: ALL ASSERTIONS PASSED")
