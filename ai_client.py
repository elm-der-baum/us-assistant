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
- Antworte AUSSCHLIESSLICH in natürlicher Sprache. Gib NIEMALS JSON, Code-Blöcke oder technische Datenstrukturen im Antworttext aus.
"""

ACTION_SCHEMA_PROMPT = """Du bist ein Klassifizierungs-Modul. Deine einzige Aufgabe ist es, Schreibaktionen für Google Calendar oder Google Tasks als JSON zu extrahieren.

WICHTIGE REGELN:
- Antworte AUSSCHLIESSLICH mit dem JSON-Objekt. Kein Einleitungstext, keine Erklärung, keine Markdown-Code-Blöcke (```json), keine Zusammenfassung.
- Wenn keine Schreibaktion verlangt ist, antworte mit: {"actions": []}
- Wenn eine Schreibaktion verlangt ist, antworte NUR mit dem JSON-Objekt im unten stehenden Format.

FORMAT:
{
  "actions": [
    {
      "type": "create_calendar_event|update_calendar_event|delete_calendar_event|create_task|update_task|complete_task|delete_task|move_task|create_tasklist|update_tasklist|delete_tasklist",
      "title": "kurzer deutscher Titel für die Freigabe",
      "payload": { }
    }
  ]
}

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


def _user_temporal_context(user_email: str | None) -> str:
    """Return a temporal+location context string for the given user."""
    if not user_email:
        tz_name = "UTC"
        location = "Unbekannt"
    else:
        tz_name = (get_user_setting(user_email, "timezone") or "").strip() or "UTC"
        location = (get_user_setting(user_email, "location") or "").strip() or "Unbekannt"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
        tz_name = "UTC"
    now = datetime.now(tz)
    return f"Aktuelle Zeit/Datum: {now.isoformat()} | Zeitzone: {tz_name} | Ort: {location}"


def _settings(user_email: str | None = None) -> dict[str, str]:
    def val(key: str) -> str:
        if user_email:
            v = get_user_setting(user_email, key, "") or ""
            if v:
                return v
        # legacy fallback for older single-user installs
        return get_setting(key, "") or ""

    return {
        "auth_type": val("AI_AUTH_TYPE") or "api_key",
        "base_url": val("AI_BASE_URL").rstrip("/"),
        "api_key": val("AI_API_KEY"),
        "model": val("AI_MODEL"),
        "think_effort": val("AI_THINK_EFFORT"),
    }


def configured(user_email: str | None = None) -> bool:
    s = _settings(user_email)
    if s.get("auth_type") == "openai_codex_oauth":
        import openai_codex_oauth
        return bool(user_email and s["model"] and openai_codex_oauth.oauth_configured(user_email))
    return bool(s["base_url"] and s["api_key"] and s["model"])


def estimate_tokens(text: str) -> int:
    return max(0, (len(text or "") + 3) // 4)


def provider_name(user_email: str | None = None) -> str:
    s = _settings(user_email)
    if s.get("auth_type") == "openai_codex_oauth":
        return "OpenAI ChatGPT OAuth (Codex)"
    base_url = s["base_url"]
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
    if any(k in model for k in ["gpt-5", "gpt-4.1", "gpt-4o", "o3", "o4", "gemini", "claude-3.5", "claude-3-5"]):
        return 272000 if "gpt-5" in model else 128000
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
    return any(k in model for k in ["gpt-5", "gpt-4o", "gpt-4.1", "o3", "o4", "gemini", "claude-3", "claude-3.5", "llava", "vision", "qwen-vl", "qwen2-vl"])


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


def chat_completion(messages: list[dict[str, Any]], temperature: float = 0.2, user_email: str | None = None, timeout_seconds: int | None = None) -> str:
    s = _settings(user_email)
    if not configured(user_email):
        raise RuntimeError("AI nicht konfiguriert. Bitte Settings öffnen und API-Key oder ChatGPT-OAuth verbinden sowie Modell setzen.")

    # inject temporal/location context as the first system message
    if user_email:
        temporal = _user_temporal_context(user_email)
        messages = [{"role": "system", "content": temporal}] + list(messages)

    if s.get("auth_type") == "openai_codex_oauth":
        if not user_email:
            raise RuntimeError("OpenAI OAuth benötigt Login")
        import openai_codex_oauth
        return openai_codex_oauth.codex_chat_completion(
            messages,
            user_email=user_email,
            model=s["model"],
            think_effort=s.get("think_effort", ""),
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )

    url = f"{s['base_url']}/chat/completions"
    body = {
        "model": s["model"],
        "messages": messages,
        "temperature": temperature,
    }
    think_effort = (s.get("think_effort") or "").strip().lower()
    if think_effort and think_effort not in {"off", "none", "disabled"}:
        host = urllib.parse.urlparse(s["base_url"]).netloc.lower()
        # OpenAI currently accepts low/medium/high for reasoning models; avoid
        # sending unsupported values such as "max" to the official API.
        if "openai.com" not in host or think_effort in {"low", "medium", "high"}:
            body["reasoning_effort"] = think_effort
    data = json.dumps(body).encode()
    # large images = large base64 payload = slower AI response → increase timeout
    has_vision = any(
        isinstance(m.get("content"), list) and any(b.get("type") == "image_url" for b in m["content"])
        for m in messages
    )
    timeout = timeout_seconds or (240 if has_vision else 120)
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
        temperature=0.1,
        user_email=user_email,
    ).strip()


def propose_actions(user_text: str, context: str = "", user_email: str | None = None) -> list[dict[str, Any]]:
    """Use the LLM to transform write intent into Safe-Mode proposals."""
    messages = [
        {"role": "system", "content": ACTION_SCHEMA_PROMPT},
        {
            "role": "system",
            "content": (
                f"Kontext:\n{context[:6000]}"
            ),
        },
        {"role": "user", "content": user_text},
    ]
    try:
        raw = chat_completion(messages, temperature=0.0, user_email=user_email)
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
    if not raw:
        return {}
    text = raw.strip()
    # 1. Try markdown fenced blocks anywhere in the text
    for pattern in (r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```"):
        for match in re.finditer(pattern, text, flags=re.S | re.I):
            candidate = match.group(1).strip()
            if candidate:
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue
    # 2. Try the whole string
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 3. Balance braces to find the outermost JSON object or array,
    #    respecting quoted strings so braces inside values don't mislead.
    candidates = []
    for start_char, end_char in (("{", "}"), ("[", "]")):
        start_idx = text.find(start_char)
        while start_idx != -1:
            depth = 0
            in_string = False
            escape_next = False
            for i in range(start_idx, len(text)):
                ch = text[i]
                if escape_next:
                    escape_next = False
                    continue
                if ch == "\\":
                    escape_next = True
                    continue
                if ch == '"':
                    in_string = not in_string
                    continue
                if not in_string:
                    if ch == start_char:
                        depth += 1
                    elif ch == end_char:
                        depth -= 1
                        if depth == 0:
                            candidate = text[start_idx : i + 1]
                            try:
                                candidates.append((start_idx, len(candidate), json.loads(candidate)))
                            except json.JSONDecodeError:
                                pass
                            break
            start_idx = text.find(start_char, start_idx + 1)
    if candidates:
        # Prefer earliest start index, and longest match if tied
        candidates.sort(key=lambda x: (x[0], -x[1]))
        return candidates[0][2]
    # 4. Fallback: greedy regex for the first {…} block
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except Exception:
            pass
    return {}


def test_connection(user_email: str | None = None) -> dict[str, Any]:
    started = time.time()
    try:
        text = chat_completion(
            [
                {"role": "system", "content": "Antworte nur mit: ok"},
                {"role": "user", "content": "ping"},
            ],
            temperature=0,
            user_email=user_email,
        )
        return {"ok": True, "reply": text.strip(), "ms": int((time.time() - started) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
