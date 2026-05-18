# assistant

KI-gesteuerter Google-Assistent: Kalender, Todos.
**Google Account = Login**. Alle Daten (Chat, Freigaben, AI/Telegram-Settings) am Google-Konto.
Kontrollierter Schreibzugriff über Safe-Mode-Genehmigung.

## Tech
- Backend: Python 3.12 stdlib (http.server, urllib, sqlite3)
- Frontend: Vanilla JS, CSS
- Auth: Google OAuth2 → Session-Cookie (`assistant_sid`, 30 Tage)
- KI: OpenAI-compatible API
- Chat: Web-UI + Telegram Long Polling
- DB: SQLite (users, sessions, user_settings, pending_actions, chat_messages + system settings)
- Port: 9400
- Keine externen Python-Abhängigkeiten

## Start
```bash
cd /home/rdpuser/assistant
python3 server.py
```

## API

### Auth
- `GET  /api/auth/status` – Login-Status + Google-Email
- `POST /api/auth/logout` – Session löschen

### System
- `GET  /api/status` – Systemstatus (incl. app_configured, logged_in, email)
- `GET  /api/settings` – Settings (App-Scope ohne Login, User-Scope mit Login)
- `POST /api/settings` – Settings speichern (App + User)
- `POST /api/settings/secret` – Einzelnes Secret entsperren
- `GET  /api/settings/oauth-info` – OAuth Setup-Infos (Redirect-URI, Scopes)

### Google
- `GET  /api/google/auth-url` – OAuth-Login-URL
- `GET  /oauth/callback` – Google OAuth Callback (erstellt User + Session)
- `POST /api/google/test` – Google-Verbindung testen

### Daten
- `GET  /api/calendar/events` – Kalender-Events
- `GET  /api/tasks` – Todos
- `GET  /api/chat/messages` – Chat-Verlauf
- `GET  /api/safe-mode/pending` – Ausstehende Freigaben
- `POST /api/safe-mode/approve` – Freigeben
- `POST /api/safe-mode/reject` – Ablehnen

### KI
- `POST /api/ai/chat` – Chat-Nachricht
- `POST /api/ai/test` – KI-Verbindung testen

### Telegram
- `POST /api/telegram/test` – Telegram-Verbindung testen

## Secrets (nur in SQLite, nie Git)
- System: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`
- Pro User: `AI_API_KEY`, `TELEGRAM_BOT_TOKEN`, `AI_MODEL`, `AI_BASE_URL`, `TELEGRAM_ALLOWED_USER_ID`

## Deployment
- systemd: `assistant.service`
- nginx: `/assistant/` → `127.0.0.1:9400`
- URL: `https://findyou.biz/assistant/` 🔐
