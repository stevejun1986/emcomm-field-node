#!/usr/bin/env python3
"""The radio interface binding: what Direwolf is told, and what is never opened.

    python3 tests/radio.py

No hardware, no display, no serial port. The assertions are about the two
things that decide whether a transmitter keys: that a declared binding reaches
direwolf.conf in the syntax Direwolf expects, and that nothing in the path from
configs/radio.conf to that file ever opens the device.

That second one is not fussiness. A Digirig Mobile keys its radio from the RTS
line of its serial port, so a provisioner or a verification row that opened the
port to "check it works" could put a station on the air.
"""
import os
import re
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.chdir(REPO)

SRC = (REPO / "deploy_emcomm_node_gui.py").read_text()
mod = {"Path": Path, "re": re, "os": os}
for fn in ("_read_radio_binding", "_alsa_card_ids", "direwolf_ptt_line",
           "modemmanager_id_serial", "radio_mm_rule_text"):
    m = re.search(r"^def %s\(.*?(?=\n\ndef |\n\n# |\n\n#: )" % fn, SRC, re.S | re.M)
    assert m, "function %s went missing" % fn
    exec(compile(m.group(0), "<prov>", "exec"), mod)


# --- the PTT directive, in Direwolf's syntax -----------------------------
# A Digirig Mobile is a CM108 sound card AND a CP2102 serial bridge behind a
# USB hub. The PTT is an open-collector switch on the CP2102's RTS line, so
# "PTT CM108" names the right chip and the wrong mechanism.
BY_ID = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_X-if00-port0"
assert mod["direwolf_ptt_line"](BY_ID, "RTS") == "PTT %s RTS" % BY_ID
assert mod["direwolf_ptt_line"](BY_ID, "DTR") == "PTT %s DTR" % BY_ID
assert mod["direwolf_ptt_line"](BY_ID, "-RTS") == "PTT %s -RTS" % BY_ID
assert mod["direwolf_ptt_line"](BY_ID, None) == "PTT %s RTS" % BY_ID, "RTS is the default"
# CM108 is a method, not a device: the chip's GPIO, with an optional hidraw.
assert mod["direwolf_ptt_line"](None, "CM108") == "PTT CM108"
assert mod["direwolf_ptt_line"]("/dev/hidraw0", "cm108") == "PTT CM108 /dev/hidraw0"
print("OK: the PTT directive matches Direwolf's syntax for each method")


# --- reading the declaration ---------------------------------------------
def with_conf(body):
    d = tempfile.mkdtemp()
    (Path(d) / "configs").mkdir()
    if body is not None:
        (Path(d) / "configs" / "radio.conf").write_text(body)
    cwd = os.getcwd()
    os.chdir(d)
    try:
        return mod["_read_radio_binding"]()
    finally:
        os.chdir(cwd)

assert with_conf(None) == (None, None, None), "absent file must not guess"
assert with_conf("") == (None, None, None)
# The shipped sample carries PTT_METHOD=RTS as the sensible default but leaves
# the two device values empty, so an unedited copy declares no devices. The
# step and the verification rows must tell that apart from "no file at all":
# telling someone a file is missing while it sits in configs/ sends them
# hunting for the wrong thing.
sample = (REPO / "configs" / "radio.conf.sample").read_text()
assert with_conf(sample) == (None, None, "RTS"), with_conf(sample)
assert "configs/radio.conf is present but does not declare" in SRC
assert "configs/radio.conf declares no ADEVICE" in SRC
assert "configs/radio.conf declares no PTT_DEVICE" in SRC

got = with_conf('ADEVICE=plughw:CARD=Device,DEV=0\nPTT_DEVICE=%s\nPTT_METHOD=RTS\n' % BY_ID)
assert got == ("plughw:CARD=Device,DEV=0", BY_ID, "RTS"), got
# Comments, blank lines, quoting and stray case must not change the answer.
got = with_conf('# a comment\n\n  adevice = "plughw:1,0"  \nPTT_METHOD=\'DTR\'\n')
assert got == ("plughw:1,0", None, "DTR"), got
print("OK: the declaration is read, and an unedited sample declares nothing")


# --- nothing in this path may open a device ------------------------------
# The functions are read from source, so check the source: any of these would
# open the port, and opening it is what keys the radio.
body = "".join(
    re.search(r"^def %s\(.*?(?=\n\ndef |\n\n# |\n\n#: )" % fn, SRC, re.S | re.M).group(0)
    for fn in ("_read_radio_binding", "_alsa_card_ids", "direwolf_ptt_line"))
for forbidden in ("open(", "Serial", "termios", "fcntl", "os.open"):
    assert forbidden not in body.replace("src.read_text", "").replace(".read_text", ""), \
        "%s appears in the binding path -- it must never open the device" % forbidden
print("OK: reading the binding opens no device")

# The verification block must not open the PTT device either.
verif = SRC[SRC.index('if "direwolf" in selected_ids:'):]
verif = verif[:verif.index('if "meshtastic" in selected_ids:')]
assert "Path(ptt_device).exists()" in verif, "the PTT row must check existence"
for forbidden in ("open(", "Serial", "termios"):
    assert forbidden not in verif, \
        "verification must not %s the PTT device" % forbidden
print("OK: verification checks the PTT path exists and never opens it")


# --- the shipped sample says the things that matter ----------------------
low = sample.lower()
assert "by-id" in low and "/dev/ttyusb0" in low, "must steer away from numbered paths"
assert "cp2102" in low and "rts" in low, "must name the Digirig PTT mechanism"
assert "cm108" in low, "must explain why the obvious answer is wrong"
assert "arecord -l" in low, "must say how to find the sound card"
for leak in ("wsnq705", "n0call"):
    assert leak not in low, "sample carries operator data"
assert re.search(r"^ADEVICE=$", sample, re.M), "sample must ship empty"
assert re.search(r"^PTT_DEVICE=$", sample, re.M), "sample must ship empty"
print("OK: the sample explains by-id, the CP2102 RTS switch, and ships empty")


# --- an install failure does not cancel the configuration ----------------
# Observed on a node running the hand-ported equivalent of this step: the
# config block sat behind apt's exit status, so a run where apt returned
# non-zero wrote no direwolf.conf at all -- on a machine that already had
# direwolf and could use one. Verification then showed "Direwolf installed"
# green and "Direwolf config written" red, one under the other, because that
# first row asks which() and not apt.
#
# Sliced to the step, not the whole file: shutil.which("direwolf") and
# proc.stdout both appear elsewhere, so a whole-file assertion passes while
# this step no longer does either -- which is exactly the shape of failure
# these lines exist to catch.
step = SRC[SRC.index("def optional_direwolf(ctx: Ctx):"):]
step = step[:step.index("\n\n\n")]
assert 'present = shutil.which("direwolf") is not None' in step, \
    "the step no longer checks whether direwolf is present, only whether apt succeeded"
assert "if status == 0 or present:" in step, \
    "the config block is gated on the install status again"
# A non-zero apt exit is the transaction's status, not a verdict on the
# package. Saying "install failed" asserted a cause from an exit code.
assert "Direwolf install failed" not in step, \
    "a non-zero apt exit is being reported as the install having failed"
# ctx.sudo() captures output into a pipe; this step used to read the return
# code, drop the rest, and point the operator at a log with nothing in it.
assert 'output = (proc.stdout or "").strip()' in step, \
    "apt's own output is being discarded again"
print("OK: the config is written when direwolf is present, not when apt succeeded")

# --- ModemManager is kept off the interface ------------------------------
# ModemManager probes an unknown serial port by writing AT commands to it.
# Writing opens the port, opening asserts RTS, and on this interface RTS keys
# the transmitter -- so this is the one piece of the step whose absence is an
# RF event rather than a misconfiguration.
id_serial = mod["modemmanager_id_serial"]

# The match is derived from the declared path, never from the device, which is
# what lets the rule be written with nothing plugged in and nothing opened.
assert id_serial(BY_ID) == \
    "Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_X", id_serial(BY_ID)
assert id_serial("/dev/serial/by-id/usb-Prolific_Technology_Inc._USB-Serial_Controller"
                 "-if00-port0") == "Prolific_Technology_Inc._USB-Serial_Controller"
assert id_serial("/dev/serial/by-id/usb-Vendor_Model_serial-with-port9-if02-port0") == \
    "Vendor_Model_serial-with-port9", "a serial containing -port must survive"
assert id_serial("/dev/serial/by-id/usb-Some_Vendor_Serial-port0") == "Some_Vendor_Serial"
assert id_serial("/dev/ttyUSB0") is None, "a numbered path carries no serial"
assert id_serial("/dev/ttyACM0") is None
assert id_serial("") is None and id_serial(None) is None
print("OK: ID_SERIAL is derived from the declaration, not from the device")

rule = mod["radio_mm_rule_text"](id_serial(BY_ID))
assert 'ENV{ID_MM_DEVICE_IGNORE}="1"' in rule, "the rule does not set the ignore flag"
assert 'SUBSYSTEM=="tty"' in rule, "the rule is not scoped to tty devices"
assert 'ENV{ID_SERIAL}=="Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_X"' in rule, rule
assert BY_ID not in rule, \
    "the rule matches on the by-id path rather than ID_SERIAL, which udev does not set"
assert "keys the transmitter" in rule and "RTS" in rule, \
    "the rule no longer says why it exists -- the next reader deletes what looks decorative"
print("OK: the rule matches on ID_SERIAL and says why it exists")

# The step writes it for a by-id declaration, and says so rather than going
# quiet when it cannot -- the hazard survives a path it cannot match on.
step = SRC[SRC.index("def optional_direwolf(ctx: Ctx):"):]
step = step[:step.index("\n\n\n")]
assert "ctx.sudo_write(RADIO_MM_RULE, radio_mm_rule_text(mm_serial))" in step, \
    "the step no longer installs the ModemManager exclusion"
# Matched on what is contiguous in the source: the message is split across
# two adjacent string literals, so the assembled sentence never appears.
assert "so no ModemManager exclusion was " in step, \
    "the step goes quiet about a numbered path it cannot protect"
assert "udevadm" in step and "reload-rules" in step, \
    "the step no longer reloads udev after writing the rule"
print("OK: the step installs the rule, and reports the path it cannot cover")

# The row has to read the rule, not stat it: a file naming an interface this
# node stopped using protects nothing. Named exactly, because other reads
# happen in the same block.
verif = SRC[SRC.index('if "direwolf" in selected_ids:'):]
verif = verif[:verif.index('if "meshtastic" in selected_ids:')]
assert "Path(RADIO_MM_RULE).read_text" in verif, \
    "the row no longer reads the rule's contents"
assert "mm_serial in rule" in verif, \
    "the row no longer checks the rule against the current declaration"
assert '_udev_property(ptt_device, "ID_MM_DEVICE_IGNORE")' in verif, \
    "the row no longer asks udev whether the rule actually applied"
for forbidden in ("open(", "Serial", "termios", "fcntl"):
    assert forbidden not in verif, \
        "verification must not %s the PTT device to check the rule" % forbidden

# Which branch carries which status is the point. A rule that is not there
# must never read as a pass.
_row = verif[verif.index("mm_serial = modemmanager_id_serial(ptt_device)"):]
_calls = re.findall(
    r'add\("ModemManager excluded from the radio interface", "(pass|warn)",\s*\n\s*"([^"]*)"',
    _row)
assert len(_calls) == 5, "the row's branches changed shape: %r" % (_calls,)
_passes = sorted(d[:24] for st, d in _calls if st == "pass")
assert _passes == ["rule names %s; not attac", "udev reports ID_MM_DEVIC"], _passes
print("OK: verification reads the rule, asks udev, and a missing rule is not a pass")


print("\nRADIO: ALL ASSERTIONS PASSED")
