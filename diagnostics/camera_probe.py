# OV5640 capture-mode probe for the YD-ESP32-S3 N16R8 (CircuitPython 10.x)
# Copy to CIRCUITPY as code.py. No photo-mode wire needed - nothing is saved.
#
# Tries a ladder of clock / pixel-format / frame-size combinations and prints
# which ones deliver frames over your jumper wiring. Takes about a minute.
# Send the printed table back and we pick the working configuration.

import time
import board
import busio
import espcamera

# ---- camera wiring (same as before) -----------------------------------
SDA, SCL = board.GPIO8, board.GPIO9
XCLK, PCLK, VSYNC, HREF = board.GPIO4, board.GPIO5, board.GPIO10, board.GPIO11
DATA = [board.GPIO2, board.GPIO42, board.GPIO41, board.GPIO40,
        board.GPIO39, board.GPIO38, board.GPIO47, board.GPIO21]   # D2..D9
RESET_PIN = None        # set to board.GPIO7 once the RT wire is added

PF = espcamera.PixelFormat
FS = espcamera.FrameSize

# (xclk_hz, pixel_format, frame_size, label)
TESTS = [
    (20_000_000, PF.GRAYSCALE, FS.QQVGA, "20 MHz  GRAY  QQVGA 160x120  (known good)"),
    (20_000_000, PF.GRAYSCALE, FS.VGA,   "20 MHz  GRAY  VGA   640x480"),
    (20_000_000, PF.GRAYSCALE, FS.SVGA,  "20 MHz  GRAY  SVGA  800x600"),
    (20_000_000, PF.JPEG,      FS.QVGA,  "20 MHz  JPEG  QVGA  320x240"),
    (20_000_000, PF.JPEG,      FS.VGA,   "20 MHz  JPEG  VGA   640x480"),
    (20_000_000, PF.JPEG,      FS.SVGA,  "20 MHz  JPEG  SVGA  800x600"),
    (10_000_000, PF.JPEG,      FS.QVGA,  "10 MHz  JPEG  QVGA  320x240"),
    (10_000_000, PF.JPEG,      FS.VGA,   "10 MHz  JPEG  VGA   640x480"),
    (10_000_000, PF.JPEG,      FS.SVGA,  "10 MHz  JPEG  SVGA  800x600"),
    (10_000_000, PF.JPEG,      FS.XGA,   "10 MHz  JPEG  XGA  1024x768"),
]
FRAMES_PER_TEST = 5
FRAME_TIMEOUT = 2      # seconds to wait for each frame

i2c = busio.I2C(SCL, SDA)


def open_camera(xclk, pixel_format, frame_size):
    """Create the camera, retrying the intermittent init failure (0x20002)."""
    last = None
    for attempt in range(3):
        try:
            return espcamera.Camera(
                data_pins=DATA, pixel_clock_pin=PCLK, vsync_pin=VSYNC, href_pin=HREF,
                i2c=i2c, external_clock_pin=XCLK, external_clock_frequency=xclk,
                reset_pin=RESET_PIN,
                pixel_format=pixel_format, frame_size=frame_size,
                jpeg_quality=10, framebuffer_count=1,
            )
        except Exception as e:  # pylint: disable=broad-except
            last = e
            print("    init attempt %d failed: %r" % (attempt + 1, e))
            time.sleep(0.5)
    raise last


def describe(frame, pixel_format):
    if pixel_format == PF.JPEG:
        n = len(frame)
        b = bytes(frame[0:2]) + bytes(frame[n - 2:n])
        ok = b[:2] == b"\xff\xd8" and b[2:] == b"\xff\xd9"
        return "%6d bytes %s" % (len(frame), "valid JPEG" if ok else "BAD markers")
    return "%dx%d bitmap" % (frame.width, frame.height)


results = []
print("\nOV5640 probe: %d frames per test, %ds timeout each\n" % (FRAMES_PER_TEST, FRAME_TIMEOUT))

for xclk, pf, fs, label in TESTS:
    print(label)
    try:
        cam = open_camera(xclk, pf, fs)
    except Exception as e:  # pylint: disable=broad-except
        print("    INIT FAILED: %r" % e)
        results.append((label, "init failed"))
        continue

    got = 0
    note = ""
    t0 = time.monotonic()
    for _ in range(FRAMES_PER_TEST):
        frame = cam.take(FRAME_TIMEOUT)
        if frame is not None:
            got += 1
            note = describe(frame, pf)
    dt = time.monotonic() - t0
    verdict = "%d/%d frames in %.1fs  %s" % (got, FRAMES_PER_TEST, dt, note)
    print("    ->", verdict)
    results.append((label, verdict))

    cam.deinit()
    time.sleep(0.5)

print("\n==== SUMMARY ====")
for label, verdict in results:
    print("%-42s %s" % (label, verdict))
print("\nDone. Send this table back.")
