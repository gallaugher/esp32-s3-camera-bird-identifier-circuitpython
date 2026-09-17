# Bird Cam with AI Identification

A radar-triggered camera that photographs whatever lands at a bird feeder and asks Claude what it is. Built on the classroom ESP32-S3 kit in CircuitPython for Boston College's Physical Computing course (NSF-funded *AI & Physical Computing* project).

**How it works:** a 24 GHz mmWave radar watches for motion → the ESP32-S3 grabs a JPEG from an OV5640 camera → the photo goes to the Claude API → the species (and a confidence) appears on a little web page the board serves, next to the photo. Around 1.5 seconds from motion to identification. Waving a printout of a bird in front of it works too — Claude will tell you it's a printout.

```
   LD2410C radar ──OUT/UART──►  YD-ESP32-S3  ◄──8-bit DVP──  OV5640 camera
                                     │
                          Wi-Fi ─────┼───── http://<board-ip>   (photo + result page)
                                     │
                    ┌────────────────┴────────────────┐
             direct mode                         helper mode
      board calls api.anthropic.com        Mac polls the board, calls the API,
      over HTTPS itself                    posts the answer back to the board
      (home Wi-Fi, hotspot)                (campus networks that block IoT HTTPS)
```

## Hardware

| Part | Notes |
|------|-------|
| YD-ESP32-S3 N16R8 (VCC-GND) on a screw-terminal adapter | CircuitPython 10.3.1, board name "VCC-GND YD-ESP32-S3 (N16R8)". 16 MB flash / 8 MB PSRAM — the PSRAM holds the camera frame buffer. |
| Adafruit OV5640 camera breakout (product 5840, 72° autofocus) | Solder two 1×9 headers in the two **outermost** rows (Adafruit's "for breadboard use"). Top + center rows is wrong — you get two copies of the same 9 signals. |
| HLK-LD2410C 24 GHz mmWave radar | 5 V supply, 3.3 V logic, UART 256000 baud. No LED on the module. |
| Jumpers | 10 cm M-F for the camera, 20 cm for the radar. |

The board's **IN-OUT solder jumper is bridged** so the 5V pin carries USB power for the radar. Rule for a bridged board: USB *or* external 5 V, never both.

### Wiring

Camera → board (`*` = wire marked with a band of blue tape where a color is reused):

| Camera | Board | Wire | Camera | Board | Wire |
|--------|-------|------|--------|-------|------|
| 3V | 3V3 | red | D2 (LSB) | GPIO2 | green |
| G | GND | black | D3 | GPIO42 | blue* |
| SDA | GPIO8 | blue | D4 | GPIO41 | gray |
| SCL | GPIO9 | yellow* | D5 | GPIO40 | white* |
| XC (XCLK) | GPIO4 | yellow | D6 | GPIO39 | purple |
| PC (PCLK) | GPIO5 | gray* | D7 | GPIO38 | orange* |
| VS (VSYNC) | GPIO10 | orange | D8 | GPIO47 | white |
| HS (HREF) | GPIO11 | purple* | D9 (MSB) | GPIO21 | green* |
| RT (reset) | GPIO7 | black* | PD | — | leave unconnected |

Radar → board (beige tape):

| Radar | Board | Wire |
|-------|-------|------|
| VCC | 5V | red |
| GND | GND | black |
| OUT | GPIO14 | purple |
| TX | GPIO13 | green |
| RX | GPIO12 | blue |

The radar's labels are on the front and the pins are on the back, so the pin order is mirrored when you look at it from behind — `radar_probe.py` tells you which GPIO the radar's TX actually landed on.

## Software setup

1. Flash CircuitPython 10.x for the YD-ESP32-S3 N16R8.
2. Copy `settings.toml.example` to the board as `settings.toml` and fill in your Wi-Fi network (WPA2-Personal — enterprise/802.1X networks like eduroam won't work) and, for direct mode, your Anthropic API key.
3. Copy `cam_radar_ai.py` to the board as `code.py`.
4. Libraries: `pip3 install circup` once, then with the board plugged in: `circup install -a` (installs `adafruit_httpserver`, `adafruit_requests`, `adafruit_connection_manager` from the imports in code.py).
5. Open a serial console (VS Code Serial Monitor, Mu, or `screen`) and press Ctrl-D. The board prints its IP address.
6. Open `http://<ip>` in a browser on the same network.

### Choosing an AI mode (`AI_MODE` at the top of `cam_radar_ai.py`)

**`"direct"`** — the board calls `api.anthropic.com` itself over HTTPS. Needs `ANTHROPIC_API_KEY` in `settings.toml` and a network that lets the board make TLS connections. Run `ssl_test.py` (copy it as `code.py` for 15 seconds) on any new network; it prints a verdict.

**`"helper"`** (default) — the board never talks to the internet. On a Mac on the same network, with the board plugged in over USB:
```
python3 birdcam_helper.py
```
The helper reads the board's IP from its USB serial console (close any other serial monitor first, or pass the IP as an argument), then polls the board, sends each new photo to Claude, and posts the answer back. Standard-library Python only; it reads the API key from the board's mounted `settings.toml` (or `ANTHROPIC_API_KEY` in the environment). Use this on campus networks that block IoT devices' HTTPS — Boston College's registered-device network does — and for classrooms, where one machine holds the key and student boards carry none.

### API account
Keys come from the Claude Console at **platform.claude.com** (separate from a claude.ai chat subscription; prepaid credits). Each identification with `claude-haiku-4-5` costs roughly a tenth of a cent; $5 covers thousands of photos. Set a monthly spend limit in the Console's billing settings, and keep `settings.toml` out of version control (it's in `.gitignore`).

## Using it

- Web page: newest photo, live radar readout (state, distance, energy), **Take photo** and **Arm/Disarm radar** buttons, and `Claude: …` under the photo.
- **BOOT** button on the board = shutter.
- Onboard LED: green = radar armed, dim blue = disarmed, white flash = taking a photo, solid blue = waiting for Claude, red = camera timeout.
- Tuning at the top of `cam_radar_ai.py`: `TRIGGER_ENERGY` (60), `MIN_CM`/`MAX_CM` (0–300), `COOLDOWN_S` (6), `FLIP_180`, `AI_PROMPT`.
- The radar sees people as readily as birds. Disarm it while you're standing in front of the sensor; the presenter should stay outside its ±60° cone.

## Files

| File | Runs on | Purpose |
|------|---------|---------|
| `cam_radar_ai.py` | board (as code.py) | **The project.** Radar trigger + camera + web page + AI (direct or helper). |
| `birdcam_helper.py` | Mac | Helper-mode companion: does the Claude call for the board. |
| `cam_radar_server.py` | board | Same without AI — radar-triggered camera + web viewer. |
| `cam_server.py` | board | Camera-only web viewer. |
| `settings.toml.example` | board | Template for Wi-Fi and API settings. |
| `ssl_test.py` | board | Can this board make HTTPS calls on this network? Verdict in 15 s. |
| `radar_test.py` | board | Radar smoke test: prints decoded frames and the OUT pin. |
| `radar_probe.py` | board | Finds which GPIO the radar's TX is on (listen-only scan). |
| `d2check.py` | board | Verifies all 8 camera data lines using the sensor's color-bar pattern. **Run this first on every new camera build.** |
| `boot.py` + `photo_test.py` | board | Save JPEGs to the CIRCUITPY drive (GPIO6→GND jumper at reset enables writing). |
| `diagnostics/` | board | `bitcheck.py`, `camera_probe.py`, `code_mirror.py` — the tools used to debug the first build. |
| `BUILD-LOG.md` | — | Step-by-step build notes and everything that went wrong, in order. |

## Things that will bite you (learned the hard way)

- **Grayscale streaming can look perfect with a dead data line.** JPEG capture validates the `FF D8`/`FF D9` markers and silently drops every frame if bit 0 is stuck. Our D2 wire was in GPIO3 instead of GPIO2. `d2check.py` catches this in ten seconds.
- **`IDFError 0x20002` at camera init** happens about one start in five (an I2C write to the sensor failed). The scripts reload themselves. A failed `espcamera.Camera()` leaves its pins claimed until a reload.
- **The OV5640 doesn't answer on I2C until its clock is running.** Scan after init, not before.
- **Check the adapter's ground sockets with a meter.** One GND socket on our terminal adapter — the one next to 5V — wasn't connected to ground.
- **A solder jumper that looks bridged may not be.** Meter the 5V pin: expect ~5 V. Half-bridged, ours floated at 2.7 V — enough to make the radar's OUT twitch, not enough to run it.
- **Campus networks.** Boston College's registered-device Wi-Fi blocks all outbound HTTPS from the board (connection resets and substituted certificates). Helper mode exists because of this. Phone hotspots work only where there's real cellular signal; for iPhones turn on *Maximize Compatibility* (2.4 GHz) and give the phone an ASCII name.
- **macOS's bundled `python3` has no root certificates**; the helper falls back to `curl` automatically.
- **mDNS (`birdcam.local`) rarely works on campus networks.** Use the IP.
- **The board's IP can change** after moving between networks. Always read it from the console.

## Next steps

Window mount (rotate the board or set `FLIP_180`; `cam.hmirror = True` to un-mirror), autofocus via the sensor's AF firmware (check the VM solder jumper), camera power-down between shots (PD pin), radar distance gates tuned through glass, battery power.

## Credits

Built by John Gallaugher (Boston College, Carroll School of Management) with Claude, September 2026, for the NSF *AI & Physical Computing* project. Camera wiring follows Adafruit's OV5640 breakout guide; radar protocol from Hi-Link's LD2410 serial documentation.
