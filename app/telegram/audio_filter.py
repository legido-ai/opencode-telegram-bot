"""Keep the model's audio-capability disclaimers out of an audio answer.

Models that know they are text-only answer a request for audio with the
requested phrase *plus* a paragraph explaining that they cannot generate audio.
The Telegram bot generates and sends the audio, so that paragraph is noise in
the text bubble and in the spoken reply. The model treats in-prompt notes as
injection, so the filter is applied to its output instead.
"""

from __future__ import annotations

import re

_AUDIO_TOKEN = re.compile(
    r"\b(audio|audios|voz|voice|tts|texto a voz|text-to-speech|hablado|spoken)\b",
    re.IGNORECASE,
)
_REQUEST = re.compile(
    r"\b(genera|generar|generas|generame|manda|mandar|mandas|env[ií]a|enviar|env[ií]as|"
    r"dame|dar|haz|hacer|quiero|puedes|podr[ií]as|responde|resp[oó]ndeme|respondeme|"
    r"contesta|cont[eé]stame|contestame|di|lee|leer|speak|say|reply|answer|respond|"
    r"send|make|generate|give|read|record)\b",
    re.IGNORECASE,
)

# “responde en castellano” is an audio request in this bot, even though it does
# not contain the word "audio".
_ANSWER_LANGUAGE = re.compile(
    r"\b(responde|resp[oó]ndeme|respondeme|contesta|cont[eé]stame|contestame|habla|dime|"
    r"reply|answer|respond|speak)\b"
    r".{0,40}\b(en\s+)?(castellano|español|espanol|ingl[eé]s|english|spanish)\b",
    re.IGNORECASE,
)

# Delivery instructions are meta, not the task. They are stripped before the
# rest of the message reaches the agent, so “foo bar. Reply with audio in
# Spanish” actually processes “foo bar” instead of throwing it away.
_CLAUSE_START = r"(?:^|(?<=[.!?;,\n]))\s*(?:(?:please|por favor|and|but|pero|y|then|so|entonces)\s+)?"
_CLAUSE_END = r"(?=[.!?;,\n]|$)"

_AUDIO_DELIVERY_CLAUSE = re.compile(
    _CLAUSE_START
    + r"\b(reply|answer|respond|speak|say|contesta|cont[eé]stame|contestame|responde|"
    r"resp[oó]ndeme|respondeme|habla|dime)\b[^.!?;,\n]*?"
    r"\b(audio|audios|voz|voice|voice note|voice message|spoken|aloud|tts|text-to-speech)\b"
    r"[^.!?;,\n]*?"
    + _CLAUSE_END,
    re.IGNORECASE,
)

_AUDIO_GENERATION_CLAUSE = re.compile(
    _CLAUSE_START
    + r"\b(genera|generar|generame|gen[eé]rame|manda|mandar|mandame|m[aá]ndame|"
    r"env[ií]a|enviar|env[ií]ame|enviame|send|make|give|produce)\b[^.!?;,\n]*?"
    r"\b(audio|audios|voz|voice|voice note|voice message|tts|text-to-speech)\b"
    r"[^.!?;,\n]*?"
    + _CLAUSE_END,
    re.IGNORECASE,
)

_ANSWER_LANGUAGE_CLAUSE = re.compile(
    _CLAUSE_START
    + r"\b(reply|answer|respond|speak|say|contesta|cont[eé]stame|contestame|responde|"
    r"resp[oó]ndeme|respondeme|habla|dime)\b[^.!?;,\n]*?"
    r"\b(castellano|español|espanol|spanish|ingl[eé]s|ingles|english)\b"
    r"[^.!?;,\n]*?"
    + _CLAUSE_END,
    re.IGNORECASE,
)

# “...y me dices respóndeme con una voz en castellano” glues the instruction to
# the task with a conjunction instead of punctuation. Catch that shape too.
_TELL_ME = r"\b(?:y|and|pero|but|then|entonces)\s+(?:me\s+dices|me\s+dicen|dime|tell\s+me)\s+"
_AUDIO_INSTRUCTION_ANYWHERE = re.compile(
    _TELL_ME
    + r"[^.!?;,\n]*?\b(reply|answer|respond|speak|say|contesta|cont[eé]stame|contestame|"
    r"responde|resp[oó]ndeme|respondeme|habla|dime)\b[^.!?;,\n]*?"
    r"\b(audio|audios|voz|voice|voice note|voice message|spoken|aloud|tts|text-to-speech)\b"
    r"[^.!?;,\n]*?"
    + _CLAUSE_END,
    re.IGNORECASE,
)
_LANGUAGE_INSTRUCTION_ANYWHERE = re.compile(
    _TELL_ME
    + r"[^.!?;,\n]*?\b(reply|answer|respond|speak|say|contesta|cont[eé]stame|contestame|"
    r"responde|resp[oó]ndeme|respondeme|habla|dime)\b[^.!?;,\n]*?"
    r"\b(castellano|español|espanol|spanish|ingl[eé]s|ingles|english)\b"
    r"[^.!?;,\n]*?"
    + _CLAUSE_END,
    re.IGNORECASE,
)

_FILLER = re.compile(
    r"(?:please|por favor|and|but|pero|y|then|so|entonces|gracias|thanks)\b",
    re.IGNORECASE,
)
_EDGES = " \t\r\n,;:.-"


def _trim_filler(text: str) -> str:
    text = text.strip(_EDGES)
    changed = True
    while changed and text:
        changed = False
        match = _FILLER.match(text)
        if match:
            text = text[match.end() :].lstrip(_EDGES)
            changed = True
        trailing = re.search(
            r"(?:^|[\s,;:.-]+)(?:please|por favor|and|but|pero|y|then|so|entonces|"
            r"gracias|thanks)$",
            text,
            re.IGNORECASE,
        )
        if trailing:
            text = text[: trailing.start()].rstrip(_EDGES)
            changed = True
    return text


def strip_audio_request(text: str) -> str:
    """Remove “reply with audio in Spanish”-style delivery instructions.

    What remains is the actual task. An empty result means the message was only
    an audio request, and the bot may generate a standalone phrase instead of
    running the coding agent.
    """

    cleaned = text
    for pattern in (
        _AUDIO_INSTRUCTION_ANYWHERE,
        _LANGUAGE_INSTRUCTION_ANYWHERE,
        _AUDIO_DELIVERY_CLAUSE,
        _AUDIO_GENERATION_CLAUSE,
        _ANSWER_LANGUAGE_CLAUSE,
    ):
        cleaned = pattern.sub(" ", cleaned)
    return _trim_filler(" ".join(cleaned.split()))


_META_ONLY = (
    "<system-update>",
    "system-update",
    "prompt injection",
    "inyección de prompt",
    "inyeccion de prompt",
    "del harness",
    "el harness",
    "no la voy a aceptar",
    "no lo voy a aceptar",
    "orden del sistema",
    "instrucción para que",
    "instruccion para que",
    "se intenta colar",
    "colar una instrucción",
    "colar una instruccion",
    "descarto ese contexto",
    "descarto la instrucción",
    "descarto la instruccion",
    "ignoro esa instrucción",
    "ignoro esa instruccion",
)

_META_WITH_AUDIO = (
    "no puedo",
    "no podré",
    "no podre",
    "no tengo",
    "no genero",
    "no adjunto",
    "no envío",
    "no envio",
    "solo texto",
    "sólo texto",
    "texto plano",
    "text only",
    "text-only",
    "no es así",
    "no es asi",
    "no voy a afirmar",
    "ningún archivo",
    "ningun archivo",
    "no hay archivo",
    "no hay ningún",
    "no hay ningun",
    "i can't",
    "i cannot",
    "i don't",
    "i do not",
    "not able",
    "cannot generate",
    "cannot send",
    "no audio",
    "si el bot",
    "si tu bot",
    "el bot de telegram",
    "telegram bot",
    "el puente",
    "dicho claro",
    "para ser preciso",
    "para ser precisa",
    "si de verdad",
    "si quieres audio",
    "si necesitas audio",
    "sigue siendo",
    "convierte en audio",
    "convierta en audio",
)


def wants_audio(text: str) -> bool:
    """True when the message asks the bot to produce or send audio."""

    if _ANSWER_LANGUAGE.search(text):
        return True
    return bool(_AUDIO_TOKEN.search(text) and _REQUEST.search(text))


def _is_disclaimer(block: str) -> bool:
    lowered = block.lower()
    if any(marker in lowered for marker in _META_ONLY):
        return True
    if not _AUDIO_TOKEN.search(lowered):
        return False
    return any(marker in lowered for marker in _META_WITH_AUDIO)


def strip_audio_meta(text: str) -> str:
    """Drop paragraphs that only explain the model's lack of audio support."""

    if not text.strip():
        return text
    blocks = re.split(r"\n\s*\n", text)
    kept = [block for block in blocks if block.strip() and not _is_disclaimer(block)]
    if not kept:
        # The model produced nothing but the disclaimer; do not blank the chat.
        return text
    return "\n\n".join(block.strip("\n") for block in kept).strip()
