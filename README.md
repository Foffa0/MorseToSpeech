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

## Tuning
The timing constants are at the top of `python/morse.py` (initial speed, adapt rate, letter/word gap factors).

## LED matrix
The UNO Q's 13×8 matrix shows a scrolling Morse tape, right to left, 40 ms per column:
- **rows 1–2:** what the user taps on button A (live)
- **rows 5–6:** Morse coming from speech-to-Morse (dot = 2 px, dash = 6 px)

## Speech → Morse
Hold **C** (≥ 0.8 s), or later say the wake word to the Nicla Voice. The UNO Q then listens to the conversation partner,
transcribes offline with Vosk, and outputs the sentence as Morse beeps on the headset and blinks on the Modulino LEDs.

Download the STT model into `python/models/`:
```
mkdir -p python/models && cd python/models
curl -LO https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip
unzip vosk-model-small-en-us-0.15.zip && rm vosk-model-small-en-us-0.15.zip
```
Test on the laptop: `python speech_to_morse.py "hello"` (beeps only) or `python speech_to_morse.py --listen`.

**Connecting the Nicla Voice:** everything goes through `wake()` in `python/main.py`.
- Via pin: on the wake word, the Nicla sets a pin HIGH briefly. Connect it to D2 and set `USE_NICLA_PIN = true` in the sketch.
  The Nicla's I/O is 1.8 V, so use a level shifter.
- Via USB serial: the Nicla prints `WAKE`, Python reads the serial port and calls `wake()`.
