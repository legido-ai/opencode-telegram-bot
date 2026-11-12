# Changelog

All notable changes to this project are documented here. Format follows
Keep a Changelog; versions use semantic versioning.

## [0.2.2] — 2026-11-12

### Added

- The chat now shows a `…` placeholder the instant a prompt is accepted, and
  the agent's first text replaces it in the same bubble. Telegram Web renders
  no typing animation while the model is silent, so a slow run used to look
  like a dropped message (Pi behaves the same way). The typing action is still
  refreshed alongside it for the clients that do render it.
- If the placeholder is deleted before the answer lands, or Telegram refuses the
  edit, the answer is sent as a fresh message instead of disappearing.

## [Unreleased]

### Fixed

- `form.created` events are read from the V2 `data.form` envelope, so the
  agent's `question` tool now becomes a Telegram keyboard instead of leaving the
  run waiting forever and ending in `[timed out waiting for the agent]`. Form
  answers are sent as `{"answer": {field: value}}`, including multi-question
  forms.
- A message that combines a task with an audio instruction (“foo bar. Reply with
  audio in Spanish”) now runs “foo bar” through the agent and speaks its answer.
  Only an audio request with no task left uses the transient text endpoint.
  English `reply`/`answer`/`respond` requests are recognised too.
- A run that stays silent past `RUN_TIMEOUT_SECONDS` (default 1800) is marked
  failed, interrupted on the server, and reported as a timeout instead of being
  recorded as a success. This releases the session for the next message.
- Attachments now use OpenCode V2's `PromptInput.FileAttachment` shape: a
  `data:<mime>;base64,...` URI plus an optional name. The old `{type, path}`
  payload was rejected with a 400, so a photo with a caption ended in “The agent
  server rejected that request.”

### Added

- A message sent while OpenCode is running joins the live turn with
  `delivery: "steer"` and the chat gets a short “still processing, sent as a
  steer” notice. The steered answer streams into the same message with one
  sign-off, matching the TUI and `grinev/opencode-telegram-bot`.

## [0.2.1] — 2026-11-07

The completion line is a message of its own again, because a reply keyboard can
only move on a fresh send: the `📊` button now shows the real consumption after
every run instead of whatever `/new` computed. The answer no longer carries
`✅ I'm done`; a second, short bubble does, with the numbers and the keyboard.

### Added

- `Runner._sign_off` sends `✅ I'm done · 42K/1.0M (4%) · 12s` after the answer,
  with the reply keyboard attached, so the context button and the pinned card
  agree. A trailing sign-off written by the agent is stripped to avoid a double.
- `percent_text` renders a started-but-tiny session as `<1%` instead of `0%`,
  in the footer, the pinned card and the keyboard label.

### Changed

- The answer is once again one message and the sign-off another; the extra
  bubble is the price of a keyboard that can be relabelled.
- Version bumped to 0.2.1.

## [0.2.0] — 2026-11-07

Validated 2026-11-07 on `@legido_opencode2_bot`: voice notes are text-only by
default; explicit audio requests are spoken with local Piper. “Responde en
castellano” and “responde en inglés” select the text language and matching
voice. The bot uses the shared `speech-to-text` and `text-to-speech`
containers, and the old `opencode` bridge remains on its dedicated STT plus Edge
TTS.

Voice notes and spoken replies, matching the bridge currently running in OC2
(`@grinev/opencode-telegram-bot`): local Whisper STT plus local Piper TTS.

### Added

- Voice/audio intake: download the note, transcribe it through a
  Whisper-compatible API (multipart or JSON `input_audio`), post
  `🎤 Recognized: …`, then answer it through the normal prompt path. The
  transcript keeps speaker attribution and quoted context.
- Group trigger parity for voice: a voice note needs a mention, a reply to the
  bot, or `/trigger all`; a caption-less note stays silent otherwise.
- `/tts off|all|auto` per chat/topic. `off` is the default: voice notes are
  transcribed and answered in text only. `auto` speaks answers to voice notes,
  `all` speaks every answer. An explicit “genera un audio…” request is always
  spoken, even in `off` mode. Spoken replies are sent as an audio file after the
  text answer; synthesis failures never lose the answer.
- TTS providers: local Piper (default, shared container), Microsoft Edge Read
  Aloud, OpenAI-compatible `/audio/speech`, and ElevenLabs. Markdown is stripped
  and answers over 4000 characters are skipped.
- Two compiled local voices: `es-ES-ElviraNeural` (Castilian) and
  `en-US-AriaNeural` (English). `TTS_VOICE` is the normal-reply voice;
  `TTS_VOICE_AUDIO` is the default for explicit audio requests.
- Explicit audio requests select language from the prompt:
  “genera un audio en castellano” / “responde en castellano” uses Spanish text
  and `TTS_VOICE`; “en inglés” uses English text and `TTS_VOICE_AUDIO`. Without a
  language, the explicit-audio path keeps its English default.
- Fully local speech: the shared `speech-to-text` (faster-whisper) and
  `text-to-speech` (Piper) containers in the `speech` compose project. The bot
  uses them through `STT_API_URL` and an OpenAI-compatible `TTS_API_URL`; no
  audio or text leaves the host. Edge TTS remains an optional provider.
- `/status` now reports `audio replies: <mode>`.
- Audio requests bypass the coding agent: the bot calls OpenCode's transient
  text endpoint (`/api/session/{id}/generate`), which does not run tools or
  mutate history, so it returns the phrase instead of building a TTS script and
  asking for input.
- The generated phrase is filtered and held until the turn finishes, removing
  paragraphs where the model explains that it cannot generate or send audio.
  In-prompt notes and OpenCode instruction entries were both rejected by the
  model as prompt injection, so this is deterministic output handling.
- Schema v5 makes `off` the default `tts_mode`; existing `auto` rows from v4
  migrate to `off` so voice notes no longer produce unasked audio. `/tts auto`
  and `/tts all` remain explicit opt-ins.

### Changed

- `deploy/sandbox-compose.yml` joins `box_default` and points at
  `http://opencode-stt:9000/v1` with `TTS_PROVIDER=edge`, matching OC2.
- Version bumped to 0.2.0; new dependency `edge-tts==7.3.1` is part of the
  default image, so TTS requires no extra install. STT ships its client in the
  image and uses the separate `opencode-stt` Whisper service for the model.

## [0.1.1] — 2026-11-05

Validated end to end in a real Telegram group (two allowlisted humans + bot,
agent running against a sandbox OpenCode V2 server).

### Added

- Group onboarding for plain `@bot …` mentions, plus bare commands in groups
  (`/new`, `/status`, `/model`, …), while another bot's commands stay ignored.
- Typing indicator while the agent works; the answer appears only when the first
  token lands, so no empty bubble is left behind.
- Provider-first model menu: pick `OpenCode`, `OpenCode Go (Headroom)` or
  `OpenCode Go (direct)`, then the model, labelled `provider/model`.
- Context counter that matches the TUI: `used/limit (percent)` on the keyboard
  button and on the pinned card, with `0/1.0M (0%)` before the first turn.

### Changed

- **One message per answer.** The `✅ I'm done` line is appended to the answer
  text instead of being a second bubble; the separate bubble only existed to
  carry the keyboard and created the reply context clients attached to it.
- The pinned card is a single editable message; a recreated card deletes the old
  one instead of accumulating duplicates.
- Running as a plain group member is supported: pinning is optional and a pinned
  card is not required for anything else.

### Fixed

- `POST /api/session/{id}/model` and session creation now send `Model.Ref`
  (`{"model": {"providerID", "id"}}`); the previous flat payload made every model
  button report "agent server unreachable" and broke `/new` once a model was set.
- Callback data is a short token instead of embedding session id, provider and
  model, which exceeded Telegram's 64-byte limit (`Button_data_invalid`).
- Health reflects the live updater; a poller killed by a 409 Conflict used to
  report `ok` while receiving nothing.
- Agent failures surface as `⚠️ <reason>` instead of an empty answer, and a stale
  session binding is healed instead of failing the turn.

### Known limits

- Plain `@mention` delivery requires **Group Privacy off** for this bot; with it
  on, Telegram delivered commands and replies but silently dropped mentions.
- The keyboard label refreshes on commands (`/new`, `📊 Context`, model
  selection), not after every run: a reply keyboard can only travel on a message,
  and we chose one message per answer.
- Model menus list the first 20 models of a provider; `OpenCode Go` has 31.

## [0.1.0] — 2026-11-03

First release: the bot runs against an OpenCode V2 server and serves one or more
allowlisted humans, including a shared group conversation.

### Added

- Private-chat loop: prompt → streamed answer → `/abort`, with the session kept
  across restarts.
- Group conversations: entity-based mention detection, reply-to-bot triggering,
  mention stripping, and silence on human-to-human chatter.
- Attribution: every prompt carries speaker name and numeric id; answers in
  multi-human conversations are prefixed with the asker's name.
- Conversation isolation by chat and forum topic, with lazy session creation and
  persistent bindings (`/new`, `/sessions`, `/switch`).
- Model and agent selection from inline menus (`/model`, `/agent`), stored per
  conversation and applied to the session.
- Permission requests and agent questions delivered as buttons; the first
  allowlisted human to answer decides, and the message records who.
- Attachment intake for images, PDF and text under a configurable size limit.
- Per-run footer with the model, context consumption (`42K/200K (21%)`), cost
  and duration, read from `Session.Info` and the model catalogue.
- `/status` and `/id` for operators and newcomers.
- `GET /healthz` (poller + agent state), `GET /readyz`, and `GET /audit` (who
  asked, when, outcome — never message bodies).
- Offline test suite: unit, contract and integration layers with fake Telegram
  and fake OpenCode servers, plus a redaction test for secrets.
- Docker image running as uid 10001 with a named volume for state.

### Design notes

- OpenCode V2 only; the adapter pins the endpoints and payload shapes it uses.
- One queue per conversation keeps two humans in one room ordered without
  blocking other chats.
- Single poller policy: Telegram `409 Conflict` is surfaced as a hard error.
