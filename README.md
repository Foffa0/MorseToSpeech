# 😀 MorseToSpeech

Tap Morse code on a button and the Arduino UNO Q speaks it out loud, fully offline.

## Hardware
- Arduino UNO Q
- Modulino Buttons on Qwiic: **A** = Morse key, **B** = backspace, **C** = speak the whole sentence
- Headset or USB speaker

## How it works
- `sketch/sketch.ino` (STM32): debounces the buttons, measures press durations, sends events over the Bridge.
- `python/morse.py`: adaptive decoder. It learns the user's dot/dash speed (EMA) and detects letter and word gaps.
- `python/tts.py`: offline TTS. It uses Piper, falls back to espeak-ng, and runs in a background thread.
- `python/main.py`: wires everything together. Each finished word is spoken immediately.

## Setup
1. In App Lab, add the **Modulino** library to the sketch.
2. Download the Piper voice into `python/voices/`:
   ```
   mkdir -p python/voices && cd python/voices
   curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx
   curl -LO https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json
   ```
3. Run the app from App Lab.

## Tests (laptop)
```
cd python && python -m pytest test_morse.py
```

## Speed calibration
At startup the device asks the user to tap **PARIS** (`.--. .- .-. .. ...`), the standard Morse timing word.
Because the pattern is known, each press is matched to its expected dot or dash. That gives the user's exact
dot, dash and pause lengths before decoding starts.
- If dots and dashes overlap (a mistyped calibration), it asks again.
- **C** skips the calibration and keeps the default timing. Holding **B** recalibrates, and a short **B** during calibration restarts it.
- After calibration, the decoder keeps adapting while the user types.

## Tuning
The timing constants are at the top of `python/morse.py` (initial speed, adapt rate, letter/word gap factors).

## LED matrix
The UNO Q's 13×8 matrix shows a scrolling Morse tape, right to left, 40 ms per column:
- **rows 1–2:** what the user taps on button A (live)
- **rows 5–6:** Morse coming from speech-to-Morse (dot = 2 px, dash = 6 px)

## Speech → Morse
Hold **C** (≥ 0.8 s). The UNO Q then listens to the conversation partner,
transcribes offline with Vosk, and outputs the sentence as Morse beeps on the headset and blinks on the Modulino LEDs.

Download the STT model into `python/models/`:
```
mkdir -p python/models && cd python/models
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && rm vosk-model-small-en-us-0.15.zip
```
Test on the laptop: `python speech_to_morse.py "hello"` (beeps only) or `python speech_to_morse.py --listen`.


## Offline translation (English ↔ German)
The user taps English. The conversation partner speaks German. Everything runs offline on the board:
- **User → partner:** tap a sentence in Morse, press **C**, and the device speaks it **in German**. In this mode words aren't
  spoken one by one, because translation needs the whole sentence.
- **Partner → user:** hold C, and German speech is recognized (Vosk), translated to English,
  and played as Morse.

Setup: `cd python && python download_models.py`. This downloads the Argos Translate models (en→de, de→en, ~157 MB each),
the German Vosk model, and the German Piper voice (thorsten).

- The Argos models run directly with **CTranslate2 in int8** instead of the `argostranslate` package, which would pull
  in PyTorch. Translation takes about 50 ms per sentence on a laptop.
- German questions are detected from the first word, because speech recognition gives no punctuation.
- Set the partner's language with the env var `MORSE_PARTNER_LANG` (`de` by default, `en` = no translation). If a model
  is missing, that direction falls back to no translation.
- Test on the laptop: `python translate.py en de "I NEED WATER"` → `Ich brauche Wasser.`

## Web page: live view and language selection
- **Now typing:** the sentence being tapped, the dots and dashes of the current letter, and a dot that lights up while
  button A is held. It also shows calibration progress.
- **Conversation:** a chat of everything said. Your sentences show the translation that was spoken. The partner's
  sentences show the original speech and the translation. Every message shows its **Morse code** letter by letter,
  and incoming Morse lights up **in time with the playback** on the device.
- **Chips:** Listening…, the languages, and the Morse speed set with the knob.
- The page polls `GET /api/live?since=<id>` every 200 ms and only gets new events. It uses polling instead of WebSockets
  so that it stays fully offline.

### Language selection
The WebUI brick serves `assets/index.html` on **port 7000**: open `http://<board-ip>:7000`, or the HDMI touch monitor's
browser on the board itself.
- Choose **your language** (the Morse side) and the **partner's language** (the speech side), or swap them.
- The page shows what will happen in each direction, and warns about missing models.
- The choice is saved in `python/settings.json` and applies immediately. Models for a new language load in the background.
- API: `GET /api/languages` and `POST /api/languages?user=en&partner=de`.
- Fully offline: no external scripts or fonts.

## Morse speed (Modulino Knob)
The knob sets how fast Morse is **played** to the user (headset beeps, LEDs and LED matrix):
- **Turn right** for faster and **left** for slower, from 5 to 30 words per minute (wpm). The default is 15 wpm, an 80 ms dot.
- **Press** to reset to 15 wpm.
- While turning, the LED matrix shows a speed bar. After you stop, a short preview (Morse `K`) plays at the new speed.
- The MCU handles the knob and the LEDs, and sends `set_speed`(wpm) to Python for the audio. After a restart, Python
  reads the current value with `get_speed`.
- The knob doesn't change the decoding of what the user taps. That still adapts automatically (see calibration).
