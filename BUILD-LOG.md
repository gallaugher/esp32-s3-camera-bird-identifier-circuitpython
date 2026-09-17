# Bird Cam — build steps (9/16–17/2026)

Board: YD-ESP32-S3 N16R8 on the QIQIAZI terminal adapter, CircuitPython 10.3.1.
Camera: Adafruit OV5640 (5840). Radar: HLK-LD2410C. All files in this folder.
Serial console: VS Code Serial Monitor. Ctrl-D = soft reload (runs code.py, skips boot.py). RST button = hard reset (runs boot.py too).

## DEMO-DAY CHECKLIST (9/17) — helper mode on BostonCollege
Verified working 9/16 night: radar → photo → Mac → Claude → "Printed Picture — Downy Woodpecker, Confidence: High" in ~1.4 s.
1. Board: USB to the Mac (power + console + CIRCUITPY for the key). `settings.toml` Wi-Fi = BostonCollege. `code.py` = `cam_radar_ai.py` (AI_MODE "helper").
2. Mac on **BostonCollege** (not eduroam — it has to see the board).
3. Terminal (with VS Code's Serial Monitor **closed** so the helper can read the board's IP over USB):
   `cd "/Users/gallaugh/Documents/NSF Builds/Bird Cam and Detection w-AI Identification"`
   `python3 birdcam_helper.py`   → it prints the IP it found, then `Watching for photos`. Leave it open.
   (If the Serial Monitor is open, the helper says the port is busy — either close it or run `python3 birdcam_helper.py <ip>`; every board heartbeat line now starts with the IP, so it's always on screen.)
4. Safari: `http://<ip the helper printed>`. Click **Arm/Disarm radar** → disarmed while you talk (the radar sees you at 30–75 cm, energy 100, and would fire every 6 s).
5. To demo: arm the radar, step to the side of the sensor, hold a printout squarely in front of the **lens** at 30–50 cm and wave it. Or just press BOOT / "Take photo". LED: white flash = shot, blue = Claude thinking, green = armed again.
6. Optional: `SPEAK = True` at the top of `birdcam_helper.py` → the Mac announces each bird.
7. Fallbacks: home Wi-Fi or a phone hotspot with real signal → set `AI_MODE = "direct"` (board calls Claude itself); `ssl_test.py` tells you in 15 s whether a network allows it.
8. Known glitches: ~1 in 30 frames comes back "Could not process image" — the helper now retakes automatically. First photo after startup isn't analyzed. `birdcam.local` doesn't resolve on campus; use the IP.

## Wiring (already done — for reference)

Camera → board (10 cm jumpers, * = blue tape):
3V→3V3 red · G→GND black · SDA→GPIO8 blue · SCL→GPIO9 yellow* · XC→GPIO4 yellow · PC→GPIO5 gray* · VS→GPIO10 orange · HS→GPIO11 purple* · D2→GPIO2 green · D3→GPIO42 blue* · D4→GPIO41 gray · D5→GPIO40 white* · D6→GPIO39 purple · D7→GPIO38 orange* · D8→GPIO47 white · D9→GPIO21 green* · RT→GPIO7 black* · PD unconnected

Radar → board (20 cm jumpers, beige tape):
VCC→5V red · GND→GND black · OUT→GPIO14 purple · TX→GPIO13 green · RX→GPIO12 blue
(5V pin is live because the board's IN-OUT jumper is bridged. Rule for this board: USB or external 5 V, never both.)

Photo-mode wire (only for photo_test.py): GPIO6→GND. Not needed for the Wi-Fi programs — leave it out.

## Step 1 — Radar smoke test (5 min)
1. Copy `radar_test.py` to CIRCUITPY as `code.py`. Ctrl-D.
2. Expect: `OUT -> HIGH/LOW` lines as you move, and every 0.5 s a line like
   `OUT=1  MOVING  moving 85 cm (e 72)  static 0 cm (e 0)  detect 85 cm  [N frames]`
3. Sitting still you read `static`; wave a hand → `MOVING`. (The LD2410C has no LED — the console is your only indicator.)
4. No frames after 3 s → check TX/RX are crossed (radar TX → GPIO13), and VCC is on 5V.

## Step 2 — Wi-Fi photo viewer (10 min)
1. `settings.toml` on CIRCUITPY:
   ```
   CIRCUITPY_WIFI_SSID = "YourNetwork"
   CIRCUITPY_WIFI_PASSWORD = "YourPassword"
   ```
   (Home WPA2 network or phone hotspot. Campus/enterprise Wi-Fi won't work.)
2. Library: put the `adafruit_httpserver` folder from the CircuitPython 10.x bundle into `lib/`.
   Fastest: in Terminal, `pip3 install circup` once, then `circup install adafruit_httpserver`.
3. Copy `cam_server.py` → `code.py`. Ctrl-D. Console prints the URL.
4. Open `http://birdcam.local` (or the IP) in Safari. "Take photo" or the BOOT button. Page refreshes itself.

## Step 3 — The demo: radar-triggered camera + viewer
1. Copy `cam_radar_server.py` → `code.py`. Ctrl-D.
2. Page shows live radar numbers, ARMED/disarmed, newest photo. Wave the bird printout → photo.
3. Tuning at the top of the file: `TRIGGER_ENERGY` (40), `MIN_CM`/`MAX_CM` (0–300), `COOLDOWN_S` (6), `FLIP_180`.
   Onboard LED: green = armed, dim blue = disarmed, white flash = taking a photo.
4. Radar faces the target (copper-patch side). Radar sees people too — disarm from the page while setting up.

## Step 4 — AI identification on campus: HELPER mode (use this on BostonCollege)
BostonCollege blocks the board's HTTPS, so the Mac makes the Claude call instead. Board and Mac both on BostonCollege — the same setup where the web page already works.
1. In `cam_radar_ai.py` confirm `AI_MODE = "helper"` (the default). Copy it → `code.py`, Ctrl-D.
   Console shows `AI identification via HELPER: on the Mac run   python3 birdcam_helper.py 10.20.76.118`.
2. Terminal on the Mac, in this folder:
   ```
   cd "/Users/gallaugh/Documents/NSF Builds/Bird Cam and Detection w-AI Identification"
   python3 birdcam_helper.py 10.20.76.118
   ```
   It reads the API key from the board's own settings.toml (CIRCUITPY must be mounted), checks the key and model against the API, finds the board, then says `Watching for photos`. Leave it running.
3. Wave the bird. Board console: `photo #2 (radar ...)`, LED blue, page says `identifying...`. Mac window: `photo #2 ... -> Claude ...` then `AI (3.1s): Downy Woodpecker ...`. Board console: `AI (helper): ...` and the page updates. LED back to green.
4. If the Mac isn't running the helper, the page says so after 45 s and the LED goes back to green.
5. `SPEAK = True` at the top of `birdcam_helper.py` makes the Mac read each answer aloud — fun for the demo.
6. Helper mode also means the API key never has to live on a student board: one Mac (or a Raspberry Pi) per class runs the helper for everyone.

## Step 4 (alt) — DIRECT mode: board calls Claude itself (home Wi-Fi or hotspot)
0. Set `AI_MODE = "direct"` at the top of the file. Only works on a network that lets the board make HTTPS calls — run `ssl_test.py` first if unsure.
1. Add to `settings.toml`:
   ```
   ANTHROPIC_API_KEY = "sk-ant-..."
   ANTHROPIC_MODEL = "claude-haiku-4-5"
   ```
   (Key from platform.claude.com → API keys. Prepaid credits required; $5 covers hundreds of photos on Haiku. If the model name is rejected with a 404, try "claude-sonnet-4-5". The hCaptcha on the credit-purchase page hung on the BostonCollege guest network — buy on eduroam or cellular.)
2. Libraries: with `cam_radar_ai.py` copied as `code.py`, run `circup install -a` (reads the imports, adds adafruit_requests + adafruit_connection_manager).
3. Ctrl-D. Console: `AI identification ON, model claude-haiku-4-5`, then the usual Wi-Fi/URL lines. Same page as Step 3 plus a "Claude: ..." line under the photo.
4. Every radar / button / web-button photo goes to Claude (2–6 s; LED turns blue while waiting). Answer appears on the page and in the console as `AI (3.2s): ...`. Startup photo is not analyzed.
5. Errors print in the console as `AI error ...`: 401 = key typo in settings.toml; 404 = model name; 400 "credit balance is too low" = credits haven't landed; a socket/SSL error = the board's network is blocking api.anthropic.com.
6. Tuning: `TRIGGER_ENERGY` (60), `AI_PROMPT` and `AI_MAX_TOKENS` near the top; `AI_ENABLED = False` to run without API calls.

## Other files here
- `birdcam_helper.py`: the Mac-side half of helper mode (standard-library Python 3, nothing to install).
- `ssl_test.py`: 15-second check of whether the board can make HTTPS calls on the current network. Run it on any new network before trying direct mode.
- `boot.py` + `photo_test.py`: save JPEGs to CIRCUITPY (photo-mode wire GPIO6→GND at RST). Finder only re-reads the drive at mount: eject + RST to see new files.
- `d2check.py`: 10-second data-line check via the sensor's color bars. Run this first on any new camera build.
- `bitcheck.py`, `camera_probe.py`, `code_mirror.py`: diagnostics used 9/16.

## Gotchas learned 9/16–17
- **BostonCollege blocks the board's HTTPS entirely** (`ssl_test.py`: connection reset on one site, `MBEDTLS_ERR_X509_FATAL_ERROR` = substituted certificate on the others). The web viewer works there (LAN only) but the AI call cannot. Demo on a phone hotspot: iPhone Settings → Personal Hotspot → **Maximize Compatibility ON** (ESP32 is 2.4 GHz only); give the phone a plain ASCII name (Settings → General → About → Name — no apostrophe). Board and Mac both join the hotspot. For campus use, BC IT would need to allow api.anthropic.com for the IoT VLAN.
- The QIQIAZI adapter's GND socket right next to 5V was NOT a real ground on John's unit (read 0.x V to true GND). Tape it over; use another GND. Check this on every adapter.
- The IN-OUT 5 V jumper needs a generous solder blob to actually bridge; verify with the meter (5V socket ≈ 5 V, both pads equal). A half-bridged jumper left the 5V pin floating at ~2.7 V — enough to make the radar's OUT go high but not enough to run it.
- Radar TX → GPIO13, radar RX → GPIO12 (the board's RX is 13). Labels are on the module's front; the pins you plug onto are on the back, so the order is mirrored when viewed from behind.
- Grayscale streaming can look perfect with a dead data line; only JPEG (or d2check) proves the wiring. Ours: D2 was in GPIO3 instead of GPIO2.
- `IDFError 0x20002` at camera init = a failed I2C write to the sensor; happens ~1 in 5 starts. Scripts reload themselves. If it persists, keep the XC wire away from SDA/SCL.
- A failed camera init leaves pins claimed until a reload (`GPIOx in use`).
- The OV5640 doesn't answer I2C until its clock runs — scan after init, not before.
