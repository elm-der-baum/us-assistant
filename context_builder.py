#!/usr/bin/env python3
"""Shared Google context for Web and Telegram assistant channels."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import google_client as gc


def build_google_context(
    email: str,
    calendar_days: int = 14,
    calendar_limit: int = 20,
    tasks_per_list: int = 30,
    fetch_tasks_per_list: int = 100,
) -> str:
    """Build one canonical context so Web and Telegram see the same Google data.

    Important: tasks are fetched per task list, not from a globally sliced flat list.
    This prevents later/new lists from looking empty when earlier lists already fill
    the global task limit.
    """
    berlin = ZoneInfo("Europe/Berlin")
    now = datetime.now(berlin)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=calendar_days)).isoformat()

    events = gc.list_events(time_min=time_min, time_max=time_max, max_results=calendar_limit, email=email)
    tasklists_data = gc.list_tasklists(email=email)
    tasklists = tasklists_data.get("items", []) if isinstance(tasklists_data, dict) else []

    parts = [f"== Kalender (kommende {calendar_days} Tage) =="]
    if isinstance(events, dict) and events.get("error"):
        parts.append(f"Kalender-Fehler: {events.get('error')}")
    else:
        event_items = events.get("items", []) if isinstance(events, dict) else []
        if not event_items:
            parts.append("Keine Termine im Zeitraum.")
        for ev in event_items[:calendar_limit]:
            start = ev.get("start", {}).get("dateTime", ev.get("start", {}).get("date", "?"))
            end = ev.get("end", {}).get("dateTime", ev.get("end", {}).get("date", "?"))
            parts.append(f"- {ev.get('summary','')}: {start} → {end} (event_id: {ev.get('id','')})")

    parts.append("== Aufgabenlisten ==")
    if isinstance(tasklists_data, dict) and tasklists_data.get("error"):
        parts.append(f"Aufgabenlisten-Fehler: {tasklists_data.get('error')}")
    elif not tasklists:
        parts.append("Keine Aufgabenlisten gefunden.")
    else:
        for tl in tasklists:
            parts.append(f"- {tl.get('title','')} (tasklist_id: {tl.get('id','')})")

    parts.append("== Todos nach Liste ==")
    for tl in tasklists:
        tl_id = str(tl.get("id", ""))
        tl_title = str(tl.get("title", tl_id))
        if not tl_id:
            continue

        result = gc.list_tasks(
            tl_id,
            max_results=fetch_tasks_per_list,
            show_completed=True,
            email=email,
        )
        if isinstance(result, dict) and result.get("error"):
            parts.append(f"\n### {tl_title} (tasklist_id: {tl_id}; Fehler beim Laden)")
            parts.append(f"- Fehler: {result.get('error')}")
            continue

        tasks = result.get("items", []) if isinstance(result, dict) else []
        open_count = sum(1 for t in tasks if t.get("status") != "completed")
        total_count = len(tasks)
        shown = tasks[:tasks_per_list]
        suffix = f"; zeige {len(shown)}/{total_count}" if total_count > len(shown) else ""
        parts.append(f"\n### {tl_title} (tasklist_id: {tl_id}; {open_count} offen, {total_count} gesamt{suffix})")
        if not tasks:
            parts.append("- Keine Todos in dieser Liste.")
            continue
        for t in shown:
            status = t.get("status", "needsAction")
            status_label = "☐" if status != "completed" else "☑"
            due = f"; due: {t.get('due')}" if t.get("due") else ""
            notes = str(t.get("notes", "")).strip().replace("\n", " ")
            notes = f"; notes: {notes[:120]}" if notes else ""
            parts.append(f"- {status_label} {t.get('title','')} (task_id: {t.get('id','')}; tasklist_id: {tl_id}{due}{notes})")

    return "\n".join(parts)
