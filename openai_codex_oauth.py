#!/usr/bin/env python3
"""OpenAI ChatGPT OAuth (Codex) support, matching pi.dev style flow."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

import db

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPE = "openid profile email offline_access"
JWT_CLAIM_PATH = "https://api.openai.com/auth"
DEFAULT_BASE_URL = "https://chatgpt.com/backend-api"
DEFAULT_MODEL = "gpt-5.2"
ORIGINATOR = os.environ.get("ASSISTANT_CODEX_ORIGINATOR", "pi")

TOKEN_SECRET_KEYS = {
    "OPENAI_CODEX_ACCESS_TOKEN",
    "OPENAI_CODEX_REFRESH_TOKEN",
    "OPENAI_CODEX_OAUTH_VERIFIER",
}

OAUTH_CLEAR_KEYS = {
    "OPENAI_CODEX_ACCESS_TOKEN",
    "OPENAI_CODEX_REFRESH_TOKEN",
    "OPENAI_CODEX_EXPIRES_AT",
    "OPENAI_CODEX_ACCOUNT_ID",
    "OPENAI_CODEX_OAUTH_STATE",
    "OPENAI_CODEX_OAUTH_VERIFIER",
    "OPENAI_CODEX_OAUTH_CREATED_AT",
}


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _decode_b64url(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def _decode_jwt(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Ungültiges OpenAI OAuth Token")
    return json.loads(_decode_b64url(parts[1]).decode("utf-8"))


def _account_id_from_token(token: str) -> str:
    payload = _decode_jwt(token)
    auth = payload.get(JWT_CLAIM_PATH) if isinstance(payload, dict) else None
    account_id = auth.get("chatgpt_account_id") if isinstance(auth, dict) else None
    if not account_id:
        raise ValueError("OpenAI Account-ID fehlt im Token")
    return str(account_id)


def _post_token(params: dict[str, str]) -> dict[str, Any]:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(TOKEN_URL, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        raise RuntimeError(f"OpenAI OAuth HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI OAuth Netzwerkfehler: {exc}") from exc


def _store(user_email: str, values: dict[str, str], secret_keys: set[str] | None = None) -> None:
    db.set_user_settings(user_email, values, secret_keys=secret_keys or set())


def _get(user_email: str, key: str) -> str:
    return db.get_user_setting(user_email, key, "") or ""


def parse_authorization_input(value: str) -> dict[str, str]:
    raw = (value or "").strip()
    if not raw:
        return {}
    try:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme and parsed.netloc:
            qs = urllib.parse.parse_qs(parsed.query)
            return {
                "code": (qs.get("code") or [""])[0],
                "state": (qs.get("state") or [""])[0],
            }
    except Exception:
        pass
    if "#" in raw and "code=" not in raw:
        code, state = raw.split("#", 1)
        return {"code": code.strip(), "state": state.strip()}
    if "code=" in raw:
        qs = urllib.parse.parse_qs(raw)
        return {
            "code": (qs.get("code") or [""])[0],
            "state": (qs.get("state") or [""])[0],
        }
    return {"code": raw, "state": ""}


def create_auth_url(user_email: str) -> dict[str, Any]:
    if not user_email:
        return {"ok": False, "error": "Nicht eingeloggt"}
    verifier = secrets.token_urlsafe(64)
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = secrets.token_urlsafe(24)
    _store(user_email, {
        "OPENAI_CODEX_OAUTH_STATE": state,
        "OPENAI_CODEX_OAUTH_VERIFIER": verifier,
        "OPENAI_CODEX_OAUTH_CREATED_AT": str(int(time.time())),
    }, secret_keys={"OPENAI_CODEX_OAUTH_VERIFIER"})
    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "originator": ORIGINATOR,
    }
    return {
        "ok": True,
        "url": f"{AUTHORIZE_URL}?{urllib.parse.urlencode(params)}",
        "redirect_uri": REDIRECT_URI,
        "manual": True,
    }


def finish_auth(user_email: str, authorization_input: str) -> dict[str, Any]:
    if not user_email:
        return {"ok": False, "error": "Nicht eingeloggt"}
    parsed = parse_authorization_input(authorization_input)
    code = parsed.get("code", "")
    state = parsed.get("state", "")
    expected_state = _get(user_email, "OPENAI_CODEX_OAUTH_STATE")
    verifier = _get(user_email, "OPENAI_CODEX_OAUTH_VERIFIER")
    created = _get(user_email, "OPENAI_CODEX_OAUTH_CREATED_AT")
    if not code or not state:
        return {"ok": False, "error": "Bitte vollständige Callback-URL inklusive code und state einfügen."}
    if not expected_state or not verifier:
        return {"ok": False, "error": "OAuth-Start fehlt. Bitte Anmeldung neu starten."}
    if not secrets.compare_digest(state, expected_state):
        return {"ok": False, "error": "OAuth-State stimmt nicht."}
    if created and created.isdigit() and int(time.time()) - int(created) > 900:
        return {"ok": False, "error": "OAuth-Flow abgelaufen. Bitte neu starten."}

    token = _post_token({
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "code_verifier": verifier,
        "redirect_uri": REDIRECT_URI,
    })
    access = str(token.get("access_token", ""))
    refresh = str(token.get("refresh_token", ""))
    expires_in = int(token.get("expires_in", 0) or 0)
    if not access or not refresh or expires_in <= 0:
        return {"ok": False, "error": "OpenAI OAuth Token-Antwort unvollständig."}
    account_id = _account_id_from_token(access)
    expires_at = str(int(time.time()) + expires_in)
    _store(user_email, {
        "AI_AUTH_TYPE": "openai_codex_oauth",
        "AI_BASE_URL": DEFAULT_BASE_URL,
        "AI_MODEL": _get(user_email, "AI_MODEL") or DEFAULT_MODEL,
        "AI_CONTEXT_MAX_TOKENS": _get(user_email, "AI_CONTEXT_MAX_TOKENS") or "272000",
        "OPENAI_CODEX_ACCESS_TOKEN": access,
        "OPENAI_CODEX_REFRESH_TOKEN": refresh,
        "OPENAI_CODEX_EXPIRES_AT": expires_at,
        "OPENAI_CODEX_ACCOUNT_ID": account_id,
        "OPENAI_CODEX_OAUTH_STATE": "",
        "OPENAI_CODEX_OAUTH_VERIFIER": "",
        "OPENAI_CODEX_OAUTH_CREATED_AT": "",
    }, secret_keys=TOKEN_SECRET_KEYS)
    return {"ok": True, "account_id": account_id, "model": _get(user_email, "AI_MODEL") or DEFAULT_MODEL}


def logout(user_email: str) -> dict[str, Any]:
    if not user_email:
        return {"ok": False, "error": "Nicht eingeloggt"}
    values = {key: "" for key in OAUTH_CLEAR_KEYS}
    values["AI_AUTH_TYPE"] = "api_key"
    _store(user_email, values, secret_keys=TOKEN_SECRET_KEYS)
    return {"ok": True}


def status(user_email: str | None) -> dict[str, Any]:
    if not user_email:
        return {"configured": False, "auth_type": "api_key"}
    auth_type = _get(user_email, "AI_AUTH_TYPE") or "api_key"
    configured = oauth_configured(user_email)
    expires_at = _get(user_email, "OPENAI_CODEX_EXPIRES_AT")
    expires_in = int(expires_at) - int(time.time()) if expires_at.isdigit() else 0
    return {
        "configured": configured,
        "auth_type": auth_type,
        "account_id": _get(user_email, "OPENAI_CODEX_ACCOUNT_ID") if configured else "",
        "expires_in": max(0, expires_in),
        "redirect_uri": REDIRECT_URI,
    }


def oauth_configured(user_email: str | None) -> bool:
    if not user_email:
        return False
    return bool(_get(user_email, "OPENAI_CODEX_REFRESH_TOKEN") and _get(user_email, "OPENAI_CODEX_ACCOUNT_ID"))


def _refresh(user_email: str) -> tuple[str, str]:
    refresh_token = _get(user_email, "OPENAI_CODEX_REFRESH_TOKEN")
    if not refresh_token:
        raise RuntimeError("OpenAI OAuth Refresh-Token fehlt")
    token = _post_token({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
    })
    access = str(token.get("access_token", ""))
    refresh = str(token.get("refresh_token", ""))
    expires_in = int(token.get("expires_in", 0) or 0)
    if not access or not refresh or expires_in <= 0:
        raise RuntimeError("OpenAI OAuth Refresh-Antwort unvollständig")
    account_id = _account_id_from_token(access)
    _store(user_email, {
        "OPENAI_CODEX_ACCESS_TOKEN": access,
        "OPENAI_CODEX_REFRESH_TOKEN": refresh,
        "OPENAI_CODEX_EXPIRES_AT": str(int(time.time()) + expires_in),
        "OPENAI_CODEX_ACCOUNT_ID": account_id,
    }, secret_keys=TOKEN_SECRET_KEYS)
    return access, account_id


def get_valid_token(user_email: str) -> tuple[str, str]:
    access = _get(user_email, "OPENAI_CODEX_ACCESS_TOKEN")
    account_id = _get(user_email, "OPENAI_CODEX_ACCOUNT_ID")
    expires_at = _get(user_email, "OPENAI_CODEX_EXPIRES_AT")
    if access and account_id and expires_at.isdigit() and int(expires_at) > int(time.time()) + 60:
        return access, account_id
    return _refresh(user_email)


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: list[str] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                chunks.append(str(block.get("text", "")))
            elif block.get("type") == "image_url":
                chunks.append("[Bildanhang]")
        return "\n".join(c for c in chunks if c)
    return str(content or "")


def _content_to_input_blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    blocks: list[dict[str, Any]] = []
    if isinstance(content, list):
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                blocks.append({"type": "input_text", "text": str(block.get("text", ""))})
            elif block.get("type") == "image_url":
                image_url = block.get("image_url") or {}
                url = image_url.get("url") if isinstance(image_url, dict) else ""
                if url:
                    blocks.append({"type": "input_image", "detail": "auto", "image_url": str(url)})
    return blocks or [{"type": "input_text", "text": _content_to_text(content)}]


def _messages_to_codex(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    idx = 0
    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content", "")
        if role in {"system", "developer"}:
            text = _content_to_text(content).strip()
            if text:
                instructions.append(text)
        elif role == "assistant":
            text = _content_to_text(content).strip()
            if text:
                items.append({
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text, "annotations": []}],
                    "status": "completed",
                    "id": f"msg_{idx}",
                })
        else:
            blocks = _content_to_input_blocks(content)
            if blocks:
                items.append({"role": "user", "content": blocks})
        idx += 1
    return "\n\n".join(instructions) or "Du bist ein hilfreicher Assistent.", items


def _reasoning_effort(value: str) -> str | None:
    effort = (value or "").strip().lower()
    if effort in {"", "off", "none", "disabled"}:
        return None
    if effort == "max":
        return "xhigh"
    if effort in {"minimal", "low", "medium", "high", "xhigh"}:
        return effort
    return None


def _extract_completed_text(response: Any) -> str:
    pieces: list[str] = []

    def walk(obj: Any) -> None:
        if isinstance(obj, dict):
            typ = obj.get("type")
            if typ in {"output_text", "refusal"}:
                text = obj.get("text") or obj.get("refusal")
                if text:
                    pieces.append(str(text))
            for value in obj.values():
                walk(value)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)

    walk(response)
    return "".join(pieces)


def _read_sse_text(resp: Any) -> str:
    pieces: list[str] = []
    fallback = ""
    data_lines: list[str] = []

    def handle_event(data: str) -> None:
        nonlocal fallback
        if not data or data == "[DONE]":
            return
        event = json.loads(data)
        typ = str(event.get("type", ""))
        if typ == "response.output_text.delta":
            pieces.append(str(event.get("delta", "")))
        elif typ in {"response.output_text.done", "response.refusal.done"} and not pieces:
            pieces.append(str(event.get("text") or event.get("refusal") or ""))
        elif typ in {"response.completed", "response.done", "response.incomplete"}:
            if not pieces:
                fallback = _extract_completed_text(event.get("response"))
        elif typ == "response.failed":
            err = ((event.get("response") or {}).get("error") or {}) if isinstance(event.get("response"), dict) else {}
            raise RuntimeError(str(err.get("message") or "OpenAI Codex Antwort fehlgeschlagen"))
        elif typ == "error":
            raise RuntimeError(str(event.get("message") or event.get("code") or "OpenAI Codex Fehler"))

    for raw in resp:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if data_lines:
                handle_event("\n".join(data_lines).strip())
                data_lines = []
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    if data_lines:
        handle_event("\n".join(data_lines).strip())
    return "".join(pieces) or fallback


def codex_chat_completion(
    messages: list[dict[str, Any]],
    user_email: str,
    model: str | None = None,
    think_effort: str = "",
    temperature: float | None = None,
    timeout_seconds: int | None = None,
) -> str:
    token, account_id = get_valid_token(user_email)
    instructions, input_items = _messages_to_codex(messages)
    body: dict[str, Any] = {
        "model": model or DEFAULT_MODEL,
        "store": False,
        "stream": True,
        "instructions": instructions,
        "input": input_items,
        "text": {"verbosity": "low"},
        "include": ["reasoning.encrypted_content"],
        "prompt_cache_key": str(uuid.uuid4()),
        "tool_choice": "auto",
        "parallel_tool_calls": True,
    }
    # ChatGPT/Codex subscription endpoint rejects the OpenAI-compatible
    # `temperature` parameter (HTTP 400: Unsupported parameter). Keep the
    # argument for the generic ai_client interface, but do not send it here.
    effort = _reasoning_effort(think_effort)
    if effort:
        body["reasoning"] = {"effort": effort, "summary": "auto"}

    data = json.dumps(body).encode()

    def build_request(access_token: str, account: str) -> urllib.request.Request:
        request_id = str(uuid.uuid4())
        req = urllib.request.Request(f"{DEFAULT_BASE_URL}/codex/responses", data=data, method="POST")
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("chatgpt-account-id", account)
        req.add_header("originator", ORIGINATOR)
        req.add_header("User-Agent", "pi (assistant)")
        req.add_header("OpenAI-Beta", "responses=experimental")
        req.add_header("accept", "text/event-stream")
        req.add_header("content-type", "application/json")
        req.add_header("session_id", request_id)
        req.add_header("x-client-request-id", request_id)
        return req

    for attempt in range(2):
        req = build_request(token, account_id)
        try:
            with urllib.request.urlopen(req, timeout=timeout_seconds or 240) as resp:
                text = _read_sse_text(resp)
            if not text:
                raise RuntimeError("Leere OpenAI Codex Antwort")
            return text
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")
            if exc.code == 401 and attempt == 0:
                token, account_id = _refresh(user_email)
                continue
            raise RuntimeError(f"OpenAI Codex HTTP {exc.code}: {body_text}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenAI Codex Netzwerkfehler: {exc}") from exc
    raise RuntimeError("OpenAI Codex Auth fehlgeschlagen")
