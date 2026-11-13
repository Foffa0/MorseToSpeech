"""Download all offline models (English + German).

    python download_models.py

  - Piper voices (TTS):           voices/en_US-lessac-medium, voices/de_DE-thorsten-medium (~63 MB each)
  - Vosk speech recognition:      models/vosk-model-small-en-us-0.15, models/vosk-model-small-de-0.15
                                  (~40-45 MB each)
  - Argos Translate en<->de:      models/translate/en_de, models/translate/de_en (~157 MB each)
Already downloaded files are skipped, so it is safe to run again.
"""

import io
import os
import shutil
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
VOICES = os.path.join(HERE, "voices")

PIPER_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
PIPER_VOICES = {
    "en_US-lessac-medium": PIPER_BASE + "en/en_US/lessac/medium/en_US-lessac-medium",
    "de_DE-thorsten-medium": PIPER_BASE + "de/de_DE/thorsten/medium/de_DE-thorsten-medium",
}
VOSK_MODELS = {
    "vosk-model-small-en-us-0.15": "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip",
    "vosk-model-small-de-0.15": "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip",
}
ARGOS = {
    "en_de": "https://argos-net.com/v1/translate-en_de-1_3.argosmodel",
    "de_en": "https://argos-net.com/v1/translate-de_en-1_3.argosmodel",
}


def fetch(url):
    print(f"downloading {url} ...", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})  # some servers block urllib
    with urllib.request.urlopen(req) as r:
        return r.read()


def piper(name, url):
    os.makedirs(VOICES, exist_ok=True)
    for ext in (".onnx.json", ".onnx"):
        dest = os.path.join(VOICES, name + ext)
        if os.path.exists(dest):
            print(f"already there: {dest}")
            continue
        with open(dest + ".part", "wb") as f:
            f.write(fetch(url + ext))
        os.replace(dest + ".part", dest)
    print(f"ok: voice {name}")


def vosk(name, url):
    dest = os.path.join(MODELS, name)
    if os.path.isdir(dest):
        print(f"already there: {dest}")
        return
    os.makedirs(MODELS, exist_ok=True)
    zipfile.ZipFile(io.BytesIO(fetch(url))).extractall(MODELS)
    print(f"ok: {dest}")


def argos(pair, url):
    dest = os.path.join(MODELS, "translate", pair)
    if os.path.isdir(os.path.join(dest, "model")):
        print(f"already there: {dest}")
        return
    z = zipfile.ZipFile(io.BytesIO(fetch(url)))
    os.makedirs(os.path.join(dest, "model"), exist_ok=True)
    for info in z.infolist():
        # keep only model/* and sentencepiece.model (skip the stanza files, we don't need them)
        parts = info.filename.split("/", 1)
        if len(parts) < 2 or info.is_dir():
            continue
        rel = parts[1]
        if rel.startswith("model/") or rel == "sentencepiece.model":
            with z.open(info) as src, open(os.path.join(dest, rel), "wb") as out:
                shutil.copyfileobj(src, out)
    print(f"ok: {dest}")


if __name__ == "__main__":
    for name, url in PIPER_VOICES.items():
        piper(name, url)
    for name, url in VOSK_MODELS.items():
        vosk(name, url)
    for pair, url in ARGOS.items():
        argos(pair, url)
