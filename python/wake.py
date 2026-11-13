"""Wake-word listener for the Nicla Voice (USB serial).

The Nicla Voice sends "WAKE <label>\n" over USB serial when it hears the
wake word (see nicla_wake/nicla_wake.ino). Lines starting with "#" are debug.

Usage:
    import wake
    wake.start(on_wake)   # on_wake(label: str) is called once per detection
"""

import os
import threading
import time

import serial
from serial.tools import list_ports

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


def _log(msg):
    # flush=True so the line shows up immediately in the App Lab console
    print(msg, flush=True)


def _describe_ports():
    ports = list(list_ports.comports())
    if not ports:
        return "no serial ports at all"
    return "; ".join(
        f"{p.device} (vid={p.vid and hex(p.vid)}, pid={p.pid and hex(p.pid)}, {p.description})"
        for p in ports
    )


def _listen(on_wake):
    """(Re)connects to the Nicla, echoes EVERY line it sends, and dispatches WAKE events."""
    last_missing_log = 0.0
    while True:
        port = find_nicla_port()
        if port is None:
            # Log at most every 15 s so the console does not flood.
            if time.time() - last_missing_log > 15:
                _log(f"[wake] Nicla Voice not found. Ports: {_describe_ports()}")
                last_missing_log = time.time()
            time.sleep(3)
            continue
        try:
            with serial.Serial(port, NICLA_BAUD, timeout=1) as ser:
                _log(f"[wake] connected to {port} @ {NICLA_BAUD}")
                last_rx = time.time()
                silent_logged = False
                while True:
                    raw = ser.readline()
                    if not raw:
                        # No data in the last second. Say so once per 10 s of silence.
                        if time.time() - last_rx > 10 and not silent_logged:
                            _log("[wake] no data from Nicla for 10 s (heartbeat missing - "
                                 "board stuck, resetting or not running nicla_wake.ino?)")
                            silent_logged = True
                        continue
                    last_rx = time.time()
                    silent_logged = False
                    line = raw.decode(errors="replace").rstrip("\r\n")
                    if not line.strip():
                        continue
                    _log(f"[nicla] {line}")  # every line, including WAKE
                    if line.startswith("WAKE"):
                        on_wake(line[4:].strip())
        except (serial.SerialException, OSError) as e:
            _log(f"[wake] serial error on {port}: {e} - reconnecting in 3 s")
            time.sleep(3)
        else:
            _log("[wake] serial loop ended, reconnecting")
            time.sleep(1)


def start(on_wake):
    """Start the listener in a background daemon thread and return the thread."""
    t = threading.Thread(target=_listen, args=(on_wake,), daemon=True)
    t.start()
    return t
