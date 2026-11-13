# 😀 MorseToSpeech

## Wake word (Nicla Voice)

An Arduino Nicla Voice listens for the wake word. When it hears it, it tells the UNO Q to start the interaction. For now the wake word is the built-in **"Alexa"** demo model. A custom name will be trained later in Edge Impulse.

### Hardware
- Connect the Nicla Voice to the UNO Q with a **Micro-USB data cable** (charge-only cables won't work).
- The UNO Q has only one USB-C port, so connect both through a **USB-C hub with power passthrough**.

### Flashing the Nicla (from a PC, once)
1. In the Arduino IDE, install the **"Arduino Mbed OS Nicla Boards"** core and select the board **Arduino Nicla Voice**.
2. Update the NDP120 firmware and load the Alexa model to the Nicla's flash. Follow "Updating the NDP120 firmware" in the [Nicla Voice user manual](https://docs.arduino.cc/tutorials/nicla-voice/user-manual/). It flashes `mcu_fw_120_v91.synpkg`, `dsp_firmware_v91.synpkg` and `alexa_334_NDP120_B0_v11_v91.synpkg`.
3. Optional sanity check: flash `File > Examples > NDP > AlexaDemo`, say "Alexa", and the LED flashes blue.
4. Flash `nicla_wake/nicla_wake.ino`. Once the models are loaded, the LED blinks green.

### Protocol (USB serial, 115200 baud)
| Line | Meaning |
|------|---------|
| `WAKE <label>` | Wake word detected (at most once per 2 s) |
| `# ...` | Debug output |

### UNO Q side
`python/main.py` finds the Nicla's port automatically. Set `NICLA_PORT` to override it. On every `WAKE` line it calls `on_wake(label)`. **Plug the recording, speech-to-text and Morse steps into `on_wake()`.**
