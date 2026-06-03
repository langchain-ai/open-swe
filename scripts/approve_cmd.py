#!/usr/bin/env python3
import sys
import os
import argparse
import sqlite3
import urllib.request
import urllib.error
import json
from pathlib import Path

# Resolve workspace root and safety database path
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = WORKSPACE_ROOT / "agent_safety.db"
API_URL_BASE = os.environ.get("OPEN_SWE_SAFETY_URL", "http://localhost:8000")

def check_db_exists() -> bool:
    return DB_PATH.is_file()

def list_pending_db():
    """Directly query local SQLite database for pending approvals."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, timestamp, command, risk_level, reason FROM approvals WHERE status = 'PENDING'")
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"Error querying SQLite database: {e}")
        return []

def list_pending_api():
    """Query FastAPI endpoint for pending approvals."""
    try:
        url = f"{API_URL_BASE}/safety/approvals"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode('utf-8'))
            approvals = data.get("approvals", [])
            pending = [a for a in approvals if a["status"] == "PENDING"]
            return [(a["id"], a["timestamp"], a["command"], a["risk_level"], a["reason"]) for a in pending]
    except Exception as e:
        print(f"Failed to connect to API ({API_URL_BASE}): {e}")
        return None

def resolve_db(approval_id: int, status: str) -> bool:
    """Directly resolve approval state inside SQLite database."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM approvals WHERE id = ?", (approval_id,))
        row = cursor.fetchone()
        if not row:
            print(f"Error: Approval ID {approval_id} not found.")
            conn.close()
            return False
        if row[0] != "PENDING":
            print(f"Error: Approval ID {approval_id} is already resolved to {row[0]}.")
            conn.close()
            return False
        cursor.execute("UPDATE approvals SET status = ? WHERE id = ?", (status, approval_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"SQLite Update failed: {e}")
        return False

def resolve_api(approval_id: int, status: str) -> bool:
    """Resolve approval state by posting to FastAPI web endpoint."""
    try:
        url = f"{API_URL_BASE}/safety/approve"
        payload = json.dumps({"approval_id": approval_id, "status": status}).encode('utf-8')
        req = urllib.request.Request(
            url, 
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            res_data = json.loads(response.read().decode('utf-8'))
            return res_data.get("status") == "success"
    except urllib.error.HTTPError as e:
        try:
            err_data = json.loads(e.read().decode('utf-8'))
            print(f"API Error: {err_data.get('detail', e.reason)}")
        except Exception:
            print(f"API HTTP Error: {e.code} {e.reason}")
        return False
    except Exception as e:
        print(f"API Connection failed: {e}")
        return False

def print_queue(pending_items):
    if not pending_items:
        print("\n✨ Sandbox safety queue is empty! No pending command approvals.")
        return

    print("\n" + "="*80)
    print(f"🛡️  PENDING COMMAND APPROVALS ({len(pending_items)} items)")
    print("="*80)
    for item in pending_items:
        appr_id, timestamp, command, risk_level, reason = item
        print(f"\nID: {appr_id}  |  Level: {risk_level}  |  Time: {timestamp}")
        print(f"Reason: {reason}")
        print(f"Command:\n  \033[1;33m{command}\033[0m")
        print("-" * 80)
    print()

def main():
    parser = argparse.ArgumentParser(description="Open-SWE Enterprise Shield Safety Approvals CLI")
    parser.add_argument("--list", action="store_true", help="List all pending approvals")
    parser.add_argument("--approve", type=int, metavar="ID", help="Approve command execution by ID")
    parser.add_argument("--reject", type=int, metavar="ID", help="Reject command execution by ID")
    parser.add_argument("--use-api", action="store_true", help="Force communicating via HTTP REST API instead of direct DB write")
    args = parser.parse_args()

    # Determine whether to use DB or API
    # Fallback to API if DB is not found locally
    use_api = args.use_api or not check_db_exists()

    if args.list:
        pending = list_pending_api() if use_api else list_pending_db()
        if pending is None and not args.use_api:
            # Fallback to local DB query
            print("Falling back to local SQLite direct read...")
            pending = list_pending_db()
        
        if pending is not None:
            print_queue(pending)
        sys.exit(0)

    if args.approve is not None:
        success = resolve_api(args.approve, "APPROVED") if use_api else resolve_db(args.approve, "APPROVED")
        if success:
            print(f"✅ Command ID {args.approve} APPROVED successfully.")
        else:
            sys.exit(1)
        sys.exit(0)

    if args.reject is not None:
        success = resolve_api(args.reject, "REJECTED") if use_api else resolve_db(args.reject, "REJECTED")
        if success:
            print(f"❌ Command ID {args.reject} REJECTED successfully.")
        else:
            sys.exit(1)
        sys.exit(0)

    # Interactive Loop
    print("\nStarting Open-SWE Enterprise Shield Interactive Approvals...")
    while True:
        pending = list_pending_api() if use_api else list_pending_db()
        if pending is None:
            print("API not running. Defaulting to local SQLite DB direct read...")
            use_api = False
            pending = list_pending_db()

        print_queue(pending)
        if not pending:
            print("Press Enter to refresh or Q to quit.")
            choice = input("> ").strip().upper()
            if choice == 'Q':
                break
            continue

        print("Options: Enter ID to resolve  |  [R]efresh  |  [Q]uit")
        choice = input("> ").strip()
        if choice.upper() == 'Q':
            break
        if choice.upper() == 'R' or choice == '':
            continue

        try:
            appr_id = int(choice)
        except ValueError:
            print("Invalid input. Please enter an Approval ID integer, R, or Q.")
            continue

        # Check if the ID entered is in the list
        valid_ids = [item[0] for item in pending]
        if appr_id not in valid_ids:
            print(f"Approval ID {appr_id} is not in the pending queue.")
            continue

        action = input(f"Approve or Reject command ID {appr_id}? (A/R/Cancel): ").strip().upper()
        if action == 'A':
            success = resolve_api(appr_id, "APPROVED") if use_api else resolve_db(appr_id, "APPROVED")
            if success:
                print(f"✅ Approved command ID {appr_id}.")
        elif action == 'R':
            success = resolve_api(appr_id, "REJECTED") if use_api else resolve_db(appr_id, "REJECTED")
            if success:
                print(f"❌ Rejected command ID {appr_id}.")
        else:
            print("Action cancelled.")

if __name__ == "__main__":
    main()
