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
