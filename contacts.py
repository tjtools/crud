#!/usr/bin/env python3
"""A small contact book you can create, read, update and delete from.

Only the Python standard library is used: sqlite3 for storage and argparse
for the command line. Data lives in a single SQLite file (contacts.db by
default), so there is no server to run and nothing to install.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from typing import Sequence

DEFAULT_DB = "contacts.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL DEFAULT '',
    phone      TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

# Only these column names may ever appear in generated SQL.
FIELDS = ("name", "email", "phone", "notes")


# --------------------------------------------------------------------------
# storage
# --------------------------------------------------------------------------
def connect(path: str) -> sqlite3.Connection:
    """Open the database, creating the contacts table on first use."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    conn.commit()
    return conn


def get_contact(conn: sqlite3.Connection, contact_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()


def find_contacts(conn: sqlite3.Connection, term: str | None = None) -> list[sqlite3.Row]:
    if not term:
        return conn.execute(
            "SELECT * FROM contacts ORDER BY name COLLATE NOCASE"
        ).fetchall()
    pattern = f"%{term.strip()}%"
    return conn.execute(
        """
        SELECT * FROM contacts
         WHERE name LIKE ? OR email LIKE ? OR phone LIKE ? OR notes LIKE ?
         ORDER BY name COLLATE NOCASE
        """,
        (pattern, pattern, pattern, pattern),
    ).fetchall()


def insert_contact(conn: sqlite3.Connection, values: dict[str, str]) -> int:
    cursor = conn.execute(
        "INSERT INTO contacts (name, email, phone, notes) VALUES (?, ?, ?, ?)",
        (values["name"], values["email"], values["phone"], values["notes"]),
    )
    conn.commit()
    return int(cursor.lastrowid)


def update_contact(conn: sqlite3.Connection, contact_id: int, changes: dict[str, str]) -> None:
    assignments = ", ".join(f"{field} = ?" for field in changes)
    conn.execute(
        f"UPDATE contacts SET {assignments} WHERE id = ?",
        (*changes.values(), contact_id),
    )
    conn.commit()


def delete_contact(conn: sqlite3.Connection, contact_id: int) -> bool:
    cursor = conn.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    conn.commit()
    return cursor.rowcount > 0


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def shorten(text: str, width: int) -> str:
    """Collapse whitespace and trim to `width` characters for table output."""
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 3] + "..."


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    def line(cells: Sequence[str]) -> str:
        return "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(cells)).rstrip()

    out = [line(headers), "  ".join("-" * width for width in widths)]
    out.extend(line(row) for row in rows)
    return "\n".join(out)


def validate_email(email: str) -> None:
    if email and (email.count("@") != 1 or email.startswith("@") or email.endswith("@")):
        raise ValueError(f"{email!r} does not look like an email address")


def not_found(contact_id: int) -> int:
    print(f"error: no contact with id {contact_id}", file=sys.stderr)
    return 1


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------
def cmd_add(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    values = {field: (getattr(args, field) or "").strip() for field in FIELDS}
    if not values["name"]:
        raise ValueError("a contact needs a name")
    validate_email(values["email"])

    contact_id = insert_contact(conn, values)
    print(f"Added contact #{contact_id}: {values['name']}")
    return 0


def cmd_list(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    rows = find_contacts(conn, args.search)
    if not rows:
        if args.search:
            print(f"No contacts matching {args.search!r}.")
        else:
            print('No contacts yet. Add one with: contacts.py add "Ada Lovelace" --email ada@example.com')
        return 0

    headers = ["ID", "NAME", "EMAIL", "PHONE", "NOTES"]
    body = [
        [
            str(row["id"]),
            shorten(row["name"], 24),
            shorten(row["email"], 28),
            shorten(row["phone"], 16),
            shorten(row["notes"], 32),
        ]
        for row in rows
    ]
    print(render_table(headers, body))
    print(f"\n{len(rows)} contact{'s' if len(rows) != 1 else ''}.")
    return 0


def cmd_show(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    row = get_contact(conn, args.id)
    if row is None:
        return not_found(args.id)

    print(f"#{row['id']}  {row['name']}")
    for label, key in (
        ("Email", "email"),
        ("Phone", "phone"),
        ("Notes", "notes"),
        ("Created", "created_at"),
    ):
        if row[key]:
            print(f"  {label:<8} {row[key]}")
    return 0


def cmd_edit(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    row = get_contact(conn, args.id)
    if row is None:
        return not_found(args.id)

    changes: dict[str, str] = {}
    for field in FIELDS:
        value = getattr(args, field)
        if value is not None:
            changes[field] = value.strip()

    if not changes:
        raise ValueError("nothing to update; pass at least one of --name, --email, --phone, --notes")
    if "name" in changes and not changes["name"]:
        raise ValueError("a contact needs a name")
    if "email" in changes:
        validate_email(changes["email"])

    update_contact(conn, args.id, changes)
    print(f"Updated contact #{args.id} ({', '.join(changes)}).")
    return 0


def cmd_delete(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    if not delete_contact(conn, args.id):
        return not_found(args.id)
    print(f"Deleted contact #{args.id}.")
    return 0


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contacts.py",
        description="A tiny contact book backed by SQLite (standard library only).",
        epilog=(
            "examples:\n"
            '  contacts.py add "Ada Lovelace" --email ada@example.com --phone 555-0100\n'
            "  contacts.py list\n"
            '  contacts.py list --search ada\n'
            "  contacts.py show 1\n"
            "  contacts.py edit 1 --phone 555-0199\n"
            "  contacts.py delete 1\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        metavar="PATH",
        help=f"SQLite database file to use (default: {DEFAULT_DB})",
    )

    subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")

    add = subparsers.add_parser("add", help="Create a contact")
    add.add_argument("name")
    add.add_argument("--email", default="")
    add.add_argument("--phone", default="")
    add.add_argument("--notes", default="")
    add.set_defaults(func=cmd_add)

    list_ = subparsers.add_parser("list", help="List contacts")
    list_.add_argument("-s", "--search", help="only show contacts matching this text")
    list_.set_defaults(func=cmd_list)

    show = subparsers.add_parser("show", help="Show one contact in full")
    show.add_argument("id", type=int)
    show.set_defaults(func=cmd_show)

    edit = subparsers.add_parser("edit", help="Change fields of an existing contact")
    edit.add_argument("id", type=int)
    edit.add_argument("--name")
    edit.add_argument("--email")
    edit.add_argument("--phone")
    edit.add_argument("--notes")
    edit.set_defaults(func=cmd_edit)

    delete = subparsers.add_parser("delete", help="Remove a contact")
    delete.add_argument("id", type=int)
    delete.set_defaults(func=cmd_delete)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    conn = connect(args.db)
    try:
        return args.func(conn, args)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
