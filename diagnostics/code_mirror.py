# OV5640 camera smoke test for the YD-ESP32-S3 N16R8 (CircuitPython 10.x)
# Adafruit OV5640 breakout (5840) wired per the project's camera-prep doc.
#
# Stage 1: camera init  -> proves 3V, G, SDA, SCL and XC
# Stage 2: I2C scan     -> with XCLK now running, the OV5640 answers at 0x3C
#                          (it stays silent until it has a clock, so a scan
#                          BEFORE init comes back empty - that is normal)
# Stage 3: ASCII mirror -> proves PC, VS, HS and all eight data lines
#
# Open the serial console (REPL) and wave your hand in front of the lens.

import time
import board
import busio
import espcamera

# ---- Wiring (camera label -> board pin) -------------------------------
SDA = board.GPIO8     # SDA  (course STEMMA QT bus, blue)
SCL = board.GPIO9     # SCL  (course STEMMA QT bus, yellow)

XCLK = board.GPIO4    # XC   clock OUT to the camera
PCLK = board.GPIO5    # PC   pixel clock back from the camera
VSYNC = board.GPIO10  # VS
HREF = board.GPIO11   # HS

DATA = [              # D2..D9 in order, least-significant first
    board.GPIO2,   # D2
    board.GPIO42,  # D3
    board.GPIO41,  # D4
    board.GPIO40,  # D5
    board.GPIO39,  # D6
    board.GPIO38,  # D7
    board.GPIO47,  # D8
    board.GPIO21,  # D9
]
# 3V -> 3V3, G -> GND.  PD and RT stay unconnected (onboard pull resistors).

# ---- ASCII mirror settings --------------------------------------------
FRAME_SIZE = espcamera.FrameSize.QQVGA  # 160 x 120
COL_STEP = 2   # 160 / 2 = 80 characters wide (fits an 80-column console)
ROW_STEP = 4   # 120 / 4 = 30 lines tall (character cells are ~2:1, so 2:4 keeps the aspect)
MIRROR = True  # True = behaves like a mirror; False = behaves like a window
RAMP = b" .:-=+*#%@"  # dark -> bright

# ---- Stage 1: camera init ---------------------------------------------
print("\nStage 1: initializing camera (starts the XCLK clock) ...")
i2c = busio.I2C(SCL, SDA)
try:
    cam = espcamera.Camera(
        data_pins=DATA,
        pixel_clock_pin=PCLK,
        vsync_pin=VSYNC,
        href_pin=HREF,
        i2c=i2c,
        external_clock_pin=XCLK,
        external_clock_frequency=20_000_000,  # drop to 10_000_000 if frames time out
        pixel_format=espcamera.PixelFormat.GRAYSCALE,
        frame_size=FRAME_SIZE,
        framebuffer_count=2,
        grab_mode=espcamera.GrabMode.LATEST,
    )
except Exception as e:  # pylint: disable=broad-except
    print("  Camera init FAILED:", repr(e))
    print("  Check, in this order: 3V and G, then SDA/SCL, then XC.")
    raise

cam.hmirror = MIRROR
print("  Camera ready:", cam.width, "x", cam.height, "grayscale")

# ---- Stage 2: I2C scan (informational) --------------------------------
print("\nStage 2: I2C scan with the camera clock running ...")
while not i2c.try_lock():
    pass
found = i2c.scan()
i2c.unlock()
print("  devices:", [hex(a) for a in found])
if 0x3C in found:
    print("  OV5640 answered at 0x3C, as expected.")
else:
    print("  0x3C not listed. If the mirror below works, ignore this line.")

# ---- Stage 3: ASCII mirror --------------------------------------------
print("\nStage 3: ASCII mirror. Wave at the lens. Ctrl-C to stop.\n")
lut = bytes(RAMP[v * (len(RAMP) - 1) // 255] for v in range(256))
cols = cam.width // COL_STEP
line = bytearray(cols)
misses = 0

while True:
    frame = cam.take(1)
    if frame is None:
        misses += 1
        print("  no frame (timeout) x", misses,
              "- check PC, VS, HS and the eight D lines")
        if misses >= 5:
            print("  Still nothing. Try external_clock_frequency=10_000_000,")
            print("  reseat the D2-D9 jumpers, and confirm D2..D9 order.")
            misses = 0
        continue
    misses = 0
    out = []
    for y in range(0, cam.height, ROW_STEP):
        for i in range(cols):
            line[i] = lut[frame[i * COL_STEP, y]]
        out.append(bytes(line).decode())
    print("\n".join(out))
    print("-" * cols)
    time.sleep(0.5)
