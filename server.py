#!/usr/bin/env python3
"""Assistant backend – Google Account = identity, session-cookie auth."""

from __future__ import annotations

import json
import os
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("ASSISTANT_PORT", "9400"))
BASE_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = BASE_DIR / "public"
SESSION_COOKIE = "assistant_sid"
SESSION_MAX_AGE = 30 * 86400  # 30 days

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
ROUTES: dict[str, dict[str, callable]] = {}


def route(path: str, methods: list[str] | None = None):
    if methods is None:
        methods = ["GET"]

    def decorator(fn):
        ROUTES.setdefault(path, {})["OPTIONS"] = _cors_options
        for m in methods:
            ROUTES[path][m] = fn
        return fn

    return decorator


def _cors_options(handler: Handler, **kwargs):
    handler.send_response(204)
    handler._cors()
    handler.end_headers()


def _json(handler: BaseHTTPRequestHandler, body: dict, status: int = 200) -> None:
    handler.send_response(status)
    handler._cors()
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.end_headers()
    handler.wfile.write(json.dumps(body, ensure_ascii=False).encode())


def _read_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    return json.loads(handler.rfile.read(length)) if length else {}


# ---------------------------------------------------------------------------
# Session helper
# ---------------------------------------------------------------------------
def _get_session_email(handler: Handler) -> str | None:
    """Extract the user email from the session cookie."""
    cookie_header = handler.headers.get("Cookie", "")
    if not cookie_header:
        return None
    c = SimpleCookie()
    c.load(cookie_header)
    sid = c.get(SESSION_COOKIE)
    if not sid:
        return None
    session = _db_get_session(str(sid.value))
    if not session:
        return None
    return str(session.get("email", ""))


def _db_get_session(sid: str) -> dict | None:
    import db
    return db.get_session(sid)


def _set_session_cookie(handler: Handler, session_id: str) -> None:
    handler.send_header(
        "Set-Cookie",
        f"{SESSION_COOKIE}={session_id}; Path=/assistant/; Max-Age={SESSION_MAX_AGE}; HttpOnly; SameSite=Lax; Secure",
    )


def _clear_session_cookie(handler: Handler) -> None:
    handler.send_header(
        "Set-Cookie",
        f"{SESSION_COOKIE}=; Path=/assistant/; Max-Age=0; HttpOnly; SameSite=Lax; Secure",
    )


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Credentials", "true")

    def log_message(self, format, *args):
        sys.stderr.write(f"[assistant] {args[0]}\n")

    def _route(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/assistant":
            path = "/"
        elif path.startswith("/assistant/"):
            path = path[len("/assistant"):]
        method = self.command

        if path in ROUTES and method in ROUTES[path]:
            return ROUTES[path][method](self)

        if path in ROUTES and "ANY" in ROUTES[path]:
            return ROUTES[path]["ANY"](self)

        if path.startswith("/static/") or path == "/" or path.endswith(".html") or path.endswith(".js") or path.endswith(".css"):
            return self._serve_static(path)

        _json(self, {"error": "not_found", "path": path}, 404)

    def do_GET(self) -> None:
        self._route()

    def do_POST(self) -> None:
        self._route()

    def do_PATCH(self) -> None:
        self._route()

    def do_PUT(self) -> None:
        self._route()

    def do_DELETE(self) -> None:
        self._route()

    def do_OPTIONS(self) -> None:
        self._route()

    def _serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        if not path.startswith("/static/"):
            file_path = PUBLIC_DIR / path.lstrip("/")
        else:
            file_path = PUBLIC_DIR / path[len("/static/"):]

        mime_map = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
        }
        mime = mime_map.get(file_path.suffix.lower(), "application/octet-stream")

        if not file_path.is_file():
            _json(self, {"error": "not_found"}, 404)
            return
        content = file_path.read_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def _query(self) -> dict:
        parsed = urlparse(self.path)
        qs: dict[str, list[str]] = parse_qs(parsed.query)
        return {k: v[0] if len(v) == 1 else v for k, v in qs.items()}

    def _json_ok(self, body: dict, status: int = 200) -> None:
        _json(self, body, status)

    def _json_err(self, msg: str, status: int = 400) -> None:
        _json(self, {"error": msg}, status)


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------
@route("/api/auth/status")
def api_auth_status(handler: Handler) -> None:
    import db
    email = _get_session_email(handler)
    app = _app_configured()
    if not email:
        handler._json_ok({"logged_in": False, "email": None, "app_configured": app})
        return
    user = db.get_user(email)
    handler._json_ok({
        "logged_in": True,
        "email": email,
        "name": user.get("name", "") if user else "",
        "picture": user.get("picture", "") if user else "",
        "app_configured": app,
    })


def _app_configured() -> bool:
    import db
    return bool(db.get_setting("GOOGLE_CLIENT_ID", "") and db.get_setting("GOOGLE_CLIENT_SECRET", ""))


@route("/api/auth/logout", methods=["POST"])
def api_auth_logout(handler: Handler) -> None:
    import db
    cookie_header = handler.headers.get("Cookie", "")
    if cookie_header:
        c = SimpleCookie()
        c.load(cookie_header)
        sid = c.get(SESSION_COOKIE)
        if sid:
            db.delete_session(str(sid.value))
    _clear_session_cookie(handler)
    handler._json_ok({"ok": True})


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------
@route("/api/status")
def api_status(handler: Handler) -> None:
    import db, ai_client, google_client as gc, telegram_bot as tg
    email = _get_session_email(handler)
    app = _app_configured()
    handler._json_ok({
        "status": "ok",
        "logged_in": bool(email),
        "email": email,
        "app_configured": app,
        "config": {
            "google": app,
            "google_connected": gc.get_valid_token(email) is not None,
            "ai": ai_client.configured(email) if email else False,
            "telegram": tg.configured(email) if email else False,
        },
        "pending_count": len(db.list_pending_actions(user_email=email)) if email else 0,
    })


# ---------------------------------------------------------------------------
# Settings (system-level: OAuth app credentials)
# ---------------------------------------------------------------------------
@route("/api/settings", methods=["GET"])
def api_get_settings(handler: Handler) -> None:
    import db
    from db import mask_secret
    email = _get_session_email(handler)

    APP_KEYS = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"]
    APP_SECRETS = {"GOOGLE_CLIENT_SECRET"}

    USER_KEYS = ["AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID"]
    USER_SECRETS = {"AI_API_KEY", "TELEGRAM_BOT_TOKEN"}

    db.init_db()
    app_raw = db.get_settings(APP_KEYS)
    out: list[dict] = []
    for k in APP_KEYS:
        v = app_raw.get(k, "")
        is_sec = k in APP_SECRETS
        out.append({"key": k, "value": "" if is_sec else v, "masked": mask_secret(v) if is_sec and v else "", "is_secret": is_sec, "configured": bool(v), "scope": "app"})

    if email:
        user_raw = db.get_user_settings(email, USER_KEYS)
        for k in USER_KEYS:
            v = user_raw.get(k, "")
            is_sec = k in USER_SECRETS
            out.append({"key": k, "value": "" if is_sec else v, "masked": mask_secret(v) if is_sec and v else "", "is_secret": is_sec, "configured": bool(v), "scope": "user"})

    handler._json_ok({"settings": out})


@route("/api/settings", methods=["POST"])
def api_save_settings(handler: Handler) -> None:
    import db
    body = _read_body(handler) or {}
    email = _get_session_email(handler)

    APP_KEYS = {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET"}
    APP_SECRETS = {"GOOGLE_CLIENT_SECRET"}
    USER_KEYS = {"AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID"}
    USER_SECRETS = {"AI_API_KEY", "TELEGRAM_BOT_TOKEN"}

    app_vals: dict[str, str] = {}
    user_vals: dict[str, str] = {}

    for k, v in body.items():
        k = str(k).upper()
        if not isinstance(v, str):
            continue
        if k in APP_KEYS:
            app_vals[k] = v
        elif k in USER_KEYS and email:
            user_vals[k] = v

    if app_vals:
        db.set_settings(app_vals, secret_keys=APP_SECRETS & set(app_vals))
    if user_vals:
        db.set_user_settings(email, user_vals, secret_keys=USER_SECRETS & set(user_vals))

    import telegram_bot as tg
    tg.start()
    handler._json_ok({"ok": True})


@route("/api/settings/secret", methods=["POST"])
def api_reveal_secret(handler: Handler) -> None:
    import db
    body = _read_body(handler) or {}
    key = str(body.get("key", "")).upper()
    email = _get_session_email(handler)
    ALLOWED = {"GOOGLE_CLIENT_SECRET", "AI_API_KEY", "TELEGRAM_BOT_TOKEN"}
    if key not in ALLOWED:
        handler._json_err("Nicht erlaubt", 403)
        return

    if key == "GOOGLE_CLIENT_SECRET":
        value = db.get_setting(key, "")
    else:
        value = db.get_user_setting(email, key, "") if email else ""
    handler._json_ok({"key": key, "value": value})


@route("/api/settings/oauth-info")
def api_oauth_info(handler: Handler) -> None:
    """Return OAuth setup instructions for the modal."""
    import google_client as gc
    handler._json_ok({
        "redirect_uri": gc.OAUTH_CALLBACK,
        "scopes": gc.SCOPES,
        "auth_url": gc.OAUTH_CALLBACK,
        "instructions": [
            "Öffne die Google Cloud Console: https://console.cloud.google.com/apis/credentials",
            "Erstelle ein OAuth 2.0-Client-ID für eine Webanwendung.",
            f"Trage als autorisierte Weiterleitungs-URI ein: {gc.OAUTH_CALLBACK}",
            "Kopiere Client-ID und Client-Secret hier in die Felder.",
        ],
    })


# ---------------------------------------------------------------------------
# Google OAuth
# ---------------------------------------------------------------------------
@route("/api/google/auth-url")
def api_google_auth_url(handler: Handler) -> None:
    import google_client as gc
    if not gc.app_configured():
        handler._json_err("GOOGLE_CLIENT_ID nicht konfiguriert", 400)
        return
    url = gc.get_oauth_url()
    if not url:
        handler._json_err("Fehler beim Erstellen der Auth-URL", 500)
        return
    handler._json_ok({"url": url})


@route("/oauth/callback")
@route("/api/google/callback")
def api_google_callback(handler: Handler) -> None:
    import google_client as gc
    import db
    code = handler._query().get("code", "")
    if not code:
        handler.send_response(302)
        handler.send_header("Location", "/assistant/")
        handler.end_headers()
        return

    token = gc.exchange_code(str(code))
    if "error" in token or "access_token" not in token:
        handler.send_response(302)
        handler.send_header("Location", "/assistant/?google_error=1")
        handler.end_headers()
        return

    access_token = str(token["access_token"])
    user_info = gc.get_user_info(access_token)
    if "error" in user_info or "email" not in user_info:
        handler.send_response(302)
        handler.send_header("Location", "/assistant/?google_error=1")
        handler.end_headers()
        return

    email = str(user_info.get("email", "")).strip().lower()
    sub = str(user_info.get("id", "") or user_info.get("sub", ""))
    name = str(user_info.get("name", "") or user_info.get("given_name", ""))
    picture = str(user_info.get("picture", ""))

    token["expires_in"] = token.get("expires_in", 3600)
    token["expires_at"] = __import__("time").time() + int(token["expires_in"])
    token_json = json.dumps(token, ensure_ascii=False)

    db.upsert_user(email, google_sub=sub, name=name, picture=picture, token_json=token_json)
    session_id = db.create_session(email)

    handler.send_response(302)
    _set_session_cookie(handler, session_id)
    handler.send_header("Location", "/assistant/?google_ok=1")
    handler.end_headers()


# ---------------------------------------------------------------------------
# AI Chat
# ---------------------------------------------------------------------------
@route("/api/ai/chat", methods=["POST"])
def api_ai_chat(handler: Handler) -> None:
    import ai_client, db, safe_mode
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return

    body = _read_body(handler) or {}
    text = str(body.get("text", "")).strip()
    if not text:
        handler._json_err("text fehlt", 400)
        return

    db.add_chat_message("web", "user", text, user_email=email)

    context = _build_context_web(email)
    history = db.recent_chat_messages("web", limit=20, user_email=email)
    history_mapped = [{"role": h["role"], "content": h["content"]} for h in history[:-1]]

    try:
        actions = ai_client.propose_actions(text, context, user_email=email)
        if actions:
            created_ids: list[str] = []
            for act in actions:
                a = safe_mode.create(act["type"], act["title"], act["payload"], source="web", user_email=email)
                created_ids.append(a["id"])
            reply = (
                f"Ich habe {len(created_ids)} Vorschläge zur Freigabe erstellt:\n\n"
                + "\n".join(f"🛡️ **{act['title']}** → `{cid}`" for act, cid in zip(actions, created_ids))
                + "\n\nBitte im Tab Freigaben prüfen und freigeben."
            )
            db.add_chat_message("web", "assistant", reply, user_email=email)
        else:
            reply = ai_client.assistant_reply(text, context=context, history=history_mapped, user_email=email)
            db.add_chat_message("web", "assistant", reply, user_email=email)
        handler._json_ok({"reply": reply, "actions_count": len(actions)})
    except Exception as exc:
        handler._json_ok({"reply": f"Fehler: {exc}", "actions_count": 0})
        db.add_chat_message("web", "assistant", f"Fehler: {exc}", user_email=email)


@route("/api/ai/test", methods=["POST"])
def api_ai_test(handler: Handler) -> None:
    import ai_client
    email = _get_session_email(handler)
    handler._json_ok(ai_client.test_connection(user_email=email))


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------
@route("/api/calendar/events")
def api_calendar_events(handler: Handler) -> None:
    import google_client as gc
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    q = handler._query()
    handler._json_ok(gc.list_events(
        time_min=str(q.get("timeMin", "")),
        time_max=str(q.get("timeMax", "")),
        email=email,
    ))


@route("/api/calendar/calendars")
def api_calendar_list(handler: Handler) -> None:
    import google_client as gc
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    handler._json_ok(gc.list_calendars(email=email))


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------
@route("/api/tasks/lists")
def api_tasklists(handler: Handler) -> None:
    import google_client as gc
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    handler._json_ok(gc.list_tasklists(email=email))


@route("/api/tasks")
def api_tasks(handler: Handler) -> None:
    import google_client as gc
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    tl = str(handler._query().get("tasklist", "")) or gc.get_tasklist_id(email=email) or "@default"
    handler._json_ok(gc.list_tasks(tl, email=email))


# ---------------------------------------------------------------------------
# Safe Mode
# ---------------------------------------------------------------------------
@route("/api/safe-mode/pending")
def api_safe_mode_pending(handler: Handler) -> None:
    import safe_mode
    email = _get_session_email(handler)
    if not email:
        handler._json_ok({"actions": []})
        return
    handler._json_ok({"actions": safe_mode.list_pending(user_email=email)})


@route("/api/safe-mode/approve", methods=["POST"])
def api_safe_mode_approve(handler: Handler) -> None:
    import safe_mode
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    body = _read_body(handler) or {}
    action_id = str(body.get("id", ""))
    if not action_id:
        handler._json_err("id fehlt", 400)
        return
    handler._json_ok(safe_mode.approve(action_id, user_email=email))


@route("/api/safe-mode/reject", methods=["POST"])
def api_safe_mode_reject(handler: Handler) -> None:
    import safe_mode
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    body = _read_body(handler) or {}
    action_id = str(body.get("id", ""))
    if not action_id:
        handler._json_err("id fehlt", 400)
        return
    handler._json_ok(safe_mode.reject(action_id, user_email=email))


# ---------------------------------------------------------------------------
# Telegram / Google test
# ---------------------------------------------------------------------------
@route("/api/telegram/test", methods=["POST"])
def api_telegram_test(handler: Handler) -> None:
    import telegram_bot as tg
    email = _get_session_email(handler)
    handler._json_ok(tg.test_connection(user_email=email))


@route("/api/google/test", methods=["POST"])
def api_google_test(handler: Handler) -> None:
    import google_client as gc
    email = _get_session_email(handler)
    handler._json_ok(gc.test_connection(email=email))


# ---------------------------------------------------------------------------
# Chat Messages
# ---------------------------------------------------------------------------
@route("/api/chat/messages")
def api_chat_messages(handler: Handler) -> None:
    import db
    email = _get_session_email(handler)
    channel = str(handler._query().get("channel", "web"))
    handler._json_ok({"messages": db.recent_chat_messages(channel, limit=50, user_email=email)})


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------
def _build_context_web(email: str) -> str:
    try:
        import google_client as gc
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo

        berlin = ZoneInfo("Europe/Berlin")
        now = datetime.now(berlin)
        time_min = now.isoformat()
        time_max = (now + timedelta(days=14)).isoformat()

        events = gc.list_events(time_min=time_min, time_max=time_max, email=email)
        tasks = gc.list_tasks(gc.get_tasklist_id(email=email) or "@default", email=email)

        parts = ["== Kalender (kommende 14 Tage) =="]
        for ev in events.get("items", [])[:20]:
            start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "?"))
            end = ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", "?"))
            parts.append(f"- {ev.get('summary','')}: {start} → {end} (ID: {ev.get('id','')})")

        parts.append("== Todos ==")
        for t in tasks.get("items", [])[:30]:
            status = t.get("status", "needsAction")
            parts.append(f"- [{status}] {t.get('title','')} (ID: {t.get('id','')})")

        return "\n".join(parts)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    import db
    db.init_db()
    db.cleanup_sessions()
    import telegram_bot as tg
    tg.start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"[assistant] Listening on 127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[assistant] Stopping.")
        server.shutdown()


if __name__ == "__main__":
    main()
