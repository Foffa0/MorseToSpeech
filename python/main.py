import os
import threading
import time

import serial
from serial.tools import list_ports

from arduino.app_utils import App

# --- Wake word (Nicla Voice over USB) -------------------------------------
# The Nicla Voice sends "WAKE <label>\n" over USB serial when it hears the
# wake word (see nicla_wake/nicla_wake.ino). Lines starting with "#" are debug.

NICLA_BAUD = 115200
ARDUINO_USB_VID = 0x2341


def find_nicla_port():
    """Return the Nicla's serial port: $NICLA_PORT, else first Arduino/ttyACM device."""
    if os.environ.get("NICLA_PORT"):
        return os.environ["NICLA_PORT"]
    ports = list(list_ports.comports())
    for p in ports:
        if p.vid == ARDUINO_USB_VID:
            return p.device
    for p in ports:
        if "ttyACM" in p.device:
            return p.device
    return None


def on_wake(label):
    """Called once per detected wake word.

    TODO (teammates): start recording / speech-to-text / Morse translation here.
    """
    print(f"Wake word detected ({label}) - start listening")


def wake_listener():
    """Background thread: (re)connects to the Nicla and dispatches WAKE events."""
    while True:
        port = find_nicla_port()
        if port is None:
            print("Nicla Voice not found, retrying in 3 s")
            time.sleep(3)
            continue
        try:
            with serial.Serial(port, NICLA_BAUD, timeout=1) as ser:
                print(f"Listening for wake word on {port}")
                while True:
                    line = ser.readline().decode(errors="ignore").strip()
                    if line.startswith("WAKE"):
                        on_wake(line[4:].strip())
                    elif line:
                        print(f"[nicla] {line}")
        except serial.SerialException as e:
            print(f"Nicla serial error: {e} - reconnecting")
            time.sleep(3)


threading.Thread(target=wake_listener, daemon=True).start()


def loop():
    """This function is called repeatedly by the App framework."""
    time.sleep(10)


# See: https://docs.arduino.cc/software/app-lab/tutorials/getting-started/#app-run
App.run(user_loop=loop)
