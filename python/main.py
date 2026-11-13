"""MorseToSpeech: Modulino Buttons -> adaptive Morse decoder -> console + offline TTS."""

import threading
import time

from arduino.app_utils import App, Bridge

import wake
from morse import MorseDecoder
from tts import Speaker

speaker = Speaker()
lock = threading.Lock()


def now_ms():
    return time.monotonic() * 1000.0


def show():
    print(f"> {decoder.text()} {decoder.symbol}_", flush=True)


def on_word(word):
    speaker.say(word)  # speak each finished word immediately


decoder = MorseDecoder(on_symbol=lambda s: show(), on_letter=lambda c: show(), on_word=on_word)


# --- events from the STM32 sketch -------------------------------------------
def key_down():
    with lock:
        decoder.key_down(now_ms())


def key_up(duration_ms):
    with lock:
        decoder.key_up(duration_ms, now_ms())
        print(f"[key] {int(duration_ms)} ms  dot~{decoder.dot_avg:.0f} dash~{decoder.dash_avg:.0f}"
              f"  letter-gap>{decoder.letter_gap_threshold:.0f} word-gap>{decoder.word_gap_threshold:.0f}",
              flush=True)


def backspace():
    with lock:
        decoder.backspace()
        show()


def speak():
    with lock:
        sentence = decoder.take_sentence()
    print(f"[sentence] {sentence}", flush=True)
    speaker.say(sentence)


Bridge.provide("key_down", key_down)
Bridge.provide("key_up", key_up)
Bridge.provide("backspace", backspace)
Bridge.provide("speak", speak)
# --- Wake word (Nicla Voice over USB, see wake.py) ---------------------------
def on_wake(label):
    """Called once per detected wake word.

    TODO (teammates): start recording / speech-to-text / Morse translation here.
    """
    print(f"Wake word detected ({label}) - start listening")


wake.start(on_wake)


def loop():
    with lock:
        decoder.tick(now_ms())
    time.sleep(0.02)


print("MorseToSpeech ready. Button A = Morse key, B = backspace, C = speak sentence.", flush=True)
App.run(user_loop=loop)
