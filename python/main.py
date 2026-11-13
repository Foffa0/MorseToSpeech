"""MorseToSpeech: Modulino Buttons -> adaptive Morse decoder -> console + offline TTS.
Web page (WebUI brick, port 7000): choose the user's and the partner's language."""

import os
import threading
import time

from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Bridge

from languages import LanguageSettings
from morse import MorseDecoder
from speech_to_morse import SpeechToMorse
from translate import Translator
from tts import VOICE_PATHS, Speaker

# --- Languages ------------------------------------------------------------------
# The user taps Morse in their language. If the partner speaks another language,
# everything is translated offline in both directions. Changeable on the web page.
translator = Translator()
speaker = Speaker()


def blink_morse_on_mcu(text, code):
    try:
        Bridge.notify("play_morse", code)   # Modulino LEDs blink the Morse code
    except Exception as e:
        print(f"[s2m] could not send to MCU: {e}", flush=True)


def translate_partner_speech(text):
    if langs.translating_in:
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

speech_to_morse = SpeechToMorse(on_morse=blink_morse_on_mcu, lang=langs.partner,
                                translate=translate_partner_speech)
on_language_change(langs.user, langs.partner)
lock = threading.Lock()


def now_ms():
    return time.monotonic() * 1000.0


def show():
    print(f"> {decoder.text()} {decoder.symbol}_", flush=True)


def on_word(word):
    if langs.translating_out:
        return                          # translation needs the whole sentence -> button C
    speaker.say(word, lang=langs.user)  # speak each finished word immediately


def on_calibration(ok):
    if ok:
        print(f"[cal] done: dot~{decoder.dot_avg:.0f} dash~{decoder.dash_avg:.0f} "
              f"letter-gap>{decoder.letter_gap_threshold:.0f} word-gap>{decoder.word_gap_threshold:.0f}",
              flush=True)
        speaker.say("Thank you. I'm ready.")
    else:
        print("[cal] dots and dashes overlapped, please tap PARIS again", flush=True)
        speaker.say("Let's try that again. Please tap Paris.")


decoder = MorseDecoder(on_symbol=lambda s: show(), on_letter=lambda c: show(), on_word=on_word,
                       on_calibration=on_calibration)


def start_calibration():
    with lock:
        decoder.start_calibration()
    print("[cal] tap PARIS:  .--. .- .-. .. ...   (press C to skip)", flush=True)
    speaker.say("Please tap Paris in Morse code, so I can learn your speed.")


# --- events from the STM32 sketch -------------------------------------------
def key_down():
    with lock:
        decoder.key_down(now_ms())


def key_up(duration_ms):
    with lock:
        was_calibrating = decoder.calibrating
        decoder.key_up(duration_ms, now_ms())
        if was_calibrating:
            done, needed = decoder.calibration_progress
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
        return
    print(f"[sentence] {sentence}", flush=True)
    if langs.translating_out and sentence:
        translated = translator.translate(sentence, langs.user, langs.partner)
        print(f"[translate] {sentence!r} -> {translated!r}", flush=True)
        speaker.say(translated, lang=langs.partner)
    else:
        speaker.say(sentence, lang=langs.user)


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


ui = WebUI()
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
