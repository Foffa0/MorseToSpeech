// Nicla Voice wake-word trigger.
// Board: "Arduino Nicla Voice" (core: Arduino Mbed OS Nicla Boards).
// NOT part of the App Lab sketch (different board) - flash it with Arduino IDE / arduino-cli.
//
// The NDP120 runs the keyword model on its own. On a match we send one line
//   WAKE <label>\n
// over USB serial (115200 baud) to the UNO Q. Other debug lines start with "#".

#include "NDP.h"

const unsigned long COOLDOWN_MS = 2000;  // ignore repeated matches within this window

volatile bool matchPending = false;
char matchLabel[32] = "";
unsigned long lastWakeMs = 0;

void onMatch(char* label) {
  strncpy(matchLabel, label, sizeof(matchLabel) - 1);
  matchLabel[sizeof(matchLabel) - 1] = '\0';
  matchPending = true;
}

void onError() {
  // Fatal: blink red forever (same behaviour as the official AlexaDemo).
  while (1) {
    nicla::leds.begin();
    nicla::leds.setColor(red);
    delay(200);
    nicla::leds.setColor(off);
    delay(200);
    nicla::leds.end();
  }
}

void blink(RGBColors color) {
  nicla::leds.begin();
  nicla::leds.setColor(color);
  delay(200);
  nicla::leds.setColor(off);
  nicla::leds.end();
}

void setup() {
  Serial.begin(115200);
  nicla::begin();
  nicla::disableLDO();
  nicla::leds.begin();

  NDP.onError(onError);
  NDP.onMatch(onMatch);

  Serial.println("# loading synpackages");
  NDP.begin("mcu_fw_120_v91.synpkg");
  NDP.load("dsp_firmware_v91.synpkg");
  // Swap this for "ei_model.synpkg" once a custom wake name is trained in Edge Impulse.
  NDP.load("alexa_334_NDP120_B0_v11_v91.synpkg");
  NDP.turnOnMicrophone();
  NDP.interrupts();
  nicla::leds.end();

  Serial.println("# ready");
  blink(green);
}

void loop() {
  if (matchPending) {
    matchPending = false;
    unsigned long now = millis();
    if (now - lastWakeMs >= COOLDOWN_MS) {
      lastWakeMs = now;
      Serial.print("WAKE ");
      Serial.println(matchLabel);
      blink(blue);
    }
  }
  delay(10);
}
