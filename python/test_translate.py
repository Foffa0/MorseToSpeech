"""Laptop tests for translation: python -m pytest python/test_translate.py
The model tests are skipped if the models aren't downloaded (python download_models.py)."""

from translate import Translator, normalize

T = Translator()


def test_normalize_morse_text():
    assert normalize("I NEED WATER") == "I need water."
    assert normalize("  WHERE IS   MY PHONE ") == "Where is my phone?"
    assert normalize("HELP!") == "Help!"
    assert normalize("") == ""


def test_normalize_german_questions():
    assert normalize("hast du schmerzen", "de") == "Hast du schmerzen?"
    assert normalize("möchtest du tee", "de") == "Möchtest du tee?"
    assert normalize("wo ist das telefon", "de") == "Wo ist das telefon?"
    assert normalize("deine tochter kommt", "de") == "Deine tochter kommt."


def test_same_language_is_unchanged():
    assert T.translate("I NEED WATER", "en", "en") == "I need water."


def test_missing_model_returns_input():
    assert Translator(models_dir="does-not-exist").translate("HELLO", "en", "de") == "Hello."


def test_translation_both_ways():
    if not (T.available("en", "de") and T.available("de", "en")):
        print("skipped: translation models not downloaded")
        return
    assert T.translate("I AM IN PAIN", "en", "de") == "Ich habe Schmerzen."
    assert T.translate("hast du hunger", "de", "en").endswith("?")
    assert "daughter" in T.translate("deine tochter kommt um drei uhr", "de", "en").lower()
