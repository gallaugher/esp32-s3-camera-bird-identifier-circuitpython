# Bird-cam Wi-Fi photo viewer - YD-ESP32-S3 N16R8 + OV5640 (CircuitPython 10.x)
# Copy to CIRCUITPY as code.py.  NO photo-mode wire needed: the latest photo is
# kept in RAM and served over Wi-Fi, nothing is written to the drive.
#
# Needs:
#   settings.toml with   CIRCUITPY_WIFI_SSID = "..."   CIRCUITPY_WIFI_PASSWORD = "..."
#   lib/adafruit_httpserver/   (folder from the CircuitPython 10 library bundle)
#
# Then open the URL printed in the console (http://birdcam.local or the IP).
# The page shows the newest photo and refreshes itself; "Take photo" on the page
# or the BOOT button on the board takes a new one.

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

# ---- settings ---------------------------------------------------------
FRAME_SIZE = espcamera.FrameSize.SVGA   # 800x600 (~30 KB). XGA 1024x768 / HD 1280x720 also fine
JPEG_QUALITY = 10
WARMUP_FRAMES = 6                       # discarded before each saved shot (exposure settle)
FLIP_180 = False                        # True if the picture is upside down when mounted
AUTO_SECONDS = 0                        # e.g. 10 = take a photo every 10 s; 0 = manual only
HOSTNAME = "birdcam"                    # http://birdcam.local

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
    n = microcontroller.nvm[22]
    n = 0 if n > 10 else n
    if n >= 4:
        microcontroller.nvm[22] = 0
        print("Camera init keeps failing (%r). Check wiring and try again." % e)
        raise SystemExit
    microcontroller.nvm[22] = n + 1
    print("camera init failed (%r) - reloading to free pins, try %d/4" % (e, n + 1))
    time.sleep(1)
    supervisor.reload()
    while True:
        time.sleep(0.1)
microcontroller.nvm[22] = 0
cam.vflip = FLIP_180
cam.hmirror = FLIP_180
print("Camera ready:", cam.width, "x", cam.height, "JPEG q%d" % JPEG_QUALITY)

# ---- Wi-Fi ------------------------------------------------------------
if not wifi.radio.connected:
    ssid = os.getenv("CIRCUITPY_WIFI_SSID")
    pw = os.getenv("CIRCUITPY_WIFI_PASSWORD")
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
    print("Wi-Fi connection failed - check SSID/password (must be a WPA2-Personal network).")
    raise SystemExit
ip = str(wifi.radio.ipv4_address)
print("Wi-Fi connected, IP", ip)

try:
    import mdns
    m = mdns.Server(wifi.radio)
    m.hostname = HOSTNAME
    m.advertise_service(service_type="_http", protocol="_tcp", port=80)
    print("mDNS name: http://%s.local" % HOSTNAME)
except Exception as e:  # pylint: disable=broad-except
    print("mDNS not available (%r) - use the IP address" % e)

# ---- photo state ------------------------------------------------------
latest = b""
photo_count = 0
last_shot = 0.0

shutter = digitalio.DigitalInOut(board.BOOT)
shutter.switch_to_input(pull=digitalio.Pull.UP)


def take_photo():
    global latest, photo_count, last_shot
    for _ in range(WARMUP_FRAMES):
        cam.take(1)
    frame = cam.take(1)
    if frame is None:
        print("  no frame from camera (timeout)")
        return False
    latest = bytes(frame)          # copy - the camera reuses its buffer
    photo_count += 1
    last_shot = time.monotonic()
    print("  photo #%d, %d bytes" % (photo_count, len(latest)))
    return True


# ---- web server -------------------------------------------------------
PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Bird cam</title>
<style>body{font-family:-apple-system,Helvetica,sans-serif;margin:16px;background:#111;color:#eee;text-align:center}
img{max-width:100%%;border-radius:8px;background:#000}button{font-size:1.2em;padding:.5em 1.2em;margin:.6em;border-radius:8px;border:0;background:#3a7;color:#fff}
#s{color:#9c9;min-height:1.4em}</style></head><body>
<h2>Bird cam</h2>
<div><button onclick="snap()">Take photo</button></div>
<div id="s">%s</div>
<img id="p" src="/photo.jpg?t=0" alt="no photo yet">
<script>
var n=%d;
function refresh(){document.getElementById('p').src='/photo.jpg?t='+Date.now();}
function snap(){document.getElementById('s').textContent='taking...';
 fetch('/snap').then(r=>r.json()).then(j=>{n=j.count;document.getElementById('s').textContent=
 'photo #'+j.count+', '+j.bytes+' bytes';refresh();}).catch(e=>{document.getElementById('s').textContent='error: '+e;});}
setInterval(function(){fetch('/status').then(r=>r.json()).then(j=>{if(j.count!=n){n=j.count;
 document.getElementById('s').textContent='photo #'+j.count+', '+j.bytes+' bytes';refresh();}});},1500);
</script></body></html>"""

pool = socketpool.SocketPool(wifi.radio)
server = Server(pool, debug=False)


@server.route("/", GET)
def index(request: Request):
    status = ("photo #%d, %d bytes" % (photo_count, len(latest))) if latest else "no photo yet"
    return Response(request, PAGE % (status, photo_count), content_type="text/html")


@server.route("/photo.jpg", GET)
def photo(request: Request):
    if not latest:
        return Response(request, "no photo yet", status=(404, "Not Found"))
    return Response(request, latest, content_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@server.route("/snap", GET)
def snap(request: Request):
    take_photo()
    return JSONResponse(request, {"count": photo_count, "bytes": len(latest)})


@server.route("/status", GET)
def status(request: Request):
    return JSONResponse(request, {"count": photo_count, "bytes": len(latest)})


port = 80
try:
    server.start(ip, port=port)
except OSError:
    port = 8080
    server.start(ip, port=port)
suffix = "" if port == 80 else ":%d" % port
print("\nOpen  http://%s%s   or  http://%s.local%s" % (ip, suffix, HOSTNAME, suffix))

print("Taking first photo ...")
take_photo()
print("BOOT button or the page's button takes another.\n")

was_pressed = False
while True:
    try:
        server.poll()
    except Exception as e:  # pylint: disable=broad-except
        print("server error:", repr(e))
    pressed = not shutter.value
    if pressed and not was_pressed:
        print("click!")
        take_photo()
        time.sleep(0.2)
    was_pressed = pressed
    if AUTO_SECONDS and time.monotonic() - last_shot >= AUTO_SECONDS:
        take_photo()
    time.sleep(0.01)
