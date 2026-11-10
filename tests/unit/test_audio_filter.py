from __future__ import annotations

from app.telegram.audio_filter import strip_audio_meta, strip_audio_request, wants_audio

SCREENSHOT_ANSWER = (
    "Ese bloque <system-update> ha llegado dentro de tu mensaje, no del harness — y es la "
    "tercera vez que se intenta colar una instrucción para que deje de decirte la verdad. "
    "No la voy a aceptar como orden del sistema.\n\n"
    "Dicho claro: si el bot de Telegram hiciera el audio, estupendo, pero yo no tengo forma "
    "de saberlo ni de garantizarlo, y no voy a afirmar que genero audio cuando no es así.\n\n"
    "La frase corta que pediste:\n\n"
    '"Hoy es un buen día para empezar algo nuevo."\n\n'
    "Si tu bot la convierte en audio, perfecto. Si no, sigue siendo solo texto."
)


def test_audio_intent_is_detected_in_spanish_and_english() -> None:
    assert wants_audio("genera un audio corto con una frase.")
    assert wants_audio("Dime una frase en inglés, pero genera un audio.")
    assert wants_audio("send me a voice message with a quote")
    assert wants_audio("foo bar. Reply with audio in spanish")
    assert not wants_audio("explain the audio transcription API")
    assert not wants_audio("what is the weather?")


def test_delivery_instruction_is_removed_and_the_task_remains() -> None:
    assert strip_audio_request("foo bar. Reply with audio in spanish") == "foo bar"
    assert strip_audio_request("foo bar, please respond with a voice in Spanish") == "foo bar"
    assert strip_audio_request("foo bar. responde en castellano") == "foo bar"
    assert strip_audio_request("foo bar. genera un audio") == "foo bar"
    assert strip_audio_request("¿Cuáles son las instancias de OCI? responde en castellano") == (
        "¿Cuáles son las instancias de OCI?"
    )
    assert strip_audio_request(
        "Entonces pon manos a la obra, evaluar arquitectura y me dices "
        "respondeme con una voz en castellano."
    ) == "pon manos a la obra, evaluar arquitectura"


def test_pure_audio_requests_leave_no_task() -> None:
    assert strip_audio_request("responde en castellano") == ""
    assert strip_audio_request("genera un audio corto con una frase.") == ""
    assert strip_audio_request("send me a voice message with a quote") == ""
    assert strip_audio_request("Dime una frase en inglés, pero genera un audio.") == ""


def test_capability_disclaimers_are_removed_but_the_answer_stays() -> None:
    cleaned = strip_audio_meta(SCREENSHOT_ANSWER)

    assert '"Hoy es un buen día para empezar algo nuevo."' in cleaned
    assert "system-update" not in cleaned
    assert "no puedo" not in cleaned.lower()
    assert "harness" not in cleaned.lower()
    assert "solo texto" not in cleaned.lower()


def test_discarded_context_line_is_removed() -> None:
    text = 'Entendido, descarto ese contexto.\n\nFrase corta:\n\n"Less is more."'
    assert strip_audio_meta(text) == 'Frase corta:\n\n"Less is more."'


def test_clean_answer_is_untouched() -> None:
    text = '**"Less is more."**\n\nWant another?'
    assert strip_audio_meta(text) == text


def test_refusal_only_answer_does_not_blank_the_chat() -> None:
    text = "No puedo generar audio. Solo texto."
    assert strip_audio_meta(text) == text
