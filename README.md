# Bird Cam with AI Identification

A radar-triggered camera that photographs whatever shows up at a bird feeder and asks Claude what it is. Built on the classroom ESP32-S3 kit in CircuitPython for Boston College's Physical Computing course (NSF *AI & Physical Computing* project).

[![Bird Identifier Demo — click to watch on YouTube](https://i.ytimg.com/vi/HVIMTLtxEno/hqdefault.jpg)](https://youtu.be/HVIMTLtxEno)

**▶ Demo video:** [Bird Identifier Demo on YouTube](https://youtu.be/HVIMTLtxEno) (Build With Prof. G.)

**How it works:** a 24 GHz mmWave radar watches for motion → the ESP32-S3 grabs a JPEG from an OV5640 camera → the photo goes to the Claude API → the species and a confidence appear under the photo on a web page the board serves. About 1.5 seconds from motion to identification. Wave a printout of a bird at it and Claude will name the bird and mention that it's a printout.

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

## Parts

| Qty | Part | Source | Approx. price |
|----:|------|--------|--------------:|
| 1 | **YD-ESP32-S3 N16R8** dev board + screw-terminal adapter (16 MB flash / 8 MB PSRAM — the PSRAM holds the camera frame buffer) | [QIQIAZI 2-pack, Amazon B0GVS4KFCM](https://www.amazon.com/dp/B0GVS4KFCM) | $19.99 / 2 sets |
| 1 | **Adafruit OV5640 camera breakout**, 72° autofocus (product 5840) | [adafruit.com/product/5840](https://www.adafruit.com/product/5840) | $9.95 |
| 1 | **HLK-LD2410C** 24 GHz mmWave presence radar, header pre-soldered | [MakerHawk 3-pack, Amazon B0FKBF3CT4](https://www.amazon.com/dp/B0FKBF3CT4) | $19.99 / 3 |
| 17 | Male-female jumper wires, 10 cm (camera) | EDGELEC 120-pc M-F 10 cm | ~$7 |
| 5 | Male-female jumper wires, 20 cm (radar) | [Aypzuke 10+20 cm 240-pc kit](https://a.co/d/0h3WT8NL) | $9.99 |
| 1 | USB-C data cable to a Mac | any | — |
| — | Two 1×9 male header strips (ship with the camera), soldering iron, blue and beige masking tape for wire bands, a multimeter | | |

Software is free except API usage: a Claude Console account at [platform.claude.com](https://platform.claude.com) with prepaid credits. Each identification with `claude-haiku-4-5` costs about a tenth of a cent; $5 covers thousands of photos.

## Wiring

Solder two 1×9 headers into the camera's two **outermost** rows (Adafruit's "for breadboard use" option). Top + center rows is wrong — you get two copies of the same nine signals. Jumpers go straight from the header pins into the adapter's socket row.

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

Notes:
- The radar needs 5 V. On the YD-ESP32-S3 the 5V pin only carries USB power if the **IN-OUT solder jumper** (two pads near the RGB LED) is bridged. Bridge it, then check with a meter: ~5 V on the 5V pin. A jumper that looks bridged may not be — ours floated at 2.7 V until re-soldered. Rule for a bridged board: USB *or* external 5 V, never both.
- Check the adapter's GND sockets with a meter too. On our adapter the GND socket right next to 5V wasn't connected to anything.
- The radar's labels are on the front and the pins on the back, so the order is mirrored viewed from behind. `radar_probe.py` tells you which GPIO its TX actually landed on.

## Setup

1. Flash [CircuitPython 10.x for the YD-ESP32-S3 N16R8](https://circuitpython.org/board/yd_esp32_s3_n16r8/).
2. Copy `settings.toml.example` to the board as `settings.toml`. Fill in your Wi-Fi (WPA2-Personal — enterprise/802.1X networks like eduroam won't work) and your Anthropic API key.
3. Copy `cam_radar_ai.py` to the board as `code.py`.
4. Libraries: `pip3 install circup` once, then with the board plugged in: `circup install -a` (installs `adafruit_httpserver`, `adafruit_requests`, `adafruit_connection_manager`).
5. First-run checks, each a 15-second copy-to-`code.py`: `d2check.py` (all eight camera data lines good?), `radar_test.py` (frames arriving?), `ssl_test.py` (can this network carry the board's HTTPS?).

### Pick an AI mode (`AI_MODE` at the top of `cam_radar_ai.py`)

**`"helper"`** (default) — the board never talks to the internet. A Mac on the same Wi-Fi does the API call. Use this on campus networks that block IoT devices' HTTPS (Boston College's registered-device network does) and in classrooms: one machine holds the key, student boards carry none.

**`"direct"`** — the board calls `api.anthropic.com` itself. Needs the key in `settings.toml` and a network that passes the board's TLS (home Wi-Fi, a phone hotspot with real signal). `ssl_test.py` tells you.

## Run

1. Plug the board into the Mac by USB. It boots, joins Wi-Fi, and serves its page. In helper mode nothing leaves the LAN until the helper runs.
2. Helper mode only — in Terminal, from this folder:
   ```
   python3 birdcam_helper.py
   ```
   It reads the board's IP over the USB serial port (close any other serial monitor first, or pass the IP as an argument), checks your key against the API, and prints `Watching for photos`. Leave it open. Standard-library Python only.
3. Open `http://<board-ip>` in a browser on the same network (the helper prints the address; so does every heartbeat line in the board's console). The page shows the newest photo, live radar numbers, **Take photo** and **Arm/Disarm radar** buttons, and `Claude: …` under the photo.
4. Disarm the radar while you're standing in front of it — it sees people as readily as birds. Arm it, wave a bird printout in front of the **lens** at arm's length, and read the answer. The **BOOT** button on the board is a shutter too.
5. LED: green = radar armed, dim blue = disarmed, white flash = taking a photo, solid blue = waiting for Claude, red = camera timeout.

Tuning at the top of `cam_radar_ai.py`: `TRIGGER_ENERGY` (60), `MIN_CM`/`MAX_CM` (0–300), `COOLDOWN_S` (6), `FLIP_180`, `AI_PROMPT`. In `birdcam_helper.py`: `SPEAK = True` makes the Mac read each answer aloud; `MAX_IDENTIFICATIONS` caps one run.

## Files

| File | Runs on | Purpose |
|------|---------|---------|
| `cam_radar_ai.py` | board (as code.py) | **The project.** Radar trigger + camera + web page + AI (helper or direct). |
| `birdcam_helper.py` | Mac | Helper-mode companion: finds the board, does the Claude call for it. |
| `settings.toml.example` | board | Template for Wi-Fi and API settings. Never commit the real one. |
| `cam_radar_server.py` | board | Same without AI — radar-triggered camera + web viewer. |
| `cam_server.py` | board | Camera-only web viewer. |
| `d2check.py` | board | Verifies all 8 camera data lines using the sensor's color-bar pattern. |
| `radar_test.py` | board | Radar smoke test: decoded frames and the OUT pin. |
| `radar_probe.py` | board | Finds which GPIO the radar's TX is on. |
| `ssl_test.py` | board | Can this board make HTTPS calls on this network? Verdict in 15 s. |
| `boot.py` + `photo_test.py` | board | Save JPEGs to the CIRCUITPY drive (GPIO6→GND jumper at reset enables writing). |
| `diagnostics/` | board | `bitcheck.py`, `camera_probe.py`, `code_mirror.py` — tools used to debug the first build. |
| `BUILD-LOG.md` | — | Step-by-step build notes and everything that went wrong, in order. |

## Things that will bite you (learned the hard way)

- **Grayscale streaming can look perfect with a dead data line.** JPEG capture validates the `FF D8`/`FF D9` markers and silently drops every frame if bit 0 is stuck. Our D2 wire was in GPIO3 instead of GPIO2. `d2check.py` catches this in ten seconds.
- **`IDFError 0x20002` at camera init** happens about one start in five (an I2C write to the sensor failed). The scripts reload themselves. A failed `espcamera.Camera()` leaves its pins claimed until a reload.
- **The OV5640 doesn't answer on I2C until its clock is running.** Scan after init, not before.
- **Campus networks.** Boston College's registered-device Wi-Fi blocks all outbound HTTPS from the board (connection resets, substituted certificates). Helper mode exists because of this. Phone hotspots work only with real cellular signal; on an iPhone turn on *Maximize Compatibility* (2.4 GHz) and give the phone an ASCII name.
- **The board's IP can change** after moving between networks; the helper reads it from the serial console, and every heartbeat line starts with it. `birdcam.local` (mDNS) rarely resolves on campus.
- **macOS's bundled `python3` has no root certificates**; the helper notices and uses `curl` instead.
- **Occasional corrupted frames** (~1 in 30) over jumper-wire DVP: the helper asks the board to retake. If it gets frequent, drop `external_clock_frequency` from 20 MHz to 10 MHz.
- **Keep `settings.toml` out of the repo.** It holds your Wi-Fi password and API key; `.gitignore` excludes it.

## Next steps

Window mount (rotate the board or set `FLIP_180`; `cam.hmirror = True` to un-mirror), autofocus via the sensor's AF firmware (check the VM solder jumper), camera power-down between shots (PD pin), radar distance gates tuned through glass, battery power.

## Credits

Built by John Gallaugher (Boston College, Carroll School of Management) with Claude, September 2026, for the NSF *AI & Physical Computing* project. Camera wiring follows Adafruit's OV5640 breakout guide; radar protocol from Hi-Link's LD2410 serial documentation.
