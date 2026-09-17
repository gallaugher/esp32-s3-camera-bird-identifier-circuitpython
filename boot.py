# boot.py - runs once at power-up / hard reset, before USB mounts CIRCUITPY.
#
# "Photo mode" switch: a jumper wire from GPIO6 to GND.
#   wire IN  -> CircuitPython may WRITE to CIRCUITPY (photos get saved);
#               the Mac sees the drive as read-only until you reset without the wire
#   wire OUT -> normal: the Mac can edit code.py, CircuitPython cannot write files
#
# GPIO6 is the course "Button 1" pin, so a real button there works the same way.

import board
import digitalio
import storage

switch = digitalio.DigitalInOut(board.GPIO6)
switch.switch_to_input(pull=digitalio.Pull.UP)

if not switch.value:  # pulled to GND = photo mode
    storage.remount("/", readonly=False)
    print("boot.py: photo mode - CircuitPython can save files; Mac sees CIRCUITPY read-only")
else:
    print("boot.py: edit mode - Mac can write; photos will NOT save")

switch.deinit()
