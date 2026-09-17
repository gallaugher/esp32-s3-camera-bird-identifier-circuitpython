# Bird cam with AI identification  (CircuitPython 10.x)
# YD-ESP32-S3 N16R8 + Adafruit OV5640 (5840) + HLK-LD2410C mmWave radar
# Copy to CIRCUITPY as code.py.  No photo-mode wire needed (photos live in RAM).
#
# Needs: settings.toml with CIRCUITPY_WIFI_SSID / CIRCUITPY_WIFI_PASSWORD
#                           ANTHROPIC_API_KEY = "sk-ant-..."         (direct mode only)
#                           ANTHROPIC_MODEL = "claude-haiku-4-5"     (optional)
#        lib/adafruit_httpserver/, lib/adafruit_requests.mpy, lib/adafruit_connection_manager.mpy
#        (circup install -a)
#
# Every radar/button/web photo is sent to Claude, which names the bird (or says
# what it sees). The answer shows under the photo on the web page and in the console.
# Two ways to reach Claude - see AI_MODE below:
#   direct  - the board makes the HTTPS call itself (home Wi-Fi / hotspot)
#   helper  - birdcam_helper.py on the Mac makes the call for it (campus networks
#             that block the board's HTTPS, e.g. BostonCollege)
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
import binascii
from adafruit_httpserver import Server, Request, Response, JSONResponse, GET, POST
import adafruit_connection_manager
import adafruit_requests

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
BEACON_PORT = 47777         # UDP "here I am" broadcast every 2 s so birdcam_helper.py can find this board

TRIGGER_ENERGY = 60           # moving-target energy 0-100 needed to fire (hand wave ~50-100); 60 = deliberate waves only
MIN_CM, MAX_CM = 0, 300       # only fire for moving targets in this distance window
COOLDOWN_S = 6                # minimum seconds between radar-triggered photos
ARMED_AT_BOOT = True          # radar trigger on/off at start (toggle on the web page)
OUT_FALLBACK = True           # if no serial frames arrive, trigger on the radar's OUT pin instead
UART_SILENT_S = 3             # seconds without frames before falling back to OUT

AI_ENABLED = True             # send photos to Claude for identification
AI_MODE = "helper"            # "direct": this board calls api.anthropic.com over HTTPS itself
                              #           (needs ANTHROPIC_API_KEY in settings.toml AND a network that
                              #           allows it - home Wi-Fi / phone hotspot; BostonCollege does not)
                              # "helper": the Mac runs birdcam_helper.py, which pulls each new photo from
                              #           this board, asks Claude, and posts the answer back. Works on
                              #           BostonCollege; no key on the board.
HELPER_TIMEOUT_S = 45         # helper mode: give up waiting for an answer after this long
AI_MAX_TOKENS = 120
AI_PROMPT = ("This photo comes from a camera watching a bird feeder through a window. "
             "If a bird or other animal is visible, give its most likely species (common name), "
             "a confidence (high/medium/low), and one short reason. If it is a printed picture or "
             "screen showing a bird, identify the bird anyway and note that it is a printout. "
             "If no animal is visible, say 'No animal detected' and describe the scene in a few words. "
             "Answer in under 40 words, as plain text: no markdown, asterisks, or headings.")

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

# ---- Claude API ---------------------------------------------------------
API_KEY = os.getenv("ANTHROPIC_API_KEY")
MODEL = os.getenv("ANTHROPIC_MODEL") or "claude-haiku-4-5"
requests = None
if AI_ENABLED and AI_MODE == "direct" and API_KEY:
    _pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
    _ssl = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
    requests = adafruit_requests.Session(_pool, _ssl)
    print("AI identification ON (direct), model", MODEL)
elif AI_ENABLED and AI_MODE == "direct":
    print("AI identification OFF: no ANTHROPIC_API_KEY in settings.toml")
elif AI_ENABLED:
    print("AI identification via HELPER: on the Mac run   python3 birdcam_helper.py %s" % ip)
last_ai = "no photo analyzed yet"
ai_pending = False            # helper mode: a photo is waiting for the Mac's answer
ai_pending_since = 0.0


def ai_identify(jpeg):
    """Send the JPEG to Claude; return its one-paragraph answer (or an error string)."""
    if not requests:
        return "AI off"
    b64 = binascii.b2a_base64(jpeg)[:-1].decode()
    body = {
        "model": MODEL,
        "max_tokens": AI_MAX_TOKENS,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": AI_PROMPT},
        ]}],
    }
    headers = {"x-api-key": API_KEY, "anthropic-version": "2023-06-01"}
    t0 = time.monotonic()
    try:
        r = requests.post("https://api.anthropic.com/v1/messages", json=body,
                          headers=headers, timeout=40)
        if r.status_code != 200:
            msg = "AI error %d: %s" % (r.status_code, r.text[:160])
            r.close()
            print("  " + msg)
            return msg
        data = r.json()
        r.close()
        text = " ".join(part.get("text", "") for part in data.get("content", [])
                        if part.get("type") == "text").strip()
        print("  AI (%.1fs): %s" % (time.monotonic() - t0, text))
        return text or "AI returned no text"
    except Exception as e:  # pylint: disable=broad-except
        print("  AI error: %r" % e)
        return "AI error: %r" % e


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
    global latest, photo_count, last_shot, last_reason, last_ai, ai_pending, ai_pending_since
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
    if reason != "startup" and requests:                       # direct mode
        light((0, 0, 255))                 # blue while Claude is thinking
        last_ai = ai_identify(latest)
        last_shot = time.monotonic()       # cooldown counts from the end of the AI call
    elif reason != "startup" and AI_ENABLED and AI_MODE == "helper":
        light((0, 0, 255))                 # blue until the Mac posts the answer to /ai
        last_ai = "identifying... (waiting for birdcam_helper.py on the Mac)"
        if not ai_pending:
            ai_pending_since = time.monotonic()
        ai_pending = True
        return True
    light((0, 255, 0) if armed else (0, 0, 40))
    return True


def status_dict():
    return {"count": photo_count, "bytes": len(latest), "reason": last_reason,
            "armed": armed, "state": STATES.get(radar["state"], "?"),
            "mov_cm": radar["mov_cm"], "mov_e": radar["mov_e"],
            "sta_cm": radar["sta_cm"], "sta_e": radar["sta_e"],
            "out": radar["out"], "frames": radar["frames"], "uart": uart_alive(), "ai": last_ai,
            "ai_pending": ai_pending, "mac": MAC, "ip": ip,
            "since": int(time.monotonic() - last_shot) if photo_count else -1}


# ---- web page ---------------------------------------------------------
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bird cam</title>
<style>body{font-family:-apple-system,Helvetica,sans-serif;margin:16px;background:#111;color:#eee;text-align:center}
img{max-width:100%;border-radius:8px;background:#000}
button{font-size:1.1em;padding:.5em 1.1em;margin:.4em;border-radius:8px;border:0;color:#fff}
#snap{background:#3a7}#arm{background:#a63}#s,#r{min-height:1.4em}#r{color:#9cf;font-family:Menlo,monospace}
#a{font-size:1.3em;color:#fd8;margin:.6em auto;max-width:800px;min-height:1.6em}</style></head><body>
<h2>Bird cam</h2>
<div><button id="snap" onclick="snap()">Take photo</button><button id="arm" onclick="arm()">Arm/Disarm radar</button></div>
<div id="r">radar: waiting...</div><div id="s">no photo yet</div>
<img id="p" src="/photo.jpg?t=0" alt="no photo yet">
<div id="a">Claude: no photo analyzed yet</div>
<script>
var n=-1;
function refresh(){document.getElementById('p').src='/photo.jpg?t='+Date.now();}
function show(j){document.getElementById('r').textContent=(j.uart?('radar: '+j.state+'  moving '+j.mov_cm+' cm (e'+j.mov_e+')  static '+j.sta_cm+' cm (e'+j.sta_e+')'):'radar serial SILENT - using OUT pin')+'  OUT='+j.out+'  '+(j.armed?'ARMED':'disarmed');
 document.getElementById('s').textContent=j.count?('photo #'+j.count+' ('+j.reason+'), '+j.bytes+' bytes, '+j.since+' s ago'):'no photo yet';
 document.getElementById('a').textContent='Claude: '+j.ai;
 if(j.count!=n){n=j.count;refresh();}}
function snap(){fetch('/snap').then(r=>r.json()).then(show);}
function arm(){fetch('/arm').then(r=>r.json()).then(show);}
setInterval(function(){fetch('/status').then(r=>r.json()).then(show).catch(function(){});},1000);
</script></body></html>"""

pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, debug=False)

# ---- discovery beacon -----------------------------------------------------
# Broadcast "BIRDCAM <ip>" on the local subnet every 2 s. birdcam_helper.py listens for it,
# so the board can be found without a USB cable even after its DHCP address changes.
MAC = ":".join("%02X" % b for b in wifi.radio.mac_address)
try:
    _ip_parts = [int(x) for x in ip.split(".")]
    _mask_parts = [int(x) for x in str(wifi.radio.ipv4_subnet).split(".")]
    broadcast_ip = ".".join(str(a | (~m & 0xFF)) for a, m in zip(_ip_parts, _mask_parts))
except Exception:  # pylint: disable=broad-except
    broadcast_ip = "255.255.255.255"
beacon_sock = None
beacon_msg = ("BIRDCAM %s %s %s" % (ip, MAC, HOSTNAME)).encode()
last_beacon = -10.0


def send_beacon():
    global beacon_sock, last_beacon
    last_beacon = time.monotonic()
    try:
        if beacon_sock is None:
            beacon_sock = pool.socket(pool.AF_INET, pool.SOCK_DGRAM)
            try:   # lwIP wants SO_BROADCAST before sending to a broadcast address
                beacon_sock.setsockopt(getattr(pool, "SOL_SOCKET", 0xFFF), getattr(pool, "SO_BROADCAST", 0x20), 1)
            except Exception:  # pylint: disable=broad-except
                pass
            beacon_sock.settimeout(0)
        beacon_sock.sendto(beacon_msg, (broadcast_ip, BEACON_PORT))
    except Exception:  # pylint: disable=broad-except
        beacon_sock = None        # try to recreate next time; beacon is best-effort


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


@server.route("/ai", POST)
def ai_result(request: Request):
    """birdcam_helper.py on the Mac posts Claude's answer here (plain-text body)."""
    global last_ai, ai_pending
    try:
        text = bytes(request.body).decode("utf-8").strip()
    except Exception:  # pylint: disable=broad-except
        text = ""
    last_ai = text or "(empty answer from helper)"
    ai_pending = False
    print("  AI (helper): %s" % last_ai)
    light((0, 255, 0) if armed else (0, 0, 40))
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

    # helper mode: stop waiting if the Mac never answers
    if ai_pending and now - ai_pending_since > HELPER_TIMEOUT_S:
        ai_pending = False
        last_ai = "no answer from the Mac - is birdcam_helper.py running?"
        print("  " + last_ai)
        light((0, 255, 0) if armed else (0, 0, 40))

    # BOOT button shutter
    pressed = not shutter.value
    if pressed and not was_pressed:
        print("click!")
        take_photo("BOOT button")
        time.sleep(0.2)
    was_pressed = pressed

    if now - last_beacon >= 2:
        send_beacon()

    # console heartbeat every 3 s - starts with the board's IP so it is always on screen
    if now - last_radar_print >= 3:
        last_radar_print = now
        if alive:
            print("%-15s radar %-13s moving %3d cm e%3d  static %3d cm e%3d  OUT=%d  [%d frames] %s"
                  % (ip, STATES.get(radar["state"], "?"), radar["mov_cm"], radar["mov_e"],
                     radar["sta_cm"], radar["sta_e"], radar["out"], radar["frames"],
                     "ARMED" if armed else "disarmed"))
        else:
            print("%-15s radar serial SILENT (check green wire on radar TX -> GPIO13) - using OUT pin, OUT=%d  %s"
                  % (ip, radar["out"], "ARMED" if armed else "disarmed"))
    time.sleep(0.01)
