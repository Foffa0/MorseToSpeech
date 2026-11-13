/*
  nicla_wake.ino - "Alexa" wake-word detector for the Arduino Nicla Voice.

  Uses the standard Syntiant packages that are already stored on the Nicla's
  external flash (mcu_fw_120_v91, dsp_firmware_v91, alexa_334_NDP120_B0_v11_v91).

  Serial protocol (USB, 115200 8N1, '\n' terminated) - consumed by python/wake.py:
      WAKE <label>      one line per detected wake word, e.g. "WAKE Alexa"
      # <text>          debug / status line (only logged by the backend)
  Library error messages (printf from the NDP library) may also appear; they
  never start with "WAKE".

  A heartbeat "# alive ..." is sent every 5 s (the backend warns after 10 s of
  silence).

  Single-character commands you can type in the Serial Monitor:
      s  status        t  send a test "WAKE test" line
      r  reboot        h  help

  Robustness:
    * NDP callbacks run in the library's small "events" thread. They only bump
      counters; all printing / LED work happens in loop().
    * The NDP error callback never blocks (the stock example loops forever).
    * Hardware watchdog: if anything hangs or crashes, the board reboots.
    * Init failure or an error storm -> error mode (red blink) -> auto reboot.
    * Hardware UART (via the on-board USB bridge, no flow control): writes never
      block waiting for a host, so an unplugged/closed port cannot stall the sketch.

  LED: yellow = loading, green flash = ready, blue flash = wake word,
       blinking red = error (reboots automatically).
*/

#include "NDP.h"
#include <stdarg.h>

// ----------------------------------------------------------------------------
// Configuration
// ----------------------------------------------------------------------------
static const char *FW_MCU   = "mcu_fw_120_v91.synpkg";
static const char *FW_DSP   = "dsp_firmware_v91.synpkg";
static const char *FW_MODEL = "alexa_334_NDP120_B0_v11_v91.synpkg";

static const uint32_t SERIAL_BAUD        = 115200;
static const char    *WAKE_KEYWORD       = "alexa";  // case-insensitive substring of the label
static const uint32_t WAKE_COOLDOWN_MS   = 1500;     // ignore repeated matches of one utterance
static const uint32_t HEARTBEAT_MS       = 5000;
static const uint32_t WATCHDOG_MS        = 20000;    // must cover the package loading in setup()
static const uint32_t ERROR_REBOOT_MS    = 30000;    // stay in error mode this long, then reboot
static const uint32_t ERROR_WINDOW_MS    = 10000;
static const uint32_t ERROR_STORM_COUNT  = 20;       // > this many NDP errors per window -> error mode
static const uint32_t LED_FLASH_MS       = 300;
static const uint32_t LOOP_DELAY_MS      = 5;
static const int      MAX_LABELS         = 32;       // NDPClass::MAX_LABELS

// ----------------------------------------------------------------------------
// State shared with the NDP "events" thread (single writer there, read in loop)
// ----------------------------------------------------------------------------
static volatile uint32_t ndpMatchCount = 0;
static volatile int      ndpLastMatch  = -1;
static volatile uint32_t ndpErrorCount = 0;
static volatile uint32_t ndpEventCount = 0;

static void onNdpMatch(int idx) {   // events thread: keep it tiny
  ndpLastMatch = idx;
  ndpMatchCount = ndpMatchCount + 1;
}
static void onNdpError() { ndpErrorCount = ndpErrorCount + 1; }
static void onNdpEvent() { ndpEventCount = ndpEventCount + 1; }

// ----------------------------------------------------------------------------
// Loop-only state
// ----------------------------------------------------------------------------
enum State { STATE_BOOT, STATE_RUNNING, STATE_ERROR };
static State state = STATE_BOOT;

static uint32_t seenMatches = 0;
static uint32_t seenErrors  = 0;
static uint32_t wakeCount   = 0;
static bool     hasWoken    = false;
static uint32_t lastWakeMs  = 0;
static uint32_t lastHeartbeatMs = 0;

static bool     ledActive   = false;
static uint32_t ledOffAtMs  = 0;

static uint32_t errorSinceMs = 0;
static char     errorReason[64] = "";
static uint32_t errWindowStartMs = 0;
static uint32_t errWindowCount   = 0;

static const char *resetReason = "unknown";

// ----------------------------------------------------------------------------
// Helpers
// ----------------------------------------------------------------------------

// Formats one complete line and writes it with a single call so it is not
// torn apart by other output.
static void sendLine(const char *fmt, ...) {
  char buf[160];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(buf, sizeof(buf) - 1, fmt, ap);
  va_end(ap);
  if (n < 0) return;
  if (n > (int)sizeof(buf) - 2) n = sizeof(buf) - 2;
  buf[n++] = '\n';
  Serial.write((const uint8_t *)buf, n);
}

static void kickWatchdog() { mbed::Watchdog::get_instance().kick(); }

static void ledSet(RGBColors c) { nicla::leds.setColor(c); }

static void ledFlash(RGBColors c, uint32_t ms) {
  ledSet(c);
  ledActive = true;
  ledOffAtMs = millis() + ms;
}

static const char *labelName(int idx) {
  if (idx < 0 || idx >= MAX_LABELS) return "?";
  char **labels = NDP.getLabels();
  if (!labels || !labels[idx]) return "?";
  const char *l = labels[idx];
  const char *colon = strchr(l, ':');   // "NN0:Alexa" -> "Alexa"
  return colon ? colon + 1 : l;
}

static bool containsIgnoreCase(const char *haystack, const char *needle) {
  size_t nl = strlen(needle);
  for (; *haystack; haystack++) {
    size_t i = 0;
    while (i < nl && haystack[i] && tolower((unsigned char)haystack[i]) == tolower((unsigned char)needle[i])) i++;
    if (i == nl) return true;
  }
  return false;
}

static const char *stateName() {
  switch (state) {
    case STATE_BOOT:    return "boot";
    case STATE_RUNNING: return "ok";
    default:            return "error";
  }
}

static void readResetReason() {
  uint32_t r = NRF_POWER->RESETREAS;
  NRF_POWER->RESETREAS = 0xFFFFFFFF;   // write-1-to-clear
  if      (r & POWER_RESETREAS_DOG_Msk)      resetReason = "watchdog";
  else if (r & POWER_RESETREAS_LOCKUP_Msk)   resetReason = "lockup";
  else if (r & POWER_RESETREAS_SREQ_Msk)     resetReason = "software";
  else if (r & POWER_RESETREAS_RESETPIN_Msk) resetReason = "reset-pin";
  else if (r == 0)                           resetReason = "power-on";
}

static void reboot(const char *why) {
  sendLine("# rebooting: %s", why);
  Serial.flush();
  delay(50);
  NVIC_SystemReset();
}

static void enterError(const char *why) {
  if (state == STATE_ERROR) return;
  state = STATE_ERROR;
  errorSinceMs = millis();
  strncpy(errorReason, why, sizeof(errorReason) - 1);
  errorReason[sizeof(errorReason) - 1] = '\0';
  sendLine("# ERROR %s - rebooting in %lu s", errorReason, (unsigned long)(ERROR_REBOOT_MS / 1000));
}

static void printLabels() {
  char **labels = NDP.getLabels();
  for (int i = 0; i < MAX_LABELS && labels && labels[i]; i++) {
    sendLine("# label %d: %s", i, labels[i]);
  }
}

static void printStatus() {
  uint32_t now = millis();
  sendLine("# status state=%s up=%lus wakes=%lu matches=%lu events=%lu errors=%lu reset=%s",
           stateName(), (unsigned long)(now / 1000), (unsigned long)wakeCount,
           (unsigned long)ndpMatchCount, (unsigned long)ndpEventCount,
           (unsigned long)ndpErrorCount, resetReason);
  if (hasWoken) sendLine("# last wake %lu ms ago", (unsigned long)(now - lastWakeMs));
  if (state == STATE_ERROR) sendLine("# error: %s", errorReason);
  printLabels();
}

static void printHelp() {
  sendLine("# commands: s=status t=test-wake r=reboot h=help");
}

// ----------------------------------------------------------------------------
// NDP init (blocking, only called from setup)
// ----------------------------------------------------------------------------
static bool loadStep(const char *what, bool ok, uint32_t errorsBefore) {
  kickWatchdog();
  if (!ok || ndpErrorCount != errorsBefore) {
    char why[64];
    snprintf(why, sizeof(why), "init failed at %s", what);
    enterError(why);
    return false;
  }
  sendLine("# %s ok", what);
  return true;
}

static bool initNdp() {
  NDP.onError(mbed::callback(onNdpError));
  NDP.onMatch(mbed::callback(onNdpMatch));
  NDP.onEvent(mbed::callback(onNdpEvent));

  uint32_t e = ndpErrorCount;
  sendLine("# loading %s", FW_MCU);
  if (!loadStep(FW_MCU, NDP.begin(FW_MCU) == 1, e)) return false;

  e = ndpErrorCount;
  sendLine("# loading %s", FW_DSP);
  if (!loadStep(FW_DSP, NDP.load(FW_DSP) == 1, e)) return false;

  e = ndpErrorCount;
  sendLine("# loading %s", FW_MODEL);
  if (!loadStep(FW_MODEL, NDP.load(FW_MODEL) == 1, e)) return false;

  e = ndpErrorCount;
  if (!loadStep("model info", NDP.getInfo() == 1, e)) return false;
  if (!NDP.getLabels() || !NDP.getLabels()[0]) {
    enterError("model has no labels");
    return false;
  }
  printLabels();

  e = ndpErrorCount;
  if (!loadStep("microphone", NDP.turnOnMicrophone() == 0, e)) return false;

  e = ndpErrorCount;
  NDP.interrupts();
  if (!loadStep("interrupts", true, e)) return false;

  return true;
}

// ----------------------------------------------------------------------------
// Loop tasks (all non-blocking)
// ----------------------------------------------------------------------------
static void handleMatches() {
  uint32_t m = ndpMatchCount;
  if (m == seenMatches) return;
  uint32_t coalesced = m - seenMatches - 1;
  seenMatches = m;

  int idx = ndpLastMatch;
  const char *name = labelName(idx);
  if (coalesced) sendLine("# %lu match(es) coalesced", (unsigned long)coalesced);

  if (!containsIgnoreCase(name, WAKE_KEYWORD)) {
    sendLine("# match '%s' (idx %d) ignored - not the wake word", name, idx);
    return;
  }

  uint32_t now = millis();
  if (hasWoken && now - lastWakeMs < WAKE_COOLDOWN_MS) {
    sendLine("# match '%s' ignored (cooldown, %lu ms since last wake)", name,
             (unsigned long)(now - lastWakeMs));
    return;
  }

  hasWoken = true;
  lastWakeMs = now;
  wakeCount++;
  sendLine("WAKE %s", name);   // the line the backend acts on - send it first
  ledFlash(blue, LED_FLASH_MS);
}

static void handleErrors() {
  uint32_t e = ndpErrorCount;
  if (e == seenErrors) return;
  uint32_t fresh = e - seenErrors;
  seenErrors = e;
  sendLine("# NDP error x%lu (total %lu)", (unsigned long)fresh, (unsigned long)e);

  uint32_t now = millis();
  if (now - errWindowStartMs > ERROR_WINDOW_MS) {
    errWindowStartMs = now;
    errWindowCount = 0;
  }
  errWindowCount += fresh;
  if (errWindowCount > ERROR_STORM_COUNT && state == STATE_RUNNING) {
    enterError("too many NDP errors");
  }
}

static void handleSerialCommands() {
  for (int i = 0; i < 16 && Serial.available() > 0; i++) {   // bounded per loop
    int c = Serial.read();
    switch (c) {
      case 's': case 'S': printStatus(); break;
      case 't': case 'T':
        wakeCount++;
        sendLine("WAKE test");
        ledFlash(blue, LED_FLASH_MS);
        break;
      case 'r': case 'R': reboot("requested"); break;
      case 'h': case 'H': case '?': printHelp(); break;
      default: break;   // ignore newlines and noise
    }
  }
}

static void handleHeartbeat(uint32_t now) {
  if (now - lastHeartbeatMs < HEARTBEAT_MS) return;
  lastHeartbeatMs = now;
  sendLine("# alive state=%s up=%lus wakes=%lu errors=%lu", stateName(),
           (unsigned long)(now / 1000), (unsigned long)wakeCount, (unsigned long)ndpErrorCount);
}

static void handleLed(uint32_t now) {
  if (state == STATE_ERROR) {
    ledSet(((now / 250) % 2) ? red : off);
    ledActive = false;
    return;
  }
  if (ledActive && (int32_t)(now - ledOffAtMs) >= 0) {
    ledSet(off);
    ledActive = false;
  }
}

// ----------------------------------------------------------------------------
// Arduino entry points
// ----------------------------------------------------------------------------
void setup() {
  Serial.begin(SERIAL_BAUD);
  readResetReason();
  mbed::Watchdog::get_instance().start(WATCHDOG_MS);

  sendLine("# nicla_wake booting (reset reason: %s)", resetReason);

  nicla::begin();
  nicla::disableLDO();
  nicla::leds.begin();
  ledSet(yellow);
  kickWatchdog();

  if (initNdp()) {
    state = STATE_RUNNING;
    seenErrors = ndpErrorCount;
    seenMatches = ndpMatchCount;
    ledFlash(green, 500);
    sendLine("# ready - listening for \"Alexa\" (cooldown %lu ms)", (unsigned long)WAKE_COOLDOWN_MS);
    printHelp();
  }
  lastHeartbeatMs = millis();
}

void loop() {
  kickWatchdog();
  uint32_t now = millis();

  handleSerialCommands();

  if (state == STATE_RUNNING) {
    handleMatches();
    handleErrors();
  } else if (state == STATE_ERROR && now - errorSinceMs > ERROR_REBOOT_MS) {
    reboot(errorReason);
  }

  handleHeartbeat(now);
  handleLed(now);

  delay(LOOP_DELAY_MS);   // yields to the NDP events thread
}
