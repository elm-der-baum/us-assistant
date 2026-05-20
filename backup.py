#!/usr/bin/env python3
"""Area backups before mutating Google data."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import google_client as gc

BASE_DIR = Path(__file__).resolve().parent
BACKUP_DIR = BASE_DIR / "data" / "backups"
KEEP_PER_AREA = 30


def _email_dir(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:24]


def _area_dir(email: str, area: str) -> Path:
    return BACKUP_DIR / _email_dir(email) / area


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def area_for_action(action_type: str) -> str | None:
    if action_type.endswith("_task") or action_type.endswith("_tasklist") or action_type in {"create_task", "update_task", "complete_task", "delete_task"}:
        return "tasks"
    if action_type.endswith("_calendar_event"):
        return "calendar"
    return None


def create_backup(area: str, email: str, reason: str = "", action: dict[str, Any] | None = None) -> dict[str, Any]:
    if area not in {"tasks", "calendar"}:
        raise ValueError(f"Unbekannter Backup-Bereich: {area}")
    data = _snapshot_tasks(email) if area == "tasks" else _snapshot_calendar(email)
    backup_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    doc = {
        "id": backup_id,
        "area": area,
        "email": email.strip().lower(),
        "created_at": _now(),
        "reason": reason,
        "action": action or {},
        "data": data,
    }
    path = _area_dir(email, area) / f"{backup_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    _prune(email, area)
    return _meta(doc)


def list_backups(area: str, email: str) -> list[dict[str, Any]]:
    path = _area_dir(email, area)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for file in sorted(path.glob("*.json"), reverse=True):
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
            out.append(_meta(doc))
        except Exception:
            continue
    return out


def apply_backup(area: str, backup_id: str, email: str) -> dict[str, Any]:
    path = _area_dir(email, area) / f"{backup_id}.json"
    if not path.is_file():
        return {"ok": False, "error": "Backup nicht gefunden"}
    doc = json.loads(path.read_text(encoding="utf-8"))
    # Safety backup directly before restore, because restore mutates data too.
    safety = create_backup(area, email, reason=f"Vor Restore von {backup_id}", action={"type": "restore_backup", "backup_id": backup_id})
    if area == "tasks":
        result = _restore_tasks(email, doc.get("data", {}))
    elif area == "calendar":
        result = _restore_calendar(email, doc.get("data", {}))
    else:
        return {"ok": False, "error": f"Unbekannter Bereich: {area}"}
    return {"ok": not _has_error(result), "backup_id": backup_id, "safety_backup": safety, "result": result}


def _meta(doc: dict[str, Any]) -> dict[str, Any]:
    data = doc.get("data", {}) if isinstance(doc.get("data"), dict) else {}
    counts = data.get("counts", {}) if isinstance(data.get("counts"), dict) else {}
    return {
        "id": doc.get("id", ""),
        "area": doc.get("area", ""),
        "created_at": doc.get("created_at", ""),
        "reason": doc.get("reason", ""),
        "action_type": (doc.get("action") or {}).get("type", ""),
        "counts": counts,
    }


def _prune(email: str, area: str) -> None:
    files = sorted(_area_dir(email, area).glob("*.json"), reverse=True)
    for old in files[KEEP_PER_AREA:]:
        try:
            old.unlink()
        except OSError:
            pass


def _snapshot_tasks(email: str) -> dict[str, Any]:
    lists = gc.list_tasklists(email=email).get("items", [])
    tasklists: list[dict[str, Any]] = []
    task_count = 0
    for tl in lists:
        tl_id = str(tl.get("id", ""))
        if not tl_id:
            continue
        tasks = gc.list_tasks(tl_id, max_results=500, show_completed=True, email=email).get("items", [])
        task_count += len(tasks)
        tasklists.append({"id": tl_id, "title": tl.get("title", tl_id), "tasks": tasks})
    return {"version": 1, "tasklists": tasklists, "counts": {"tasklists": len(tasklists), "tasks": task_count}}


def _restore_tasks(email: str, data: dict[str, Any]) -> dict[str, Any]:
    desired_lists = data.get("tasklists", []) if isinstance(data, dict) else []
    current_lists = gc.list_tasklists(email=email).get("items", [])
    by_id = {str(tl.get("id", "")): tl for tl in current_lists}
    by_title = {str(tl.get("title", "")).strip().lower(): tl for tl in current_lists}
    keep_list_ids: set[str] = set()
    stats = {"tasklists_created": 0, "tasklists_updated": 0, "tasklists_deleted": 0, "tasks_created": 0, "tasks_updated": 0, "tasks_deleted": 0, "errors": []}

    for desired in desired_lists:
        old_id = str(desired.get("id", ""))
        title = str(desired.get("title", old_id) or old_id)
        current = by_id.get(old_id) or by_title.get(title.strip().lower())
        if current:
            tl_id = str(current.get("id", ""))
            if title and current.get("title") != title:
                res = gc.update_tasklist(tl_id, title, email=email)
                if _has_error(res):
                    stats["errors"].append(res)
                else:
                    stats["tasklists_updated"] += 1
        else:
            res = gc.create_tasklist(title, email=email)
            if _has_error(res):
                stats["errors"].append(res)
                continue
            tl_id = str(res.get("id", ""))
            stats["tasklists_created"] += 1
        if not tl_id:
            continue
        keep_list_ids.add(tl_id)
        _restore_tasks_in_list(email, tl_id, desired.get("tasks", []), stats)

    # Delete current lists that are not present in backup. Google may reject deleting the default list; keep error in stats.
    for tl in current_lists:
        tl_id = str(tl.get("id", ""))
        if tl_id and tl_id not in keep_list_ids:
            res = gc.delete_tasklist(tl_id, email=email)
            if _has_error(res):
                stats["errors"].append(res)
            else:
                stats["tasklists_deleted"] += 1
    return stats


def _restore_tasks_in_list(email: str, tasklist_id: str, desired_tasks: list[dict[str, Any]], stats: dict[str, Any]) -> None:
    current = gc.list_tasks(tasklist_id, max_results=500, show_completed=True, email=email).get("items", [])
    current_by_id = {str(t.get("id", "")): t for t in current}
    keep_ids: set[str] = set()
    allowed = {"title", "notes", "status", "due"}
    for task in desired_tasks:
        old_id = str(task.get("id", ""))
        payload = {k: v for k, v in task.items() if k in allowed and v not in (None, "")}
        if old_id in current_by_id:
            res = gc.update_task(tasklist_id, old_id, payload, email=email)
            keep_ids.add(old_id)
            if _has_error(res):
                stats["errors"].append(res)
            else:
                stats["tasks_updated"] += 1
        else:
            res = gc.create_task(tasklist_id, payload, email=email)
            if _has_error(res):
                stats["errors"].append(res)
            else:
                keep_ids.add(str(res.get("id", "")))
                stats["tasks_created"] += 1
    for task in current:
        task_id = str(task.get("id", ""))
        if task_id and task_id not in keep_ids:
            res = gc.delete_task(tasklist_id, task_id, email=email)
            if _has_error(res):
                stats["errors"].append(res)
            else:
                stats["tasks_deleted"] += 1


def _snapshot_calendar(email: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    time_min = (now - timedelta(days=365)).isoformat()
    time_max = (now + timedelta(days=365)).isoformat()
    calendars = gc.list_calendars(email=email).get("items", [])
    out: list[dict[str, Any]] = []
    event_count = 0
    for cal in calendars:
        cal_id = str(cal.get("id", ""))
        if not cal_id:
            continue
        events = gc.list_events(calendar_id=cal_id, time_min=time_min, time_max=time_max, max_results=2500, email=email).get("items", [])
        event_count += len(events)
        out.append({"id": cal_id, "summary": cal.get("summary", cal_id), "primary": bool(cal.get("primary")), "events": events})
    return {"version": 1, "range": {"time_min": time_min, "time_max": time_max}, "calendars": out, "counts": {"calendars": len(out), "events": event_count}}


def _restore_calendar(email: str, data: dict[str, Any]) -> dict[str, Any]:
    stats = {"events_created": 0, "events_updated": 0, "events_deleted": 0, "errors": []}
    allowed_skip = {"kind", "etag", "id", "htmlLink", "created", "updated", "creator", "organizer", "iCalUID", "sequence", "reminders", "eventType"}
    for cal in data.get("calendars", []):
        cal_id = str(cal.get("id", "primary")) or "primary"
        desired_events = cal.get("events", []) or []
        rng = data.get("range", {})
        current = gc.list_events(calendar_id=cal_id, time_min=str(rng.get("time_min", "")), time_max=str(rng.get("time_max", "")), max_results=2500, email=email).get("items", [])
        current_by_id = {str(ev.get("id", "")): ev for ev in current}
        keep_ids: set[str] = set()
        for ev in desired_events:
            ev_id = str(ev.get("id", ""))
            payload = {k: v for k, v in ev.items() if k not in allowed_skip}
            if ev_id and ev_id in current_by_id:
                res = gc.update_event(cal_id, ev_id, payload, email=email)
                keep_ids.add(ev_id)
                if _has_error(res):
                    stats["errors"].append(res)
                else:
                    stats["events_updated"] += 1
            else:
                res = gc.create_event(cal_id, payload, email=email)
                if _has_error(res):
                    stats["errors"].append(res)
                else:
                    keep_ids.add(str(res.get("id", "")))
                    stats["events_created"] += 1
        for ev in current:
            ev_id = str(ev.get("id", ""))
            if ev_id and ev_id not in keep_ids:
                res = gc.delete_event(cal_id, ev_id, email=email)
                if _has_error(res):
                    stats["errors"].append(res)
                else:
                    stats["events_deleted"] += 1
    return stats


def _has_error(result: Any) -> bool:
    return not isinstance(result, dict) or "error" in result or result.get("ok") is False
