"""Adaptive Morse decoder.

Pure logic, no hardware imports, so it can be unit-tested on a laptop and fed
from any input source (button, IMU, distance sensor, keyboard).

All times are in milliseconds. The caller provides the clock (`now_ms`), which
keeps the decoder deterministic and easy to test.
"""

# --- Tuning constants -------------------------------------------------------
INITIAL_DOT_MS = 150.0      # starting guess for a dot press
INITIAL_DASH_MS = 450.0     # starting guess for a dash press
ADAPT_ALPHA = 0.2           # EMA weight of a new press (higher = adapts faster)
MIN_DOT_MS = 40.0
MAX_DOT_MS = 600.0
MIN_DASH_DOT_RATIO = 2.0    # dash is always at least this many times a dot
LETTER_GAP_FACTOR = 2.5     # silence > factor * dot  -> letter finished
WORD_GAP_FACTOR = 6.0       # silence > factor * dot  -> word finished
# Real users pause much longer between letters than textbook Morse (3 dots).
# So the gaps are learned too: the pauses the user makes between letters set the word gap.
INTRA_GAP_FACTOR = 2.0      # letter ends if silence > factor * learned gap inside a letter
LETTER_TO_WORD_FACTOR = 2.0 # word ends if silence > factor * learned gap between letters
MIN_WORD_GAP_MS = 1200.0    # never end a word faster than this
MAX_LETTER_GAP_MS = 4000.0  # ignore extreme pauses when learning

MORSE_TABLE = {
    ".-": "A", "-...": "B", "-.-.": "C", "-..": "D", ".": "E", "..-.": "F",
    "--.": "G", "....": "H", "..": "I", ".---": "J", "-.-": "K", ".-..": "L",
    "--": "M", "-.": "N", "---": "O", ".--.": "P", "--.-": "Q", ".-.": "R",
    "...": "S", "-": "T", "..-": "U", "...-": "V", ".--": "W", "-..-": "X",
    "-.--": "Y", "--..": "Z",
    "-----": "0", ".----": "1", "..---": "2", "...--": "3", "....-": "4",
    ".....": "5", "-....": "6", "--...": "7", "---..": "8", "----.": "9",
    ".-.-.-": ".", "--..--": ",", "..--..": "?", "-..-.": "/",
}
UNKNOWN_CHAR = "?"


def _noop(*_args):
    pass


class MorseDecoder:
    """Turns key press durations and silences into letters and words.

    Callbacks:
        on_symbol(symbol)  -- current dot/dash pattern changed, e.g. ".-"
        on_letter(char)    -- a letter was finished
        on_word(word)      -- a word was finished (silence long enough)
    """

    def __init__(self, on_symbol=None, on_letter=None, on_word=None,
                 dot_ms=INITIAL_DOT_MS, dash_ms=INITIAL_DASH_MS):
        self.on_symbol = on_symbol or _noop
        self.on_letter = on_letter or _noop
        self.on_word = on_word or _noop
        self.dot_avg = float(dot_ms)
        self.dash_avg = float(dash_ms)
        self.symbol = ""          # dots/dashes of the letter being typed
        self.word = ""            # letters of the word being typed
        self.words = []           # finished words of the sentence
        self._word_open = False   # word got new letters since it was last finished
        self._key_down = False
        self._last_release = None
        self.intra_gap_avg = float(dot_ms)       # pause between dots/dashes of a letter
        self.letter_gap_avg = 3.0 * float(dot_ms)  # pause between letters of a word

    # --- input events -------------------------------------------------------
    def key_down(self, now_ms=None):
        self._key_down = True
        if now_ms is None or self._last_release is None:
            return
        gap = now_ms - self._last_release
        if self.symbol:                    # pause inside a letter
            self.intra_gap_avg = self._ema(self.intra_gap_avg, gap)
        elif self._word_open:              # pause between letters of the same word
            gap = min(gap, MAX_LETTER_GAP_MS)
            self.letter_gap_avg = self._ema(self.letter_gap_avg, gap)

    def key_up(self, duration_ms, now_ms):
        """Key released after being held `duration_ms`. `now_ms` = release time."""
        self._key_down = False
        self._last_release = now_ms
        duration_ms = float(duration_ms)

        if duration_ms < self.threshold:
            self.symbol += "."
            self.dot_avg = self._ema(self.dot_avg, duration_ms)
        else:
            self.symbol += "-"
            self.dash_avg = self._ema(self.dash_avg, duration_ms)
        self._clamp()
        self.on_symbol(self.symbol)

    def tick(self, now_ms):
        """Call regularly. Finishes letters/words when the silence is long enough."""
        if self._key_down or self._last_release is None:
            return
        gap = now_ms - self._last_release
        if self.symbol and gap > self.letter_gap_threshold:
            self._finish_letter()
        if self._word_open and not self.symbol and gap > self.word_gap_threshold:
            self._finish_word()

    def backspace(self):
        """Remove the current dot/dash pattern, else the last letter."""
        if self.symbol:
            self.symbol = ""
            self.on_symbol(self.symbol)
        elif self.word:
            self.word = self.word[:-1]
        elif self.words:
            self.word = self.words.pop()[:-1]

    def take_sentence(self):
        """Return everything typed so far (including the open word) and clear it."""
        if self.symbol:
            self._finish_letter()
        parts = self.words + ([self.word] if self.word else [])
        self.words, self.word, self._word_open = [], "", False
        return " ".join(parts)

    # --- helpers ------------------------------------------------------------
    @property
    def threshold(self):
        return (self.dot_avg + self.dash_avg) / 2.0

    @property
    def letter_gap_threshold(self):
        return max(LETTER_GAP_FACTOR * self.dot_avg, INTRA_GAP_FACTOR * self.intra_gap_avg)

    @property
    def word_gap_threshold(self):
        return max(WORD_GAP_FACTOR * self.dot_avg,
                   LETTER_TO_WORD_FACTOR * self.letter_gap_avg,
                   MIN_WORD_GAP_MS,
                   self.letter_gap_threshold * 1.5)

    def text(self):
        """Live view, e.g. 'HELLO WOR' (without the pending symbol)."""
        return " ".join(self.words + ([self.word] if self.word else []))

    def _finish_letter(self):
        char = MORSE_TABLE.get(self.symbol, UNKNOWN_CHAR)
        self.symbol = ""
        self.word += char
        self._word_open = True
        self.on_letter(char)

    def _finish_word(self):
        self._word_open = False
        if self.word:
            self.words.append(self.word)
            word, self.word = self.word, ""
            self.on_word(word)

    @staticmethod
    def _ema(avg, value):
        return (1 - ADAPT_ALPHA) * avg + ADAPT_ALPHA * value

    def _clamp(self):
        self.dot_avg = min(max(self.dot_avg, MIN_DOT_MS), MAX_DOT_MS)
        if self.dash_avg < MIN_DASH_DOT_RATIO * self.dot_avg:
            self.dash_avg = MIN_DASH_DOT_RATIO * self.dot_avg
