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
    "move_task",
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
        backup_meta = _backup_before(action, user_email=user_email)
        result = execute(action, user_email=user_email)
        if isinstance(result, dict) and backup_meta:
            result.setdefault("backup", backup_meta)
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


def approve_all(user_email: str | None = None) -> dict[str, Any]:
    actions = db.list_pending_actions(include_done=False, limit=500, user_email=user_email)
    if not actions:
        return {"ok": True, "approved": 0, "failed": 0, "results": [], "backups": []}

    try:
        backups = _backup_for_batch(actions, user_email=user_email)
    except Exception as exc:
        return {"ok": False, "error": f"Backup fehlgeschlagen: {exc}", "approved": 0, "failed": len(actions), "results": [], "backups": []}

    results: list[dict[str, Any]] = []
    approved = 0
    failed = 0
    for action in actions:
        action_id = str(action.get("id", ""))
        try:
            result = execute(action, user_email=user_email)
            area = _area_for_action(action)
            if isinstance(result, dict) and area and area in backups:
                result.setdefault("backup", backups[area])
        except Exception as exc:
            result = {"error": str(exc)}

        if _is_error(result):
            failed += 1
            err = _error_text(result)
            updated = db.update_pending_action(action_id, "error", result=result, error=err, user_email=user_email)
            results.append({"ok": False, "id": action_id, "error": err, "action": updated})
        else:
            approved += 1
            updated = db.update_pending_action(action_id, "done", result=result, user_email=user_email)
            results.append({"ok": True, "id": action_id, "result": result, "action": updated})

    return {"ok": failed == 0, "approved": approved, "failed": failed, "results": results, "backups": list(backups.values())}


def _area_for_action(action: dict[str, Any]) -> str | None:
    import backup
    return backup.area_for_action(str(action.get("type", "")))


def _backup_for_batch(actions: list[dict[str, Any]], user_email: str | None = None) -> dict[str, dict[str, Any]]:
    if not user_email:
        return {}
    import backup
    areas = sorted({area for action in actions if (area := backup.area_for_action(str(action.get("type", ""))))})
    out: dict[str, dict[str, Any]] = {}
    for area in areas:
        area_actions = [a for a in actions if backup.area_for_action(str(a.get("type", ""))) == area]
        reason = f"Batch-Freigabe ({len(area_actions)} Aktionen)"
        out[area] = backup.create_backup(area, user_email, reason=reason, action={"type": "approve_all", "count": len(area_actions), "actions": [{"id": a.get("id"), "type": a.get("type"), "title": a.get("title")} for a in area_actions]})
    return out


def _backup_before(action: dict[str, Any], user_email: str | None = None) -> dict[str, Any] | None:
    if not user_email:
        return None
    import backup
    area = backup.area_for_action(str(action.get("type", "")))
    if not area:
        return None
    return backup.create_backup(area, user_email, reason=str(action.get("title", "")), action=action)


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

    if action_type == "move_task":
        return _move_task(payload, user_email=user_email)

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


def _move_task(payload: dict[str, Any], user_email: str | None = None) -> dict[str, Any]:
    if not any(payload.get(k) for k in ("source_tasklist_id", "from_tasklist_id", "source_tasklist_title", "from_tasklist_title", "source_list_title", "from_list_title", "tasklist_id", "tasklist_title", "list_title")):
        found = _find_unique_task_location(payload, user_email=user_email)
        if found:
            payload.setdefault("source_tasklist_id", found["tasklist_id"])
            payload.setdefault("task_id", found["task_id"])

    source_tasklist_id = _resolve_tasklist_id(
        payload,
        user_email=user_email,
        required=True,
        id_keys=("source_tasklist_id", "from_tasklist_id", "tasklist_id"),
        title_keys=("source_tasklist_title", "from_tasklist_title", "source_list_title", "from_list_title", "tasklist_title", "list_title"),
    )
    target_tasklist_id = _resolve_tasklist_id(
        payload,
        user_email=user_email,
        required=True,
        id_keys=("target_tasklist_id", "to_tasklist_id"),
        title_keys=("target_tasklist_title", "to_tasklist_title", "target_list_title", "to_list_title"),
    )
    if source_tasklist_id == target_tasklist_id:
        return {"error": "Quell- und Zielliste sind identisch"}

    task_id = _resolve_task_id(payload, source_tasklist_id, user_email=user_email)
    if not task_id:
        return {"error": "task_id fehlt oder Aufgabe nicht eindeutig gefunden"}

    original = gc.get_task(source_tasklist_id, task_id, email=user_email)
    if _is_error(original):
        return original

    allowed = {"title", "notes", "due", "status"}
    new_payload = {k: v for k, v in original.items() if k in allowed and v not in (None, "")}
    created = gc.create_task(target_tasklist_id, new_payload, email=user_email)
    if _is_error(created):
        return created
    deleted = gc.delete_task(source_tasklist_id, task_id, email=user_email)
    if _is_error(deleted):
        return {"error": "Aufgabe wurde in Zielliste erstellt, aber Löschen in Quellliste ist fehlgeschlagen", "created": created, "delete_result": deleted}
    return {"ok": True, "moved": True, "source_tasklist_id": source_tasklist_id, "target_tasklist_id": target_tasklist_id, "old_task_id": task_id, "new_task": created}


def _find_unique_task_location(payload: dict[str, Any], user_email: str | None = None) -> dict[str, str] | None:
    wanted = str(payload.get("task_title") or payload.get("task") or payload.get("title") or "").strip().lower()
    if not wanted:
        return None
    tasks = gc.list_all_tasks(max_per_list=500, show_completed=True, email=user_email)
    matches = [t for t in tasks if str(t.get("title", "")).strip().lower() == wanted]
    if len(matches) != 1:
        matches = [t for t in tasks if wanted in str(t.get("title", "")).strip().lower()]
    if len(matches) == 1:
        return {"task_id": str(matches[0].get("id", "")), "tasklist_id": str(matches[0].get("_tasklist_id", ""))}
    return None


def _resolve_task_id(payload: dict[str, Any], tasklist_id: str, user_email: str | None = None) -> str:
    explicit = str(payload.get("task_id", "")).strip()
    if explicit:
        return explicit
    wanted = str(payload.get("task_title") or payload.get("task") or payload.get("title") or "").strip().lower()
    if not wanted:
        return ""
    tasks = gc.list_tasks(tasklist_id, max_results=500, show_completed=True, email=user_email).get("items", [])
    matches = [t for t in tasks if str(t.get("title", "")).strip().lower() == wanted]
    if len(matches) == 1:
        return str(matches[0].get("id", ""))
    contains = [t for t in tasks if wanted in str(t.get("title", "")).strip().lower()]
    if len(contains) == 1:
        return str(contains[0].get("id", ""))
    return ""


def _strip_tasklist_helpers(payload: dict[str, Any]) -> None:
    for key in ("tasklist_id", "tasklist_title", "tasklist", "list_title", "list_name", "source_tasklist_id", "source_tasklist_title", "from_tasklist_id", "from_tasklist_title", "target_tasklist_id", "target_tasklist_title", "to_tasklist_id", "to_tasklist_title"):
        payload.pop(key, None)


def _resolve_tasklist_id(
    payload: dict[str, Any],
    user_email: str | None = None,
    required: bool = False,
    id_keys: tuple[str, ...] = ("tasklist_id",),
    title_keys: tuple[str, ...] = ("tasklist_title", "tasklist", "list_title", "list_name"),
    allow_fallback: bool = True,
) -> str:
    explicit = ""
    for key in id_keys:
        explicit = str(payload.get(key, "")).strip()
        if explicit:
            return explicit

    wanted = ""
    for key in title_keys:
        wanted = str(payload.get(key, "")).strip().lower()
        if wanted:
            break
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

    if required or not allow_fallback:
        raise ValueError("tasklist_id fehlt")
    return str(gc.get_tasklist_id(email=user_email) or "@default")


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
