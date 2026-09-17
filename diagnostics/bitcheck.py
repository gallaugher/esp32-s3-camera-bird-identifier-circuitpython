# OV5640 data-line integrity check for the YD-ESP32-S3 (CircuitPython 10.x)
# Copy to CIRCUITPY as code.py. Nothing is saved. Runs ~1 minute.
#
# Self-healing: if the camera driver fails during init (IDFError 0x20002) it
# leaves the pins claimed, so this script RELOADS ITSELF and resumes where it
# was, remembering its place in microcontroller.nvm. Expect to see
# "soft reboot" once or twice in the console - that's normal.
#
# Test A - bit census on a real grayscale frame: bits 0-4 should be ~50% set.
#          0% or 100% = that data line is stuck / not connected.
# Test B - sensor color-bar pattern in grayscale: 8 bars stepping DOWN L->R.
# Test C - JPEG capture with the data pins re-ordered in software: as wired,
#          each neighbouring-bit swap, and full reversal.

import time
import board
import busio
import microcontroller
import supervisor
import espcamera

SDA, SCL = board.GPIO8, board.GPIO9
XCLK, PCLK, VSYNC, HREF = board.GPIO4, board.GPIO5, board.GPIO10, board.GPIO11
DATA = [board.GPIO2, board.GPIO42, board.GPIO41, board.GPIO40,
        board.GPIO39, board.GPIO38, board.GPIO47, board.GPIO21]   # D2..D9 as wired
RESET_PIN = board.GPIO7       # RT wire added 9/16; None to disable
I2C_HZ = 50_000               # slower SCCB/I2C = more tolerant of noisy jumpers
FRESH_START = False           # True = ignore any saved progress and start over
MAX_INIT_FAILURES = 8

PF = espcamera.PixelFormat
FS = espcamera.FrameSize

# ---- persistent progress (survives supervisor.reload) ------------------
# nvm layout: [0]=magic [1]=init failures [2]=stage (0=A/B, 1=C, 2=done)
#             [3]=next Test C index [4..12]=Test C results (0 none,1 init fail,2 no frames,3 OK)
MAGIC = 0xB7
nvm = microcontroller.nvm

ORDER_LABELS = ["as wired"] + ["swap D%d<->D%d" % (b + 2, b + 3) for b in range(7)] + ["reversed"]
N_ORDERS = len(ORDER_LABELS)


def order_for(idx):
    if idx == 0:
        return list(range(8))
    if idx == N_ORDERS - 1:
        return list(range(7, -1, -1))
    b = idx - 1
    o = list(range(8))
    o[b], o[b + 1] = o[b + 1], o[b]
    return o


def load_state():
    if FRESH_START or nvm[0] != MAGIC:
        return {"fails": 0, "stage": 0, "cidx": 0, "cres": [0] * N_ORDERS}
    return {"fails": nvm[1], "stage": nvm[2], "cidx": nvm[3],
            "cres": list(nvm[4:4 + N_ORDERS])}


def save_state(st):
    nvm[0:4 + N_ORDERS] = bytes([MAGIC, st["fails"], st["stage"], st["cidx"]] + st["cres"])


def clear_state():
    nvm[0] = 0


st = load_state()
if st["stage"] or st["fails"]:
    print("\n(resuming: stage %d, Test C index %d, %d init failures so far)"
          % (st["stage"], st["cidx"], st["fails"]))

if st["fails"] >= MAX_INIT_FAILURES:
    print("\nToo many camera init failures (%d). Stopping so this doesn't loop forever."
          % st["fails"])
    print("Check 3V/G/SDA/SCL seating, keep the XC wire away from SDA/SCL, then run again.")
    clear_state()
    raise SystemExit

i2c = busio.I2C(SCL, SDA, frequency=I2C_HZ)


def open_camera_or_reload(pixel_format, frame_size, data_pins, xclk=20_000_000):
    """Create the camera. On init failure: record it and reload the board,
    because a failed init leaves the GPIOs claimed until the next reload."""
    try:
        return espcamera.Camera(
            data_pins=data_pins, pixel_clock_pin=PCLK, vsync_pin=VSYNC,
            href_pin=HREF, i2c=i2c, external_clock_pin=XCLK,
            external_clock_frequency=xclk, reset_pin=RESET_PIN,
            pixel_format=pixel_format, frame_size=frame_size,
            jpeg_quality=12, framebuffer_count=1,
        )
    except Exception as e:  # pylint: disable=broad-except
        st["fails"] += 1
        save_state(st)
        print("  camera init failed (%r) - reloading to free the pins and retry (%d/%d)"
              % (e, st["fails"], MAX_INIT_FAILURES))
        time.sleep(1.0)
        supervisor.reload()
        while True:            # reload is asynchronous; park here until it happens
            time.sleep(0.1)


def get_frame(cam, tries=4, timeout=1.5):
    for _ in range(tries):
        f = cam.take(timeout)
        if f is not None:
            return f
    return None


# ================================================================ Tests A + B
if st["stage"] == 0:
    print("\n=== Test A: bit census, grayscale QVGA, real scene ===")
    cam = open_camera_or_reload(PF.GRAYSCALE, FS.QVGA, DATA)
    for _ in range(3):
        cam.take(1)                     # let exposure settle
    frame = get_frame(cam)
    if frame is None:
        print("  no grayscale frame?! (this worked before - check power/wires)")
    else:
        w, h = frame.width, frame.height
        counts = [0] * 8
        n = 0
        for y in range(0, h, 3):
            for x in range(0, w, 3):
                v = frame[x, y]
                n += 1
                for b in range(8):
                    if v & (1 << b):
                        counts[b] += 1
        print("  %d pixels sampled" % n)
        for b in range(8):
            pct = 100 * counts[b] // n
            flag = "   <-- STUCK? (camera D%d wire)" % (b + 2) if (pct <= 2 or pct >= 98) else ""
            print("  bit %d (camera D%d): %3d%% set%s" % (b, b + 2, pct, flag))
        print("  (bits 0-4 should sit near 50%; bits 5-7 depend on scene brightness)")

    print("\n=== Test B: sensor color bars, grayscale QVGA ===")
    try:
        cam.colorbar = True
        for _ in range(3):
            cam.take(1)
        frame = get_frame(cam)
        if frame is None:
            print("  no frame with colorbar on")
        else:
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
            print("  bar averages left->right:", vals)
            steps_down = all(vals[i] > vals[i + 1] for i in range(7))
            print("  monotonic staircase:", "YES" if steps_down else "NO  <-- bit order problem likely")
            for i, v in enumerate(vals):
                print("    bar %d: %3d  %s" % (i, v, "{:08b}".format(v)))
        cam.colorbar = False
    except Exception as e:  # pylint: disable=broad-except
        print("  colorbar test skipped:", repr(e))
    cam.deinit()
    time.sleep(0.5)
    st["stage"] = 1
    save_state(st)

# ================================================================ Test C
if st["stage"] == 1:
    print("\n=== Test C: JPEG QVGA with software pin re-ordering ===")
    while st["cidx"] < N_ORDERS:
        idx = st["cidx"]
        label = ORDER_LABELS[idx]
        pins = [DATA[i] for i in order_for(idx)]
        print(label)
        cam = open_camera_or_reload(PF.JPEG, FS.QVGA, pins)   # reloads on failure, idx unchanged
        good = 0
        size = 0
        for _ in range(3):
            f = cam.take(1.5)
            if f is not None:
                n = len(f)
                if bytes(f[0:2]) == b"\xff\xd8" and bytes(f[n - 2:n]) == b"\xff\xd9":
                    good += 1
                    size = n
        print("    -> %d/3 valid JPEG%s" % (good, (" (%d bytes)" % size) if good else ""))
        st["cres"][idx] = 3 if good else 2
        st["cidx"] = idx + 1
        save_state(st)
        cam.deinit()
        time.sleep(0.5)
    st["stage"] = 2
    save_state(st)

# ================================================================ Summary
if st["stage"] == 2:
    print("\n==== Test C summary ====")
    words = {0: "not run", 1: "init failed", 2: "no valid frames", 3: "VALID JPEG  <-- works"}
    for i, label in enumerate(ORDER_LABELS):
        print("  %-16s %s" % (label, words.get(st["cres"][i], "?")))
    print("  (%d camera init failure(s) were retried along the way)" % st["fails"])
    clear_state()
    print("\nDone. Send back Tests A, B and this summary.")
