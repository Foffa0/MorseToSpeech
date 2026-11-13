"""Laptop tests for the decoder: python -m pytest python/test_morse.py"""

from morse import CHAR_TO_MORSE, MorseDecoder, encode, timeline


class Sim:
    """Simulated user + clock that taps text into a decoder."""

    def __init__(self):
        self.t = 0.0
        self.words = []
        self.calibrations = []
        self.dec = MorseDecoder(on_word=self.words.append, on_calibration=self.calibrations.append)

    def wait(self, ms):
        end = self.t + ms
        while self.t < end:
            self.t = min(self.t + 10, end)
            self.dec.tick(self.t)

    def press(self, ms):
        self.dec.key_down(self.t)
        self.t += ms
        self.dec.key_up(ms, self.t)

    def tap_text(self, text, unit, dash_ratio=3.0, letter_gap=None, word_gap=None):
        letter_gap = letter_gap or 3 * unit
        word_gap = word_gap or max(7 * unit, 2600)
        for w_i, word in enumerate(text.split(" ")):
            if w_i:
                self.wait(word_gap)
            for c_i, ch in enumerate(word):
                if c_i:
                    self.wait(letter_gap)
                for s_i, s in enumerate(CHAR_TO_MORSE[ch]):
                    if s_i:
                        self.wait(unit)
                    self.press(unit if s == "." else dash_ratio * unit)
        self.wait(max(10 * unit, 4000))


def test_sos():
    sim = Sim()
    sim.tap_text("SOS", unit=150)
    assert sim.words == ["SOS"]


def test_hello_world():
    sim = Sim()
    sim.tap_text("HELLO WORLD", unit=150)
    assert sim.words == ["HELLO", "WORLD"]
    assert sim.dec.take_sentence() == "HELLO WORLD"
    assert sim.dec.text() == ""


def test_adapts_to_slow_user():
    sim = Sim()
    for unit in (170, 200, 230, 260, 300):   # user slows down gradually
        sim.tap_text("PARIS", unit=unit)
    sim.words.clear()
    sim.tap_text("HELP ME", unit=300)
    assert sim.words == ["HELP", "ME"]
    assert 250 < sim.dec.dot_avg < 350


def test_adapts_to_fast_user():
    sim = Sim()
    for unit in (130, 110, 95, 80):          # user speeds up gradually
        sim.tap_text("PARIS", unit=unit)
    sim.words.clear()
    sim.tap_text("YES NO", unit=80)
    assert sim.words == ["YES", "NO"]
    assert sim.dec.dot_avg < 110


def test_sloppy_dash_ratio():
    sim = Sim()
    sim.tap_text("WATER", unit=150, dash_ratio=2.5)
    assert sim.words == ["WATER"]


def test_long_pauses_between_letters_stay_one_word():
    # Real beginner: quick taps (dot 100 ms) but ~1 s thinking pause between letters.
    sim = Sim()
    sim.tap_text("HELLO WORLD", unit=100, letter_gap=1000, word_gap=3000)
    assert sim.words == ["HELLO", "WORLD"]


def test_fast_tapper_with_slow_letter_pauses():
    sim = Sim()
    sim.tap_text("I NEED WATER", unit=110, letter_gap=900, word_gap=2500)
    assert sim.words == ["I", "NEED", "WATER"]


def test_unknown_symbol():
    sim = Sim()
    for _ in range(8):                       # 8 dots is not a letter
        sim.press(150)
        sim.wait(150)
    sim.wait(1500)
    assert sim.words == ["?"]


def test_backspace():
    sim = Sim()
    sim.press(150)                           # start a symbol...
    sim.dec.backspace()                      # ...and drop it
    assert sim.dec.symbol == ""
    sim.wait(1500)
    assert sim.words == []

    sim.tap_text("HI", unit=150)
    sim.dec.backspace()                      # reopens finished word, drops "I"
    assert sim.dec.text() == "H"
    sim.wait(1500)                           # must not re-speak the edited word
    assert sim.words == ["HI"]


# --- speech -> Morse encoding -----------------------------------------------
def test_encode():
    assert encode("sos") == "... --- ..."
    assert encode("Hi  you!") == ".... .. / -.-- --- ..-"   # '!' unknown -> skipped
    assert encode("!!!") == ""


def test_timeline_standard_timing():
    assert timeline(encode("ET A"), unit_ms=100) == [
        (True, 100), (False, 300), (True, 300),   # E, letter gap, T
        (False, 700),                              # word gap
        (True, 100), (False, 100), (True, 300),   # A
    ]


def test_roundtrip_encode_then_decode():
    """What we send as Morse must be decodable by our own decoder."""
    sim = Sim()
    # unit 200 -> word gap 1400 ms, above the decoder's MIN_WORD_GAP_MS (1200)
    for on, ms in timeline(encode("I NEED HELP"), unit_ms=200):
        if on:
            sim.press(ms)
        else:
            sim.wait(ms)
    sim.wait(4000)
    assert sim.dec.take_sentence() == "I NEED HELP"


# --- speed calibration --------------------------------------------------------
def test_calibration_fast_user():
    """Without calibration a sudden 70 ms dot is misread (dashes < initial threshold)."""
    sim = Sim()
    sim.dec.start_calibration()
    sim.tap_text("PARIS", unit=70)
    assert sim.calibrations == [True]
    assert not sim.dec.calibrating
    assert sim.words == []                     # calibration word is not spoken
    assert 60 < sim.dec.dot_avg < 80 and 190 < sim.dec.dash_avg < 230
    sim.tap_text("I NEED WATER", unit=70)
    assert sim.words == ["I", "NEED", "WATER"]


def test_calibration_slow_user_with_long_pauses():
    sim = Sim()
    sim.dec.start_calibration()
    sim.tap_text("PARIS", unit=300, letter_gap=1500)
    assert sim.calibrations == [True]
    sim.tap_text("HELP ME", unit=300, letter_gap=1500, word_gap=4000)
    assert sim.words == ["HELP", "ME"]


def test_calibration_learns_sloppy_dash_ratio():
    sim = Sim()
    sim.dec.start_calibration()
    sim.tap_text("PARIS", unit=120, dash_ratio=2.0)
    assert sim.calibrations == [True]
    sim.tap_text("YES", unit=120, dash_ratio=2.0)
    assert sim.words == ["YES"]


def test_calibration_retries_on_mistake():
    sim = Sim()
    sim.dec.start_calibration()
    for _ in range(14):                       # only dots -> can't tell dot from dash
        sim.press(100)
        sim.wait(100)
    assert sim.calibrations == [False]
    assert sim.dec.calibrating                 # restarted, waiting for PARIS again
    assert sim.dec.calibration_progress == (0, 14)
    sim.tap_text("PARIS", unit=100)
    assert sim.calibrations == [False, True]


def test_calibration_can_be_skipped():
    sim = Sim()
    sim.dec.start_calibration()
    sim.press(150)
    sim.dec.cancel_calibration()
    sim.tap_text("SOS", unit=150)
    assert sim.words == ["SOS"]
