# LD2410C serial probe v2: find which board pin the radar's TX is actually on.
# Copy to CIRCUITPY as code.py, Ctrl-D. Listen-only, ~12 seconds, radar must be powered.
# Expected result: frames on GPIO13 at 256000 baud.

import time
import binascii
import board
import busio

HEADER = b"\xF4\xF3\xF2\xF1"

# every GPIO not used by the camera (2,4,5,7,8,9,10,11,21,38-42,47) or radar OUT (14)
CANDIDATES = [("GPIO13", board.GPIO13), ("GPIO12", board.GPIO12), ("GPIO1", board.GPIO1),
              ("GPIO3", board.GPIO3), ("GPIO6", board.GPIO6), ("GPIO15", board.GPIO15),
              ("GPIO16", board.GPIO16), ("GPIO17", board.GPIO17), ("GPIO18", board.GPIO18),
              ("GPIO45", board.GPIO45), ("GPIO46", board.GPIO46), ("GPIO48", board.GPIO48)]


def listen(pin, baud, seconds):
    u = busio.UART(None, pin, baudrate=baud, timeout=0.05, receiver_buffer_size=2048)
    time.sleep(seconds)
    n = u.in_waiting
    data = u.read(n) if n else b""
    u.deinit()
    return data


print("\nPass 1: listening on every free pin at 256000 baud ...")
hits = []
for name, pin in CANDIDATES:
    data = listen(pin, 256000, 0.7)
    hit = data.find(HEADER) >= 0
    if data or hit:
        sample = binascii.hexlify(data[:12]).decode()
        print("  %-7s %4d bytes  %-26s %s" % (name, len(data), sample, "<-- LD2410C FRAMES" if hit else "(noise/other)"))
    if hit:
        hits.append(name)

if not hits:
    print("\nPass 2: GPIO13 and GPIO12 at other baud rates ...")
    for name, pin in CANDIDATES[:2]:
        for baud in (115200, 57600, 9600):
            data = listen(pin, baud, 1.0)
            if data.find(HEADER) >= 0:
                print("  %s @ %d: LD2410C FRAMES" % (name, baud))
                hits.append("%s@%d" % (name, baud))
            elif data:
                print("  %s @ %d: %d bytes, no header" % (name, baud, len(data)))

print("\n==== VERDICT ====")
if hits == ["GPIO13"]:
    print("Radar TX is on GPIO13 at 256000 - correct. radar_test.py and the demo will work as written.")
elif hits:
    print("Radar TX signal found on: %s" % ", ".join(hits))
    print("=> Move the radar's TX wire (green) to GPIO13. If a baud rate other than 256000 is shown,")
    print("   change baudrate=256000 to that value in radar_test.py and cam_radar_server.py.")
else:
    print("No radar serial data on any free pin.")
    print("=> The green wire isn't carrying the signal: press its female end firmly onto the radar's")
    print("   TX pin, or try a different jumper. Confirm the radar's LED is lit (5 V present).")
