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
- WICHTIG: Wenn du eine Safe-Mode-Freigabe vorschlägst, FRAGE den Nutzer zuerst, ob du sie erstellen sollst. Erstelle sie NICHT automatisch im Chat-Text. Sage NICHT "Ich habe X in den Safe Mode geschrieben", wenn du es nicht WIRKLICH getan hast.
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
        "think_effort": val("AI_THINK_EFFORT"),
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


def _supports_vision(user_email: str | None = None) -> bool:
    model = model_name(user_email).lower()
    return any(k in model for k in ["gpt-4o", "gpt-4.1", "o3", "o4", "gemini", "claude-3", "claude-3.5", "llava", "vision", "qwen-vl", "qwen2-vl"])


def _attachment_text_content(atch: dict[str, Any]) -> str:
    """Build a text description for non-vision attachments or unsupported file types."""
    name = atch.get("filename", "Datei")
    mime = atch.get("mime_type", "")
    size = atch.get("size", 0)
    return f"[Anhang: {name} ({mime}, {size} Bytes)]"


def _attachment_to_content(atch: dict[str, Any], user_email: str | None = None) -> list[dict[str, Any]]:
    """Convert an upload attachment dict into OpenAI message content blocks."""
    from pathlib import Path
    import base64

    mime = atch.get("mime_type", "").lower()
    path = atch.get("path", "")
    filename = atch.get("filename", "")
    result: list[dict[str, Any]] = []

    filepath = Path(__file__).resolve().parent / path
    vision_mimes = {"image/png", "image/jpeg", "image/gif", "image/webp"}

    # Images -> base64 vision for API-supported web image formats if the model supports vision.
    # Other image formats (SVG/BMP/TIFF/ICO) are still uploaded/served/displayed, but not sent to the LLM as vision data.
    if mime in vision_mimes and _supports_vision(user_email):
        if filepath.is_file():
            data = filepath.read_bytes()
            b64 = base64.b64encode(data).decode("ascii")
            result.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}", "detail": "auto"}})
        else:
            result.append({"type": "text", "text": f"[Bild nicht gefunden: {filename}]"})
    elif mime == "image/svg+xml" and filepath.is_file():
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
            result.append({"type": "text", "text": f"--- SVG-Bild {filename} ---\n{text[:60000]}\n--- Ende {filename} ---"})
        except Exception:
            result.append({"type": "text", "text": f"[SVG-Bildanhang: {filename}]"})
    elif mime.startswith("image/"):
        result.append({"type": "text", "text": f"[Bildanhang: {filename} ({mime}, {atch.get('size',0)} Bytes)]"})
    elif mime == "application/pdf":
        if filepath.is_file():
            try:
                import subprocess
                proc = subprocess.run(["pdftotext", "-layout", str(filepath), "-"], capture_output=True, text=True, timeout=20)
                text = proc.stdout.strip()
                if text:
                    result.append({"type": "text", "text": f"--- PDF-Text von {filename} ---\n{text[:60000]}\n--- Ende {filename} ---"})
                else:
                    result.append({"type": "text", "text": f"[PDF-Anhang: {filename} ({atch.get('size',0)} Bytes). Kein extrahierbarer Text gefunden.]"})
            except Exception as exc:
                result.append({"type": "text", "text": f"[PDF-Anhang: {filename} ({atch.get('size',0)} Bytes). Text-Extraktion fehlgeschlagen: {exc}]"})
        else:
            result.append({"type": "text", "text": f"[PDF nicht gefunden: {filename}]"})
    elif mime.startswith("audio/"):
        result.append({"type": "text", "text": f"[Audio-Anhang: {filename} ({mime}, {atch.get('size',0)} Bytes). Audio-Upload ist gespeichert/verlinkt; automatische Transkription ist noch nicht aktiviert.]"})
    elif mime.startswith("text/") or filename.lower().endswith((
        ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".log",
        ".py", ".js", ".ts", ".jsx", ".tsx", ".html", ".htm", ".css",
        ".sh", ".sql", ".c", ".cpp", ".h", ".java", ".go", ".rs", ".php",
        ".swift", ".kt", ".xml", ".ini", ".cfg", ".toml", ".properties", ".env"
    )):
        filepath = Path(__file__).resolve().parent / path
        if filepath.is_file():
            try:
                text = filepath.read_text(encoding="utf-8", errors="replace")
                result.append({"type": "text", "text": f"--- Inhalt von {filename} ---\n{text[:60000]}\n--- Ende {filename} ---"})
            except Exception:
                result.append({"type": "text", "text": f"[Anhang: {filename} konnte nicht gelesen werden]"})
        else:
            result.append({"type": "text", "text": f"[Anhang nicht gefunden: {filename}]"})
    else:
        result.append({"type": "text", "text": _attachment_text_content(atch)})
    return result


def chat_completion(messages: list[dict[str, Any]], max_tokens: int = 1000, temperature: float = 0.2, user_email: str | None = None, timeout_seconds: int | None = None) -> str:
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
    if s.get("think_effort"):
        body["reasoning_effort"] = s["think_effort"]
    data = json.dumps(body).encode()
    # large images = large base64 payload = slower AI response → increase timeout
    has_vision = any(
        isinstance(m.get("content"), list) and any(b.get("type") == "image_url" for b in m["content"])
        for m in messages
    )
    timeout = timeout_seconds or (180 if has_vision else 90)
    for attempt in range(2):
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {s['api_key']}")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
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
        except TimeoutError as exc:
            if attempt == 0 and has_vision:
                time.sleep(3)
                continue
            raise RuntimeError(f"Die KI benötigt zu lange für die Antwort (Timeout nach {timeout}s). Bitte mit einem kürzeren Text nochmals versuchen.") from exc
        except Exception as exc:
            raise RuntimeError(f"Unerwartete AI-Antwort: {exc}") from exc
    raise RuntimeError("AI Rate-Limit – bitte in ein paar Sekunden erneut versuchen.")


def assistant_reply(user_text: str, context: str = "", history: list[dict[str, Any]] | None = None, attachments: list[dict[str, Any]] | None = None, user_email: str | None = None) -> str:
    history = history or []
    attachments = attachments or []
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append({"role": "system", "content": f"Aktueller Kontext:\n{context}"})
    for msg in history:
        if msg.get("role") in {"user", "assistant"}:
            content_blocks: list[dict[str, Any]] = []
            text = str(msg.get("content", ""))
            if text:
                content_blocks.append({"type": "text", "text": text})
            # If history entries carry attachments, convert them
            for atch in msg.get("attachments") or []:
                if isinstance(atch, dict):
                    content_blocks.extend(_attachment_to_content(atch, user_email))
            if content_blocks:
                messages.append({"role": str(msg["role"]), "content": content_blocks})
            else:
                messages.append({"role": str(msg["role"]), "content": text})
    # Build user message with text + attachments
    user_content: list[dict[str, Any]] = []
    if user_text:
        user_content.append({"type": "text", "text": user_text})
    for atch in attachments:
        user_content.extend(_attachment_to_content(atch, user_email))
    if not user_content:
        user_content = [{"type": "text", "text": user_text}]
    messages.append({"role": "user", "content": user_content})
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
