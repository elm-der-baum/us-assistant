#!/usr/bin/env python3
"""OpenAI-compatible AI client for assistant."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from db import get_setting, get_user_setting

SYSTEM_PROMPT = """Du bist der persönliche Assistent des Nutzers.

Regeln:
- Du darfst Kalender, Todos und später Emails lesen/analysieren.
- Du darfst NIEMALS direkt schreiben, ändern oder löschen.
- Schreibaktionen müssen immer als Vorschlag in den Safe Mode.
- Antworte kurz, konkret und auf Deutsch.
- Wenn Daten fehlen, frage nach.
"""

ACTION_SCHEMA_PROMPT = """Prüfe, ob die Nutzernachricht eine Schreibaktion für Google Calendar oder Google Tasks verlangt.

Gib ausschließlich gültiges JSON zurück:
{
  "actions": [
    {
      "type": "create_calendar_event|update_calendar_event|delete_calendar_event|create_task|update_task|complete_task|delete_task",
      "title": "kurzer deutscher Titel für die Freigabe",
      "payload": { }
    }
  ]
}

Wenn keine Schreibaktion gewünscht ist: {"actions": []}

Payload-Regeln:
- create_calendar_event: payload ist ein Google Calendar Event Body, z.B.
  {"summary":"Zahnarzt", "start":{"dateTime":"2026-05-19T10:00:00+02:00", "timeZone":"Europe/Berlin"}, "end":{"dateTime":"2026-05-19T11:00:00+02:00", "timeZone":"Europe/Berlin"}, "description":"..."}
- create_task: payload ist ein Google Tasks Task Body, z.B. {"title":"Steuer erledigen", "notes":"...", "due":"2026-05-19T00:00:00.000Z"}
- Für update/delete brauchst du eine eindeutige ID. Wenn keine ID vorhanden ist, erstelle KEINE Aktion und schreibe reason ins JSON.
"""


def _settings(user_email: str | None = None) -> dict[str, str]:
    def val(key: str) -> str:
        if user_email:
            v = get_user_setting(user_email, key, "") or ""
            if v:
                return v
        # legacy fallback for older single-user installs
        return get_setting(key, "") or ""

    return {
        "base_url": val("AI_BASE_URL").rstrip("/"),
        "api_key": val("AI_API_KEY"),
        "model": val("AI_MODEL"),
    }


def configured(user_email: str | None = None) -> bool:
    s = _settings(user_email)
    return bool(s["base_url"] and s["api_key"] and s["model"])


def chat_completion(messages: list[dict[str, str]], max_tokens: int = 1000, temperature: float = 0.2, user_email: str | None = None) -> str:
    s = _settings(user_email)
    if not configured(user_email):
        raise RuntimeError("AI nicht konfiguriert. Bitte Settings öffnen und AI_BASE_URL, AI_API_KEY, AI_MODEL setzen.")

    url = f"{s['base_url']}/chat/completions"
    body = {
        "model": s["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {s['api_key']}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise RuntimeError(f"AI HTTP {exc.code}: {body_text}") from exc

    try:
        return str(payload["choices"][0]["message"]["content"] or "")
    except Exception as exc:
        raise RuntimeError(f"Unerwartete AI-Antwort: {payload}") from exc


def assistant_reply(user_text: str, context: str = "", history: list[dict[str, str]] | None = None, user_email: str | None = None) -> str:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": f"Aktueller Kontext:\n{context}"})
    for msg in history or []:
        if msg.get("role") in {"user", "assistant"}:
            messages.append({"role": str(msg["role"]), "content": str(msg.get("content", ""))})
    messages.append({"role": "user", "content": user_text})
    return chat_completion(messages, user_email=user_email)


def propose_actions(user_text: str, context: str = "", user_email: str | None = None) -> list[dict[str, Any]]:
    """Use the LLM to transform write intent into Safe-Mode proposals."""
    berlin = ZoneInfo("Europe/Berlin")
    now = datetime.now(berlin)
    messages = [
        {"role": "system", "content": ACTION_SCHEMA_PROMPT},
        {
            "role": "system",
            "content": (
                f"Heute/Jetzt: {now.isoformat()} | Zeitzone: Europe/Berlin.\n"
                f"Kontext:\n{context[:6000]}"
            ),
        },
        {"role": "user", "content": user_text},
    ]
    try:
        raw = chat_completion(messages, max_tokens=1200, temperature=0.0, user_email=user_email)
    except Exception:
        return []

    parsed = _parse_json(raw)
    actions = parsed.get("actions", []) if isinstance(parsed, dict) else []
    clean: list[dict[str, Any]] = []
    allowed = {
        "create_calendar_event",
        "update_calendar_event",
        "delete_calendar_event",
        "create_task",
        "update_task",
        "complete_task",
        "delete_task",
    }
    for item in actions:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("type", ""))
        title = str(item.get("title", "") or action_type)
        payload = item.get("payload", {})
        if action_type not in allowed or not isinstance(payload, dict):
            continue
        clean.append({"type": action_type, "title": title, "payload": payload})
    return clean


def _parse_json(raw: str) -> Any:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
        raw = re.sub(r"```$", "", raw).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # last resort: extract first JSON object
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return {}
        try:
            return json.loads(match.group(0))
        except Exception:
            return {}


def test_connection(user_email: str | None = None) -> dict[str, Any]:
    started = time.time()
    try:
        text = chat_completion(
            [
                {"role": "system", "content": "Antworte nur mit: ok"},
                {"role": "user", "content": "ping"},
            ],
            max_tokens=10,
            temperature=0,
            user_email=user_email,
        )
        return {"ok": True, "reply": text.strip(), "ms": int((time.time() - started) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
