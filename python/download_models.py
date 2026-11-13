"""Download all offline models for English <-> German translation mode.

    python download_models.py

  - Argos Translate en->de and de->en  -> models/translate/en_de, de_en  (~165 MB each)
  - Vosk German speech recognition      -> models/vosk-model-small-de-0.15 (~45 MB)
  - Piper German voice (thorsten)       -> voices/de_DE-thorsten-medium.onnx (~63 MB)
Already downloaded files are skipped.
"""

import io
import os
import shutil
import urllib.request
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "models")
VOICES = os.path.join(HERE, "voices")

ARGOS = {
    "en_de": "https://argos-net.com/v1/translate-en_de-1_3.argosmodel",
    "de_en": "https://argos-net.com/v1/translate-de_en-1_3.argosmodel",
}
VOSK_DE = "https://alphacephei.com/vosk/models/vosk-model-small-de-0.15.zip"
PIPER_DE = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/"
            "de/de_DE/thorsten/medium/de_DE-thorsten-medium")


def fetch(url):
    print(f"downloading {url} ...", flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8.0"})  # some servers block urllib
    with urllib.request.urlopen(req) as r:
        return r.read()


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


def vosk_de():
    dest = os.path.join(MODELS, "vosk-model-small-de-0.15")
    if os.path.isdir(dest):
        print(f"already there: {dest}")
        return
    zipfile.ZipFile(io.BytesIO(fetch(VOSK_DE))).extractall(MODELS)
    print(f"ok: {dest}")


def piper_de():
    os.makedirs(VOICES, exist_ok=True)
    for ext in (".onnx.json", ".onnx"):
        dest = os.path.join(VOICES, "de_DE-thorsten-medium" + ext)
        if os.path.exists(dest):
            print(f"already there: {dest}")
            continue
        with open(dest + ".part", "wb") as f:
            f.write(fetch(PIPER_DE + ext))
        os.replace(dest + ".part", dest)
    print("ok: German voice")


if __name__ == "__main__":
    for pair, url in ARGOS.items():
        argos(pair, url)
    piper_de()
    vosk_de()
