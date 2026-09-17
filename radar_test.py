# LD2410C mmWave radar smoke test for the YD-ESP32-S3 N16R8 (CircuitPython 10.x)
# Copy to CIRCUITPY as code.py (or import it from the REPL).
#
# Wiring (radar label -> board):
#   VCC -> 5V   (needs the IN-OUT jumper bridged, or external 5 V on the adapter's 5Vin)
#   GND -> GND
#   OUT -> GPIO14   (3.3 V logic: HIGH while a target is present, holds ~5 s after)
#   TX  -> GPIO13   (radar transmits, board receives)
#   RX  -> GPIO12   (board transmits, radar receives)
#
# Stage 0 (no code): power it and wave - the little LED on the module should light.
# Stage 1: OUT pin read with digitalio
# Stage 2: UART reports at 256000 baud - target state, distance (cm), energy (0-100)
#
# The radar sees YOU. Sitting still at the desk you will read "static"; waving a
# hand or a printout flips it to "MOVING". The bird-cam trigger will key on MOVING.

import time
import board
import busio
import digitalio

OUT_PIN = board.GPIO14
UART_TX = board.GPIO12  # board TX -> radar RX
UART_RX = board.GPIO13  # board RX <- radar TX

out = digitalio.DigitalInOut(OUT_PIN)
out.switch_to_input(pull=digitalio.Pull.DOWN)

uart = busio.UART(UART_TX, UART_RX, baudrate=256000, timeout=0.02,
                  receiver_buffer_size=512)

HEADER = b"\xF4\xF3\xF2\xF1"
FOOTER = b"\xF8\xF7\xF6\xF5"
STATES = {0: "none", 1: "MOVING", 2: "static", 3: "MOVING+static"}


def parse_reports(buf):
    """Pull complete basic-mode report frames out of buf.

    Returns (reports, leftover_bytes). Each report is a tuple:
    (state, moving_cm, moving_energy, static_cm, static_energy, detect_cm)
    Frame: F4 F3 F2 F1 | len(2, LE) | 02 AA | state | mov_cm(2) | mov_e |
           sta_cm(2) | sta_e | det_cm(2) | 55 00 | F8 F7 F6 F5
    """
    reports = []
    while True:
        i = buf.find(HEADER)
        if i < 0:
            return reports, buf[-3:]        # keep a possible partial header
        buf = buf[i:]
        if len(buf) < 6:
            return reports, buf
        length = buf[4] | (buf[5] << 8)
        total = 6 + length + 4
        if len(buf) < total:
            return reports, buf
        frame, buf = buf[:total], buf[total:]
        if frame[-4:] != FOOTER:
            continue                          # garbage; resync on next header
        d = frame[6:6 + length]
        if length >= 13 and d[0] == 0x02 and d[1] == 0xAA:
            reports.append((
                d[2],
                d[3] | (d[4] << 8), d[5],
                d[6] | (d[7] << 8), d[8],
                d[9] | (d[10] << 8),
            ))


print("\nLD2410C test. OUT on GPIO14, UART TX=GPIO12 RX=GPIO13 @ 256000.")
print("Wave a hand or a printout in front of the antenna side of the module.\n")

buf = b""
last_print = 0
last_frame = time.monotonic()
frames = 0
latest = None
prev_out = None

while True:
    n = uart.in_waiting
    if n:
        buf += uart.read(n)
        reports, buf = parse_reports(buf)
        if reports:
            frames += len(reports)
            latest = reports[-1]
            last_frame = time.monotonic()

    now = time.monotonic()
    o = out.value
    if o != prev_out:                         # Stage 1: edge on the OUT pin
        print("OUT ->", "HIGH (target present)" if o else "LOW (clear)")
        prev_out = o

    if now - last_print >= 0.5:               # Stage 2: half-second status line
        last_print = now
        if latest:
            st, mcm, me, scm, se, dcm = latest
            print("OUT=%d  %-13s moving %3d cm (e%3d)   static %3d cm (e%3d)   "
                  "detect %3d cm   [%d frames]"
                  % (o, STATES.get(st, "?"), mcm, me, scm, se, dcm, frames))
        elif now - last_frame > 3:
            print("OUT=%d  no UART frames yet - check TX/RX are crossed "
                  "(radar TX -> GPIO13), baud 256000, and 5 V on VCC" % o)
    time.sleep(0.01)
