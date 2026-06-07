# JARVIS — Voice AI Assistant

## Overview
JARVIS (Just A Rather Very Intelligent System) is a voice-first AI assistant for Linux. It runs locally on your machine, connecting to your calendar (CalDAV), mail (IMAP, read-only), notes (filesystem), and can spawn Claude Code sessions for development tasks.

## Quick Start
When a user clones this repo and starts Claude Code, help them:
1. Copy .env.example to .env
2. Get an OpenRouter API key from openrouter.ai
3. Get a Fish Audio API key from fish.audio
4. Install Python dependencies: pip install -r requirements.txt
5. Install frontend dependencies: cd frontend && npm install
6. Generate SSL certs: openssl req -x509 -newkey rsa:2048 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
7. Run the backend: python server.py
8. Run the frontend: cd frontend && npm run dev
9. Open Chrome to http://localhost:5173
10. Click to enable audio, speak to JARVIS

## Architecture
- **Backend**: FastAPI + Python (server.py, ~2300 lines)
- **Frontend**: Vite + TypeScript + Three.js (audio-reactive orb)
- **Communication**: WebSocket (JSON messages + binary audio)
- **AI**: LLM via OpenRouter (fast + deep models)
- **TTS**: Fish Audio with JARVIS voice model
- **System**: CalDAV (calendar) + IMAP (mail, read-only) + filesystem notes

## Key Files
- `server.py` — Main server, WebSocket handler, LLM integration, action system
- `frontend/src/orb.ts` — Three.js particle orb visualization
- `frontend/src/voice.ts` — Web Speech API + audio playback
- `frontend/src/main.ts` — Frontend state machine
- `memory.py` — SQLite memory system with FTS5 search
- `calendar_access.py` — Calendar integration (CalDAV)
- `mail_access.py` — Mail integration (IMAP, read-only)
- `notes_access.py` — Notes integration (filesystem)
- `actions.py` — System actions (Terminal, Chrome, Claude Code)
- `browser.py` — Playwright web automation
- `work_mode.py` — Persistent Claude Code sessions

## Environment Variables
- `OPENROUTER_API_KEY` (required) — LLM API access via OpenRouter
- `FISH_API_KEY` (required) — Fish Audio TTS
- `FISH_VOICE_ID` (optional) — Voice model ID
- `USER_NAME` (optional) — Your name for JARVIS to use
- `CALENDAR_ACCOUNTS` (optional) — Comma-separated calendar emails

## Conventions
- JARVIS personality: British butler, dry wit, economy of language
- Max 1-2 sentences per voice response
- Action tags: [ACTION:BUILD], [ACTION:BROWSE], [ACTION:RESEARCH], etc.
- Read-only for Mail (safety by design)
- SQLite for all local data storage
