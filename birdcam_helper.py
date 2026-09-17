#!/usr/bin/env python3
"""
birdcam_helper.py - runs on the Mac, next to the bird cam board.

Watches the board's web server for a new photo, sends it to Claude, and posts
Claude's answer back so it appears under the photo on the board's page.
The board never needs outbound internet - only the Mac does. Use this on
networks that block the board's HTTPS (BostonCollege). The board must be
running cam_radar_ai.py with AI_MODE = "helper".

Usage (Terminal, from the folder containing this file):
    python3 birdcam_helper.py                 <- finds the board by itself: listens for the board's
                                                 network beacon, then tries last time's address,
                                                 birdcam.local, and the USB serial console
    python3 birdcam_helper.py --scan          <- ... and if all else fails, probes the whole subnet
    python3 birdcam_helper.py 10.20.76.118    <- or just give it the IP from the board's console

API key: read from ANTHROPIC_API_KEY in the environment, else from the board's
own /Volumes/CIRCUITPY/settings.toml, else from a settings.toml next to this file.
Nothing else to install - standard library only.
"""

import base64
import glob
import json
import os
import select
import re
import socket
import ssl
import subprocess
import sys
import termios
import time
import tty
import urllib.error
import urllib.request

BOARD = next((a for a in sys.argv[1:] if not a.startswith("--")), None)   # None = find the board automatically
POLL_S = 0.7            # how often to ask the board for status
SPEAK = False           # True: the Mac reads each answer aloud (macOS "say")
MAX_TOKENS = 120
MAX_IDENTIFICATIONS = 250   # safety cap per run of this script (restart it to continue)
PRICE_IN, PRICE_OUT = 1.00, 5.00   # $ per million tokens, Haiku 4.5 - only used for the running estimate
PROMPT = ("This photo comes from a camera watching a bird feeder through a window. "
          "If a bird or other animal is visible, give its most likely species (common name), "
          "a confidence (high/medium/low), and one short reason. If it is a printed picture or "
          "screen showing a bird, identify the bird anyway and note that it is a printout. "
          "If no animal is visible, say 'No animal detected' and describe the scene in a few words. "
          "Answer in under 40 words, as plain text: no markdown, asterisks, or headings.")

API_URL = "https://api.anthropic.com/v1/messages"
HERE = os.path.dirname(os.path.abspath(__file__))


# ---- settings -----------------------------------------------------------
def load_setting(name):
    value = os.environ.get(name)
    if value:
        return value, "environment"
    for path in ("/Volumes/CIRCUITPY/settings.toml", os.path.join(HERE, "settings.toml")):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    m = re.match(r'\s*%s\s*=\s*"([^"]*)"' % re.escape(name), line)
                    if m and m.group(1):
                        return m.group(1), path
        except OSError:
            pass
    return None, None


API_KEY, key_source = load_setting("ANTHROPIC_API_KEY")
MODEL = load_setting("ANTHROPIC_MODEL")[0] or "claude-haiku-4-5"
if not API_KEY:
    sys.exit("No API key found. Put ANTHROPIC_API_KEY = \"sk-ant-...\" in the board's settings.toml "
             "(CIRCUITPY must be mounted), or run:  export ANTHROPIC_API_KEY=sk-ant-...")
print("API key from %s, model %s" % (key_source, MODEL))

SSL_CTX = ssl.create_default_context()
use_curl = False        # flips on if this Python has no usable root certificates


# ---- HTTP helpers ---------------------------------------------------------
def http(url, data=None, headers=None, timeout=10, method=None):
    """Return (status, body_bytes). Raises on connection problems."""
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout,
                                    context=SSL_CTX if url.startswith("https") else None) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def curl(url, data=None, headers=None, timeout=60, method="GET"):
    cmd = ["curl", "-sS", "-X", method, url, "-m", str(timeout), "-w", "\n%{http_code}"]
    for k, v in (headers or {}).items():
        cmd += ["-H", "%s: %s" % (k, v)]
    if data is not None:
        cmd += ["--data-binary", "@-"]
    p = subprocess.run(cmd, input=data, capture_output=True, check=False)
    out = p.stdout
    body, _, code = out.rpartition(b"\n")
    return int(code or 0), body


def anthropic(path, data=None, timeout=60):
    """GET or POST to the Anthropic API, falling back to curl if Python's SSL roots are missing."""
    global use_curl
    headers = {"x-api-key": API_KEY, "anthropic-version": "2023-06-01"}
    method = "POST" if data is not None else "GET"
    if data is not None:
        headers["content-type"] = "application/json"
    if not use_curl:
        try:
            return http(API_URL.replace("/v1/messages", path), data, headers, timeout, method)
        except urllib.error.URLError as e:
            if "CERTIFICATE_VERIFY_FAILED" in str(e):
                print("(this Python has no root certificates - using curl instead)")
                use_curl = True
            else:
                raise
    return curl(API_URL.replace("/v1/messages", path), data, headers, timeout, method)


# ---- startup checks --------------------------------------------------------
print("Checking Claude API from this Mac ...", end=" ", flush=True)
try:
    code, body = anthropic("/v1/models", timeout=20)
except Exception as e:  # pylint: disable=broad-except
    sys.exit("\nCannot reach api.anthropic.com from this Mac: %r\nIs the Mac on a network with internet?" % e)
if code == 401:
    sys.exit("\nAPI key rejected (401). Re-copy the key into settings.toml.")
if code != 200:
    sys.exit("\nUnexpected reply %d: %s" % (code, body[:200]))
ids = [m.get("id", "") for m in json.loads(body).get("data", [])]
if not any(i.startswith(MODEL) for i in ids):
    print("\n  WARNING: model '%s' not in this account's list (%s). Set ANTHROPIC_MODEL to one of these."
          % (MODEL, ", ".join(ids[:6])))
else:
    print("OK (key valid, model available)")

# ---- find the board ----------------------------------------------------------
IP_CACHE = os.path.expanduser("~/.birdcam_ip")
IP_RE = re.compile(rb"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")
BEACON_PORT = 47777          # must match BEACON_PORT in cam_radar_ai.py
SCAN = "--scan" in sys.argv  # last resort: probe every address on the Mac's subnet


def board_status(host, timeout=3):
    """Return the board's /status dict, or None if nothing bird-cam-like answers there."""
    try:
        st = json.loads(http("http://%s/status" % host, timeout=timeout)[1])
        return st if "count" in st else None
    except Exception:  # pylint: disable=broad-except
        return None


def ip_from_beacon(wait_s=4):
    """The board broadcasts 'BIRDCAM <ip> <mac> <name>' over UDP every 2 s. Listen for one."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", BEACON_PORT))
        sock.settimeout(wait_s)
        data, addr = sock.recvfrom(256)
        sock.close()
        if data.startswith(b"BIRDCAM"):
            return addr[0]
    except Exception:  # pylint: disable=broad-except
        pass
    return None


def ip_from_serial(wait_s=12):
    """Read the board's IP from its USB serial console (the board prints it every few seconds).
    If the board is silent for a while, send Ctrl-D so it reboots and prints its Wi-Fi line."""
    ports = glob.glob("/dev/cu.usbmodem*")
    if not ports:
        return None
    for port in ports:
        try:
            fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            print("\n  %s is busy (%s) - a serial monitor has it open" % (port, e.strerror), end=" ")
            continue
        try:
            tty.setraw(fd)
            attrs = termios.tcgetattr(fd)
            attrs[4] = attrs[5] = termios.B115200
            attrs[2] |= termios.CLOCAL | termios.CREAD
            termios.tcsetattr(fd, termios.TCSANOW, attrs)
            buf = b""
            t_end = time.time() + wait_s
            reload_sent = False
            while time.time() < t_end:
                ready, _, _ = select.select([fd], [], [], 0.5)
                if ready:
                    try:
                        buf += os.read(fd, 4096)
                    except BlockingIOError:
                        pass
                    hits = IP_RE.findall(buf)
                    if hits:
                        return hits[-1].decode()
                elif not reload_sent and time.time() > t_end - wait_s * 0.6:
                    os.write(fd, b"\x04")          # board quiet - reload it so it prints its IP
                    reload_sent = True
                    t_end = time.time() + 25         # camera + Wi-Fi bring-up takes a while
        finally:
            os.close(fd)
    return None


def mac_subnet():
    """(own ip, netmask) of the Mac's active interface, via a UDP socket trick + ipconfig."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("10.255.255.255", 1))
        my_ip = probe.getsockname()[0]
        probe.close()
    except Exception:  # pylint: disable=broad-except
        return None, None
    mask = "255.255.255.0"
    for iface in ("en0", "en1", "en2"):
        try:
            out = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True, check=False).stdout.strip()
            if out == my_ip:
                m = subprocess.run(["ipconfig", "getoption", iface, "subnet_mask"], capture_output=True, text=True, check=False).stdout.strip()
                if m:
                    mask = m
                break
        except OSError:
            break
    return my_ip, mask


def ip_from_scan():
    """Probe every host on the Mac's subnet (capped at 1024 addresses) for a bird-cam /status page."""
    import concurrent.futures
    my_ip, mask = mac_subnet()
    if not my_ip:
        return None
    ip_i = sum(int(o) << (8 * (3 - i)) for i, o in enumerate(my_ip.split(".")))
    m_i = sum(int(o) << (8 * (3 - i)) for i, o in enumerate(mask.split(".")))
    if (0xFFFFFFFF ^ m_i) > 1023:                       # bigger than /22: just do our own /24
        m_i = 0xFFFFFF00
    base = ip_i & m_i
    hosts = [".".join(str((base + n) >> (8 * k) & 0xFF) for k in (3, 2, 1, 0))
             for n in range(1, (0xFFFFFFFF ^ m_i))]
    print("\n  scanning %d addresses around %s ..." % (len(hosts), my_ip), end=" ", flush=True)

    def probe(h):
        try:
            sock = socket.create_connection((h, 80), timeout=0.4)
            sock.close()
        except OSError:
            return None
        return h if board_status(h, timeout=2) else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=96) as ex:
        for hit in ex.map(probe, hosts):
            if hit:
                return hit
    return None


def load_cache():
    try:
        c = json.loads(open(IP_CACHE).read())
        return c if isinstance(c, dict) else {"ip": str(c)}
    except Exception:  # pylint: disable=broad-except
        try:
            return {"ip": open(IP_CACHE).read().strip()}
        except OSError:
            return {}


cache = load_cache()
st = None
BOARD_FOUND_BY = None

if BOARD:
    print("Trying the board at http://%s (command line) ..." % BOARD, end=" ", flush=True)
    st = board_status(BOARD, timeout=4)
    print("OK" if st else "no answer")
else:
    steps = [
        ("beacon",   "listening for the board's beacon on the network", lambda: ip_from_beacon(4)),
        ("last time", "trying the address from last time",             lambda: cache.get("ip")),
        ("mDNS",     "trying birdcam.local",                            lambda: "birdcam.local"),
        ("serial",   "reading the USB serial console",                  lambda: ip_from_serial(10)),
    ]
    if SCAN:
        steps.append(("scan", "scanning the Mac's subnet (--scan)", ip_from_scan))
    for how, msg, fn in steps:
        print("%s ..." % msg, end=" ", flush=True)
        host = fn()
        if host:
            st = board_status(host, timeout=4)
        if st:
            BOARD, BOARD_FOUND_BY = host, how
            print("found at http://%s" % host)
            break
        print("no")

if not st:
    sys.exit("\nCannot reach the board. Check that the Mac is on the same Wi-Fi network as the board\n"
             "(BostonCollege, not eduroam). Then try, in order:\n"
             "  python3 birdcam_helper.py --scan          (probes every address on this subnet)\n"
             "  python3 birdcam_helper.py 10.20.xx.xx     (the IP from the board's console lines)\n"
             "  or plug the board into this Mac by USB, close any serial monitor, and rerun.")

print("Board: photo #%d so far, radar %s%s" % (st["count"], "ARMED" if st["armed"] else "disarmed",
                                              (", MAC " + st["mac"]) if st.get("mac") else ""))
try:
    open(IP_CACHE, "w").write(json.dumps({"ip": BOARD, "mac": st.get("mac", "")}))
except OSError:
    pass
if "ai_pending" not in st:
    print("  NOTE: the board is running an older code.py - copy the new cam_radar_ai.py to code.py "
          "(AI_MODE = \"helper\") and Ctrl-D, or nothing will be identified.")


# ---- identify one photo -----------------------------------------------------
def identify(jpeg):
    b64 = base64.b64encode(jpeg).decode()
    body = json.dumps({
        "model": MODEL, "max_tokens": MAX_TOKENS,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": b64}},
            {"type": "text", "text": PROMPT}]}],
    }).encode()
    code, resp = anthropic("/v1/messages", body, timeout=60)
    if code != 200:
        return "AI error %d: %s" % (code, resp.decode("utf-8", "replace")[:160]), 0, 0
    data = json.loads(resp)
    usage = data.get("usage", {})
    text = " ".join(p.get("text", "") for p in data.get("content", []) if p.get("type") == "text").strip()
    return (text or "AI returned no text"), usage.get("input_tokens", 0), usage.get("output_tokens", 0)


def post_answer(text, tries=4):
    """Send Claude's answer to the board; retry, since the board ignores the network while it shoots."""
    for attempt in range(tries):
        try:
            http("http://%s/ai" % BOARD, data=text.encode("utf-8"),
                 headers={"Content-Type": "text/plain; charset=utf-8"}, timeout=6, method="POST")
            return True
        except Exception as e:  # pylint: disable=broad-except
            if attempt == tries - 1:
                print("  could not deliver the answer to the board (%s)" % e.__class__.__name__, flush=True)
                return False
            time.sleep(1.0)


# ---- main loop -------------------------------------------------------------------
print("\nWatching for photos. Leave this window open during the demo. Ctrl-C to stop.\n")
handled = -1
retried_for = -1
board_down = False
calls = 0
cost = 0.0
while True:
    try:
        try:
            st = json.loads(http("http://%s/status" % BOARD, timeout=3)[1])
            if board_down:
                print("board is back")
                board_down = False
        except Exception as e:  # pylint: disable=broad-except
            if not board_down:
                print("board not answering (%s) - waiting ..." % e.__class__.__name__)
                board_down = True
            time.sleep(2)
            continue

        if st["count"] < handled:          # board rebooted - photo numbers restarted
            handled = -1
        if st.get("ai_pending") and st["count"] != handled:
            handled = st["count"]
            jpeg = http("http://%s/photo.jpg" % BOARD, timeout=8)[1]
            print("photo #%d (%s), %d bytes -> Claude ..." % (st["count"], st["reason"], len(jpeg)), flush=True)
            if calls >= MAX_IDENTIFICATIONS:
                msg = "helper paused: %d identifications this run (safety cap) - restart birdcam_helper.py" % calls
                print(msg)
                http("http://%s/ai" % BOARD, data=msg.encode(), headers={"Content-Type": "text/plain"},
                     timeout=8, method="POST")
                break
            t0 = time.time()
            text, tok_in, tok_out = identify(jpeg)
            text = text.replace("**", "").replace("\n\n", " ").replace("\n", " ").lstrip("# ")
            calls += 1
            cost += tok_in * PRICE_IN / 1e6 + tok_out * PRICE_OUT / 1e6
            print("  AI (%.1fs): %s" % (time.time() - t0, text), flush=True)
            print("  [%d identifications this run, est. $%.3f]" % (calls, cost), flush=True)
            if "Could not process image" in text and retried_for != st["count"]:
                # a rare bad JPEG frame - ask the board for a fresh photo, once
                print("  bad frame - asking the board to retake", flush=True)
                snap = json.loads(http("http://%s/snap" % BOARD, timeout=15)[1])
                retried_for = snap["count"]
                continue
            post_answer(text)
            if SPEAK and not text.startswith("AI error"):
                subprocess.Popen(["say", text])
        time.sleep(POLL_S)
    except KeyboardInterrupt:
        print("\nstopped")
        break
    except Exception as e:  # pylint: disable=broad-except
        # the board is single-threaded and goes quiet while it takes a photo or reboots;
        # never let one missed request take the helper down
        print("  hiccup talking to the board (%s: %s) - retrying" % (e.__class__.__name__, str(e)[:80]), flush=True)
        time.sleep(1.5)
