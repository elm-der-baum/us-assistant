#!/usr/bin/env python3
"""Safe Mode: all write actions require explicit approval."""

from __future__ import annotations

from typing import Any

import db
import google_client as gc

ALLOWED_ACTIONS = {
    "create_calendar_event",
    "update_calendar_event",
    "delete_calendar_event",
    "create_task",
    "update_task",
    "complete_task",
    "delete_task",
    "create_tasklist",
    "update_tasklist",
    "delete_tasklist",
}


def create(action_type: str, title: str, payload: dict[str, Any], source: str = "web", user_email: str | None = None) -> dict[str, Any]:
    if action_type not in ALLOWED_ACTIONS:
        raise ValueError(f"Unbekannte Aktion: {action_type}")
    return db.create_pending_action(action_type, title, payload, source=source, user_email=user_email)


def list_pending(include_done: bool = False, user_email: str | None = None) -> list[dict[str, Any]]:
    return db.list_pending_actions(include_done=include_done, user_email=user_email)


def approve(action_id: str, user_email: str | None = None) -> dict[str, Any]:
    action = db.get_pending_action(action_id, user_email=user_email)
    if not action:
        return {"ok": False, "error": "Aktion nicht gefunden"}
    if action["status"] != "pending":
        return {"ok": False, "error": f"Aktion ist nicht pending: {action['status']}"}

    try:
        result = execute(action, user_email=user_email)
    except Exception as exc:
        result = {"error": str(exc)}
    if _is_error(result):
        err = _error_text(result)
        db.update_pending_action(action_id, "error", result=result, error=err, user_email=user_email)
        return {"ok": False, "error": err, "action": db.get_pending_action(action_id, user_email=user_email)}

    updated = db.update_pending_action(action_id, "done", result=result, user_email=user_email)
    return {"ok": True, "result": result, "action": updated}


def reject(action_id: str, user_email: str | None = None) -> dict[str, Any]:
    action = db.get_pending_action(action_id, user_email=user_email)
    if not action:
        return {"ok": False, "error": "Aktion nicht gefunden"}
    updated = db.update_pending_action(action_id, "rejected", user_email=user_email)
    return {"ok": True, "action": updated}


def execute(action: dict[str, Any], user_email: str | None = None) -> dict[str, Any]:
    action_type = action["type"]
    payload = dict(action.get("payload") or {})

    if action_type == "create_calendar_event":
        calendar_id = payload.pop("calendar_id", "primary")
        return gc.create_event(str(calendar_id), payload, email=user_email)

    if action_type == "update_calendar_event":
        calendar_id = payload.pop("calendar_id", "primary")
        event_id = str(payload.pop("event_id", ""))
        if not event_id:
            return {"error": "event_id fehlt"}
        return gc.update_event(str(calendar_id), event_id, payload, email=user_email)

    if action_type == "delete_calendar_event":
        calendar_id = str(payload.get("calendar_id", "primary"))
        event_id = str(payload.get("event_id", ""))
        if not event_id:
            return {"error": "event_id fehlt"}
        return gc.delete_event(calendar_id, event_id, email=user_email)

    if action_type == "create_task":
        tasklist_id = _resolve_tasklist_id(payload, user_email=user_email)
        _strip_tasklist_helpers(payload)
        return gc.create_task(tasklist_id, payload, email=user_email)

    if action_type == "update_task":
        tasklist_id = _resolve_tasklist_id(payload, user_email=user_email)
        _strip_tasklist_helpers(payload)
        task_id = str(payload.pop("task_id", ""))
        if not task_id:
            return {"error": "task_id fehlt"}
        return gc.update_task(tasklist_id, task_id, payload, email=user_email)

    if action_type == "complete_task":
        tasklist_id = _resolve_tasklist_id(payload, user_email=user_email)
        _strip_tasklist_helpers(payload)
        task_id = str(payload.pop("task_id", ""))
        if not task_id:
            return {"error": "task_id fehlt"}
        return gc.complete_task(tasklist_id, task_id, email=user_email)

    if action_type == "delete_task":
        tasklist_id = _resolve_tasklist_id(payload, user_email=user_email)
        _strip_tasklist_helpers(payload)
        task_id = str(payload.pop("task_id", ""))
        if not task_id:
            return {"error": "task_id fehlt"}
        return gc.delete_task(tasklist_id, task_id, email=user_email)

    if action_type == "create_tasklist":
        title = str(payload.get("title", "")).strip()
        if not title:
            return {"error": "title fehlt"}
        return gc.create_tasklist(title, email=user_email)

    if action_type == "update_tasklist":
        tasklist_id = _resolve_tasklist_id(payload, user_email=user_email, required=True)
        title = str(payload.get("title", "")).strip()
        if not title:
            return {"error": "title fehlt"}
        return gc.update_tasklist(tasklist_id, title, email=user_email)

    if action_type == "delete_tasklist":
        tasklist_id = _resolve_tasklist_id(payload, user_email=user_email, required=True)
        return gc.delete_tasklist(tasklist_id, email=user_email)

    return {"error": f"Unbekannte Aktion: {action_type}"}


def _strip_tasklist_helpers(payload: dict[str, Any]) -> None:
    for key in ("tasklist_id", "tasklist_title", "tasklist", "list_title", "list_name"):
        payload.pop(key, None)


def _resolve_tasklist_id(payload: dict[str, Any], user_email: str | None = None, required: bool = False) -> str:
    explicit = str(payload.get("tasklist_id", "")).strip()
    if explicit:
        return explicit

    wanted = str(payload.get("tasklist_title") or payload.get("tasklist") or payload.get("list_title") or payload.get("list_name") or "").strip().lower()
    if wanted:
        tasklists = gc.list_tasklists(email=user_email).get("items", [])
        matches = [tl for tl in tasklists if str(tl.get("title", "")).strip().lower() == wanted]
        if len(matches) == 1:
            return str(matches[0].get("id", ""))
        contains = [tl for tl in tasklists if wanted in str(tl.get("title", "")).strip().lower()]
        if len(contains) == 1:
            return str(contains[0].get("id", ""))
        if required:
            raise ValueError(f"Aufgabenliste nicht eindeutig gefunden: {wanted}")

    fallback = gc.get_tasklist_id(email=user_email) or "@default"
    if required and not fallback:
        raise ValueError("tasklist_id fehlt")
    return str(fallback)


def _is_error(result: dict[str, Any]) -> bool:
    if not isinstance(result, dict):
        return True
    if "error" in result:
        return True
    if result.get("ok") is False:
        return True
    return False


def _error_text(result: dict[str, Any]) -> str:
    err = result.get("error") if isinstance(result, dict) else result
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err or "Unbekannter Fehler")
