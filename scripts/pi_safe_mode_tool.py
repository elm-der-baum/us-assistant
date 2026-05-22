#!/usr/bin/env python3
"""Pi-Agent Tool für Assistant Safe-Mode Freigaben.

Usage:
  python3 pi_safe_mode_tool.py list [--json]
  python3 pi_safe_mode_tool.py delete <id>
  python3 pi_safe_mode_tool.py edit <id> --title "..." [--payload '{...}']
  python3 pi_safe_mode_tool.py reject <id>
  python3 pi_safe_mode_tool.py approve <id>

Liefert Ergebnisse als JSON wenn --json angegeben (fuer Agent-Parsing),
sonst menschenlesbar.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import safe_mode


def _list_pending(json_out: bool = False) -> None:
    actions = safe_mode.list_pending()
    if json_out:
        print(json.dumps(actions, indent=2, ensure_ascii=False))
        return
    if not actions:
        print("Keine pending Aktionen.")
        return
    for a in actions:
        print(f"[{a.get('id')}] {a.get('type')} | {a.get('title')} | {a.get('status')}")


def _delete(action_id: str, json_out: bool = False) -> None:
    result = safe_mode.delete(action_id)
    if json_out:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if result.get("ok"):
        print(f"✅ Aktion {action_id} gelöscht.")
    else:
        print(f"❌ Fehler: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def _edit(action_id: str, title: str | None = None, payload_json: str | None = None, json_out: bool = False) -> None:
    payload = json.loads(payload_json) if payload_json else None
    result = safe_mode.edit(action_id, title=title, payload=payload)
    if json_out:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if result.get("ok"):
        print(f"✅ Aktion {action_id} bearbeitet.")
        print(json.dumps(result.get("action"), indent=2, ensure_ascii=False))
    else:
        print(f"❌ Fehler: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def _reject(action_id: str, json_out: bool = False) -> None:
    result = safe_mode.reject(action_id)
    if json_out:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if result.get("ok"):
        print(f"✅ Aktion {action_id} abgelehnt.")
    else:
        print(f"❌ Fehler: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def _approve(action_id: str, json_out: bool = False) -> None:
    result = safe_mode.approve(action_id)
    if json_out:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return
    if result.get("ok"):
        print(f"✅ Aktion {action_id} freigegeben.")
        print(json.dumps(result.get("result"), indent=2, ensure_ascii=False))
    else:
        print(f"❌ Fehler: {result.get('error')}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Assistant Safe-Mode Tool")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="Pending Aktionen anzeigen")
    p_list.add_argument("--json", action="store_true", dest="json_flag", help="JSON-Output")

    p_del = sub.add_parser("delete", help="Pending Aktion löschen")
    p_del.add_argument("id", help="Aktions-ID")
    p_del.add_argument("--json", action="store_true", dest="json_flag", help="JSON-Output")

    p_edit = sub.add_parser("edit", help="Pending Aktion bearbeiten")
    p_edit.add_argument("id", help="Aktions-ID")
    p_edit.add_argument("--title", default=None, help="Neuer Titel")
    p_edit.add_argument("--payload", default=None, help='Neues Payload als JSON-String')
    p_edit.add_argument("--json", action="store_true", dest="json_flag", help="JSON-Output")

    p_rej = sub.add_parser("reject", help="Pending Aktion ablehnen")
    p_rej.add_argument("id", help="Aktions-ID")
    p_rej.add_argument("--json", action="store_true", dest="json_flag", help="JSON-Output")

    p_app = sub.add_parser("approve", help="Pending Aktion freigeben")
    p_app.add_argument("id", help="Aktions-ID")
    p_app.add_argument("--json", action="store_true", dest="json_flag", help="JSON-Output")

    args = parser.parse_args()
    json_out = getattr(args, 'json_flag', False)

    if args.cmd == "list":
        _list_pending(json_out=json_out)
    elif args.cmd == "delete":
        _delete(args.id, json_out=json_out)
    elif args.cmd == "edit":
        _edit(args.id, title=args.title, payload_json=args.payload, json_out=json_out)
    elif args.cmd == "reject":
        _reject(args.id, json_out=json_out)
    elif args.cmd == "approve":
        _approve(args.id, json_out=json_out)


if __name__ == "__main__":
    main()
