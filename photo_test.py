# OV5640 JPEG photo test for the YD-ESP32-S3 N16R8 (CircuitPython 10.x) - v2
# Copy to CIRCUITPY as code.py.  Needs boot.py + the GPIO6->GND "photo mode" wire
# so CircuitPython is allowed to write files.
#
# Takes one photo a few seconds after boot, then another every time you press
# the board's BOOT button.  Photos land in /photos/photo_NNN.jpg on CIRCUITPY.
# (Finder shows CIRCUITPY read-only while in photo mode; if a new file doesn't
# appear right away, eject and re-plug.)
#
# v2: uses the RT reset wire (GPIO7), 50 kHz I2C, and reloads itself if the
# camera init flake (IDFError 0x20002) strikes.

import os
import time
import board
import busio
import digitalio
import microcontroller
import supervisor
import espcamera

# ---- camera wiring ----------------------------------------------------
SDA, SCL = board.GPIO8, board.GPIO9
XCLK, PCLK, VSYNC, HREF = board.GPIO4, board.GPIO5, board.GPIO10, board.GPIO11
DATA = [board.GPIO2, board.GPIO42, board.GPIO41, board.GPIO40,
        board.GPIO39, board.GPIO38, board.GPIO47, board.GPIO21]   # D2..D9
RESET_PIN = board.GPIO7

# ---- photo settings ---------------------------------------------------
FRAME_SIZE = espcamera.FrameSize.SVGA   # 800x600. Also: XGA 1024x768, HD 1280x720, UXGA 1600x1200
JPEG_QUALITY = 10                        # 0 (best) .. 63 (worst); 10-15 is a good range
WARMUP_FRAMES = 8                        # let auto-exposure/white-balance settle before saving
MIRROR = False                           # a camera looking out a window is a window, not a mirror
PHOTO_DIR = "/photos"

# ---- camera init, with self-reload on the init flake ------------------
i2c = busio.I2C(SCL, SDA, frequency=50_000)
try:
    cam = espcamera.Camera(
        data_pins=DATA, pixel_clock_pin=PCLK, vsync_pin=VSYNC, href_pin=HREF,
        i2c=i2c, external_clock_pin=XCLK, external_clock_frequency=20_000_000,
        reset_pin=RESET_PIN,
        pixel_format=espcamera.PixelFormat.JPEG, frame_size=FRAME_SIZE,
        jpeg_quality=JPEG_QUALITY, framebuffer_count=1,
    )
except Exception as e:  # pylint: disable=broad-except
    n = microcontroller.nvm[21]
    n = 0 if n > 10 else n
    if n >= 4:
        microcontroller.nvm[21] = 0
        print("Camera init keeps failing (%r). Check wiring and try again." % e)
        raise SystemExit
    microcontroller.nvm[21] = n + 1
    print("camera init failed (%r) - reloading to free pins, try %d/4" % (e, n + 1))
    time.sleep(1)
    supervisor.reload()
    while True:
        time.sleep(0.1)
microcontroller.nvm[21] = 0

cam.hmirror = MIRROR
print("Camera ready:", cam.width, "x", cam.height, "JPEG q%d" % JPEG_QUALITY)

shutter = digitalio.DigitalInOut(board.BOOT)   # onboard BOOT button, LOW when pressed
shutter.switch_to_input(pull=digitalio.Pull.UP)

# ---- filesystem check --------------------------------------------------
writable = True
try:
    os.mkdir(PHOTO_DIR)
except OSError as e:
    if getattr(e, "errno", None) == 30 or "Read-only" in str(e):
        writable = False
    # otherwise the folder already exists - fine
if not writable:
    print("CIRCUITPY is READ-ONLY: photos will not save.")
    print("  Put the GPIO6 -> GND wire in and press RST (boot.py must be present).")
else:
    print("Photo mode: CIRCUITPY is writable. Photos go to", PHOTO_DIR)


def next_filename():
    try:
        names = os.listdir(PHOTO_DIR)
    except OSError:
        names = []
    used = [int(n[6:9]) for n in names
            if n.startswith("photo_") and n.endswith(".jpg") and n[6:9].isdigit()]
    return "%s/photo_%03d.jpg" % (PHOTO_DIR, (max(used) + 1) if used else 1)


def take_photo():
    for _ in range(WARMUP_FRAMES):           # discard frames while exposure settles
        cam.take(1)
    frame = cam.take(1)
    if frame is None:
        print("  no frame from camera (timeout)")
        return
    n = len(frame)
    ok = bytes(frame[0:2]) == b"\xff\xd8" and bytes(frame[n - 2:n]) == b"\xff\xd9"
    print("  got %d-byte JPEG%s" % (n, "" if ok else " (marker check FAILED)"))
    if not writable:
        return
    name = next_filename()
    try:
        with open(name, "wb") as f:
            f.write(frame)
    except OSError as e:
        print("  could not write %s: %s" % (name, e))
        return
    print("  saved", name)


print("First photo in 3 seconds - point the camera at something...")
time.sleep(3)
take_photo()
print("Press BOOT for another photo.")

was_pressed = False
while True:
    pressed = not shutter.value
    if pressed and not was_pressed:
        print("click!")
        take_photo()
        time.sleep(0.3)   # debounce
    was_pressed = pressed
    time.sleep(0.02)
