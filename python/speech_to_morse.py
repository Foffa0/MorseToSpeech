"""Speech -> Morse: listen to the conversation partner, transcribe offline (Vosk),
then output the sentence as Morse beeps (headset) and LED blinks (via callback).

Entry point for the wake word (Nicla Voice): SpeechToMorse.activate()
Laptop test:
    python speech_to_morse.py "hello"      # only play Morse beeps for a text
    python speech_to_morse.py --listen     # listen on the mic, then play Morse
"""

import json
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import wave
from array import array

from morse import encode, timeline

MODEL_PATH = os.path.join(os.path.dirname(__file__), "models", "vosk-model-small-en-us-0.15")
SAMPLE_RATE = 16000
UNIT_MS = 80             # Morse output speed (80 ms dot ~ 15 words per minute)
TONE_HZ = 700
MAX_LISTEN_S = 8         # stop listening after this, even without a pause
CHUNK_BYTES = 4000       # 125 ms of 16 kHz mono 16-bit audio


class SpeechToMorse:
    def __init__(self, on_morse=None, unit_ms=UNIT_MS, model_path=MODEL_PATH):
        """on_morse(text, code): called before playback, e.g. to blink LEDs on the MCU."""
        self.on_morse = on_morse
        self.unit_ms = unit_ms
        self._busy = threading.Lock()
        self._arecord = shutil.which("arecord")
        self._aplay = shutil.which("aplay")
        self._model = self._load_model(model_path)

    # --- public API -----------------------------------------------------------
    def activate(self):
        """Wake word detected: listen once and send the sentence as Morse. Non-blocking.
        Returns False if a listening session is already running."""
        if not self._busy.acquire(blocking=False):
            print("[s2m] already listening, wake ignored", flush=True)
            return False
        threading.Thread(target=self._session, daemon=True).start()
        return True

    def send(self, text):
        """Convert text to Morse and play it (beeps + on_morse callback). Blocking."""
        code = encode(text)
        if not code:
            return
        print(f"[s2m] {text.upper()}  ->  {code}", flush=True)
        if self.on_morse:
            self.on_morse(text, code)
        self._play_beeps(timeline(code, self.unit_ms))

    def listen(self):
        """Record until the speaker pauses (or MAX_LISTEN_S). Returns the recognized text."""
        if self._model is None:
            print("[s2m] no Vosk model, cannot listen", flush=True)
            return ""
        from vosk import KaldiRecognizer
        rec = KaldiRecognizer(self._model, SAMPLE_RATE)
        print("[s2m] listening...", flush=True)
        text = ""
        deadline = time.monotonic() + MAX_LISTEN_S
        for chunk in self._mic_chunks():
            if rec.AcceptWaveform(chunk):          # Vosk detected end of an utterance
                text = json.loads(rec.Result()).get("text", "")
                if text:
                    break
            if time.monotonic() > deadline:
                text = json.loads(rec.FinalResult()).get("text", "")
                break
        print(f"[s2m] heard: {text!r}", flush=True)
        return text

    # --- internals ------------------------------------------------------------
    def _session(self):
        try:
            text = self.listen()
            if text:
                self.send(text)
        except Exception as e:
            print(f"[s2m] error: {e}", flush=True)
        finally:
            self._busy.release()

    @staticmethod
    def _load_model(path):
        if not os.path.isdir(path):
            print(f"[s2m] Vosk model not found: {path}", flush=True)
            return None
        try:
            from vosk import Model, SetLogLevel
            SetLogLevel(-1)
            return Model(path)
        except Exception as e:
            print(f"[s2m] Vosk unavailable: {e}", flush=True)
            return None

    def _mic_chunks(self):
        if self._arecord:  # Linux / UNO Q
            proc = subprocess.Popen(
                [self._arecord, "-q", "-f", "S16_LE", "-r", str(SAMPLE_RATE), "-c", "1", "-t", "raw"],
                stdout=subprocess.PIPE)
            try:
                while True:
                    data = proc.stdout.read(CHUNK_BYTES)
                    if not data:
                        return
                    yield data
            finally:
                proc.kill()
        else:              # laptop fallback
            import sounddevice as sd
            with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=CHUNK_BYTES // 2,
                                   dtype="int16", channels=1) as stream:
                while True:
                    data, _ = stream.read(CHUNK_BYTES // 2)
                    yield bytes(data)

    def _play_beeps(self, tl):
        samples = array("h")
        ramp = int(SAMPLE_RATE * 0.005)  # 5 ms fade in/out, avoids clicks
        for on, ms in tl:
            n = int(SAMPLE_RATE * ms / 1000)
            if not on:
                samples.extend([0] * n)
                continue
            for i in range(n):
                env = min(1.0, i / ramp, (n - i) / ramp)
                samples.append(int(12000 * env * math.sin(2 * math.pi * TONE_HZ * i / SAMPLE_RATE)))
        samples.extend([0] * int(SAMPLE_RATE * 0.2))

        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(samples.tobytes())
            if self._aplay:
                subprocess.run([self._aplay, "-q", path], check=False)
            elif os.name == "nt":
                import winsound
                winsound.PlaySound(path, winsound.SND_FILENAME)
            else:
                print("[s2m] no audio player found", flush=True)
        finally:
            os.remove(path)


if __name__ == "__main__":
    import sys
    s2m = SpeechToMorse()
    if "--listen" in sys.argv:
        heard = s2m.listen()
        if heard:
            s2m.send(heard)
    else:
        s2m.send(" ".join(sys.argv[1:]) or "SOS")
