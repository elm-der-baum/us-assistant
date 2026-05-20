#!/usr/bin/env python3
"""Google OAuth2 + Calendar/Tasks API client – pure stdlib."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from db import get_setting, set_setting

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
OAUTH_CALLBACK = "https://findyou.biz/assistant/oauth/callback"
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_CALENDAR_API = "https://www.googleapis.com/calendar/v3"
GOOGLE_TASKS_API = "https://tasks.googleapis.com/tasks/v1"
GOOGLE_USERINFO = "https://www.googleapis.com/oauth2/v1/userinfo?alt=json"

SCOPES = [
    "openid",
    "email",
    "profile",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks.readonly",
    "https://www.googleapis.com/auth/tasks",
]


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------
def app_configured() -> bool:
    return bool(get_setting("GOOGLE_CLIENT_ID", "") and get_setting("GOOGLE_CLIENT_SECRET", ""))


def get_oauth_url(state: str = "assistant") -> str:
    client_id = get_setting("GOOGLE_CLIENT_ID", "") or ""
    if not client_id:
        return ""
    params = {
        "client_id": client_id,
        "redirect_uri": OAUTH_CALLBACK,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(auth_code: str) -> dict[str, Any]:
    data = {
        "code": auth_code,
        "client_id": get_setting("GOOGLE_CLIENT_ID", "") or "",
        "client_secret": get_setting("GOOGLE_CLIENT_SECRET", "") or "",
        "redirect_uri": OAUTH_CALLBACK,
        "grant_type": "authorization_code",
    }
    return _token_request(data)


def refresh_token(refresh_token_val: str) -> dict[str, Any]:
    data = {
        "client_id": get_setting("GOOGLE_CLIENT_ID", "") or "",
        "client_secret": get_setting("GOOGLE_CLIENT_SECRET", "") or "",
        "refresh_token": refresh_token_val,
        "grant_type": "refresh_token",
    }
    return _token_request(data)


def _token_request(data: dict[str, str]) -> dict[str, Any]:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(GOOGLE_TOKEN_URL, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        try:
            return json.loads(body_text)
        except Exception:
            return {"error": body_text, "status": exc.code}
    except Exception as exc:
        return {"error": str(exc)}


def get_valid_token(email: str | None = None) -> str | None:
    """Return a valid access token. If email is set, use that Google user."""
    if email:
        from db import get_user, update_user_token
        user = get_user(email)
        token_json = str(user.get("google_token_json", "")) if user else ""
    else:
        # legacy fallback; new login flow stores tokens per user
        user = None
        token_json = get_setting("GOOGLE_TOKEN_JSON", "") or ""

    if not token_json:
        return None
    try:
        token = json.loads(token_json)
    except json.JSONDecodeError:
        return None

    expires_at = float(token.get("expires_at", 0) or 0)
    if expires_at - time.time() < 60:
        refresh_val = token.get("refresh_token", "")
        if not refresh_val:
            return None
        new_token = refresh_token(str(refresh_val))
        if "error" in new_token or "access_token" not in new_token:
            return None
        new_token.setdefault("refresh_token", refresh_val)
        new_token["expires_in"] = new_token.get("expires_in", 3600)
        new_token["expires_at"] = time.time() + int(new_token["expires_in"])
        new_token_json = json.dumps(new_token, ensure_ascii=False)
        if email:
            update_user_token(email, new_token_json)
        else:
            set_setting("GOOGLE_TOKEN_JSON", new_token_json, is_secret=True)
        return str(new_token["access_token"])

    return str(token.get("access_token", ""))


def get_user_info(access_token: str) -> dict[str, Any]:
    req = urllib.request.Request(GOOGLE_USERINFO)
    req.add_header("Authorization", f"Bearer {access_token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        try:
            return json.loads(body_text)
        except Exception:
            return {"error": body_text, "status": exc.code}
    except Exception as exc:
        return {"error": str(exc)}


# ---------------------------------------------------------------------------
# Low-level API helpers
# ---------------------------------------------------------------------------
def _google_get(endpoint: str, params: dict[str, str] | None = None, email: str | None = None) -> dict[str, Any]:
    token = get_valid_token(email)
    if not token:
        return {"error": "no_valid_token"}
    url = endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        try:
            return json.loads(body_text)
        except Exception:
            return {"error": body_text, "status": exc.code}


def _google_post(endpoint: str, body: dict[str, Any], email: str | None = None) -> dict[str, Any]:
    token = get_valid_token(email)
    if not token:
        return {"error": "no_valid_token"}
    data = json.dumps(body).encode()
    req = urllib.request.Request(endpoint, data=data, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        try:
            return json.loads(body_text)
        except Exception:
            return {"error": body_text, "status": exc.code}


def _google_patch(endpoint: str, body: dict[str, Any], email: str | None = None) -> dict[str, Any]:
    token = get_valid_token(email)
    if not token:
        return {"error": "no_valid_token"}
    data = json.dumps(body).encode()
    req = urllib.request.Request(endpoint, data=data, method="PATCH")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        try:
            return json.loads(body_text)
        except Exception:
            return {"error": body_text, "status": exc.code}


def _google_delete(endpoint: str, email: str | None = None) -> dict[str, Any]:
    token = get_valid_token(email)
    if not token:
        return {"error": "no_valid_token"}
    req = urllib.request.Request(endpoint, method="DELETE")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {"status": "ok"}
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        try:
            return json.loads(body_text)
        except Exception:
            return {"error": body_text, "status": exc.code}


# ---------------------------------------------------------------------------
# Calendar API
# ---------------------------------------------------------------------------
def list_calendars(email: str | None = None) -> dict[str, Any]:
    return _google_get(f"{GOOGLE_CALENDAR_API}/users/me/calendarList", email=email)


def get_primary_calendar_id(email: str | None = None) -> str:
    cals = list_calendars(email=email)
    for item in cals.get("items", []):
        if item.get("primary"):
            return str(item.get("id", "primary"))
    return "primary"


def list_events(
    calendar_id: str = "primary",
    time_min: str = "",
    time_max: str = "",
    max_results: int = 100,
    email: str | None = None,
) -> dict[str, Any]:
    params = {
        "singleEvents": "true",
        "orderBy": "startTime",
        "maxResults": str(max_results),
    }
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    return _google_get(f"{GOOGLE_CALENDAR_API}/calendars/{calendar_id}/events", params, email=email)


def create_event(calendar_id: str = "primary", payload: dict[str, Any] | None = None, email: str | None = None) -> dict[str, Any]:
    return _google_post(f"{GOOGLE_CALENDAR_API}/calendars/{calendar_id}/events", payload or {}, email=email)


def update_event(calendar_id: str, event_id: str, payload: dict[str, Any], email: str | None = None) -> dict[str, Any]:
    return _google_patch(f"{GOOGLE_CALENDAR_API}/calendars/{calendar_id}/events/{event_id}", payload, email=email)


def delete_event(calendar_id: str, event_id: str, email: str | None = None) -> dict[str, Any]:
    return _google_delete(f"{GOOGLE_CALENDAR_API}/calendars/{calendar_id}/events/{event_id}", email=email)


# ---------------------------------------------------------------------------
# Tasks API
# ---------------------------------------------------------------------------
def list_tasklists(email: str | None = None) -> dict[str, Any]:
    return _google_get(f"{GOOGLE_TASKS_API}/users/@me/lists", email=email)


def get_tasklist_id(email: str | None = None) -> str | None:
    tasklists = list_tasklists(email=email)
    items = tasklists.get("items", [])
    if items:
        return str(items[0].get("id", ""))
    return None


def list_tasks(tasklist_id: str, max_results: int = 100, show_completed: bool = False, email: str | None = None) -> dict[str, Any]:
    params = {"maxResults": str(max_results)}
    if not show_completed:
        params["showCompleted"] = "false"
    return _google_get(f"{GOOGLE_TASKS_API}/lists/{tasklist_id}/tasks", params, email=email)


def list_all_tasks(max_per_list: int = 100, show_completed: bool = False, email: str | None = None) -> list[dict[str, Any]]:
    """Aggregate tasks from ALL tasklists, each annotated with its list title."""
    all_tasks: list[dict[str, Any]] = []
    tasklists = list_tasklists(email=email)
    for tl in tasklists.get("items", []):
        tl_id = str(tl.get("id", ""))
        tl_title = str(tl.get("title", tl_id))
        if not tl_id:
            continue
        result = list_tasks(tl_id, max_results=max_per_list, show_completed=show_completed, email=email)
        for t in result.get("items", []):
            t["_tasklist_title"] = tl_title
            t["_tasklist_id"] = tl_id
            all_tasks.append(t)
    return all_tasks


def create_task(tasklist_id: str, payload: dict[str, Any], email: str | None = None) -> dict[str, Any]:
    return _google_post(f"{GOOGLE_TASKS_API}/lists/{tasklist_id}/tasks", payload, email=email)


def create_tasklist(title: str, email: str | None = None) -> dict[str, Any]:
    return _google_post(f"{GOOGLE_TASKS_API}/users/@me/lists", {"title": title}, email=email)


def update_task(tasklist_id: str, task_id: str, payload: dict[str, Any], email: str | None = None) -> dict[str, Any]:
    return _google_patch(f"{GOOGLE_TASKS_API}/lists/{tasklist_id}/tasks/{task_id}", payload, email=email)


def complete_task(tasklist_id: str, task_id: str, email: str | None = None) -> dict[str, Any]:
    return update_task(tasklist_id, task_id, {"status": "completed"}, email=email)


def delete_task(tasklist_id: str, task_id: str, email: str | None = None) -> dict[str, Any]:
    return _google_delete(f"{GOOGLE_TASKS_API}/lists/{tasklist_id}/tasks/{task_id}", email=email)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------
def test_connection(email: str | None = None) -> dict[str, Any]:
    """Test Google connection – fetches user profile to verify auth."""
    token = get_valid_token(email)
    if not token:
        return {"ok": False, "error": "Nicht authentifiziert. Bitte zuerst mit Google verbinden."}
    data = get_user_info(token)
    if data.get("error"):
        return {"ok": False, "error": str(data.get("error"))}
    return {"ok": True, "email": data.get("email", email or "unbekannt")}
