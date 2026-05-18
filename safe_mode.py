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

    result = execute(action, user_email=user_email)
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
        tasklist_id = str(payload.pop("tasklist_id", "") or gc.get_tasklist_id(email=user_email) or "@default")
        return gc.create_task(tasklist_id, payload, email=user_email)

    if action_type == "update_task":
        tasklist_id = str(payload.pop("tasklist_id", "") or gc.get_tasklist_id(email=user_email) or "@default")
        task_id = str(payload.pop("task_id", ""))
        if not task_id:
            return {"error": "task_id fehlt"}
        return gc.update_task(tasklist_id, task_id, payload, email=user_email)

    if action_type == "complete_task":
        tasklist_id = str(payload.pop("tasklist_id", "") or gc.get_tasklist_id(email=user_email) or "@default")
        task_id = str(payload.pop("task_id", ""))
        if not task_id:
            return {"error": "task_id fehlt"}
        return gc.complete_task(tasklist_id, task_id, email=user_email)

    if action_type == "delete_task":
        tasklist_id = str(payload.pop("tasklist_id", "") or gc.get_tasklist_id(email=user_email) or "@default")
        task_id = str(payload.pop("task_id", ""))
        if not task_id:
            return {"error": "task_id fehlt"}
        return gc.delete_task(tasklist_id, task_id, email=user_email)

    return {"error": f"Unbekannte Aktion: {action_type}"}


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
