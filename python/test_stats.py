"""Laptop tests for stats.py with fake /proc and /sys files: python -m pytest python/test_stats.py"""

import os

import stats
from stats import SystemStats

FILES = {
    "/proc/stat": "cpu  100 0 100 800 0 0 0 0 0 0\ncpu0 1 2 3 4\n",
    "/proc/meminfo": "MemTotal:  2000000 kB\nMemFree:  500000 kB\nMemAvailable:  1500000 kB\n",
    "/proc/self/status": "Name:\tpython\nVmRSS:\t  204800 kB\n",
    "/proc/self/stat": "1234 (python main) S 1 2 3 4 5 6 7 8 9 10 300 200 0 0",
}


def fake(monkeypatch, files, temps=(), hwmon=None):
    monkeypatch.setattr(stats, "_read", lambda p: files.get(p) if p in files else
                        (hwmon or {}).get(p))
    monkeypatch.setattr(stats.glob, "glob", lambda pattern:
                        list(temps) if "thermal" in pattern else
                        (["/sys/class/hwmon/hwmon0"] if hwmon and "hwmon" in pattern else []))


def test_cpu_ram_and_app(monkeypatch):
    fake(monkeypatch, dict(FILES))
    s = SystemStats()
    s.sample()                                            # first sample: no delta yet
    FILES2 = dict(FILES, **{"/proc/stat": "cpu  150 0 150 900 0 0 0 0 0 0\n"})
    fake(monkeypatch, FILES2)
    s.sample()
    snap = s.snapshot()
    assert snap["cpu"]["percent"] == 50.0                 # 100 busy of 200 ticks
    assert snap["ram"]["used"] == 500000 * 1024 and snap["ram"]["percent"] == 25.0
    assert snap["app"]["ram"] == 204800 * 1024
    assert snap["power"]["source"] == "unavailable" and snap["power"]["watts"] is None


def test_temperature_and_measured_power(monkeypatch):
    files = dict(FILES, **{"/sys/class/thermal/thermal_zone0/temp": "48500",
                           "/sys/class/thermal/thermal_zone1/temp": "52000"})
    fake(monkeypatch, files, temps=["/sys/class/thermal/thermal_zone0/temp",
                                    "/sys/class/thermal/thermal_zone1/temp"],
         hwmon={os.path.join("/sys/class/hwmon/hwmon0", "power1_input"): "3250000"})
    snap = SystemStats().snapshot()
    assert snap["temperature"] == 52.0
    assert snap["power"] == {"watts": 3.25, "source": "measured", "energy_wh": 0.0}


def test_power_estimate_only_with_user_values(monkeypatch):
    fake(monkeypatch, dict(FILES))
    monkeypatch.setenv("MORSE_IDLE_W", "2")
    monkeypatch.setenv("MORSE_FULL_W", "6")
    s = SystemStats()
    assert s._power(50.0) == {"watts": 4.0, "source": "estimated"}


def test_missing_proc_files_dont_crash(monkeypatch):
    fake(monkeypatch, {})
    s = SystemStats()
    s.sample()
    snap = s.snapshot()
    assert snap["cpu"]["percent"] is None and snap["ram"]["used"] is None


def test_timings():
    s = SystemStats()
    s.record("translate", 100)
    s.record("translate", 300)
    assert s.snapshot()["timings"]["translate"] == {"count": 2, "avg_ms": 200, "last_ms": 300}
