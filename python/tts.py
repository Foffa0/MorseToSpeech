"""Offline text-to-speech in a background thread.

Engine order: Piper (natural voice) -> espeak-ng (robotic, but tiny) -> print only.
"""

import os
import queue
import shutil
import subprocess
import tempfile
import threading
import wave

VOICE_PATH = os.path.join(os.path.dirname(__file__), "voices", "en_US-lessac-medium.onnx")


class Speaker:
    def __init__(self, voice_path=VOICE_PATH):
        self._queue = queue.Queue()
        self._piper = self._load_piper(voice_path)
        self._espeak = shutil.which("espeak-ng") or shutil.which("espeak")
        self._aplay = shutil.which("aplay")
        if self._piper:
            self.engine = "piper"
        elif self._espeak:
            self.engine = "espeak-ng"
        else:
            self.engine = "none"
        print(f"[tts] engine: {self.engine}", flush=True)
        threading.Thread(target=self._worker, daemon=True).start()

    def say(self, text):
        text = (text or "").strip()
        if text:
            self._queue.put(text)

    # --- internals ------------------------------------------------------------
    @staticmethod
    def _load_piper(voice_path):
        if not os.path.exists(voice_path):
            print(f"[tts] Piper voice not found: {voice_path}", flush=True)
            return None
        try:
            from piper import PiperVoice
            return PiperVoice.load(voice_path)
        except Exception as e:  # missing package, onnxruntime problem, ...
            print(f"[tts] Piper unavailable: {e}", flush=True)
            return None

    def _worker(self):
        while True:
            text = self._queue.get()
            print(f"[tts] speaking: {text}", flush=True)
            try:
                if self._piper:
                    self._speak_piper(text)
                elif self._espeak:
                    subprocess.run([self._espeak, "-v", "en-us", "-s", "150", text], check=False)
            except Exception as e:
                print(f"[tts] error: {e}", flush=True)

    def _speak_piper(self, text):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as wav:
                if hasattr(self._piper, "synthesize_wav"):   # piper-tts >= 1.3
                    self._piper.synthesize_wav(text, wav)
                else:                                        # piper-tts 1.2
                    self._piper.synthesize(text, wav)
            self._play(path)
        finally:
            os.remove(path)

    def _play(self, path):
        if self._aplay:
            subprocess.run([self._aplay, "-q", path], check=False)
            return
        import numpy as np
        import sounddevice as sd
        with wave.open(path, "rb") as wav:
            data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
            sd.play(data, wav.getframerate(), blocking=True)


if __name__ == "__main__":
    # Quick audio check: python tts.py "hello world"
    import sys
    import time
    Speaker().say(" ".join(sys.argv[1:]) or "Hello, I am Morse to Speech.")
    time.sleep(5)
