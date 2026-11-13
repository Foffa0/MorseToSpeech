"""Offline translation (English <-> German) with Argos Translate models.

We run the Argos model files directly with CTranslate2 + SentencePiece instead of the
`argostranslate` package, which would pull in PyTorch (far too heavy for the UNO Q).
Models are loaded in int8: smaller, faster, less energy.

Models: python download_models.py   ->  python/models/translate/en_de/, de_en/
"""

import os
import re
import threading

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models", "translate")
BEAM_SIZE = 2          # 1 = fastest, 4 = slightly better quality
THREADS = 4            # the UNO Q has 4 cores


# Speech recognition gives no punctuation, so we guess questions from the first word.
QUESTION_STARTERS = {
    "en": set("do does did are is am was were can could would will should shall have has had "
              "may might what where when who whom whose why how which".split()),
    "de": set("was wo wohin woher wann wer wen wem wessen wie warum wieso weshalb welche welcher "
              "welches ist sind bin war kann kannst können darf darfst soll sollen will willst "
              "möchte möchtest hast habe haben hat brauchst gibt geht tut".split()),
}


def is_question(text, lang):
    words = text.lower().split()
    if not words:
        return False
    # German: verb + "du" at the start ("hast du", "möchtest du") is a question
    return words[0] in QUESTION_STARTERS.get(lang, ()) or (
        lang == "de" and len(words) > 1 and words[1] == "du")


def normalize(text, lang="en"):
    """Morse gives 'I NEED WATER'. The model expects normal text: 'I need water.'"""
    text = " ".join(text.split()).lower()
    if not text:
        return ""
    if lang == "en":
        text = re.sub(r"\bi\b", "I", text)
    text = text[0].upper() + text[1:]
    if text[-1] not in ".?!":
        text += "?" if is_question(text, lang) else "."
    return text


class Translator:
    def __init__(self, models_dir=MODELS_DIR):
        self.models_dir = models_dir
        self._models = {}            # "en_de" -> (ctranslate2.Translator, SentencePieceProcessor)
        self._lock = threading.Lock()
        self._load_lock = threading.Lock()

    def available(self, src, tgt):
        return os.path.isdir(os.path.join(self.models_dir, f"{src}_{tgt}", "model"))

    def translate(self, text, src, tgt):
        """Translate text. Returns the input unchanged if src == tgt or the model is missing."""
        text = normalize(text, src)
        if not text or src == tgt:
            return text
        model = self._load(src, tgt)
        if model is None:
            return text
        translator, sp = model
        with self._lock:  # one translation at a time (Morse input and speech can overlap)
            tokens = sp.encode(text, out_type=str)
            result = translator.translate_batch([tokens], beam_size=BEAM_SIZE,
                                                max_decoding_length=256)
        return sp.decode(result[0].hypotheses[0])

    def preload(self, *pairs):
        """Load models in the background, so the first translation isn't slow."""
        def run():
            for src, tgt in pairs:
                self._load(src, tgt)
        threading.Thread(target=run, daemon=True).start()

    def _load(self, src, tgt):
        with self._load_lock:
            return self._load_locked(src, tgt)

    def _load_locked(self, src, tgt):
        pair = f"{src}_{tgt}"
        if pair in self._models:
            return self._models[pair]
        path = os.path.join(self.models_dir, pair)
        model = None
        if not self.available(src, tgt):
            print(f"[translate] model {pair} not found in {self.models_dir} "
                  f"(run download_models.py)", flush=True)
        else:
            try:
                import ctranslate2
                import sentencepiece
                model = (ctranslate2.Translator(os.path.join(path, "model"), device="cpu",
                                                compute_type="int8", intra_threads=THREADS),
                         sentencepiece.SentencePieceProcessor(
                             model_file=os.path.join(path, "sentencepiece.model")))
                print(f"[translate] loaded {pair}", flush=True)
            except Exception as e:
                print(f"[translate] could not load {pair}: {e}", flush=True)
        self._models[pair] = model
        return model


if __name__ == "__main__":
    # python translate.py en de "I NEED WATER"
    import sys
    src, tgt, *words = sys.argv[1:] or ["en", "de", "I NEED WATER"]
    print(Translator().translate(" ".join(words), src, tgt))
