# Bird cam: radar-triggered camera with Wi-Fi viewer  (CircuitPython 10.x)
# YD-ESP32-S3 N16R8 + Adafruit OV5640 (5840) + HLK-LD2410C mmWave radar
# Copy to CIRCUITPY as code.py.  No photo-mode wire needed (photos live in RAM).
#
# Needs: settings.toml with CIRCUITPY_WIFI_SSID / CIRCUITPY_WIFI_PASSWORD
#        lib/adafruit_httpserver/  (from the CircuitPython 10 bundle)
#
# Behaviour: the radar reports ~10x/sec. When it sees a MOVING target with
# energy >= TRIGGER_ENERGY inside the distance window, and the cooldown has
# passed, the camera takes a photo. The web page shows the newest photo, live
# radar numbers, and has Take-photo and Arm/Disarm buttons. BOOT button = shutter.

import os
import time
import board
import busio
import digitalio
import microcontroller
import supervisor
import wifi
import socketpool
import espcamera
from adafruit_httpserver import Server, Request, Response, JSONResponse, GET

# ---- camera wiring ----------------------------------------------------
SDA, SCL = board.GPIO8, board.GPIO9
XCLK, PCLK, VSYNC, HREF = board.GPIO4, board.GPIO5, board.GPIO10, board.GPIO11
DATA = [board.GPIO2, board.GPIO42, board.GPIO41, board.GPIO40,
        board.GPIO39, board.GPIO38, board.GPIO47, board.GPIO21]   # D2..D9
RESET_PIN = board.GPIO7

# ---- radar wiring -----------------------------------------------------
RADAR_OUT = board.GPIO14      # purple
RADAR_TX = board.GPIO12       # board TX -> radar RX (blue)
RADAR_RX = board.GPIO13       # board RX <- radar TX (green)

# ---- tuning -----------------------------------------------------------
FRAME_SIZE = espcamera.FrameSize.SVGA
JPEG_QUALITY = 10
FLIP_180 = False
HOSTNAME = "birdcam"

TRIGGER_ENERGY = 40           # moving-target energy 0-100 needed to fire (hand wave ~50-100)
MIN_CM, MAX_CM = 0, 300       # only fire for moving targets in this distance window
COOLDOWN_S = 6                # minimum seconds between radar-triggered photos
ARMED_AT_BOOT = True          # radar trigger on/off at start (toggle on the web page)
OUT_FALLBACK = True           # if no serial frames arrive, trigger on the radar's OUT pin instead
UART_SILENT_S = 3             # seconds without frames before falling back to OUT

# ---- camera init, self-reload on the init flake -----------------------
i2c = busio.I2C(SCL, SDA, frequency=50_000)
try:
    cam = espcamera.Camera(
        data_pins=DATA, pixel_clock_pin=PCLK, vsync_pin=VSYNC, href_pin=HREF,
        i2c=i2c, external_clock_pin=XCLK, external_clock_frequency=20_000_000,
        reset_pin=RESET_PIN, pixel_format=espcamera.PixelFormat.JPEG,
        frame_size=FRAME_SIZE, jpeg_quality=JPEG_QUALITY, framebuffer_count=1,
    )
except Exception as e:  # pylint: disable=broad-except
    n = microcontroller.nvm[23]
    n = 0 if n > 10 else n
    if n >= 4:
        microcontroller.nvm[23] = 0
        print("Camera init keeps failing (%r). Check wiring and try again." % e)
        raise SystemExit
    microcontroller.nvm[23] = n + 1
    print("camera init failed (%r) - reloading to free pins, try %d/4" % (e, n + 1))
    time.sleep(1)
    supervisor.reload()
    while True:
        time.sleep(0.1)
microcontroller.nvm[23] = 0
cam.vflip = FLIP_180
cam.hmirror = FLIP_180
print("Camera ready:", cam.width, "x", cam.height, "JPEG q%d" % JPEG_QUALITY)

# ---- radar ------------------------------------------------------------
radar_out = digitalio.DigitalInOut(RADAR_OUT)
radar_out.switch_to_input(pull=digitalio.Pull.DOWN)
uart = busio.UART(RADAR_TX, RADAR_RX, baudrate=256000, timeout=0.01,
                  receiver_buffer_size=1024)
HEADER = b"\xF4\xF3\xF2\xF1"
FOOTER = b"\xF8\xF7\xF6\xF5"
STATES = {0: "none", 1: "MOVING", 2: "static", 3: "MOVING+static"}
radar = {"state": 0, "mov_cm": 0, "mov_e": 0, "sta_cm": 0, "sta_e": 0, "det_cm": 0,
         "frames": 0, "out": 0, "last_frame_t": -1000.0}
ubuf = b""


def parse_reports(buf):
    reports = []
    while True:
        i = buf.find(HEADER)
        if i < 0:
            return reports, buf[-3:]
        buf = buf[i:]
        if len(buf) < 6:
            return reports, buf
        length = buf[4] | (buf[5] << 8)
        total = 6 + length + 4
        if len(buf) < total:
            return reports, buf
        frame, buf = buf[:total], buf[total:]
        if frame[-4:] != FOOTER:
            continue
        d = frame[6:6 + length]
        if length >= 13 and d[0] == 0x02 and d[1] == 0xAA:
            reports.append((d[2], d[3] | (d[4] << 8), d[5],
                            d[6] | (d[7] << 8), d[8], d[9] | (d[10] << 8)))


def poll_radar():
    global ubuf
    n = uart.in_waiting
    if n:
        ubuf += uart.read(n)
        reports, ubuf = parse_reports(ubuf)
        if reports:
            st, mcm, me, scm, se, dcm = reports[-1]
            radar.update(state=st, mov_cm=mcm, mov_e=me, sta_cm=scm, sta_e=se, det_cm=dcm)
            radar["frames"] += len(reports)
            radar["last_frame_t"] = time.monotonic()
    radar["out"] = 1 if radar_out.value else 0


def uart_alive():
    return time.monotonic() - radar["last_frame_t"] < UART_SILENT_S


# ---- status light (onboard RGB LED) -----------------------------------
try:
    import neopixel
    led = neopixel.NeoPixel(board.NEOPIXEL, 1, brightness=0.2)
except Exception:  # pylint: disable=broad-except
    led = None


def light(color):
    if led:
        led[0] = color


# ---- Wi-Fi ------------------------------------------------------------
print("Board Wi-Fi MAC address:", ":".join("%02X" % b for b in wifi.radio.mac_address),
      "(for network registration)")
if not wifi.radio.connected:
    ssid = os.getenv("CIRCUITPY_WIFI_SSID")
    pw = os.getenv("CIRCUITPY_WIFI_PASSWORD") or ""
    if not ssid:
        print("No CIRCUITPY_WIFI_SSID in settings.toml - cannot start the viewer.")
        raise SystemExit
    print("Connecting to Wi-Fi '%s' ..." % ssid)
    for attempt in range(5):
        try:
            wifi.radio.connect(ssid, pw)
            break
        except ConnectionError as e:
            print("  attempt %d failed: %s" % (attempt + 1, e))
            time.sleep(2)
if not wifi.radio.connected:
    print("Wi-Fi connection failed - check SSID/password (WPA2-Personal network).")
    raise SystemExit
ip = str(wifi.radio.ipv4_address)
print("Wi-Fi connected, IP", ip)
try:
    import mdns
    m = mdns.Server(wifi.radio)
    m.hostname = HOSTNAME
    m.advertise_service(service_type="_http", protocol="_tcp", port=80)
except Exception as e:  # pylint: disable=broad-except
    print("mDNS not available (%r) - use the IP address" % e)

# ---- photo state ------------------------------------------------------
latest = b""
photo_count = 0
last_shot = -1000.0
last_reason = "none yet"
armed = ARMED_AT_BOOT
shutter = digitalio.DigitalInOut(board.BOOT)
shutter.switch_to_input(pull=digitalio.Pull.UP)


def take_photo(reason, warmup=2):
    """warmup=2: with one frame buffer the first frame is stale (captured right
    after the previous take), so always discard at least one."""
    global latest, photo_count, last_shot, last_reason
    light((255, 255, 255))
    for _ in range(warmup):
        cam.take(1)
    frame = cam.take(1)
    if frame is None:
        print("  no frame from camera (timeout)")
        light((255, 0, 0))
        return False
    latest = bytes(frame)
    photo_count += 1
    last_shot = time.monotonic()
    last_reason = reason
    print("  photo #%d (%s), %d bytes" % (photo_count, reason, len(latest)))
    light((0, 255, 0) if armed else (0, 0, 40))
    return True


def status_dict():
    return {"count": photo_count, "bytes": len(latest), "reason": last_reason,
            "armed": armed, "state": STATES.get(radar["state"], "?"),
            "mov_cm": radar["mov_cm"], "mov_e": radar["mov_e"],
            "sta_cm": radar["sta_cm"], "sta_e": radar["sta_e"],
            "out": radar["out"], "frames": radar["frames"], "uart": uart_alive(),
            "since": int(time.monotonic() - last_shot) if photo_count else -1}


# ---- web page ---------------------------------------------------------
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bird cam</title>
<style>body{font-family:-apple-system,Helvetica,sans-serif;margin:16px;background:#111;color:#eee;text-align:center}
img{max-width:100%;border-radius:8px;background:#000}
button{font-size:1.1em;padding:.5em 1.1em;margin:.4em;border-radius:8px;border:0;color:#fff}
#snap{background:#3a7}#arm{background:#a63}#s,#r{min-height:1.4em}#r{color:#9cf;font-family:Menlo,monospace}</style></head><body>
<h2>Bird cam</h2>
<div><button id="snap" onclick="snap()">Take photo</button><button id="arm" onclick="arm()">Arm/Disarm radar</button></div>
<div id="r">radar: waiting...</div><div id="s">no photo yet</div>
<img id="p" src="/photo.jpg?t=0" alt="no photo yet">
<script>
var n=-1;
function refresh(){document.getElementById('p').src='/photo.jpg?t='+Date.now();}
function show(j){document.getElementById('r').textContent=(j.uart?('radar: '+j.state+'  moving '+j.mov_cm+' cm (e'+j.mov_e+')  static '+j.sta_cm+' cm (e'+j.sta_e+')'):'radar serial SILENT - using OUT pin')+'  OUT='+j.out+'  '+(j.armed?'ARMED':'disarmed');
 document.getElementById('s').textContent=j.count?('photo #'+j.count+' ('+j.reason+'), '+j.bytes+' bytes, '+j.since+' s ago'):'no photo yet';
 if(j.count!=n){n=j.count;refresh();}}
function snap(){fetch('/snap').then(r=>r.json()).then(show);}
function arm(){fetch('/arm').then(r=>r.json()).then(show);}
setInterval(function(){fetch('/status').then(r=>r.json()).then(show).catch(function(){});},1000);
</script></body></html>"""

pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, debug=False)


@server.route("/", GET)
def index(request: Request):
    return Response(request, PAGE, content_type="text/html")


@server.route("/photo.jpg", GET)
def photo(request: Request):
    if not latest:
        return Response(request, "no photo yet", status=(404, "Not Found"))
    return Response(request, latest, content_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@server.route("/snap", GET)
def snap(request: Request):
    take_photo("web button")
    return JSONResponse(request, status_dict())


@server.route("/arm", GET)
def arm(request: Request):
    global armed
    armed = not armed
    light((0, 255, 0) if armed else (0, 0, 40))
    print("radar trigger", "ARMED" if armed else "disarmed")
    return JSONResponse(request, status_dict())


@server.route("/status", GET)
def status(request: Request):
    return JSONResponse(request, status_dict())


port = 80
try:
    server.start(ip, port=port)
except OSError:
    port = 8080
    server.start(ip, port=port)
suffix = "" if port == 80 else ":%d" % port
print("\nOpen  http://%s%s   or  http://%s.local%s" % (ip, suffix, HOSTNAME, suffix))
print("Radar trigger: energy>=%d, %d-%d cm, cooldown %ds, %s\n"
      % (TRIGGER_ENERGY, MIN_CM, MAX_CM, COOLDOWN_S, "ARMED" if armed else "disarmed"))

take_photo("startup", warmup=6)
light((0, 255, 0) if armed else (0, 0, 40))

was_pressed = False
last_radar_print = 0.0
prev_out = radar_out.value
while True:
    try:
        server.poll()
    except Exception as e:  # pylint: disable=broad-except
        print("server error:", repr(e))

    poll_radar()
    now = time.monotonic()
    alive = uart_alive()

    # radar trigger - serial frames (selective) or OUT pin rising edge (fallback)
    if alive:
        moving = radar["state"] in (1, 3)
        if (armed and moving and radar["mov_e"] >= TRIGGER_ENERGY
                and MIN_CM <= radar["mov_cm"] <= MAX_CM
                and now - last_shot >= COOLDOWN_S):
            print("RADAR TRIGGER: moving %d cm, energy %d" % (radar["mov_cm"], radar["mov_e"]))
            take_photo("radar %dcm e%d" % (radar["mov_cm"], radar["mov_e"]))
    elif OUT_FALLBACK:
        out_now = radar["out"] == 1
        if armed and out_now and not prev_out and now - last_shot >= COOLDOWN_S:
            print("RADAR TRIGGER (OUT pin)")
            take_photo("radar OUT")
        prev_out = out_now

    # BOOT button shutter
    pressed = not shutter.value
    if pressed and not was_pressed:
        print("click!")
        take_photo("BOOT button")
        time.sleep(0.2)
    was_pressed = pressed

    # console heartbeat every 2 s
    if now - last_radar_print >= 2:
        last_radar_print = now
        if alive:
            print("radar %-13s moving %3d cm e%3d  static %3d cm e%3d  OUT=%d  [%d frames] %s"
                  % (STATES.get(radar["state"], "?"), radar["mov_cm"], radar["mov_e"],
                     radar["sta_cm"], radar["sta_e"], radar["out"], radar["frames"],
                     "ARMED" if armed else "disarmed"))
        else:
            print("radar serial SILENT (check blue wire on radar TX -> GPIO13) - using OUT pin, OUT=%d  %s"
                  % (radar["out"], "ARMED" if armed else "disarmed"))
    time.sleep(0.01)
