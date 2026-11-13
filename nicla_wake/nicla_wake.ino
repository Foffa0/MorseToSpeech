// Nicla Voice wake-word trigger.
// Board: "Arduino Nicla Voice" (core: Arduino Mbed OS Nicla Boards).
// NOT part of the App Lab sketch (different board) - flash it with Arduino IDE / arduino-cli.
//
// The NDP120 runs the keyword model on its own. On a match we send one line
//   WAKE <label>\n
// over USB serial (115200 baud) to the UNO Q. Every other line starts with "#" (debug/status).
//
// ---- Serial status lines (all start with "#") ----------------------------------------------
//   # boot  fw=... reset=<reason> raw=0x..   first line after every (re)start. If you see this
//                                            repeatedly in the host log, the board keeps resetting.
//                                            reset=power-on/brownout -> supply problem (USB power!)
//                                            reset=watchdog          -> a stage hung
//                                            reset=soft              -> we reset ourselves (NDP error)
//   # stage <n>/<m> <what>                   progress through setup(); the last one printed before
//                                            silence is where it hung/crashed.
//   # ready  setup_ms=...                    setup finished, listening.
//   # hb up=<s>s wakes=<n> matches=<n> ...   heartbeat every HEARTBEAT_MS while alive.
//   # match <label> ...                      model matched (even if suppressed by cooldown).
//   # ERROR ...                              fatal NDP error, board restarts itself after a few s.
//
// ---- LED codes ------------------------------------------------------------------------------
//   magenta (solid)       setup in progress (loading models)
//   green, 1 long blink   ready
//   green, tiny blink     heartbeat every HEARTBEAT_MS (alive and listening)
//   blue, 1 blink         wake word detected and sent
//   red, 10 fast blinks   fatal NDP error, then automatic restart
//   (nothing / odd pattern with the above never appearing: this sketch is NOT what is running,
//    or the board is in the bootloader / browning out before setup() runs.)

#include "NDP.h"

const char FW_VERSION[] = "wake-1.2";

const unsigned long COOLDOWN_MS = 2000;   // ignore repeated matches within this window
const unsigned long HEARTBEAT_MS = 5000;  // serial + LED heartbeat period (0 = off)
const bool HEARTBEAT_LED = true;          // short green flash with each heartbeat
const uint32_t WATCHDOG_MS = 30000;       // restart if loop()/setup() stalls this long (0 = off)

volatile bool matchPending = false;
volatile uint32_t matchCount = 0;         // raw model matches
char matchLabel[32] = "";
unsigned long lastWakeMs = 0;
uint32_t wakeCount = 0;                   // WAKE lines actually sent
uint32_t suppressedCount = 0;             // matches dropped by cooldown
volatile bool ndpFailed = false;

// ---------------------------------------------------------------------------------------------
// Watchdog (optional, only if the core exposes it)
// ---------------------------------------------------------------------------------------------
#if defined(DEVICE_WATCHDOG)
#define HAVE_WDT 1
#else
#define HAVE_WDT 0
#endif

void wdtStart()
{
#if HAVE_WDT
  if (WATCHDOG_MS) mbed::Watchdog::get_instance().start(WATCHDOG_MS);
#endif
}

void wdtKick()
{
#if HAVE_WDT
  if (WATCHDOG_MS) mbed::Watchdog::get_instance().kick();
#endif
}

// ---------------------------------------------------------------------------------------------
// Reset reason (nRF52832 RESETREAS). Note: brownout / power loss does NOT set any bit, so a value
// of 0 means "power-on reset or brownout" - on a board that was not just plugged in, suspect power.
// ---------------------------------------------------------------------------------------------
uint32_t readResetReason()
{
#if defined(NRF_POWER)
  uint32_t r = NRF_POWER->RESETREAS;
  NRF_POWER->RESETREAS = 0xFFFFFFFF; // clear (write 1 to clear)
  return r;
#else
  return 0xFFFFFFFF;
#endif
}

const char *resetReasonText(uint32_t r)
{
  if (r == 0xFFFFFFFF) return "unknown";
  if (r == 0) return "power-on/brownout";
  if (r & 0x00000002) return "watchdog";
  if (r & 0x00000004) return "soft";
  if (r & 0x00000008) return "lockup";
  if (r & 0x00000001) return "reset-pin";
  return "other";
}

// ---------------------------------------------------------------------------------------------
// LED helpers
// ---------------------------------------------------------------------------------------------
void blinkMs(RGBColors color, unsigned int ms)
{
  nicla::leds.begin();
  nicla::leds.setColor(color);
  delay(ms);
  nicla::leds.setColor(off);
  nicla::leds.end();
}

void blink(RGBColors color) { blinkMs(color, 200); }

// ---------------------------------------------------------------------------------------------
// Fatal NDP error: report, blink red, then restart instead of hanging forever.
// ---------------------------------------------------------------------------------------------
void onError()
{
  // Runs from the NDP error callback: keep it short and do not return into the library.
  Serial.println("# ERROR ndp fatal error - restarting in ~4 s");
  for (int i = 0; i < 10; i++)
  {
    nicla::leds.begin();
    nicla::leds.setColor(red);
    delay(200);
    nicla::leds.setColor(off);
    delay(200);
    nicla::leds.end();
  }
  Serial.println("# ERROR restarting now");
  delay(50);
  NVIC_SystemReset();
}

void onMatch(char *label)
{
  if (label)
  {
    strncpy(matchLabel, label, sizeof(matchLabel) - 1);
    matchLabel[sizeof(matchLabel) - 1] = '\0';
  }
  else
  {
    strcpy(matchLabel, "alexa");
  }
  matchCount++;
  matchPending = true;
}

void stage(int n, int total, const char *what)
{
  Serial.print("# stage ");
  Serial.print(n);
  Serial.print("/");
  Serial.print(total);
  Serial.print(" ");
  Serial.println(what);
  wdtKick();
}

// ---------------------------------------------------------------------------------------------
void setup()
{
  unsigned long t0 = millis();
  uint32_t reason = readResetReason();

  Serial.begin(115200);
  // Do NOT wait for a host (while(!Serial)): on the UNO Q the port may open late or never.
  delay(300); // give the USB CDC a moment so the first lines are not lost

  Serial.print("# boot fw=");
  Serial.print(FW_VERSION);
  Serial.print(" reset=");
  Serial.print(resetReasonText(reason));
  Serial.print(" raw=0x");
  Serial.println(reason, HEX);

  wdtStart();

  stage(1, 6, "nicla::begin");
  nicla::begin();
  nicla::disableLDO();
  nicla::leds.begin();
  nicla::leds.setColor(magenta); // "setup in progress"

  NDP.onError(onError);
  NDP.onMatch(onMatch);

  stage(2, 6, "NDP.begin mcu firmware");
  NDP.begin("mcu_fw_120_v91.synpkg");
  stage(3, 6, "load dsp firmware");
  NDP.load("dsp_firmware_v91.synpkg");
  stage(4, 6, "load model");
  // Swap this for "ei_model.synpkg" once a custom wake name is trained in Edge Impulse.
  NDP.load("alexa_334_NDP120_B0_v11_v91.synpkg");
  stage(5, 6, "getInfo (label table)");
  // Required: fills the label table. Without it the library passes a NULL label to onMatch
  // and the board crashes on the first detection.
  NDP.getInfo();
  stage(6, 6, "microphone + interrupts on");
  NDP.turnOnMicrophone();
  NDP.interrupts();
  nicla::leds.setColor(off);
  nicla::leds.end();

  Serial.print("# ready setup_ms=");
  Serial.println(millis() - t0);
  blink(green);
  lastWakeMs = millis() - COOLDOWN_MS; // allow an immediate first wake
}

// ---------------------------------------------------------------------------------------------
void loop()
{
  static unsigned long lastHb = 0;
  unsigned long now = millis();

  wdtKick();

  if (matchPending)
  {
    matchPending = false;
    if (now - lastWakeMs >= COOLDOWN_MS)
    {
      lastWakeMs = now;
      wakeCount++;
      Serial.print("WAKE ");
      Serial.println(matchLabel);
      Serial.print("# match ");
      Serial.print(matchLabel);
      Serial.print(" sent n=");
      Serial.println(wakeCount);
      blink(blue);
    }
    else
    {
      suppressedCount++;
      Serial.print("# match ");
      Serial.print(matchLabel);
      Serial.println(" suppressed (cooldown)");
    }
  }

  if (HEARTBEAT_MS && now - lastHb >= HEARTBEAT_MS)
  {
    lastHb = now;
    Serial.print("# hb up=");
    Serial.print(now / 1000);
    Serial.print("s wakes=");
    Serial.print(wakeCount);
    Serial.print(" matches=");
    Serial.print((unsigned long)matchCount);
    Serial.print(" suppressed=");
    Serial.println(suppressedCount);
    if (HEARTBEAT_LED) blinkMs(green, 30);
  }

  delay(10);
}
