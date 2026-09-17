# ssl_test.py - can the board make HTTPS calls on this network?
# Copy to CIRCUITPY as code.py, Ctrl-D. Uses the Wi-Fi lines in settings.toml.
# Any HTTP status code (200, 401, 404, 405...) means TLS worked. "FAIL" means it didn't.

import os
import time
import wifi
import adafruit_connection_manager
import adafruit_requests

ssid = os.getenv("CIRCUITPY_WIFI_SSID")
if not wifi.radio.connected:
    print("Connecting to", ssid, "...")
    wifi.radio.connect(ssid, os.getenv("CIRCUITPY_WIFI_PASSWORD"))
print("Wi-Fi '%s'  IP %s\n" % (ssid, wifi.radio.ipv4_address))

pool = adafruit_connection_manager.get_radio_socketpool(wifi.radio)
ssl = adafruit_connection_manager.get_radio_ssl_context(wifi.radio)
requests = adafruit_requests.Session(pool, ssl)

TESTS = [
    ("Adafruit test page", "https://wifitest.adafruit.com/testwifi/index.html"),
    ("Google", "https://www.google.com/"),
    ("Anthropic API", "https://api.anthropic.com/v1/messages"),   # no key sent; expect 401/405
]

results = {}
dns_failed = False
for name, url in TESTS:
    t0 = time.monotonic()
    try:
        r = requests.get(url, timeout=20)
        print("%-20s OK    HTTP %d in %.1fs   %r" % (name, r.status_code, time.monotonic() - t0, r.text[:50]))
        results[name] = True
        r.close()
    except Exception as e:  # pylint: disable=broad-except
        print("%-20s FAIL  %r" % (name, e))
        results[name] = False
        if "gaierror" in repr(e) or "Name or service" in repr(e):
            dns_failed = True

print("\n==== VERDICT ====")
if all(results.values()):
    print("HTTPS works everywhere from this network. The demo's AI call should work - Ctrl-D with the demo code and retry.")
elif not any(results.values()) and dns_failed:
    print("Joined '%s' but it has NO INTERNET behind it: DNS lookups fail." % ssid)
    print("On a phone hotspot that means weak cellular signal - move the phone to a window, or use another network.")
elif not any(results.values()):
    print("No HTTPS at all for the board on '%s'. This network blocks or intercepts the board's" % ssid)
    print("encrypted traffic (X509 = a substituted certificate). Fix: a phone hotspot or home Wi-Fi.")
elif not results["Anthropic API"]:
    print("General HTTPS works but api.anthropic.com is blocked/intercepted on '%s'." % ssid)
    print("Fix for the demo: phone hotspot in settings.toml (Mac joins the same hotspot).")
else:
    print("Mixed results - a flaky connection. Ctrl-D to run again.")
