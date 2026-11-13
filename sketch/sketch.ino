// MorseToSpeech - STM32 side of the UNO Q.
// Reads Modulino Buttons, measures press durations and sends events to Python.
//   Button A: Morse key        -> "key_down", "key_up"(duration_ms)
//   Button B: backspace        -> "backspace"
//   Button C: short = speak    -> "speak"
//             long  = listen   -> "wake"   (stand-in until the Nicla Voice is wired)
//   Nicla Voice wake pin       -> "wake"
// Python -> MCU: "play_morse"(code) blinks the Modulino LEDs, e.g. "... --- ..."
// LED matrix (13x8): scrolling Morse tape, right to left.
//   rows 1-2: what the user taps on button A    rows 5-6: Morse from speech-to-Morse

#include <Arduino_RouterBridge.h>
#include <Arduino_LED_Matrix.h>
#include <Modulino.h>

ModulinoButtons buttons;
Arduino_LED_Matrix matrix;

const unsigned long POLL_MS = 5;
const unsigned long DEBOUNCE_MS = 15;
const unsigned long LONG_PRESS_MS = 800;
const unsigned long MORSE_UNIT_MS = 80;     // same as UNIT_MS in speech_to_morse.py

// Nicla Voice: set to true once its output pin is wired (pulses HIGH on the wake word).
// Careful: Nicla Voice I/O is 1.8 V, the UNO Q is 3.3 V -> use a level shifter.
const bool USE_NICLA_PIN = false;
const int NICLA_WAKE_PIN = 2;

bool stableState[3] = {false, false, false};
bool lastRaw[3] = {false, false, false};
unsigned long lastChange[3] = {0, 0, 0};
unsigned long pressStart[3] = {0, 0, 0};

// --- Morse LED playback state ---
String morseOut;
volatile bool morsePending = false;
String morseIncoming;
int morseIdx = -1;
unsigned long morseNext = 0;
bool morseLedOn = false;

bool niclaLast = false;
unsigned long niclaLastWake = 0;

// --- LED matrix tape ---
const int MATRIX_W = 13;
const int MATRIX_H = 8;
const unsigned long TAPE_STEP_MS = 40;   // 1 column per 40 ms -> output dot = 2 px, dash = 6 px
const uint8_t BRIGHT = 7;                // grayscale 0..7
const uint8_t INPUT_ROWS[] = {1, 2};
const uint8_t OUTPUT_ROWS[] = {5, 6};
uint8_t tape[MATRIX_W * MATRIX_H] = {0};
unsigned long lastTapeStep = 0;
bool tapeEmpty = true;

void playMorse(String code) {
  morseIncoming = code;
  morsePending = true;
}

void setup() {
  Bridge.begin();
  // provide_safe runs the callback in the loop thread.
  Bridge.provide_safe("play_morse", playMorse);
  Modulino.begin();
  buttons.begin();
  buttons.setLeds(false, false, false);
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.draw(tape);
  if (USE_NICLA_PIN) pinMode(NICLA_WAKE_PIN, INPUT_PULLDOWN);
}

void onPress(int i, unsigned long t) {
  pressStart[i] = t;
  if (i == 0) {
    buttons.setLeds(true, false, false);
    Bridge.notify("key_down");
  }
}

void onRelease(int i, unsigned long t) {
  unsigned long duration = t - pressStart[i];
  if (i == 0) {
    buttons.setLeds(false, false, false);
    Bridge.notify("key_up", (int)duration);
  } else if (i == 1) {
    Bridge.notify("backspace");
  } else if (i == 2) {
    if (duration >= LONG_PRESS_MS) Bridge.notify("wake");
    else Bridge.notify("speak");
  }
}

void updateButtons(unsigned long now) {
  buttons.update();
  for (int i = 0; i < 3; i++) {
    bool raw = buttons.isPressed(i);
    if (raw != lastRaw[i]) {
      lastRaw[i] = raw;
      lastChange[i] = now;
    }
    if (raw != stableState[i] && now - lastChange[i] >= DEBOUNCE_MS) {
      stableState[i] = raw;
      // Use the time of the raw edge, so debouncing doesn't distort durations.
      if (raw) onPress(i, lastChange[i]);
      else onRelease(i, lastChange[i]);
    }
  }
}

void updateNicla(unsigned long now) {
  if (!USE_NICLA_PIN) return;
  bool level = digitalRead(NICLA_WAKE_PIN) == HIGH;
  if (level && !niclaLast && now - niclaLastWake > 2000) {
    niclaLastWake = now;
    Bridge.notify("wake");
  }
  niclaLast = level;
}

// Non-blocking LED player. After every dot/dash comes 1 unit off;
// ' ' adds 2 more (= 3 between letters), '/' adds 2 (= 7 between words with both spaces).
void updateMorse(unsigned long now) {
  if (morsePending) {
    morseOut = morseIncoming;
    morsePending = false;
    morseIdx = 0;
    morseNext = now;
    morseLedOn = false;
  }
  if (morseIdx < 0 || (long)(now - morseNext) < 0) return;

  if (morseLedOn) {
    buttons.setLeds(false, false, false);
    morseLedOn = false;
    morseNext = now + MORSE_UNIT_MS;
    return;
  }
  if (morseIdx >= (int)morseOut.length()) {
    morseIdx = -1;
    return;
  }
  char c = morseOut[morseIdx++];
  if (c == '.' || c == '-') {
    buttons.setLeds(true, true, true);
    morseLedOn = true;
    morseNext = now + (c == '.' ? 1 : 3) * MORSE_UNIT_MS;
  } else {
    morseNext = now + 2 * MORSE_UNIT_MS;  // ' ' or '/'
  }
}

// Shift the tape one column left and draw the current key/playback state on the right.
// Stops redrawing once everything has scrolled out.
void updateTape(unsigned long now) {
  if (now - lastTapeStep < TAPE_STEP_MS) return;
  lastTapeStep = now;

  bool keyOn = stableState[0];
  bool outOn = morseLedOn;
  if (tapeEmpty && !keyOn && !outOn) return;

  tapeEmpty = true;
  for (int r = 0; r < MATRIX_H; r++) {
    uint8_t *row = &tape[r * MATRIX_W];
    for (int c = 0; c < MATRIX_W - 1; c++) {
      row[c] = row[c + 1];
      if (row[c]) tapeEmpty = false;
    }
    row[MATRIX_W - 1] = 0;
  }
  for (uint8_t r : INPUT_ROWS) tape[r * MATRIX_W + MATRIX_W - 1] = keyOn ? BRIGHT : 0;
  for (uint8_t r : OUTPUT_ROWS) tape[r * MATRIX_W + MATRIX_W - 1] = outOn ? BRIGHT : 0;
  if (keyOn || outOn) tapeEmpty = false;

  matrix.draw(tape);
}

void loop() {
  static unsigned long lastPoll = 0;
  unsigned long now = millis();
  if (now - lastPoll < POLL_MS) return;
  lastPoll = now;

  updateButtons(now);
  updateNicla(now);
  updateMorse(now);
  updateTape(now);
}
