#!/usr/bin/env python3
"""Pi-Agent CLI für Safe-Mode Freigaben im Assistant.

Kann direkt auf die DB zugreifen (lokal auf dem Server) oder via API.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow importing from parent dir
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import safe_mode


def _list_pending(user_email: str | None = None) -> None:
    actions = safe_mode.list_pending(user_email=user_email)
    if not actions:
        print("Keine pending Aktionen.")
        return
    for a in actions:
        print(f"[{a.get('id')}] {a.get('type')} | {a.get('title')} | {a.get('status')}")


def _edit(action_id: str, title: str | None = None, payload_json: str | None = None, action_type: str | None = None, user_email: str | None = None) -> None:
    payload = json.loads(payload_json) if payload_json else None
    result = safe_mode.edit(action_id, title=title, payload=payload, action_type=action_type, user_email=user_email)
    if result.get("ok"):
        print(f"Aktion {action_id} bearbeitet.")
        print(json.dumps(result.get("action"), indent=2, ensure_ascii=False))
    else:
        print(f"Fehler: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def _delete(action_id: str, user_email: str | None = None) -> None:
    result = safe_mode.delete(action_id, user_email=user_email)
    if result.get("ok"):
        print(f"Aktion {action_id} gelöscht.")
    else:
        print(f"Fehler: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def _reject(action_id: str, user_email: str | None = None) -> None:
    result = safe_mode.reject(action_id, user_email=user_email)
    if result.get("ok"):
        print(f"Aktion {action_id} abgelehnt.")
    else:
        print(f"Fehler: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def _approve(action_id: str, user_email: str | None = None) -> None:
    result = safe_mode.approve(action_id, user_email=user_email)
    if result.get("ok"):
        print(f"Aktion {action_id} freigegeben.")
        print(json.dumps(result.get("result"), indent=2, ensure_ascii=False))
    else:
        print(f"Fehler: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Assistant Safe-Mode CLI")
    parser.add_argument("--user-email", default=None, help="Nutzer-Email (optional, für Multi-User)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="Pending Aktionen anzeigen")

    p_edit = sub.add_parser("edit", help="Pending Aktion bearbeiten")
    p_edit.add_argument("id", help="Aktions-ID")
    p_edit.add_argument("--title", default=None, help="Neuer Titel")
    p_edit.add_argument("--payload", default=None, help='Neues Payload als JSON-String, z.B. {"summary":"X"}')
    p_edit.add_argument("--type", default=None, help="Neuer Aktionstyp")

    p_del = sub.add_parser("delete", help="Pending Aktion löschen")
    p_del.add_argument("id", help="Aktions-ID")

    p_rej = sub.add_parser("reject", help="Pending Aktion ablehnen")
    p_rej.add_argument("id", help="Aktions-ID")

    p_app = sub.add_parser("approve", help="Pending Aktion freigeben")
    p_app.add_argument("id", help="Aktions-ID")

    args = parser.parse_args()
    email = args.user_email

    if args.cmd == "list":
        _list_pending(user_email=email)
    elif args.cmd == "edit":
        _edit(args.id, title=args.title, payload_json=args.payload, action_type=args.type, user_email=email)
    elif args.cmd == "delete":
        _delete(args.id, user_email=email)
    elif args.cmd == "reject":
        _reject(args.id, user_email=email)
    elif args.cmd == "approve":
        _approve(args.id, user_email=email)


if __name__ == "__main__":
    main()
