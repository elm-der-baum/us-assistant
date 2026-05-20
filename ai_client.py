#!/usr/bin/env python3
"""OpenAI-compatible AI client for assistant."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
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

Wichtig: Schreibaktionen werden NICHT direkt ausgeführt. Du erstellst nur Safe-Mode-Vorschläge.
Gib ausschließlich gültiges JSON zurück:
{
  "actions": [
    {
      "type": "create_calendar_event|update_calendar_event|delete_calendar_event|create_task|update_task|complete_task|delete_task|move_task|create_tasklist|update_tasklist|delete_tasklist",
      "title": "kurzer deutscher Titel für die Freigabe",
      "payload": { }
    }
  ]
}

Wenn keine Schreibaktion gewünscht ist: {"actions": []}

Payload-Regeln:
- create_calendar_event: payload ist ein Google Calendar Event Body, z.B.
  {"summary":"Zahnarzt", "start":{"dateTime":"2026-05-19T10:00:00+02:00", "timeZone":"Europe/Berlin"}, "end":{"dateTime":"2026-05-19T11:00:00+02:00", "timeZone":"Europe/Berlin"}, "description":"..."}
- create_task: IMMER eine Aktion erzeugen, wenn der Nutzer ein Todo/eine Aufgabe anlegen will.
  Payload ist ein Google Tasks Task Body, z.B. {"title":"Steuer erledigen", "notes":"...", "due":"2026-05-19T00:00:00.000Z", "tasklist_id":"..."}
  Wenn eine Liste genannt ist, nutze tasklist_id aus dem Kontext. Wenn nur der Listenname bekannt ist, nutze "tasklist_title".
- update_task/complete_task/delete_task: nutze task_id UND tasklist_id aus dem Kontext. Wenn eine eindeutige Aufgabe genannt ist, erzeuge die Aktion.
- move_task: IMMER eine Aktion erzeugen, wenn der Nutzer eine Aufgabe/ein Todo in eine andere Liste verschieben will.
  Payload: {"task_id":"...", "source_tasklist_id":"...", "target_tasklist_id":"..."}
  Wenn IDs fehlen, aber Namen eindeutig sind: {"task_title":"Aufgabentitel", "source_tasklist_title":"Quellliste", "target_tasklist_title":"Zielliste"}
  Synonyme für Verschieben: verschiebe, packe in Liste, tue in Liste, schiebe nach, in andere Liste.
- create_tasklist: payload {"title":"Listenname"}
- update_tasklist: payload {"tasklist_id":"...", "title":"Neuer Name"}
- delete_tasklist: payload {"tasklist_id":"..."}
- Wenn eine ID wirklich nicht eindeutig bestimmbar ist, erstelle KEINE Aktion und schreibe reason ins JSON.
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


def estimate_tokens(text: str) -> int:
    return max(0, (len(text or "") + 3) // 4)


def provider_name(user_email: str | None = None) -> str:
    base_url = _settings(user_email)["base_url"]
    host = urllib.parse.urlparse(base_url).netloc.lower()
    if "openai" in host:
        return "OpenAI"
    if "anthropic" in host:
        return "Anthropic"
    if "groq" in host:
        return "Groq"
    if "openrouter" in host:
        return "OpenRouter"
    if "localhost" in host or "127.0.0.1" in host or "ollama" in host:
        return "Ollama"
    return host or "Unbekannt"


def model_name(user_email: str | None = None) -> str:
    return _settings(user_email)["model"] or "Nicht gesetzt"


def model_context_max(user_email: str | None = None) -> int:
    override = get_user_setting(user_email, "AI_CONTEXT_MAX_TOKENS", "") if user_email else ""
    if override and str(override).isdigit():
        return int(str(override))
    model = model_name(user_email).lower()
    if any(k in model for k in ["gpt-4.1", "gpt-4o", "o3", "o4", "gemini", "claude-3.5", "claude-3-5"]):
        return 128000
    if any(k in model for k in ["qwen", "llama3.1", "llama-3.1", "mistral-large"]):
        return 128000
    if any(k in model for k in ["llama", "mistral", "mixtral"]):
        return 32000
    return 128000


def context_info(user_email: str | None = None) -> dict[str, Any]:
    return {
        "provider": provider_name(user_email),
        "model": model_name(user_email),
        "max_tokens": model_context_max(user_email),
        "configured": configured(user_email),
    }


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
    for attempt in range(2):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {s['api_key']}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                payload = json.loads(resp.read())
            return str(payload["choices"][0]["message"]["content"] or "")
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            if exc.code == 429 and attempt == 0:
                retry = exc.headers.get("Retry-After", "5")
                try:
                    time.sleep(min(float(retry), 30))
                except ValueError:
                    time.sleep(5)
                continue
            raise RuntimeError(f"AI HTTP {exc.code}: {body_text}") from exc
        except urllib.error.URLError as exc:
            if attempt == 0:
                time.sleep(2)
                continue
            raise RuntimeError(f"AI Netzwerkfehler: {exc}") from exc
        except Exception as exc:
            raise RuntimeError(f"Unerwartete AI-Antwort: {exc}") from exc
    raise RuntimeError("AI Rate-Limit – bitte in ein paar Sekunden erneut versuchen.")


def assistant_reply(user_text: str, context: str = "", history: list[dict[str, str]] | None = None, user_email: str | None = None) -> str:
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": f"Aktueller Kontext:\n{context}"})
    for msg in history or []:
        if msg.get("role") in {"user", "assistant"}:
            messages.append({"role": str(msg["role"]), "content": str(msg.get("content", ""))})
    messages.append({"role": "user", "content": user_text})
    return chat_completion(messages, user_email=user_email)


def compact_context(existing_summary: str, messages: list[dict[str, str]], user_email: str | None = None) -> str:
    transcript = "\n".join(f"{m.get('role','?')}: {m.get('content','')}" for m in messages)
    prompt = (
        "Kompaktiere den bisherigen Chat-Kontext für spätere Antworten. "
        "Bewahre Nutzerpräferenzen, offene Aufgaben, Zusagen, wichtige Fakten, Kalender/Todo-Bezüge und Entscheidungen. "
        "Entferne Wiederholungen und irrelevante Details. Antworte nur mit der kompakten Zusammenfassung auf Deutsch."
    )
    content = ""
    if existing_summary:
        content += f"Bisherige kompakte Zusammenfassung:\n{existing_summary}\n\n"
    content += f"Chat-Transkript:\n{transcript}"
    return chat_completion(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": content[:60000]},
        ],
        max_tokens=1800,
        temperature=0.1,
        user_email=user_email,
    ).strip()


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
        "move_task",
        "create_tasklist",
        "update_tasklist",
        "delete_tasklist",
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
