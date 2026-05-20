#!/usr/bin/env python3
"""Telegram bot – long polling. Bound to the Google user that configured it."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from typing import Any

from db import (
    add_chat_message,
    create_pending_action,
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


def _handle_user_message(user_msg: str, chat_id: int, username: str, user_email: str) -> None:
    from ai_client import assistant_reply, propose_actions

    add_chat_message("telegram", "user", user_msg, user_email=user_email)
    context = _build_context(user_email)
    history = recent_chat_messages("telegram", limit=12, user_email=user_email)
    history = [{"role": h["role"], "content": h["content"]} for h in history[:-1]]

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
        reply = assistant_reply(user_msg, context=context, history=history, user_email=user_email)
        add_chat_message("telegram", "assistant", reply, user_email=user_email)
        send_message(chat_id, reply, user_email=user_email)


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
    action = get_pending_action(action_id, user_email=user_email)
    if not action:
        send_message(chat_id, "Aktion nicht gefunden.", user_email=user_email)
        return

    result = _exec_action(action, user_email)
    if result.get("ok") or ("error" not in result and result.get("status") != "error"):
        update_pending_action(action_id, "done", result=result, user_email=user_email)
        send_message(chat_id, f"✅ Ausgeführt: {action['title']}", user_email=user_email)
    else:
        update_pending_action(action_id, "error", error=result.get("error", "Fehler"), user_email=user_email)
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
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("callback_query", {}).get("message")
                if not msg:
                    continue
                chat_id = int(msg["chat"]["id"])
                if chat_id != _allowed_user_id(user_email):
                    send_message(chat_id, "Nicht autorisiert.", user_email=user_email)
                    continue
                user = msg.get("from", {})
                username = user.get("first_name", user.get("username", "?"))
                cb = upd.get("callback_query")
                if cb:
                    _handle_callback(chat_id, str(cb.get("data", "")), user_email)
                else:
                    text = str(msg.get("text", "")).strip()
                    if text:
                        if text == "/start":
                            send_message(chat_id, "Hallo! Ich bin dein Assistent. Frag mich einfach – oder sag mir, was ich in deinen Kalender/Todos eintragen soll.", user_email=user_email)
                        else:
                            _handle_user_message(text, chat_id, username, user_email)
        except Exception:
            time.sleep(5)


_poll_thread: threading.Thread | None = None


def start() -> None:
    global _poll_thread
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
