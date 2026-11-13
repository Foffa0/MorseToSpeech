"""Language settings for user (taps Morse) and partner (speaks), changeable at runtime.

Used by main.py and the web page (assets/index.html) via /api/languages.
Saved to settings.json, so the choice survives a restart.
"""

import json
import os
import threading

LANGUAGES = {"en": "English", "de": "Deutsch"}
DEFAULT_USER = "en"
DEFAULT_PARTNER = os.environ.get("MORSE_PARTNER_LANG", "de")
SETTINGS_PATH = os.path.join(os.path.dirname(__file__), "settings.json")


def _always(_lang):
    return True


class LanguageSettings:
    def __init__(self, translator, settings_path=SETTINGS_PATH, on_change=None,
                 stt_available=_always, voice_available=_always):
        """translator: translate.Translator (for availability checks).
        on_change(user, partner): called after every change.
        stt_available(lang) / voice_available(lang): are the Vosk model / Piper voice there?"""
        self.translator = translator
        self.settings_path = settings_path
        self.on_change = on_change
        self.stt_available = stt_available
        self.voice_available = voice_available
        self._lock = threading.Lock()
        self.user, self.partner = DEFAULT_USER, DEFAULT_PARTNER
        self._load()

    # --- what the rest of the app asks -----------------------------------------
    @property
    def translating_out(self):
        """User's Morse is translated before it's spoken to the partner."""
        return self.user != self.partner and self.translator.available(self.user, self.partner)

    @property
    def translating_in(self):
        """Partner's speech is translated before it's played as Morse."""
        return self.user != self.partner and self.translator.available(self.partner, self.user)

    def set(self, user, partner):
        """Change both languages. Raises ValueError for unknown codes."""
        for code in (user, partner):
            if code not in LANGUAGES:
                raise ValueError(f"unknown language '{code}', choose from {list(LANGUAGES)}")
        with self._lock:
            self.user, self.partner = user, partner
            self._save()
        print(f"[lang] user={user} partner={partner}  "
              f"out={'translate' if self.translating_out else 'direct'} "
              f"in={'translate' if self.translating_in else 'direct'}", flush=True)
        if self.on_change:
            self.on_change(user, partner)

    def status(self):
        """Everything the web page needs to show, incl. warnings about missing models."""
        warnings = []
        if self.user != self.partner:
            if not self.translating_out:
                warnings.append(f"Translation model {self.user}->{self.partner} missing: "
                                f"your Morse is spoken untranslated.")
            if not self.translating_in:
                warnings.append(f"Translation model {self.partner}->{self.user} missing: "
                                f"the partner's speech is played untranslated.")
        if not self.voice_available(self.partner if self.translating_out else self.user):
            warnings.append("No voice for the spoken language: using the robotic fallback voice.")
        if not self.stt_available(self.partner):
            warnings.append(f"No speech recognition model for {LANGUAGES[self.partner]}: "
                            f"listening to the partner won't work.")
        return {
            "languages": [{"code": c, "name": n} for c, n in LANGUAGES.items()],
            "user": self.user,
            "partner": self.partner,
            "translating_out": self.translating_out,
            "translating_in": self.translating_in,
            "warnings": warnings,
        }

    # --- persistence ---------------------------------------------------------------
    def _load(self):
        try:
            with open(self.settings_path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("user") in LANGUAGES and data.get("partner") in LANGUAGES:
                self.user, self.partner = data["user"], data["partner"]
        except (OSError, ValueError):
            pass  # first start or unreadable -> defaults

    def _save(self):
        try:
            with open(self.settings_path, "w", encoding="utf-8") as f:
                json.dump({"user": self.user, "partner": self.partner}, f)
        except OSError as e:  # read-only file system -> just don't persist
            print(f"[lang] could not save settings: {e}", flush=True)
