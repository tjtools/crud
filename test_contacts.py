"""End-to-end tests for the contacts CLI (stdlib unittest only).

Run with: python3 -m unittest -v
"""

from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import contacts


class ContactsCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = str(Path(self._tmp.name) / "test.db")

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = contacts.main(["--db", self.db, *argv])
        return code, out.getvalue(), err.getvalue()

    def add_contact(self, name: str, **fields: str) -> int:
        argv = ["add", name]
        for key, value in fields.items():
            argv += [f"--{key}", value]
        code, out, err = self.run_cli(*argv)
        self.assertEqual(code, 0, err)
        return int(out.split("#")[1].split(":")[0])

    def test_add_then_list(self) -> None:
        contact_id = self.add_contact("Ada Lovelace", email="ada@example.com", phone="555-0100")
        self.assertEqual(contact_id, 1)

        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertIn("Ada Lovelace", out)
        self.assertIn("ada@example.com", out)
        self.assertIn("555-0100", out)
        self.assertIn("1 contact", out)

    def test_list_on_empty_database(self) -> None:
        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertIn("No contacts yet", out)

    def test_show_prints_all_fields(self) -> None:
        contact_id = self.add_contact("Grace Hopper", email="grace@example.com", notes="COBOL")

        code, out, err = self.run_cli("show", str(contact_id))
        self.assertEqual(code, 0, err)
        self.assertIn("Grace Hopper", out)
        self.assertIn("grace@example.com", out)
        self.assertIn("COBOL", out)

    def test_show_missing_id_fails(self) -> None:
        code, _, err = self.run_cli("show", "42")
        self.assertEqual(code, 1)
        self.assertIn("no contact with id 42", err)

    def test_edit_changes_only_given_fields(self) -> None:
        contact_id = self.add_contact("Alan Turing", email="alan@example.com", phone="555-0111")

        code, out, err = self.run_cli("edit", str(contact_id), "--phone", "555-9999")
        self.assertEqual(code, 0, err)
        self.assertIn("Updated contact", out)

        code, out, err = self.run_cli("show", str(contact_id))
        self.assertEqual(code, 0, err)
        self.assertIn("555-9999", out)
        self.assertNotIn("555-0111", out)
        self.assertIn("alan@example.com", out)

    def test_edit_without_fields_is_rejected(self) -> None:
        contact_id = self.add_contact("Katherine Johnson")
        code, _, err = self.run_cli("edit", str(contact_id))
        self.assertEqual(code, 2)
        self.assertIn("nothing to update", err)

    def test_delete_removes_contact(self) -> None:
        contact_id = self.add_contact("Margaret Hamilton")

        code, out, err = self.run_cli("delete", str(contact_id))
        self.assertEqual(code, 0, err)
        self.assertIn("Deleted contact", out)

        code, _, err = self.run_cli("delete", str(contact_id))
        self.assertEqual(code, 1)
        self.assertIn("no contact with id", err)

        code, out, _ = self.run_cli("list")
        self.assertEqual(code, 0)
        self.assertNotIn("Margaret Hamilton", out)

    def test_search_filters_contacts(self) -> None:
        self.add_contact("Ada Lovelace", email="ada@example.com")
        self.add_contact("Bob Smith", email="bob@example.org")

        code, out, err = self.run_cli("list", "--search", "ada@")
        self.assertEqual(code, 0, err)
        self.assertIn("Ada Lovelace", out)
        self.assertNotIn("Bob Smith", out)

    def test_add_requires_a_name(self) -> None:
        code, _, err = self.run_cli("add", "   ")
        self.assertEqual(code, 2)
        self.assertIn("needs a name", err)

    def test_add_rejects_bad_email(self) -> None:
        code, _, err = self.run_cli("add", "No Email", "--email", "not-an-email")
        self.assertEqual(code, 2)
        self.assertIn("does not look like an email", err)

    def test_data_persists_across_connections(self) -> None:
        self.add_contact("Edsger Dijkstra")
        with contacts.connect(self.db) as conn:
            names = [row["name"] for row in contacts.find_contacts(conn)]
        self.assertEqual(names, ["Edsger Dijkstra"])


if __name__ == "__main__":
    unittest.main()
