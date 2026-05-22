#!/usr/bin/env python3
"""Assistant backend – Google Account = identity, session-cookie auth."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
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

        # Regex routes (keys starting with ^)
        import re
        for route_path, methods in ROUTES.items():
            if route_path.startswith("^"):
                m = re.match(route_path[1:], path)
                if m and method in methods:
                    return methods[method](self, *m.groups())

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

    USER_KEYS = ["AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_THINK_EFFORT", "AI_CONTEXT_MAX_TOKENS", "timezone", "location", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID"]
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
    USER_KEYS = {"AI_BASE_URL", "AI_API_KEY", "AI_MODEL", "AI_THINK_EFFORT", "AI_CONTEXT_MAX_TOKENS", "timezone", "location", "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID"}
    USER_SECRETS = {"AI_API_KEY", "TELEGRAM_BOT_TOKEN"}

    app_vals: dict[str, str] = {}
    user_vals: dict[str, str] = {}

    for k, v in body.items():
        k = str(k)
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
# Multipart upload helper
# ---------------------------------------------------------------------------
import re as _re

def _parse_multipart(handler: Handler) -> dict[str, Any]:
    content_type = handler.headers.get("Content-Type", "")
    if "boundary=" not in content_type:
        return {}
    boundary = content_type.split("boundary=")[1].split(";")[0].strip('"')
    length = int(handler.headers.get("Content-Length", 0))
    raw = handler.rfile.read(length)
    parts = raw.split(b"--" + boundary.encode())
    files: dict[str, Any] = {}
    for part in parts:
        if not part or part.strip() == b"--" or part.strip() == b"":
            continue
        header_end = part.find(b"\r\n\r\n")
        sep = 4
        if header_end == -1:
            header_end = part.find(b"\n\n")
            sep = 2
        headers = part[:header_end].decode("utf-8", errors="ignore")
        body = part[header_end + sep:]
        if body.endswith(b"\r\n"):
            body = body[:-2]
        elif body.endswith(b"\n"):
            body = body[:-1]
        if body.endswith(b"--"):
            body = body[:-2]
        if body.endswith(b"\r\n"):
            body = body[:-2]
        elif body.endswith(b"\n"):
            body = body[:-1]
        name_match = _re.search(r'name="([^"]+)"', headers)
        filename_match = _re.search(r'filename="([^"]*)"', headers)
        if name_match:
            name = name_match.group(1)
            if filename_match:
                files[name] = {
                    "filename": filename_match.group(1),
                    "data": body,
                    "headers": headers,
                }
            else:
                files[name] = {"value": body.decode("utf-8", errors="ignore")}
    return files


def _looks_like_text(data: bytes) -> bool:
    """Best-effort detection for text files with arbitrary extensions."""
    if not data:
        return True
    sample = data[:8192]
    if b"\x00" in sample:
        return False
    try:
        decoded = sample.decode("utf-8")
    except UnicodeDecodeError:
        try:
            decoded = sample.decode("latin-1")
        except Exception:
            return False
    if not decoded:
        return True
    printable = sum(1 for ch in decoded if ch.isprintable() or ch in "\r\n\t")
    return (printable / max(len(decoded), 1)) > 0.85


def _detect_mime(filename: str, data: bytes) -> str:
    """Detect supported upload MIME types by extension, magic bytes and text fallback."""
    ext = Path(filename).suffix.lower()
    MIME_MAP = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
        ".bmp": "image/bmp", ".tiff": "image/tiff", ".tif": "image/tiff", ".ico": "image/x-icon",
        ".txt": "text/plain", ".md": "text/markdown", ".csv": "text/csv",
        ".json": "application/json", ".yaml": "text/yaml", ".yml": "text/yaml",
        ".xml": "text/xml", ".html": "text/html", ".htm": "text/html",
        ".css": "text/css", ".js": "text/javascript", ".ts": "text/plain",
        ".jsx": "text/plain", ".tsx": "text/plain", ".py": "text/x-python",
        ".sh": "text/x-shellscript", ".sql": "text/plain", ".log": "text/plain",
        ".c": "text/plain", ".cpp": "text/plain", ".h": "text/plain",
        ".java": "text/plain", ".go": "text/plain", ".rs": "text/plain",
        ".php": "text/plain", ".swift": "text/plain", ".kt": "text/plain",
        ".ini": "text/plain", ".cfg": "text/plain", ".toml": "text/plain",
        ".properties": "text/plain", ".env": "text/plain",
        ".pdf": "application/pdf",
        ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".oga": "audio/ogg",
        ".m4a": "audio/mp4", ".mp4a": "audio/mp4", ".flac": "audio/flac", ".aac": "audio/aac",
        ".wma": "audio/x-ms-wma", ".opus": "audio/opus", ".weba": "audio/webm",
    }
    if ext in MIME_MAP:
        return MIME_MAP[ext]
    # Magic-byte fallback for renamed files
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"BM"):
        return "image/bmp"
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return "image/tiff"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return "audio/wav"
    if data.startswith(b"OggS"):
        return "audio/ogg"
    if data.startswith(b"fLaC"):
        return "audio/flac"
    if data.startswith(b"ID3") or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if len(data) > 12 and data[4:8] == b"ftyp":
        return "audio/mp4"
    if _looks_like_text(data):
        return "text/plain"
    return "application/octet-stream"


# ---------------------------------------------------------------------------
# Uploads
# ---------------------------------------------------------------------------
@route("/api/upload", methods=["POST"])
def api_upload(handler: Handler) -> None:
    import db
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    files = _parse_multipart(handler)
    file_info = files.get("file")
    if not file_info or "data" not in file_info:
        handler._json_err("file fehlt", 400)
        return
    data = file_info["data"]
    filename = file_info["filename"] or "upload"
    mime = _detect_mime(filename, data)
    upload = db.create_upload(email, filename, mime, len(data), data)
    handler._json_ok({
        "id": upload["id"],
        "filename": upload["filename"],
        "mime_type": upload["mime_type"],
        "size": upload["size"],
        "url": f"/api/upload/{upload['id']}",
    })


@route("^/api/upload/([a-zA-Z0-9_-]+)$")
def api_upload_get(handler: Handler, upload_id: str = "") -> None:
    import db
    email = _get_session_email(handler)
    upload = db.get_upload(upload_id, user_email=email)
    if not upload:
        handler._json_err("Nicht gefunden", 404)
        return
    file_path = BASE_DIR / upload["path"]
    if not file_path.is_file():
        handler._json_err("Datei nicht gefunden", 404)
        return
    data = file_path.read_bytes()
    mime = upload.get("mime_type", "application/octet-stream")
    handler.send_response(200)
    handler._cors()
    handler.send_header("Content-Type", mime)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Content-Disposition", f'inline; filename="{upload["filename"]}"')
    handler.end_headers()
    handler.wfile.write(data)


# ---------------------------------------------------------------------------
# Chat commands
# ---------------------------------------------------------------------------
_CHAT_CLEAR_COMMANDS = {
    "/clear", "/reset", "/clear-chat", "/clear-context",
    "/loeschen", "/löschen", "/chat-loeschen", "/chat-löschen",
    "/verlauf-loeschen", "/verlauf-löschen", "/kontext-loeschen", "/kontext-löschen",
}

_CHAT_HELP_COMMANDS = {"/help", "/hilfe", "/commands", "/befehle"}

_CHAT_HELP_TEXT = """📋 Verfügbare Chat-Befehle:

• /help, /hilfe, /commands, /befehle – diese Übersicht
• /clear, /reset, /löschen, /chat-löschen, /verlauf-löschen, /kontext-löschen – kompletten Chat-Verlauf und Kontext löschen

Alle Befehle funktionieren sowohl im WebChat als auch via Telegram."""

def _is_clear_chat_command(text: str) -> bool:
    t = text.strip().lower()
    return t in _CHAT_CLEAR_COMMANDS

def _is_help_command(text: str) -> bool:
    t = text.strip().lower()
    return t in _CHAT_HELP_COMMANDS

def _chat_help_text() -> str:
    return _CHAT_HELP_TEXT


# ---------------------------------------------------------------------------
# AI Chat
# ---------------------------------------------------------------------------
@route("/api/ai/chat", methods=["POST"])
def api_ai_chat(handler: Handler) -> None:
    import ai_client, db, safe_mode, json as _json
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return

    body = _read_body(handler) or {}
    text = str(body.get("text", "")).strip()

    # Chat clear command – löscht Verlauf + Kontext sofort (ohne KI)
    if _is_clear_chat_command(text):
        try:
            db.clear_chat_history("web", user_email=email)
            handler._json_ok({"status": "cleared", "messages": db.recent_chat_messages("web", limit=50, user_email=email)})
        except Exception as exc:
            handler._json_err(str(exc), 500)
        return

    # Help command – zeigt alle Befehle an
    if _is_help_command(text):
        help_text = _chat_help_text()
        db.add_chat_message("web", "user", text, user_email=email)
        db.add_chat_message("web", "assistant", help_text, user_email=email)
        handler._json_ok({"status": "help", "messages": db.recent_chat_messages("web", limit=50, user_email=email)})
        return

    if not text:
        handler._json_err("text fehlt", 400)
        return

    attachments_raw = body.get("attachments", [])
    attachments_ids = [str(a) for a in attachments_raw if a] if attachments_raw else []
    attachments_json = _json.dumps(attachments_ids)

    db.add_chat_message("web", "user", text, user_email=email, attachments_json=attachments_json)
    db.set_chat_processing("web", True, user_email=email)

    def _process():
        try:
            _do_chat_process(email, text, attachments_ids)
        except Exception:
            pass
        finally:
            db.set_chat_processing("web", False, user_email=email)

    threading.Thread(target=_process, daemon=True).start()
    handler._json_ok({"status": "processing"})


def _do_chat_process(email: str, text: str, attachment_ids: list[str]) -> None:
    import ai_client, db, safe_mode

    sys.stderr.write(f"[assistant-chat] process start email={email} text_len={len(text)} attachments={len(attachment_ids)}\n")

    # Resolve attachments
    attachments: list[dict[str, Any]] = []
    for up_id in attachment_ids:
        up = db.get_upload(up_id, user_email=email)
        if up:
            attachments.append(dict(up))

    auto_compacted = False
    try:
        auto_compacted = _auto_compact_if_needed(email)
        if auto_compacted:
            sys.stderr.write(f"[assistant-chat] auto compacted\n")
    except Exception as exc:
        sys.stderr.write(f"[assistant-chat] auto compact failed: {exc}\n")
        auto_compacted = False

    compact_summary = str(db.get_chat_context("web", user_email=email).get("summary", ""))
    live_context = _build_context_web(email)
    context_parts: list[str] = []
    if compact_summary:
        context_parts.append("== Kompakter Chat-Kontext ==\n" + compact_summary)
    if live_context:
        context_parts.append(live_context)
    context = "\n\n".join(context_parts)
    history_limit = 10 if compact_summary else 20
    history = db.recent_chat_messages("web", limit=history_limit, user_email=email)
    for h in history:
        aids = h.get("attachments")
        if isinstance(aids, list):
            h["attachments"] = [dict(up) for aid in aids if isinstance(aid, str) and (up := db.get_upload(aid, user_email=email))]
    history_mapped = [
        {"role": h["role"], "content": h["content"], "attachments": h["attachments"]}
        for h in history[:-1]
        if not str(h.get("content", "")).startswith("🧠 Kompakter Kontext:")
    ]

    try:
        raw_actions = ai_client.propose_actions(text, context, user_email=email)
        sys.stderr.write(f"[assistant-chat] propose_actions returned {len(raw_actions)} actions: {[a.get('type') + ':' + a.get('title','') for a in raw_actions]}\n")
        if raw_actions:
            created_ids: list[str] = []
            for act in raw_actions:
                try:
                    a = safe_mode.create(act["type"], act["title"], act["payload"], source="web", user_email=email)
                    created_ids.append(a["id"])
                    sys.stderr.write(f"[assistant-chat]   created action {a['id']}\n")
                except Exception as exc:
                    sys.stderr.write(f"[assistant-chat]   FAILED {act['type']}: {exc}\n")
            if created_ids:
                reply = (
                    f"Ich habe {len(created_ids)} Vorschläge zur Freigabe erstellt:\n\n"
                    + "\n".join(
                        f"🛡️ **{act['title']}** → `{cid}`"
                        for act, cid in zip(raw_actions, created_ids)
                    )
                    + "\n\nBitte im Tab Freigaben prüfen und freigeben."
                )
            else:
                reply = ai_client.assistant_reply(text, context=context, history=history_mapped, attachments=attachments, user_email=email)
                if "Safe Mode" in reply or "Freigabe" in reply:
                    reply += "\n\n(Hinweis: Es wurden keine Safe-Mode-Aktionen erstellt – bitte formuliere den Auftrag konkreter.)"
            db.add_chat_message("web", "assistant", reply, user_email=email)
        else:
            reply = ai_client.assistant_reply(text, context=context, history=history_mapped, attachments=attachments, user_email=email)
            db.add_chat_message("web", "assistant", reply, user_email=email)
    except Exception as exc:
        sys.stderr.write(f"[assistant-chat] ERROR: {exc}\n")
        err_msg = f"Fehler: {exc}"
        if "429" in str(exc) or "Rate-Limit" in str(exc) or "rate" in str(exc).lower():
            err_msg = "⚠️ Rate-Limit – die KI antwortet zu langsam. Bitte kurz warten und erneut versuchen."
        db.add_chat_message("web", "assistant", err_msg, user_email=email)

    sys.stderr.write(f"[assistant-chat] process done email={email}\n")


@route("/api/chat/pending")
def api_chat_pending(handler: Handler) -> None:
    import db
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    handler._json_ok({"pending": db.get_chat_processing("web", user_email=email)})


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
    tl = str(handler._query().get("tasklist", ""))
    show_completed = handler._query().get("show_completed", "") == "1"
    if tl:
        handler._json_ok(gc.list_tasks(tl, show_completed=show_completed, email=email))
    else:
        handler._json_ok({"items": gc.list_all_tasks(show_completed=show_completed, email=email)})


@route("/api/tasks/export")
def api_tasks_export(handler: Handler) -> None:
    import google_client as gc
    from datetime import datetime, timezone
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    lists_data = gc.list_tasklists(email=email)
    show_completed = handler._query().get("show_completed", "") == "1"
    filter_tl = str(handler._query().get("tasklist", ""))
    export = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "email": email,
        "tasklists": []
    }
    for tl in lists_data.get("items", []):
        tl_id = str(tl.get("id", ""))
        tl_title = str(tl.get("title", tl_id))
        if not tl_id:
            continue
        if filter_tl and tl_id != filter_tl:
            continue
        tasks = gc.list_tasks(tl_id, max_results=500, show_completed=show_completed, email=email)
        export["tasklists"].append({
            "title": tl_title,
            "id": tl_id,
            "tasks": tasks.get("items", [])
        })
    body = json.dumps(export, ensure_ascii=False, indent=2)
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Disposition", "attachment; filename=tasks-export.json")
    handler.send_header("Content-Length", str(len(body.encode())))
    handler.end_headers()
    handler.wfile.write(body.encode())


@route("/api/tasks/import", methods=["POST"])
def api_tasks_import(handler: Handler) -> None:
    import google_client as gc
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    body = _read_body(handler)
    if not body:
        handler._json_err("Keine Daten", 400)
        return
    try:
        data = json.loads(body) if isinstance(body, str) else body
    except json.JSONDecodeError:
        handler._json_err("Ungültiges JSON", 400)
        return
    if not isinstance(data, dict) or "tasklists" not in data:
        handler._json_err('JSON muss {"tasklists": [...]} enthalten', 400)
        return
    # Build existing-list index by title
    existing = gc.list_tasklists(email=email)
    list_by_title: dict[str, str] = {}
    list_by_id: dict[str, str] = {}
    for tl in existing.get("items", []):
        list_by_title[str(tl.get("title", "")).lower()] = str(tl.get("id", ""))
        list_by_id[str(tl.get("id", ""))] = str(tl.get("id", ""))
    created = 0
    errors = 0
    for tl_data in data.get("tasklists", []):
        tl_title = str(tl_data.get("title", ""))
        tl_id = tl_data.get("id", "")
        # Find or create list
        actual_id = list_by_id.get(str(tl_id)) or list_by_title.get(tl_title.lower())
        if not actual_id:
            if tl_title:
                new_list = gc.create_tasklist(tl_title, email=email)
                actual_id = new_list.get("id", "")
                if actual_id:
                    list_by_title[tl_title.lower()] = actual_id
            if not actual_id:
                # fallback to first list
                actual_id = gc.get_tasklist_id(email=email)
        if not actual_id:
            errors += len(tl_data.get("tasks", []))
            continue
        for task in tl_data.get("tasks", []):
            try:
                payload = {
                    "title": str(task.get("title", "Unbenannt")),
                }
                if task.get("notes"):
                    payload["notes"] = str(task["notes"])
                if task.get("due"):
                    payload["due"] = str(task["due"])
                if task.get("status") == "completed":
                    payload["status"] = "completed"
                gc.create_task(actual_id, payload, email=email)
                created += 1
            except Exception:
                errors += 1
    handler._json_ok({"created": created, "errors": errors})


@route("/api/backups")
def api_backups(handler: Handler) -> None:
    import backup
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    area = str(handler._query().get("area", "tasks"))
    if area not in {"tasks", "calendar"}:
        handler._json_err("Ungültiger Bereich", 400)
        return
    handler._json_ok({"backups": backup.list_backups(area, email)})


@route("/api/backups/apply", methods=["POST"])
def api_backups_apply(handler: Handler) -> None:
    import backup
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    body = _read_body(handler) or {}
    area = str(body.get("area", ""))
    backup_id = str(body.get("id", ""))
    if area not in {"tasks", "calendar"} or not backup_id:
        handler._json_err("area/id fehlt", 400)
        return
    handler._json_ok(backup.apply_backup(area, backup_id, email))


@route("/api/tasks/export/pdf")
def api_tasks_export_pdf(handler: Handler) -> None:
    import google_client as gc
    from fpdf import FPDF
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return

    def _safe(s: str) -> str:
        return s.encode("latin-1", errors="replace").decode("latin-1")

    show_completed = handler._query().get("show_completed", "") == "1"
    filter_tl = str(handler._query().get("tasklist", ""))
    lists_data = gc.list_tasklists(email=email)
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    # Title
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Google Tasks Export", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "I", 9)
    from datetime import datetime, timezone
    pdf.cell(0, 6, datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M UTC"), new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(4)
    for tl in lists_data.get("items", []):
        tl_id = str(tl.get("id", ""))
        if filter_tl and tl_id != filter_tl:
            continue
        tl_title = _safe(str(tl.get("title", "")))
        tasks = gc.list_tasks(str(tl.get("id", "")), max_results=500, show_completed=show_completed, email=email)
        items = tasks.get("items", [])
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_fill_color(230, 235, 250)
        count_open = sum(1 for t in items if t.get("status") != "completed")
        pdf.cell(0, 9, f"{tl_title}  ({count_open} offen)", new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 10)
        for t in items:
            done = t.get("status") == "completed"
            checkbox = "[x]" if done else "[ ]"
            title = _safe(str(t.get("title", "")))
            notes = _safe(str(t.get("notes", "")))
            due = str(t.get("due", ""))
            pdf.set_font("Helvetica", "B" if done else "", 10)
            line = f"{checkbox} {title}"
            if due:
                try:
                    from datetime import datetime as dt
                    d = dt.fromisoformat(due.replace("Z", ""))
                    line += f"  (fällig: {d.strftime('%d.%m.%Y')})"
                except Exception:
                    line += f"  ({due})"
            pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
            if notes:
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_text_color(100, 100, 100)
                pdf.cell(0, 5, f"     {notes}", new_x="LMARGIN", new_y="NEXT")
                pdf.set_text_color(0, 0, 0)
        pdf.ln(5)
    pdf_bytes = pdf.output()
    handler.send_response(200)
    handler.send_header("Content-Type", "application/pdf")
    handler.send_header("Content-Disposition", "attachment; filename=tasks-export.pdf")
    handler.send_header("Content-Length", str(len(pdf_bytes)))
    handler.end_headers()
    handler.wfile.write(pdf_bytes)


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


@route("/api/safe-mode/approve-all", methods=["POST"])
def api_safe_mode_approve_all(handler: Handler) -> None:
    import safe_mode
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    handler._json_ok(safe_mode.approve_all(user_email=email))


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


@route("/api/safe-mode/edit", methods=["POST"])
def api_safe_mode_edit(handler: Handler) -> None:
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
    title = body.get("title")
    payload = body.get("payload")
    action_type = body.get("type")
    handler._json_ok(safe_mode.edit(
        action_id,
        title=str(title) if title is not None else None,
        payload=dict(payload) if isinstance(payload, dict) else None,
        action_type=str(action_type) if action_type is not None else None,
        user_email=email,
    ))


@route("/api/safe-mode/delete", methods=["POST"])
def api_safe_mode_delete(handler: Handler) -> None:
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
    handler._json_ok(safe_mode.delete(action_id, user_email=email))


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


@route("/api/chat/context/status")
def api_chat_context_status(handler: Handler) -> None:
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    handler._json_ok(_chat_context_status(email))


@route("/api/chat/context/compact", methods=["POST"])
def api_chat_context_compact(handler: Handler) -> None:
    import db, ai_client
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    try:
        ctx = db.get_chat_context("web", user_email=email)
        messages = db.recent_chat_messages("web", limit=80, user_email=email)
        if not messages:
            handler._json_ok({"ok": True, "summary": ctx.get("summary", ""), **_chat_context_status(email)})
            return
        summary = ai_client.compact_context(str(ctx.get("summary", "")), [{"role": m["role"], "content": m["content"]} for m in messages], user_email=email)
        db.replace_chat_with_compact_summary("web", summary, user_email=email)
        handler._json_ok({"ok": True, "summary": summary, "messages": db.recent_chat_messages("web", limit=50, user_email=email), **_chat_context_status(email)})
    except Exception as exc:
        handler._json_err(str(exc), 500)


@route("/api/chat/clear", methods=["POST"])
def api_chat_clear(handler: Handler) -> None:
    import db
    email = _get_session_email(handler)
    if not email:
        handler._json_err("Nicht eingeloggt", 401)
        return
    channel = str(handler._query().get("channel", "web"))
    try:
        db.clear_chat_history(channel, user_email=email)
        msgs = db.recent_chat_messages(channel, limit=50, user_email=email)
        handler._json_ok({"ok": True, "messages": msgs, **_chat_context_status(email)})
    except Exception as exc:
        handler._json_err(str(exc), 500)


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------
def _build_context_web(email: str) -> str:
    try:
        import context_builder
        return context_builder.build_google_context(email)
    except Exception:
        return ""


def _chat_context_status(email: str) -> dict[str, Any]:
    import ai_client, db
    info = ai_client.context_info(email)
    summary = db.get_chat_context("web", user_email=email)
    messages = db.recent_chat_messages("web", limit=50, user_email=email)
    chat_text = "\n".join(f"{m['role']}: {m['content']}" for m in messages)
    summary_text = str(summary.get("summary", ""))
    google_context = _build_context_web(email)
    used_tokens = ai_client.estimate_tokens(SYSTEM_PROMPT_STATUS + summary_text + chat_text + google_context)
    max_tokens = int(info.get("max_tokens") or 0)
    pct = round((used_tokens / max_tokens) * 100, 1) if max_tokens else 0
    return {
        "provider": info.get("provider", "Unbekannt"),
        "model": info.get("model", "Nicht gesetzt"),
        "configured": bool(info.get("configured")),
        "used_tokens": used_tokens,
        "max_tokens": max_tokens,
        "used_percent": pct,
        "auto_compact_at_percent": 80,
        "summary_chars": len(summary_text),
        "last_compacted_at": int(summary.get("last_compacted_at") or 0),
    }


SYSTEM_PROMPT_STATUS = "Du bist der persönliche Assistent des Nutzers. Aktueller Kontext und Chatverlauf."


def _auto_compact_if_needed(email: str) -> bool:
    import db, ai_client
    status = _chat_context_status(email)
    if float(status.get("used_percent", 0)) < 80:
        return False
    ctx = db.get_chat_context("web", user_email=email)
    messages = db.recent_chat_messages("web", limit=80, user_email=email)
    if len(messages) < 12:
        return False
    summary = ai_client.compact_context(str(ctx.get("summary", "")), [{"role": m["role"], "content": m["content"]} for m in messages], user_email=email)
    db.replace_chat_with_compact_summary("web", summary, user_email=email)
    return True


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
