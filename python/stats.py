"""Device statistics for the web page: CPU, RAM, temperature, power, app usage.

Reads /proc and /sys directly (no psutil -> no extra package to install on the board).
A background thread samples once per second and keeps the last 2 minutes for the charts.

Power: shown as "measured" only if the board exposes a power/current sensor in /sys.
Otherwise it is estimated from CPU load, but ONLY if you set your own measurements:
    MORSE_IDLE_W=<watts at idle>  MORSE_FULL_W=<watts at 100 % CPU>
(measure both with a USB-C power meter). Without them, no power number is shown.
"""

import glob
import os
import threading
import time
from collections import deque

SAMPLE_S = 1.0
HISTORY = 120          # samples kept for the charts (2 min)


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


def _env_float(name):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return None


class SystemStats:
    def __init__(self):
        self._lock = threading.Lock()
        self.cpu_history = deque(maxlen=HISTORY)   # % of all cores
        self.ram_history = deque(maxlen=HISTORY)   # % used
        self._prev_cpu = None
        self._prev_proc = None
        self._started = time.time()
        self._app_cpu_s = 0.0
        self.idle_w = _env_float("MORSE_IDLE_W")
        self.full_w = _env_float("MORSE_FULL_W")
        self._energy_wh = 0.0                      # integrated from measured/estimated power
        self._timings = {}                         # name -> [count, total_ms, last_ms]
        self.cores = os.cpu_count() or 1

    # --- app-level counters (called from main.py) --------------------------------
    def record(self, name, ms):
        """Remember how long an operation took, e.g. record('translate', 180)."""
        with self._lock:
            c = self._timings.setdefault(name, [0, 0.0, 0.0])
            c[0] += 1
            c[1] += ms
            c[2] = ms

    def timed(self, name):
        stats = self

        class _Timer:
            def __enter__(self):
                self.t = time.perf_counter()

            def __exit__(self, *exc):
                stats.record(name, (time.perf_counter() - self.t) * 1000)
        return _Timer()

    # --- sampling ------------------------------------------------------------------
    def start(self):
        threading.Thread(target=self._run, daemon=True).start()
        return self

    def _run(self):
        while True:
            try:
                self.sample()
            except Exception as e:  # never let statistics break the app
                print(f"[stats] {e}", flush=True)
            time.sleep(SAMPLE_S)

    def sample(self):
        cpu = self._cpu_percent()
        ram = self._ram()
        app_cpu = self._app_cpu_delta()
        power = self._power(cpu)
        with self._lock:
            if cpu is not None:
                self.cpu_history.append(round(cpu, 1))
            if ram:
                self.ram_history.append(round(100 * ram[0] / ram[1], 1))
            self._app_cpu_s += app_cpu
            if power and power["watts"] is not None:
                self._energy_wh += power["watts"] * SAMPLE_S / 3600

    def _cpu_percent(self):
        line = _read("/proc/stat")
        if not line:
            return None
        fields = [int(x) for x in line.splitlines()[0].split()[1:]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)   # idle + iowait
        total = sum(fields[:8])
        prev, self._prev_cpu = self._prev_cpu, (idle, total)
        if not prev or total == prev[1]:
            return None
        return 100.0 * (1 - (idle - prev[0]) / (total - prev[1]))

    @staticmethod
    def _ram():
        text = _read("/proc/meminfo")
        if not text:
            return None
        info = {}
        for line in text.splitlines():
            key, _, rest = line.partition(":")
            info[key] = int(rest.split()[0]) * 1024
        total = info.get("MemTotal")
        avail = info.get("MemAvailable", info.get("MemFree"))
        return (total - avail, total) if total and avail is not None else None

    def _app_cpu_delta(self):
        """CPU seconds used by this app (all threads) since the last sample."""
        text = _read("/proc/self/stat")
        if not text:
            return 0.0
        parts = text.rsplit(")", 1)[1].split()
        ticks = int(parts[11]) + int(parts[12])          # utime + stime
        hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        secs = ticks / hz
        prev, self._prev_proc = self._prev_proc, secs
        return secs - prev if prev is not None else 0.0

    @staticmethod
    def _app_ram():
        text = _read("/proc/self/status")
        if not text:
            return None
        for line in text.splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
        return None

    @staticmethod
    def _temperature():
        temps = []
        for zone in glob.glob("/sys/class/thermal/thermal_zone*/temp"):
            raw = _read(zone)
            if raw and raw.lstrip("-").isdigit():
                t = int(raw) / 1000
                if 0 < t < 150:
                    temps.append(t)
        return max(temps) if temps else None

    def _power(self, cpu):
        # 1) a real sensor (hwmon power or voltage*current, or a power_supply)
        for d in glob.glob("/sys/class/hwmon/hwmon*"):
            p = _read(os.path.join(d, "power1_input"))          # microwatts
            if p and p.isdigit():
                return {"watts": int(p) / 1e6, "source": "measured"}
            v, i = _read(os.path.join(d, "in1_input")), _read(os.path.join(d, "curr1_input"))
            if v and i and v.isdigit() and i.isdigit():        # millivolts, milliamps
                return {"watts": int(v) * int(i) / 1e6, "source": "measured"}
        for d in glob.glob("/sys/class/power_supply/*"):
            p = _read(os.path.join(d, "power_now"))
            if p and p.isdigit() and int(p) > 0:
                return {"watts": int(p) / 1e6, "source": "measured"}
        # 2) estimate from CPU load, only with the user's own measurements
        if self.idle_w is not None and self.full_w is not None and cpu is not None:
            return {"watts": self.idle_w + (self.full_w - self.idle_w) * cpu / 100,
                    "source": "estimated"}
        return {"watts": None, "source": "unavailable"}

    # --- for the web page -------------------------------------------------------------
    def snapshot(self):
        with self._lock:
            cpu_hist = list(self.cpu_history)
            ram_hist = list(self.ram_history)
            timings = {k: {"count": c, "avg_ms": round(t / c), "last_ms": round(last)}
                       for k, (c, t, last) in self._timings.items()}
            app_cpu_s = self._app_cpu_s
            energy_wh = self._energy_wh
        ram = self._ram()
        uptime = time.time() - self._started
        power = self._power(cpu_hist[-1] if cpu_hist else None)
        return {
            "cpu": {"percent": cpu_hist[-1] if cpu_hist else None, "history": cpu_hist,
                    "cores": self.cores},
            "ram": {"used": ram[0] if ram else None, "total": ram[1] if ram else None,
                    "percent": ram_hist[-1] if ram_hist else None, "history": ram_hist},
            "app": {"ram": self._app_ram(), "cpu_seconds": round(app_cpu_s, 1),
                    "cpu_percent_avg": round(100 * app_cpu_s / uptime / self.cores, 1)
                    if uptime > 0 else None},
            "temperature": self._temperature(),
            "power": {**power, "energy_wh": round(energy_wh, 4)
                      if power["source"] != "unavailable" else None},
            "uptime_s": round(uptime),
            "sample_s": SAMPLE_S,
            "timings": timings,
        }
