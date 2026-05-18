# assistant MEMORY

## Projekt
- Pfad: `/home/rdpuser/assistant/`
- Ziel: persönlicher KI-Assistent für Google Calendar + Tasks.
- UI: mobil + desktop, Web-Chat + Telegram-Chat.
- Schreibzugriff ausschließlich über Safe Mode.
- **Google Account = Login / Identität**. Alles (Chat, Safe-Mode, AI/Telegram-Settings) hängt am Google-Konto.

## Entscheidungen
- Name: `assistant`
- Port: `9400`
- Backend ohne externe Dependencies: Python stdlib + SQLite.
- Google OAuth: Session-Cookie (`assistant_sid`, 30 Tage), kein Passwort-Login nötig.
- Google OAuth App (Client-ID/Secret) systemweit konfigurierbar – danach Login mit beliebigem Google-Konto möglich.
- Persönliche Settings (AI, Telegram) pro Google-Email gespeichert.
- Secrets nie in Git.
- Telegram: Long Polling, per User-ID auf eine Google-E-Mail gebunden.
- Google OAuth Callback: `https://findyou.biz/assistant/oauth/callback`
- AI: OpenAI-compatible API.

## Safe Mode
Lesen erlaubt, Schreiben nur nach Freigabe (Web-UI oder Telegram-Inline-Buttons).
Aktionen sind an die Google-E-Mail des Users gebunden.

## Security
- App privat: nginx Basic Auth + WireGuard/VPN.
- Session-Cookies: HttpOnly, Secure, SameSite Lax, Path /assistant/.
- Kein Secret in Dateien, Git oder Logs.

## Nächste Schritte
- Google OAuth in echter Umgebung testen
- AI-Verbindung mit Ollama Cloud testen
- Telegram Bot mit User-ID testen
