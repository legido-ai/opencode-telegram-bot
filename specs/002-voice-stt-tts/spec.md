# Feature Specification: Voice input and spoken replies

**Feature Branch**: `002-voice-stt-tts`
**Created**: 2026-11-06
**Status**: Validated

## Goal

Match the speech capabilities of the bridge the bot replaced
(`@grinev/opencode-telegram-bot`): transcribe voice notes and audio files, and
reply with TTS, without leaving the Telegram conversation.

## Behaviour

1. A voice note or audio file is downloaded, transcribed through a
   Whisper-compatible API, shown as `🎤 Recognized: …`, and answered through the
   normal prompt path.
2. In a group, a voice note follows the same trigger rules as text: mention,
   reply to the bot, or `/trigger all`.
3. Audio replies have three modes per chat/topic: `/tts off` (default:
   text-only), `/tts auto` (answers to voice/audio messages), `/tts all`.
   An explicit “generate an audio” request is always spoken.
4. `TTS_VOICE` is the default voice (Spanish in this deployment).
   `TTS_VOICE_AUDIO` is used when the user explicitly asks for an audio, and the
   text for that path is generated in English.
5. An audio request that carries no task of its own (for example “genera un
   audio corto con una frase”) bypasses the coding agent and uses OpenCode's
   transient `/api/session/{id}/generate`: no tools, no workspace changes, no
   TTS script. A request that combines a task with an audio instruction (for
   example “foo bar. Reply with audio in Spanish”) strips the delivery
   instruction, runs the task through the agent, and speaks its answer.
6. Capability disclaimers are filtered from text and speech; the answer is held
   until the filter has run so they never reach the chat.
7. Failures in STT or TTS are reported without losing the text answer or
   leaking URLs, keys or audio bodies.

## Out of scope

- Real-time voice sessions (OpenCode's WebRTC/WebSocket voice API).
- Google Cloud TTS; local Piper, Edge, OpenAI-compatible and ElevenLabs are
  supported.
- Automatic language detection for the default voice.
