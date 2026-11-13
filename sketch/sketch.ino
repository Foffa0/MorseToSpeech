// MorseToSpeech - STM32 side of the UNO Q.
// Reads Modulino Buttons, measures press durations and sends events to Python.
//   Button A: Morse key  -> "key_down", "key_up"(duration_ms)
//   Button B: backspace  -> "backspace"
//   Button C: speak      -> "speak"

#include <Arduino_RouterBridge.h>
#include <Modulino.h>

ModulinoButtons buttons;

const unsigned long POLL_MS = 5;
const unsigned long DEBOUNCE_MS = 15;

bool stableState[3] = {false, false, false};
bool lastRaw[3] = {false, false, false};
unsigned long lastChange[3] = {0, 0, 0};
unsigned long pressStart = 0;

void setup() {
  Bridge.begin();
  Modulino.begin();
  buttons.begin();
  buttons.setLeds(false, false, false);
}

void onPress(int i, unsigned long now) {
  if (i == 0) {
    pressStart = now;
    buttons.setLeds(true, false, false);
    Bridge.notify("key_down");
  }
}

void onRelease(int i, unsigned long now) {
  if (i == 0) {
    int duration = (int)(now - pressStart);
    buttons.setLeds(false, false, false);
    Bridge.notify("key_up", duration);
  } else if (i == 1) {
    Bridge.notify("backspace");
  } else if (i == 2) {
    Bridge.notify("speak");
  }
}

void loop() {
  static unsigned long lastPoll = 0;
  unsigned long now = millis();
  if (now - lastPoll < POLL_MS) return;
  lastPoll = now;

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
