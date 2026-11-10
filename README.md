# OpenCode Telegram Bot

Drive an OpenCode V2 server from Telegram — including from a **group where two
humans and the agent talk together**.

The bot is not a chatbot wrapper: it is a bridge to a real OpenCode server. It
opens sessions, sends prompts, streams the answer back as it is produced, and
shuttles permission requests and agent questions into the chat as buttons.

## Why this one

The bridge we ran before was single-owner and private-chat only: one allowed user
id, one DM, one session. This bot keeps that loop and adds the part a small team
needs:

| Concern | How it works here |
|---|---|
| Two humans, one agent | every conversation is keyed by chat **and topic**, sessions never leak across them |
| Who said what | every prompt carries `[speaker] name + telegram_id`; answers are prefixed with the asker's name |
| When to answer | only on @mention, reply to a bot message, or a command addressed to the bot — human-to-human chatter costs nothing |
| Quoted messages | a reply quotes the previous answer as *context*, and a mention inside a quote never triggers |
| Approvals | the first allowlisted human to press Allow/Deny decides; the message records who |
| Restarts | sessions, bindings, turn history and the dedupe ledger survive in SQLite on a volume |
| Consumption | every run closes with `✅ I'm done · 42K/200K (21%) · 12s` in its own message, which also relabels the `📊` button |
| Voice input | send a voice note or audio file; it is transcribed (Whisper-compatible API) and answered like text |
| Spoken replies | `/tts all` reads every answer, `/tts auto` only answers to voice messages, `/tts off` is silent |

Deliberately out of scope for now: scheduled tasks, worktree switching, skills
catalogue, file browser and extra languages.

## How it fits together

```text
Telegram ──long poll──► app.telegram.app  ──► trigger + identity ──► runner (queue per chat/topic)
                              │                                            │
                              │  buttons / messages                        ▼
                              └────────────────────────────── app.opencode.client ──► OpenCode V2 server
                                                                     ▲
                                              app.opencode.events ◄──┘  SSE /api/event
```

- `app/opencode/` is the only place that speaks HTTP to OpenCode. Nothing else
  builds a URL.
- `app/telegram/trigger.py` decides whether the message is addressed to the bot.
- `app/telegram/runner.py` owns one queue per conversation, so two humans in a
  room are answered in order and sessions never interleave. A message that
  arrives while a run is in flight is steered into it (`delivery: "steer"`) and
  the chat gets a short “still processing” notice.
- `app/store.py` is the durable memory; `app/health.py` is the operator view.

## Container image

Public, no credentials needed:

```bash
docker pull ghcr.io/legido-ai/opencode-telegram-bot:0.2.1   # or :latest
```

Multi-arch (`linux/amd64` and `linux/arm64`), built by `.github/workflows/publish.yml`
on every push to `main` and on `v*` tags. Tags published: `latest`, the
`pyproject.toml` version, and `sha-<commit>`.

The image **includes the speech clients by default**: `edge-tts` and `httpx` are
in `requirements.txt`, so TTS needs no extra install. STT includes the client and
expects `STT_API_URL` to point at the shared `speech-to-text` service; the Whisper model
lives in that container, not in the bot image. The old `0.1.1` image predates
speech and should not be used for voice.

## Quickstart

```bash
cp .env.example .env          # set TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_IDS, OPENCODE_SERVER_PASSWORD
make install
make test                     # offline: no token, no server, no network
make run                      # health on http://127.0.0.1:8080/healthz
```

Docker:

```bash
make build && make up
docker compose logs -f bot
curl -s localhost:8080/healthz
```

Full setup, including the group walkthrough: `specs/001-telegram-group-bot/quickstart.md`.

## Using it with two humans

1. Add the bot to the group. Privacy mode can stay on: the bot applies its own
   mention/reply policy either way.
2. Each person posts `/id@your_bot` once; keep the printed ids in
   `TELEGRAM_ALLOWED_USER_IDS`.
3. Mention it: `@your_bot what did we decide about the deploy?` — the answer
   starts with the asker's name.
4. Reply to any bot message for a follow-up; the quoted answer becomes context.
5. In a forum group, give each workstream its own topic. Each topic gets its own
   session; `/sessions` switches, `/new` starts clean.

Useful commands: `/new`, `/sessions`, `/abort`, `/status`, `/model`, `/agent`,
`/tts`, `/trigger`, `/id`, `/help`.

## Running it in a group

1. Add the bot and **turn Group Privacy off** for it. With privacy on, Telegram
   delivers commands and replies but drops plain `@mentions` — verified in a real
   group, not theory. Admin rights are *not* required; they only buy pinning.
2. Each human sends `/id` once, so participants are registered and answers get
   attributed.
3. Talk to it either way:

```
@your_bot summarise what we just decided
/new            reset the context (bare commands work in groups)
/status         session, model, agent, queue
/model          pick provider, then model
```

The answer is one message; a second, short one closes the run:
`✅ I'm done · 42K/1.0M (4%) · 12s`. That bubble exists because Telegram only
relabels a reply keyboard from a fresh send, so it is also what keeps the `📊`
button current. The pinned card carries model, context usage, cost and queue
depth; the keyboard carries the current model and `used/limit (percent)`.

## Voice and audio replies

**Validated 2026-11-07:** voice notes are text-only by default; an explicit
“genera un audio” request is spoken with local Piper.

Voice input uses any Whisper-compatible `/audio/transcriptions` endpoint. The
bot downloads the note, shows `🎤 Recognizing audio…`, posts the transcript and
then treats it as a normal prompt. In a group, a voice note follows the same
trigger rules as text (mention, reply to a bot message, or a chat set to
`/trigger all`).

Audio replies work in three modes, per chat or topic:

```
/tts off    text only (default)
/tts auto   speak answers to voice/audio messages
/tts all    speak every assistant answer
```

An explicit “genera un audio…” request is always spoken, even in `off` mode.
Ordinary voice notes are transcribed and answered in text unless you enable
`/tts auto` or `/tts all`.

The local TTS image contains **two compiled voices**:

- Castilian: `es-ES-ElviraNeural`
- English: `en-US-AriaNeural`

Say **“genera un audio en castellano”**, **“responde en castellano”**,
**“genera un audio en inglés”** or **“responde en inglés”**. The bot generates
the text in that language and selects the matching voice. Without an explicit
language, an audio request uses `TTS_VOICE_AUDIO` (English by default). TTS
itself does not translate; the model writes the requested language.

A message can carry both a task and an audio instruction, for example
**“foo bar. Reply with audio in Spanish”**. The bot strips only the delivery
instruction, sends **“foo bar”** to the coding agent with a note to answer in
Spanish, then speaks the answer. When nothing remains after stripping (for
example **“genera un audio corto con una frase”**), the bot does **not** start
the coding agent: that agent reaches for tools, builds a TTS script and asks for
a phrase. Instead it uses OpenCode's transient text endpoint to produce only the
words to speak, holds them back until the turn finishes, and removes any
capability disclaimer. The cleaned phrase is what appears in the chat and what
gets spoken.

A run that stays silent past `RUN_TIMEOUT_SECONDS` (default 1800) is stopped on
the server and reported as a timeout, so the session is free for the next
message.

Speech runs fully locally in the shared `speech` compose project:

```text
voice in  → stt:9000   (faster-whisper, local model)
voice out → tts:9000   (Piper, local voices)
```

Both services are shared with the music app, and no audio or text leaves the
host. Edge TTS remains available as an optional provider, but it is no longer
the default.

```dotenv
STT_API_URL=http://speech-to-text:9000/v1
STT_API_KEY=local
STT_MODEL=small
STT_LANGUAGE=es
STT_REQUEST_FORMAT=multipart

TTS_PROVIDER=openai
TTS_API_URL=http://text-to-speech:9000/v1
TTS_API_KEY=local
TTS_MODEL=piper
TTS_VOICE=es-ES-ElviraNeural        # normal replies: Spanish
TTS_VOICE_AUDIO=en-US-AriaNeural    # explicit "generate an audio": English
```

There are two voices: `TTS_VOICE` is the default for normal replies, and
`TTS_VOICE_AUDIO` is used when the user explicitly asks the bot to generate an
audio. The explicit-audio path generates its text in English and uses the second
voice; it falls back to `TTS_VOICE` when `TTS_VOICE_AUDIO` is empty.

The `tts` service exposes an OpenAI-compatible `/audio/speech` endpoint, so
`TTS_PROVIDER=openai` points the bot at local Piper. `TTS_PROVIDER` also accepts
`edge` (Microsoft cloud) and `elevenlabs` (`TTS_API_URL`, `TTS_API_KEY`,
`TTS_VOICE` is the voice id). Markdown is stripped before synthesis, and answers
longer than 4000 characters are skipped rather than read for minutes.

The Docker sandbox attaches the bot to `box_default` so it can resolve
`speech-to-text`; on another host point `STT_API_URL` at wherever that service
runs.

## Security model

- **Allowlist only.** Unknown users and chats are ignored without side effects;
  attempts are logged with the numeric id and nothing else.
- **Secrets come from the environment.** The bot reads the token and server
  password from its process environment and never from a secret store; they
  never appear in logs, health payloads or error messages (there is a test for
  that).
- **Message bodies are not logged.** `/audit` shows conversation, user, session,
  timestamps, status and cost — never text.
- **Single poller.** A second process on the same token gets Telegram `409
  Conflict`; the bot reports it and exits instead of spinning.
- **No inbound port.** Long polling only; the health endpoint binds to loopback
  inside the container.

Attribution is cooperative, not adversarial: the room is trusted, and the model
is told who is speaking rather than being asked to verify it.

## Configuration

See `.env.example` for the full list. The important ones:

| Variable | Meaning |
|---|---|
| `TELEGRAM_BOT_TOKEN` | token from @BotFather |
| `TELEGRAM_ALLOWED_USER_IDS` | comma-separated numeric ids; the first is the operator |
| `TELEGRAM_ALLOWED_CHAT_IDS` | optional group allowlist; empty means "any group an allowlisted human uses" |
| `TELEGRAM_TRIGGER` | `mention` (default) or `all` |
| `OPENCODE_API_URL` | OpenCode V2 server, default `http://127.0.0.1:49374` |
| `OPENCODE_SERVER_PASSWORD` | server password (`opencode service get password`) |
| `DATA_DIR` | SQLite, temp attachments; mount a volume here |
| `MAX_ATTACHMENT_MB` | per-file limit, default 20 (also voice/audio) |
| `RUN_TIMEOUT_SECONDS` | silence budget for one agent turn, default 1800 |
| `STT_API_URL` / `STT_API_KEY` | Whisper-compatible transcription endpoint; empty disables voice input |
| `STT_MODEL` / `STT_LANGUAGE` | model name and optional language hint (`es`, `en`, …) |
| `STT_REQUEST_FORMAT` | `multipart` (default) or `json` (base64 `input_audio`) |
| `TTS_PROVIDER` | `edge` (default here), `openai` or `elevenlabs` |
| `TTS_API_URL` / `TTS_API_KEY` | only for the HTTP providers; Edge needs no key |
| `TTS_MODEL` / `TTS_VOICE` | provider model and default voice for normal replies |
| `TTS_VOICE_AUDIO` | voice for explicit audio-generation requests; defaults to `TTS_VOICE` |

## Development

```bash
make test          # unit + contract + integration, all offline
make lint
make test-unit
```

### Sandbox test rig

`deploy/sandbox-up.sh` starts a throwaway agent server plus the bot pointed at
it, so live testing never touches a real workspace:

```bash
deploy/sandbox-up.sh            # build the image and start both containers
deploy/sandbox-up.sh --no-build # reuse the last image
```

It fails fast when the Docker runtime cannot start containers, waits for
`/healthz` to report `ok`, and prints the health payload. The agent workspace is
`/tmp/opencode/otb-sandbox`, the server password lives at
`/tmp/opencode/otb-password` (mode 600), and state is the `otb-test-data`
volume. `deploy/sandbox-compose.yml` is the compose file it uses.

Layout:

```text
app/                service code (config, store, health, main)
app/opencode/       adapter: HTTP client + SSE consumer
app/speech/         STT and TTS clients (Whisper API, Edge/OpenAI/ElevenLabs)
app/telegram/       intake, triggers, identity, streaming, runner, commands
tests/              unit/, contract/, integration/, fakes/
specs/001-…/        the specification, plan, contracts and task list
```

Tests never touch the network: `tests/fakes/opencode.py` is a scripted agent
server and `tests/fakes/telegram.py` records outgoing calls.

## Roadmap

- Scheduled prompts and reminders.
- Worktree switching and skills catalogue for multi-repo work.
- Per-session cost display in `/status`.

## License

Private, internal to legido-ai.
