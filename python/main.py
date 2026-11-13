"""MorseToSpeech: Modulino Buttons -> adaptive Morse decoder -> console + offline TTS.
Web page (WebUI brick, port 7000): live conversation + language selection."""

import os
import threading
import time

from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Bridge

from languages import LanguageSettings
from live import LiveState
from morse import MorseDecoder, encode
from speech_to_morse import SpeechToMorse
from stats import SystemStats
from translate import Translator
from tts import VOICE_PATHS, Speaker

# --- Languages ------------------------------------------------------------------
# The user taps Morse in their language. If the partner speaks another language,
# everything is translated offline in both directions. Changeable on the web page.
translator = Translator()
speaker = Speaker()
live = LiveState()   # what the web page shows (see live.py)
stats = SystemStats().start()   # CPU / RAM / power for the web page (see stats.py)


def blink_morse_on_mcu(text, code):
    try:
        Bridge.notify("play_morse", code)   # Modulino LEDs blink the Morse code
    except Exception as e:
        print(f"[s2m] could not send to MCU: {e}", flush=True)


def translate_partner_speech(text):
    if langs.translating_in:
        with stats.timed("translate"):
            return translator.translate(text, langs.partner, langs.user)
    return text


def on_language_change(user, partner):
    # loading models takes a moment -> in the background
    def load():
        speech_to_morse.set_lang(partner)
        if langs.translating_out:
            translator.preload((user, partner))
        if langs.translating_in:
            translator.preload((partner, user))
    threading.Thread(target=load, daemon=True).start()


langs = LanguageSettings(
    translator,
    on_change=on_language_change,
    stt_available=SpeechToMorse.stt_available,
    voice_available=lambda lang: os.path.exists(VOICE_PATHS.get(lang, "")))

def on_speech_event(kind, data):
    if kind == "listening":
        live.update(listening=data)
    elif kind == "heard":
        live.add("partner", heard=data["heard"], text=data["text"], code=data["code"],
                 lang=langs.partner, text_lang=langs.user if langs.translating_in else langs.partner,
                 wpm=live.current["wpm"])


speech_to_morse = SpeechToMorse(on_morse=blink_morse_on_mcu, lang=langs.partner,
                                translate=translate_partner_speech, on_event=on_speech_event)
on_language_change(langs.user, langs.partner)
lock = threading.Lock()


def now_ms():
    return time.monotonic() * 1000.0


def show():
    print(f"> {decoder.text()} {decoder.symbol}_", flush=True)
    live.update(typing=decoder.text(), symbol=decoder.symbol)


def on_word(word):
    live.update(typing=decoder.text(), symbol=decoder.symbol)
    if langs.translating_out:
        return                          # translation needs the whole sentence -> button C
    speaker.say(word, lang=langs.user)  # speak each finished word immediately


def on_calibration(ok):
    if ok:
        print(f"[cal] done: dot~{decoder.dot_avg:.0f} dash~{decoder.dash_avg:.0f} "
              f"letter-gap>{decoder.letter_gap_threshold:.0f} word-gap>{decoder.word_gap_threshold:.0f}",
              flush=True)
        speaker.say("Thank you. I'm ready.")
        live.update(calibrating=False)
        live.add("info", text=f"Calibrated: dot {decoder.dot_avg:.0f} ms, dash {decoder.dash_avg:.0f} ms")
    else:
        live.update(calibration=[0, decoder.calibration_progress[1]])
        print("[cal] dots and dashes overlapped, please tap PARIS again", flush=True)
        speaker.say("Let's try that again. Please tap Paris.")


decoder = MorseDecoder(on_symbol=lambda s: show(), on_letter=lambda c: show(), on_word=on_word,
                       on_calibration=on_calibration)


def start_calibration():
    with lock:
        decoder.start_calibration()
        live.update(calibrating=True, calibration=list(decoder.calibration_progress),
                    typing="", symbol="")
    print("[cal] tap PARIS:  .--. .- .-. .. ...   (press C to skip)", flush=True)
    speaker.say("Please tap Paris in Morse code, so I can learn your speed.")


# --- events from the STM32 sketch -------------------------------------------
def key_down():
    live.update(key_down=True)
    with lock:
        decoder.key_down(now_ms())


def key_up(duration_ms):
    live.update(key_down=False)
    with lock:
        was_calibrating = decoder.calibrating
        decoder.key_up(duration_ms, now_ms())
        if was_calibrating:
            done, needed = decoder.calibration_progress
            live.update(calibration=[done, needed])
            if decoder.calibrating:
                print(f"[cal] {int(duration_ms)} ms  ({done}/{needed})", flush=True)
            return
        print(f"[key] {int(duration_ms)} ms  dot~{decoder.dot_avg:.0f} dash~{decoder.dash_avg:.0f}"
              f"  letter-gap>{decoder.letter_gap_threshold:.0f} word-gap>{decoder.word_gap_threshold:.0f}",
              flush=True)


def backspace():
    with lock:
        calibrating = decoder.calibrating
        if not calibrating:
            decoder.backspace()
            show()
    if calibrating:          # mistake during calibration -> start over
        start_calibration()


def speak():
    with lock:
        if decoder.calibrating:  # C skips calibration, default timing is used
            decoder.cancel_calibration()
            skipped = True
        else:
            skipped = False
            sentence = decoder.take_sentence()
    if skipped:
        print("[cal] skipped, using default timing", flush=True)
        speaker.say("Okay, skipped.")
        live.update(calibrating=False)
        live.add("info", text="Calibration skipped, using default timing")
        return
    live.update(typing="", symbol="")
    print(f"[sentence] {sentence}", flush=True)
    if not sentence:
        return
    if langs.translating_out:
        with stats.timed("translate"):
            translated = translator.translate(sentence, langs.user, langs.partner)
        print(f"[translate] {sentence!r} -> {translated!r}", flush=True)
        speaker.say(translated, lang=langs.partner)
        live.add("you", text=sentence, code=encode(sentence), spoken=translated,
                 lang=langs.user, spoken_lang=langs.partner)
    else:
        speaker.say(sentence, lang=langs.user)
        live.add("you", text=sentence, code=encode(sentence), spoken=None, lang=langs.user)


def activate_listening():
    """Long press on C: listen to the partner, answer in Morse."""
    print("[listen] activating speech-to-Morse", flush=True)
    speech_to_morse.activate()


Bridge.provide("listen", activate_listening)  # long press on C (MCU sketch)
Bridge.provide("key_down", key_down)
Bridge.provide("key_up", key_up)
Bridge.provide("backspace", backspace)
Bridge.provide("speak", speak)
Bridge.provide("calibrate", start_calibration)


# --- Morse output speed (Modulino Knob on the MCU) ------------------------------
SPEED_PREVIEW_DELAY_S = 0.6     # play the preview once the knob stops turning
_preview_timer = None


def set_speed(wpm):
    global _preview_timer
    speech_to_morse.set_wpm(wpm)
    live.update(wpm=int(wpm))
    print(f"[speed] {int(wpm)} wpm (dot {speech_to_morse.unit_ms:.0f} ms)", flush=True)
    if _preview_timer:
        _preview_timer.cancel()
    _preview_timer = threading.Timer(SPEED_PREVIEW_DELAY_S, speech_to_morse.preview)
    _preview_timer.daemon = True
    _preview_timer.start()


def sync_speed_from_mcu():
    """After a restart of the Python side, take over the speed the knob is set to."""
    try:
        wpm = Bridge.call("get_speed", timeout=2)
        speech_to_morse.set_wpm(wpm)
        live.update(wpm=int(wpm))
        print(f"[speed] {int(wpm)} wpm (from knob)", flush=True)
    except Exception as e:
        print(f"[speed] could not read knob speed, using default: {e}", flush=True)


Bridge.provide("set_speed", set_speed)
threading.Thread(target=sync_speed_from_mcu, daemon=True).start()


# --- Web page: language selection (assets/index.html) -------------------------
def api_get_languages():
    return langs.status()


def api_set_languages(user: str, partner: str):
    from fastapi import HTTPException
    try:
        langs.set(user, partner)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return langs.status()


def api_live(since: int = 0):
    """Current typing state + log entries newer than `since` (the page polls this)."""
    return live.snapshot(since)


ui = WebUI()
ui.expose_api("GET", "/api/live", api_live)
ui.expose_api("GET", "/api/stats", lambda: stats.snapshot())
ui.expose_api("GET", "/api/languages", api_get_languages)
ui.expose_api("POST", "/api/languages", api_set_languages)


def loop():
    with lock:
        decoder.tick(now_ms())
    time.sleep(0.02)


print("MorseToSpeech ready. A = Morse key, B = backspace (hold: recalibrate), "
      "C = speak sentence (hold: listen, speech -> Morse).", flush=True)
start_calibration()
App.run(user_loop=loop)
