#!/usr/bin/env python3
"""Telegram bot – long polling. Bound to the Google user that configured it."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from db import (
    add_chat_message,
    clear_chat_history,
    create_pending_action,
    create_upload,
    get_upload,
    first_user_with_settings,
    get_pending_action,
    get_user_setting,
    list_pending_actions,
    recent_chat_messages,
    update_pending_action,
)


def _telegram_user_email() -> str | None:
    return first_user_with_settings(["TELEGRAM_BOT_TOKEN", "TELEGRAM_ALLOWED_USER_ID"])


def _bot_token(user_email: str | None = None) -> str:
    email = user_email or _telegram_user_email()
    return get_user_setting(email, "TELEGRAM_BOT_TOKEN", "") if email else ""


def _telegram_call(method: str, body: dict[str, Any] | None = None, user_email: str | None = None) -> dict[str, Any]:
    token = _bot_token(user_email)
    if not token:
        return {"ok": False, "error": "Telegram Bot Token nicht konfiguriert"}
    url = f"https://api.telegram.org/bot{token}/{method}"
    if body:
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
    else:
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        try:
            return json.loads(body_text)
        except Exception:
            return {"ok": False, "error": body_text, "status": exc.code}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def configured(user_email: str | None = None) -> bool:
    email = user_email or _telegram_user_email()
    return bool(email and get_user_setting(email, "TELEGRAM_BOT_TOKEN", "") and get_user_setting(email, "TELEGRAM_ALLOWED_USER_ID", ""))


def _allowed_user_id(user_email: str | None = None) -> int:
    email = user_email or _telegram_user_email()
    if not email:
        return 0
    try:
        return int(get_user_setting(email, "TELEGRAM_ALLOWED_USER_ID", "0") or "0")
    except ValueError:
        return 0


_CLEAR_COMMANDS = {
    "/clear", "/reset", "/clear-chat", "/clear-context",
    "/loeschen", "/löschen", "/chat-loeschen", "/chat-löschen",
    "/verlauf-loeschen", "/verlauf-löschen", "/kontext-loeschen", "/kontext-löschen",
}

_HELP_COMMANDS = {"/help", "/hilfe", "/commands", "/befehle"}

_HELP_TEXT = """📋 Verfügbare Chat-Befehle:

• /help, /hilfe, /commands, /befehle – diese Übersicht
• /clear, /reset, /löschen, /chat-löschen, /verlauf-löschen, /kontext-löschen – kompletten Chat-Verlauf und Kontext löschen

Alle Befehle funktionieren sowohl im WebChat als auch via Telegram."""


def _is_clear_chat_command(text: str) -> bool:
    return text.strip().lower() in _CLEAR_COMMANDS


def _is_help_command(text: str) -> bool:
    return text.strip().lower() in _HELP_COMMANDS


def _send_help(chat_id: int, user_email: str) -> None:
    send_message(chat_id, _HELP_TEXT, user_email=user_email)


def send_message(chat_id: int, text: str, buttons: list[list[dict[str, Any]]] | None = None, user_email: str | None = None) -> int | None:
    body: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if buttons:
        body["reply_markup"] = json.dumps({"inline_keyboard": buttons})
    result = _telegram_call("sendMessage", body, user_email=user_email)
    if result.get("ok"):
        return int(result["result"]["message_id"])
    if buttons:
        body.pop("reply_markup", None)
        result = _telegram_call("sendMessage", body, user_email=user_email)
        if result.get("ok"):
            return int(result["result"]["message_id"])
    return None


def _handle_user_message(user_msg: str, chat_id: int, username: str, user_email: str, attachment_ids: list[str] | None = None) -> None:
    """Persist user message and start AI processing in background thread."""
    attachment_ids = attachment_ids or []
    attachments_json = json.dumps(attachment_ids, ensure_ascii=False) if attachment_ids else ""
    add_chat_message("telegram", "user", user_msg, user_email=user_email, attachments_json=attachments_json)
    threading.Thread(
        target=_process_message,
        args=(user_msg, chat_id, username, user_email, attachment_ids),
        daemon=True,
    ).start()


def _process_message(user_msg: str, chat_id: int, username: str, user_email: str, attachment_ids: list[str]) -> None:
    try:
        from ai_client import assistant_reply, propose_actions

        attachments = [dict(up) for aid in attachment_ids if (up := get_upload(aid, user_email=user_email))]
        context = _build_context(user_email)
        history = recent_chat_messages("telegram", limit=12, user_email=user_email)
        for h in history:
            aids = h.get("attachments")
            if isinstance(aids, list):
                h["attachments"] = [dict(up) for aid in aids if isinstance(aid, str) and (up := get_upload(aid, user_email=user_email))]
        history = [{"role": h["role"], "content": h["content"], "attachments": h["attachments"]} for h in history[:-1]]

        actions = propose_actions(user_msg, context, user_email=user_email)
        if actions:
            action_results = []
            for act in actions:
                a = create_pending_action(act["type"], act["title"], act["payload"], source="telegram", user_email=user_email)
                action_results.append(a)
            pending_summary = [f"🛡️ {a['title']} ({a['type']})" for a in action_results]
            reply = "Vorschläge zur Freigabe:\n\n" + "\n".join(pending_summary)
            add_chat_message("telegram", "assistant", reply, user_email=user_email)
            buttons = [[
                {"text": f"✅ {a['title'][:30]}", "callback_data": f"approve:{a['id']}"},
                {"text": "❌ Ablehnen", "callback_data": f"reject:{a['id']}"}
            ] for a in action_results]
            send_message(chat_id, reply, buttons=buttons, user_email=user_email)
        else:
            reply = assistant_reply(user_msg, context=context, history=history, attachments=attachments, user_email=user_email)
            add_chat_message("telegram", "assistant", reply, user_email=user_email)
            send_message(chat_id, reply, user_email=user_email)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        try:
            send_message(chat_id, f"Fehler bei der Verarbeitung: {exc}", user_email=user_email)
        except Exception:
            pass


def _handle_callback(chat_id: int, data: str, user_email: str) -> None:
    parts = data.split(":", 1)
    if len(parts) != 2:
        return
    cmd, action_id = parts[0], parts[1]
    if cmd == "approve":
        _perform_approve(chat_id, action_id, user_email)
    elif cmd == "reject":
        _perform_reject(chat_id, action_id, user_email)


def _perform_approve(chat_id: int, action_id: str, user_email: str) -> None:
    import safe_mode

    result = safe_mode.approve(action_id, user_email=user_email)
    action = result.get("action") or get_pending_action(action_id, user_email=user_email)
    title = str(action.get("title", action_id)) if isinstance(action, dict) else action_id
    if result.get("ok"):
        send_message(chat_id, f"✅ Ausgeführt: {title}", user_email=user_email)
    else:
        send_message(chat_id, f"❌ Fehler: {result.get('error', 'Unbekannt')}", user_email=user_email)


def _perform_reject(chat_id: int, action_id: str, user_email: str) -> None:
    update_pending_action(action_id, "rejected", user_email=user_email)
    send_message(chat_id, "❌ Abgelehnt.", user_email=user_email)


def _exec_action(action: dict[str, Any], user_email: str) -> dict[str, Any]:
    import safe_mode

    try:
        backup_meta = safe_mode._backup_before(action, user_email=user_email)
        result = safe_mode.execute(action, user_email=user_email)
        if isinstance(result, dict) and backup_meta:
            result.setdefault("backup", backup_meta)
        return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _build_context(user_email: str) -> str:
    try:
        import context_builder
        return context_builder.build_google_context(user_email)
    except Exception as exc:
        return f"(Kontext nicht verfügbar: {exc})"


def _download_telegram_file(file_id: str, user_email: str) -> tuple[bytes, str] | None:
    """Download a Telegram file and return (bytes, telegram_file_path)."""
    token = _bot_token(user_email)
    if not token:
        return None
    info = _telegram_call("getFile", {"file_id": file_id}, user_email=user_email)
    if not info.get("ok"):
        return None
    file_path = str(info.get("result", {}).get("file_path", ""))
    if not file_path:
        return None
    url = f"https://api.telegram.org/file/bot{token}/{file_path}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as resp:
            return resp.read(), file_path
    except Exception:
        return None


def _telegram_file_info(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the best file candidate from a Telegram message."""
    if msg.get("document"):
        doc = msg["document"]
        return {
            "file_id": doc.get("file_id"),
            "filename": doc.get("file_name") or "telegram_document",
            "mime_type": doc.get("mime_type") or "application/octet-stream",
            "size": doc.get("file_size") or 0,
            "kind": "Dokument",
        }
    if msg.get("photo"):
        photos = msg.get("photo") or []
        if not photos:
            return None
        photo = photos[-1]
        return {
            "file_id": photo.get("file_id"),
            "filename": "telegram_photo.jpg",
            "mime_type": "image/jpeg",
            "size": photo.get("file_size") or 0,
            "kind": "Bild",
        }
    if msg.get("audio"):
        audio = msg["audio"]
        return {
            "file_id": audio.get("file_id"),
            "filename": audio.get("file_name") or "telegram_audio.mp3",
            "mime_type": audio.get("mime_type") or "audio/mpeg",
            "size": audio.get("file_size") or 0,
            "kind": "Audio",
        }
    if msg.get("voice"):
        voice = msg["voice"]
        return {
            "file_id": voice.get("file_id"),
            "filename": "telegram_voice.ogg",
            "mime_type": voice.get("mime_type") or "audio/ogg",
            "size": voice.get("file_size") or 0,
            "kind": "Sprachnachricht",
        }
    if msg.get("video"):
        video = msg["video"]
        return {
            "file_id": video.get("file_id"),
            "filename": video.get("file_name") or "telegram_video.mp4",
            "mime_type": video.get("mime_type") or "video/mp4",
            "size": video.get("file_size") or 0,
            "kind": "Video",
        }
    if msg.get("animation"):
        anim = msg["animation"]
        return {
            "file_id": anim.get("file_id"),
            "filename": anim.get("file_name") or "telegram_animation.mp4",
            "mime_type": anim.get("mime_type") or "video/mp4",
            "size": anim.get("file_size") or 0,
            "kind": "Animation",
        }
    return None


def _handle_file_message(msg: dict[str, Any], chat_id: int, username: str, user_email: str) -> None:
    info = _telegram_file_info(msg)
    if not info or not info.get("file_id"):
        send_message(chat_id, "Datei konnte nicht gelesen werden.", user_email=user_email)
        return
    # Send immediate reaction
    send_message(chat_id, f"📥 {info['kind']} erkannt: {info.get('filename','unbekannt')}. Lade herunter…", user_email=user_email)
    downloaded = _download_telegram_file(str(info["file_id"]), user_email)
    if not downloaded:
        send_message(chat_id, "Download der Telegram-Datei fehlgeschlagen.", user_email=user_email)
        return
    data, telegram_path = downloaded
    filename = Path(str(info.get("filename") or Path(telegram_path).name or "telegram_upload")).name or "telegram_upload"
    mime = str(info.get("mime_type") or "application/octet-stream")
    upload = create_upload(user_email, filename, mime, len(data), data)
    caption = str(msg.get("caption", "")).strip()
    text = caption or f"[{info.get('kind', 'Datei')}-Anhang: {filename}]"
    send_message(chat_id, f"📎 Datei empfangen: {filename} ({len(data)} Bytes). Ich verarbeite sie jetzt.", user_email=user_email)
    # AI processing async via _handle_user_message (which now spawns a thread)
    _handle_user_message(text, chat_id, username, user_email, attachment_ids=[upload["id"]])


def _run_poll_loop() -> None:
    offset = 0
    while True:
        try:
            user_email = _telegram_user_email()
            if not user_email or not configured(user_email):
                time.sleep(10)
                continue
            result = _telegram_call("getUpdates", {"offset": offset, "timeout": 30}, user_email=user_email)
            if not result.get("ok"):
                time.sleep(5)
                continue
            for upd in result.get("result", []):
                try:
                    offset = upd["update_id"] + 1
                    msg = upd.get("message") or upd.get("callback_query", {}).get("message")
                    if not msg:
                        continue
                    chat_id = int(msg["chat"]["id"])
                    cb = upd.get("callback_query")
                    user = (cb.get("from") if cb else msg.get("from", {})) or {}
                    sender_id = int(user.get("id") or 0)
                    if sender_id != _allowed_user_id(user_email):
                        send_message(chat_id, "Nicht autorisiert.", user_email=user_email)
                        continue
                    username = user.get("first_name", user.get("username", "?"))
                    if cb:
                        _handle_callback(chat_id, str(cb.get("data", "")), user_email)
                    else:
                        if _telegram_file_info(msg):
                            threading.Thread(
                                target=_handle_file_message,
                                args=(msg, chat_id, username, user_email),
                                daemon=True,
                            ).start()
                            continue
                        text = str(msg.get("text", "")).strip()
                        if text:
                            if text == "/start":
                                send_message(chat_id, "Hallo! Ich bin dein Assistent. Frag mich einfach – oder sag mir, was ich in deinen Kalender/Todos eintragen soll.\n\n/help zeigt alle Befehle.", user_email=user_email)
                            elif _is_clear_chat_command(text):
                                try:
                                    clear_chat_history("telegram", user_email=user_email)
                                    send_message(chat_id, "✅ Chat-Verlauf & Kontext gelöscht. Frischer Start!", user_email=user_email)
                                except Exception as exc:
                                    send_message(chat_id, f"Fehler beim Löschen: {exc}", user_email=user_email)
                            elif _is_help_command(text):
                                _send_help(chat_id, user_email)
                            else:
                                _handle_user_message(text, chat_id, username, user_email)
                except Exception:
                    traceback.print_exc(file=sys.stderr)
        except Exception:
            traceback.print_exc(file=sys.stderr)
            time.sleep(5)


_poll_thread: threading.Thread | None = None


def start() -> None:
    global _poll_thread
    if os.environ.get("ASSISTANT_TELEGRAM_POLLING", "1").lower() in {"0", "false", "no", "off"}:
        return
    if _poll_thread is not None:
        return
    _poll_thread = threading.Thread(target=_run_poll_loop, daemon=True)
    _poll_thread.start()


def test_connection(user_email: str | None = None) -> dict[str, Any]:
    email = user_email or _telegram_user_email()
    try:
        result = _telegram_call("getMe", user_email=email)
        if result.get("ok"):
            user = result.get("result", {})
            return {"ok": True, "bot": user.get("username") or user.get("first_name") or "?"}
        return {"ok": False, "error": result.get("description") or result.get("error") or "Telegram Fehler"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
