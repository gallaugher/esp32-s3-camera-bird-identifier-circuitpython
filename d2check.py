# Quick data-line check for the OV5640 on the YD-ESP32-S3 (CircuitPython 10.x)
# Copy to CIRCUITPY as code.py. ~10 seconds. Nothing is saved.
# Reports each camera data line as OK / STUCK HIGH / STUCK LOW using the
# sensor's built-in color-bar pattern, so lighting doesn't matter.

import time
import board
import busio
import microcontroller
import supervisor
import espcamera

SDA, SCL = board.GPIO8, board.GPIO9
XCLK, PCLK, VSYNC, HREF = board.GPIO4, board.GPIO5, board.GPIO10, board.GPIO11
DATA = [board.GPIO2, board.GPIO42, board.GPIO41, board.GPIO40,
        board.GPIO39, board.GPIO38, board.GPIO47, board.GPIO21]   # D2..D9
RESET_PIN = board.GPIO7

# Standard color bars as the OV5640 emits them in grayscale (white -> black)
EXPECTED = [255, 240, 198, 182, 78, 62, 18, 4]

i2c = busio.I2C(SCL, SDA, frequency=50_000)

try:
    cam = espcamera.Camera(
        data_pins=DATA, pixel_clock_pin=PCLK, vsync_pin=VSYNC, href_pin=HREF,
        i2c=i2c, external_clock_pin=XCLK, external_clock_frequency=20_000_000,
        reset_pin=RESET_PIN, pixel_format=espcamera.PixelFormat.GRAYSCALE,
        frame_size=espcamera.FrameSize.QVGA, framebuffer_count=1,
    )
except Exception as e:  # pylint: disable=broad-except
    n = microcontroller.nvm[20]
    n = 0 if n > 10 else n
    if n >= 4:
        microcontroller.nvm[20] = 0
        print("Camera init keeps failing (%r). Check 3V/G/SDA/SCL and try again." % e)
        raise SystemExit
    microcontroller.nvm[20] = n + 1
    print("camera init failed (%r) - reloading to free pins, try %d/4" % (e, n + 1))
    time.sleep(1)
    supervisor.reload()
    while True:
        time.sleep(0.1)
microcontroller.nvm[20] = 0

cam.colorbar = True
frame = None
for _ in range(6):
    frame = cam.take(1)
if frame is None:
    print("No frames from the camera - check PC/VS/HS wiring.")
    raise SystemExit

w, h = frame.width, frame.height
y = h // 2
bar_w = w // 8
vals = []
for i in range(8):
    x0 = i * bar_w + bar_w // 4
    x1 = i * bar_w + 3 * bar_w // 4
    s = 0
    for x in range(x0, x1):
        s += frame[x, y]
    vals.append(s // (x1 - x0))

print("\nColor bars read :", vals)
print("Expected        :", EXPECTED)

# Per-bit verdict: compare each bit of each bar to the expected pattern
all_ok = True
for b in range(8):
    ones = sum((v >> b) & 1 for v in vals)
    exp_ones = sum((v >> b) & 1 for v in EXPECTED)
    if ones == 8 and exp_ones != 8:
        verdict = "STUCK HIGH  <-- fix this one"
        all_ok = False
    elif ones == 0 and exp_ones != 0:
        verdict = "STUCK LOW   <-- fix this one"
        all_ok = False
    elif abs(ones - exp_ones) <= 1:
        verdict = "OK"
    else:
        verdict = "odd pattern (%d of 8 bars set, expected %d) - swapped with a neighbour?" % (ones, exp_ones)
        all_ok = False
    print("  bit %d  camera D%d -> %-6s  %s" % (b, b + 2, str(DATA[b]).replace("board.", ""), verdict))

exact = vals == EXPECTED
print()
if all_ok and exact:
    print("ALL EIGHT DATA LINES GOOD - values match exactly. JPEG should work now.")
elif all_ok:
    print("Bits look healthy; values differ slightly from the reference table (fine).")
else:
    print("Fix the flagged line, then Ctrl-D to run this again.")
cam.colorbar = False
cam.deinit()
