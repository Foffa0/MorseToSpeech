"""Wake-word listener for the Nicla Voice over Bluetooth LE, fully non-blocking.

The Nicla Voice (nicla_wake/nicla_wake.ino) is a BLE peripheral named "NiclaWake".
It sends only the wake signal: a uint32 little-endian counter on WAKE_CHAR_UUID
that is incremented and notified once per detected "Alexa". All debug output
stays on the Nicla's own USB serial port (open it with the Serial Monitor).

Usage:
    import wake
    wake.start(on_wake)   # returns immediately; on_wake(label: str) runs once per detection

Env vars:
    NICLA_BLE_ADDRESS   connect to this MAC address instead of scanning for the service

Guarantees:
  * start() never blocks and never raises; everything runs in daemon threads.
  * A missing bleak, a missing/out-of-range board or BLE errors are logged and
    retried - they never crash the app.
  * on_wake runs in its own worker thread, so a slow callback never stalls the
    BLE loop. Wakes arriving while on_wake is still running are dropped (logged),
    they do not pile up.
"""

import asyncio
import os
import queue
import threading
import time

BLE_NAME = "NiclaWake"
SERVICE_UUID = "4a8b0001-6c1d-4b7e-9f2a-3d5e7c9b1a20"
WAKE_CHAR_UUID = "4a8b0002-6c1d-4b7e-9f2a-3d5e7c9b1a20"
WAKE_LABEL = "Alexa"
SCAN_TIMEOUT_S = 10
CONNECT_TIMEOUT_S = 15
RECONNECT_DELAY_S = 3
NOT_FOUND_LOG_EVERY_S = 30
CONNECTION_CHECK_S = 5

_started = False
_start_lock = threading.Lock()
_wake_queue = queue.Queue(maxsize=1)


def _log(msg):
    try:
        print(msg, flush=True)
    except Exception:
        pass


# --- wake callback worker -------------------------------------------------------
def _wake_worker(on_wake):
    while True:
        label = _wake_queue.get()
        try:
            on_wake(label)
        except Exception as e:  # never let the callback kill the worker
            _log(f"[wake] on_wake({label!r}) raised {type(e).__name__}: {e}")


def _dispatch_wake(label):
    try:
        _wake_queue.put_nowait(label)
    except queue.Full:
        _log(f"[wake] {label} ignored - previous wake still being handled")


# --- BLE ------------------------------------------------------------------------
def _is_nicla(device, adv):
    uuids = [u.lower() for u in (adv.service_uuids or [])]
    return SERVICE_UUID in uuids or (adv.local_name or device.name) == BLE_NAME


async def _find_device(BleakScanner):
    address = os.environ.get("NICLA_BLE_ADDRESS")
    if address:
        return await BleakScanner.find_device_by_address(address, timeout=SCAN_TIMEOUT_S)
    return await BleakScanner.find_device_by_filter(_is_nicla, timeout=SCAN_TIMEOUT_S)


_ADDRESS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".nicla_address")


def _load_address():
    try:
        with open(_ADDRESS_FILE) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _save_address(address):
    try:
        if address and address != _load_address():
            with open(_ADDRESS_FILE, "w") as f:
                f.write(address)
    except OSError:
        pass  # purely an optimisation, never fatal


async def _drop_stale_link():
    """A connected BLE peripheral stops advertising. After an unclean app restart BlueZ
    can still hold the old link, which hides the Nicla from the scan. Disconnect any
    connected BlueZ device that is our Nicla (by name or saved address). Returns True
    if one was disconnected. Never raises."""
    try:
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus
        bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        try:
            root = await bus.introspect("org.bluez", "/")
            om = bus.get_proxy_object("org.bluez", "/", root).get_interface(
                "org.freedesktop.DBus.ObjectManager")
            saved = os.environ.get("NICLA_BLE_ADDRESS") or _load_address()
            dropped = False
            for path, ifaces in (await om.call_get_managed_objects()).items():
                dev = ifaces.get("org.bluez.Device1")
                if not dev or not dev["Connected"].value:
                    continue
                name = dev["Name"].value if "Name" in dev else ""
                addr = dev["Address"].value
                if name == BLE_NAME or (saved and addr.upper() == saved.upper()):
                    _log(f"[wake] {addr} still connected from an earlier run - disconnecting")
                    intro = await bus.introspect("org.bluez", path)
                    iface = bus.get_proxy_object("org.bluez", path, intro).get_interface(
                        "org.bluez.Device1")
                    await iface.call_disconnect()
                    dropped = True
            return dropped
        finally:
            bus.disconnect()
    except Exception as e:
        _log(f"[wake] could not check for a stale BLE link: {type(e).__name__}: {e}")
        return False


def _parse_counter(data):
    if len(data) < 4:
        return None
    return int.from_bytes(bytes(data[:4]), "little")


async def _session(BleakClient, device):
    """One connection to the board; returns when it disconnects."""
    disconnected = asyncio.Event()
    state = {"last": None}

    def on_notify(_sender, data):
        count = _parse_counter(data)
        if count is None:
            _log(f"[wake] ignoring malformed notification {bytes(data)!r}")
            return
        last = state["last"]
        state["last"] = count
        if last is not None and count == last:
            return  # duplicate
        if last is not None and count > last + 1:
            _log(f"[wake] {count - last - 1} wake(s) missed (counter {last} -> {count})")
        _log(f"[nicla] WAKE {WAKE_LABEL} #{count}")
        _dispatch_wake(WAKE_LABEL)

    async with BleakClient(device, timeout=CONNECT_TIMEOUT_S,
                           disconnected_callback=lambda _c: disconnected.set()) as client:
        address = getattr(device, "address", device)
        _save_address(address)
        _log(f"[wake] connected to {getattr(device, 'name', None) or BLE_NAME} ({address})")
        try:
            state["last"] = _parse_counter(await client.read_gatt_char(WAKE_CHAR_UUID))
        except Exception as e:
            _log(f"[wake] could not read wake counter: {e}")
        await client.start_notify(WAKE_CHAR_UUID, on_notify)
        _log(f"[wake] listening for wake notifications (board counter: {state['last']})")
        while client.is_connected and not disconnected.is_set():
            try:
                await asyncio.wait_for(disconnected.wait(), CONNECTION_CHECK_S)
            except asyncio.TimeoutError:
                pass
    _log("[wake] Nicla disconnected, reconnecting")


async def _listen_async(BleakScanner, BleakClient):
    last_missing_log = float("-inf")
    while True:
        try:
            device = await _find_device(BleakScanner)
            if device is None and await _drop_stale_link():
                continue  # it advertises again now; rescan right away
            if device is None:
                if time.monotonic() - last_missing_log > NOT_FOUND_LOG_EVERY_S:
                    _log(f"[wake] Nicla Voice ('{BLE_NAME}') not found over BLE - is it powered, "
                         "running nicla_wake.ino and in range? (still scanning)")
                    last_missing_log = time.monotonic()
                continue  # the scan itself already waited SCAN_TIMEOUT_S
            await _session(BleakClient, device)
        except Exception as e:  # anything (BlueZ down, connect timeout, ...): log and retry
            _log(f"[wake] BLE error {type(e).__name__}: {e} - retrying in {RECONNECT_DELAY_S} s")
        await asyncio.sleep(RECONNECT_DELAY_S)


def _use_dbus_bridge():
    """Inside the App Lab container the host's system D-Bus is not mounted, so bleak
    cannot reach BlueZ. A host-side user service (dbus-bridge.service, socat) exposes
    it as /app/.dbus.sock; use that only if the normal socket is missing and the
    bridge exists. Does nothing on a laptop or if the bridge is not installed."""
    if os.environ.get("DBUS_SYSTEM_BUS_ADDRESS"):
        return
    bridge = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".dbus.sock")
    if not os.path.exists("/var/run/dbus/system_bus_socket") and os.path.exists(bridge):
        os.environ["DBUS_SYSTEM_BUS_ADDRESS"] = f"unix:path={bridge}"
        _log(f"[wake] using D-Bus bridge {bridge}")


def _listen():
    _use_dbus_bridge()
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError:
        _log("[wake] bleak is not installed - add 'bleak' to python/requirements.txt. "
             "Wake word disabled, app keeps running.")
        return
    while True:
        try:
            asyncio.run(_listen_async(BleakScanner, BleakClient))
        except Exception as e:
            _log(f"[wake] BLE loop crashed ({type(e).__name__}: {e}) - restarting")
        time.sleep(RECONNECT_DELAY_S)


def start(on_wake):
    """Start the listener in background daemon threads. Never blocks, never raises.

    Calling it more than once has no effect.
    """
    global _started
    with _start_lock:
        if _started:
            return
        _started = True
    try:
        threading.Thread(target=_wake_worker, args=(on_wake,), name="nicla-wake-cb",
                         daemon=True).start()
        threading.Thread(target=_listen, name="nicla-ble", daemon=True).start()
        _log("[wake] Nicla wake-word listener (BLE) started")
    except Exception as e:
        _log(f"[wake] could not start listener: {e}")
