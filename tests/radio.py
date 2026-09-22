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
mod = {"Path": Path, "re": re}
for fn in ("_read_radio_binding", "_alsa_card_ids", "direwolf_ptt_line"):
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

print("\nRADIO: ALL ASSERTIONS PASSED")
