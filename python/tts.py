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

VOICES_DIR = os.path.join(os.path.dirname(__file__), "voices")
VOICE_PATH = os.path.join(VOICES_DIR, "en_US-lessac-medium.onnx")
# One voice per language. Extra languages are loaded on first use (saves RAM if unused).
VOICE_PATHS = {
    "en": VOICE_PATH,
    "de": os.path.join(VOICES_DIR, "de_DE-thorsten-medium.onnx"),
}
ESPEAK_LANG = {"en": "en-us", "de": "de"}


class Speaker:
    def __init__(self, voice_path=VOICE_PATH, voice_paths=None, lang="en"):
        self._queue = queue.Queue()
        self.lang = lang                          # default language for say()
        self._voice_paths = dict(VOICE_PATHS if voice_paths is None else voice_paths)
        self._voice_paths[lang] = voice_path
        self._voices = {lang: self._load_piper(voice_path)}
        self._piper = self._voices[lang]
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

    def say(self, text, lang=None):
        """Queue text to speak, optionally in another language ('de')."""
        text = (text or "").strip()
        if text:
            self._queue.put((text, lang or self.lang))

    def _voice(self, lang):
        if lang not in self._voices:
            path = self._voice_paths.get(lang)
            self._voices[lang] = self._load_piper(path) if path else None
        return self._voices[lang]

    # --- internals ------------------------------------------------------------
    @staticmethod
    def _load_piper(voice_path):
        if not voice_path or not os.path.exists(voice_path):
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
            text, lang = self._queue.get()
            print(f"[tts] speaking ({lang}): {text}", flush=True)
            try:
                voice = self._voice(lang)
                if voice:
                    self._speak_piper(text, voice)
                elif self._espeak:
                    subprocess.run([self._espeak, "-v", ESPEAK_LANG.get(lang, lang), "-s", "150",
                                    text], check=False)
            except Exception as e:
                print(f"[tts] error: {e}", flush=True)

    def _speak_piper(self, text, voice):
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            with wave.open(path, "wb") as wav:
                if hasattr(voice, "synthesize_wav"):   # piper-tts >= 1.3
                    voice.synthesize_wav(text, wav)
                else:                                  # piper-tts 1.2
                    voice.synthesize(text, wav)
            self._play(path)
        finally:
            os.remove(path)

    def _play(self, path):
        if self._aplay:
            subprocess.run([self._aplay, "-q", path], check=False)
            return
        if os.name == "nt":                     # laptop testing on Windows
            import winsound
            winsound.PlaySound(path, winsound.SND_FILENAME)
            return
        import numpy as np
        import sounddevice as sd
        with wave.open(path, "rb") as wav:
            data = np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16)
            sd.play(data, wav.getframerate(), blocking=True)


if __name__ == "__main__":
    # Quick audio check: python tts.py "hello world"   or   python tts.py --de "Hallo"
    import sys
    import time
    args = sys.argv[1:]
    lang = "de" if args[:1] == ["--de"] else "en"
    args = args[1:] if lang == "de" else args
    Speaker().say(" ".join(args) or "Hello, I am Morse to Speech.", lang=lang)
    time.sleep(6)
