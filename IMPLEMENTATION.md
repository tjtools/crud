# Implementation notes: Contacts

This document explains how `contacts.py` works internally: the layers, the data
model, how each command is wired, how errors are reported, and why the pieces
are arranged the way they are. It targets readers who want to modify or extend
the app rather than just use it.

- Interpreter used during development: CPython **3.13.9** (stdlib only)
- SQLite bundled with it: **3.51.1**
- Runtime dependencies: none
- Files: `contacts.py` (application, 278 lines), `test_contacts.py` (tests),
  `README.md` (usage), `.gitignore`, this document

---

## 1. Purpose and constraints

The brief was "a simple CRUD application using just Python". That was read
strictly as **standard library only** — no Flask, no SQLAlchemy, no click, no
`requirements.txt`. A command-line interface keeps the surface small: there is
no template layer, no HTTP layer, and no process to keep running.

The only two stdlib modules doing real work are:

| Module     | Role                                        |
| ---------- | ------------------------------------------- |
| `sqlite3`  | persistence, one file, transactional writes |
| `argparse` | parsing, subcommand dispatch, `--help`      |

SQLite was chosen over a JSON file because it gives atomic writes, real column
types, `LIKE` queries and `ORDER BY ... COLLATE NOCASE` for free. Persisting a
list to JSON by hand would mean rewriting the whole file on every edit and
hand-rolling search.

---

## 2. Architecture at a glance

The file is a single module split into four horizontal layers, from bottom to
top. Nothing in a lower layer knows about a higher one.

```
┌──────────────────────────────────────────────────────┐
│  command line      build_parser(), main()            │  argparse, dispatch
├──────────────────────────────────────────────────────┤
│  commands          cmd_add, cmd_list, cmd_show,      │  one per subcommand,
│                    cmd_edit, cmd_delete              │  signature (conn, args) -> int
├──────────────────────────────────────────────────────┤
│  presentation      shorten(), render_table()         │  pure functions
├──────────────────────────────────────────────────────┤
│  storage           connect(), get_contact(),         │  the only SQL in the
│                    find_contacts(), insert_contact(),│  program
│                    update_contact(), delete_contact()│
└──────────────────────────────────────────────────────┘
```

Two rules hold this together:

1. **All SQL lives in the storage section.** Handlers never see a cursor.
2. **Presentation helpers are pure** — no database access, no printing — so
   their output is trivially testable and reusable.

The call path for a single invocation is always:

```
__main__ → sys.exit(main())
                 │
                 ├── build_parser().parse_args(argv)   # argparse
                 ├── connect(args.db)                  # schema bootstrap
                 ├── args.func(conn, args)             # one cmd_* handler
                 │        └── storage helper(s) ──────► sqlite3
                 ├── except ValueError → stderr, exit 2
                 └── finally → conn.close()
```

---

## 3. Data model

One table, created lazily on first connection:

```sql
CREATE TABLE IF NOT EXISTS contacts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    email      TEXT NOT NULL DEFAULT '',
    phone      TEXT NOT NULL DEFAULT '',
    notes      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

Notes on the choices:

- **Optional fields default to `''`, not `NULL`.** There is then only one
  "empty" representation, so code never has to branch on `None` vs `''`.
  `shorten()` and the display loops both rely on `''` being falsy.
- **`NULL` appears nowhere in the table**, which is why the create path can
  insert four values unconditionally.
- **`created_at` is filled by SQLite** in UTC, using ISO-like
  `YYYY-MM-DD HH:MM:SS`. It is display-only: no code reads or writes it after
  insert, and no ordering depends on it.
- **`AUTOINCREMENT`** guarantees ids are never reused, so a stale id from
  `list` fails cleanly with "no contact with id N" instead of silently
  pointing at a different person.
- **No unique constraints.** Two contacts may share a name or email; this is an
  address book, not an identity store.

`FIELDS` is the code-level mirror of the column list that users may write:

```python
# Only these column names may ever appear in generated SQL.
FIELDS = ("name", "email", "phone", "notes")
```

`id` and `created_at` are deliberately absent. `FIELDS` is the **whitelist that
makes the one dynamic SQL statement in the program safe** (see §4 and §8).

---

## 4. Storage layer

Every function takes an open `sqlite3.Connection`; none of them open or close
it. That keeps them usable from both the CLI and the tests.

| Function | Signature | Behaviour |
| --- | --- | --- |
| `connect` | `(path) -> Connection` | opens the file, sets `row_factory`, runs the schema, commits |
| `get_contact` | `(conn, id) -> Row \| None` | single row by primary key |
| `find_contacts` | `(conn, term=None) -> list[Row]` | all rows, or a four-column `LIKE` search |
| `insert_contact` | `(conn, values: dict) -> int` | inserts, commits, returns the new id |
| `update_contact` | `(conn, id, changes: dict) -> None` | updates only the keys present |
| `delete_contact` | `(conn, id) -> bool` | deletes, returns whether a row matched |

### `connect()`

```python
conn = sqlite3.connect(path)
conn.row_factory = sqlite3.Row
conn.execute(SCHEMA)
conn.commit()
```

`sqlite3.Row` makes result rows indexable by column name (`row["email"]`)
*and* by position, without a `dict` copy per row. Because `SCHEMA` uses
`CREATE TABLE IF NOT EXISTS`, running the app against a fresh path bootstraps
the database on the spot — there is no separate `init` command and no
migration step at this size.

### Reading: `find_contacts()`

```python
pattern = f"%{term.strip()}%"
... WHERE name LIKE ? OR email LIKE ? OR phone LIKE ? OR notes LIKE ?
    ORDER BY name COLLATE NOCASE
```

- A falsy `term` short-circuits to "return everything", so `list` and
  `list --search` share one function.
- The same pattern is bound four times, once per searchable column, so a
  search for `compiler` finds Grace Hopper through her **notes** as well as
  through visible columns. That is why the tests assert on a note fragment.
- `ORDER BY name COLLATE NOCASE` sorts case-insensitively, so `alice` and
  `Alice` do not split the list.
- `LIKE` is case-insensitive for ASCII in SQLite by default; non-ASCII case
  folding is not handled (see §12).

### Writing: `insert_contact()` / `update_contact()` / `delete_contact()`

All three commit immediately. There is no transaction spanning several
statements, because no user action needs one.

`insert_contact` returns `cursor.lastrowid` so the handler can tell the user
which id to use next.

`update_contact` is the one place the SQL text is assembled from pieces:

```python
assignments = ", ".join(f"{field} = ?" for field in changes)
conn.execute(f"UPDATE contacts SET {assignments} WHERE id = ?",
             (*changes.values(), contact_id))
```

Two things make this safe and correct:

- **Whitelist:** `changes` is only ever populated by iterating `FIELDS` in
  `cmd_edit`, so the interpolated substrings are literal column names from a
  tuple in this file. No user-supplied string reaches the SQL text.
- **Ordering:** `assignments` and `*changes.values()` are generated by walking
  the *same* dict, so the `?` placeholders and the bound values line up
  positionally by construction. The dict is insertion-ordered (Python 3.7+),
  guaranteeing both walks match.

`delete_contact` returns `cursor.rowcount > 0` instead of raising, which lets
`cmd_delete` distinguish "deleted" from "there was nothing there" and report
the latter as an error.

---

## 5. Presentation layer

Both helpers are pure functions — strings in, strings out.

### `shorten(text, width)`

```python
text = " ".join(str(text).split())
return text if len(text) <= width else text[: width - 3] + "..."
```

- `" ".join(split())` collapses newlines and runs of spaces, so a multi-line
  note cannot break the table's row structure.
- Truncation reserves three characters for the ellipsis, so the result never
  exceeds `width`. Callers use widths of 16–32, comfortably above the minimum
  of 3 below which slicing would misbehave.
- Only `list` uses it. `show` prints the full value, which is the reason both
  commands exist.

### `render_table(headers, rows)`

Column widths are the max of the header and every cell, then each line is
joined with two spaces and left-padded with `ljust`, and `rstrip()` drops
trailing blanks so no line carries invisible padding. A dash rule is inserted
under the header.

The function takes strings only — the caller stringifies numbers — which keeps
it free of type branching and lets it be used for any future table. Rows must
have the same length as `headers`; that is an internal invariant rather than a
checked precondition, since there is exactly one call site.

---

## 6. Exit codes and the error model

There are two distinct failure kinds, and they map to different codes so shell
scripts can react:

| Code | Meaning | Raised by | Printed as |
| --- | --- | --- | --- |
| `0` | success | handler returns 0 | normal output |
| `1` | the requested id does not exist | `not_found()` | `error: no contact with id N` |
| `2` | input is invalid | `ValueError` from a handler | `error: <message>` |

"Not found" is delivered by returning a value, not by an exception, because it
is an expected outcome of a well-formed request. Invalid input is delivered by
raising `ValueError`, caught once in `main()`:

```python
try:
    return args.func(conn, args)
except ValueError as exc:
    print(f"error: {exc}", file=sys.stderr)
    return 2
finally:
    conn.close()
```

Centralising this means handlers contain no `print`-and-return-2 boilerplate:
`cmd_add` and `cmd_edit` simply state the rule that was violated and let `main`
render it. All diagnostics go to **stderr**; all data goes to **stdout**, so
`contacts.py list > contacts.txt` is clean.

`not_found()` is a shared helper so the wording of the 1-code message is
identical in `show`, `edit` and `delete`.

Validation itself is deliberately shallow — enough to catch typos, not enough
to fight the user:

```python
def validate_email(email):
    if email and (email.count("@") != 1 or email.startswith("@") or email.endswith("@")):
        raise ValueError(f"{email!r} does not look like an email address")
```

Empty is allowed (email is optional); exactly one `@`, not at either end. No
regex, no domain lookup: RFC 5322 is not worth encoding for an address book,
and a wrong rejection is worse than a missing one here.

---

## 7. Command handlers

Every handler has the same shape — `(conn, args) -> int` — which is what makes
the dispatch table in §8 a dictionary lookup. They own *policy* (what is
required, which message to print) and delegate *mechanism* to the storage and
presentation layers.

### `add`

Builds the value dict in one comprehension over the whitelist, so flags,
columns and defaults cannot drift apart:

```python
values = {field: (getattr(args, field) or "").strip() for field in FIELDS}
```

`or ""` covers an omitted flag; `.strip()` trims stray spaces. Then: name must
be non-empty after stripping (a name of `"   "` is rejected), and the email
must pass `validate_email`. On success it prints the new id, which is the
handle the user needs for `show`, `edit` and `delete`.

### `list`

Two different empty states, because the causes differ and so should the advice:

- no search term → `No contacts yet. Add one with: contacts.py add "Ada Lovelace" --email ada@example.com`
- with a search term → `No contacts matching 'ada'.`

Otherwise it renders `ID / NAME / EMAIL / PHONE / NOTES` via `shorten()` and
finishes with a count line (`1 contact.` / `2 contacts.`) — actually computed
from `len(rows)`, not from a separate `COUNT(*)` query, so it always agrees
with what was printed.

### `show`

Header line `#id  name`, then one line per **non-empty** field using a fixed
label width:

```python
print(f"  {label:<8} {row[key]}")   # Email, Phone, Notes, Created
```

`8` is one more than the longest label (`Created`), giving a single space of
separation that lines up across all four rows. Empty fields are skipped rather
than printed as blanks, so a sparse contact does not look broken. This is the
command that exists so notes are readable in full.

### `edit`

```python
for field in FIELDS:
    value = getattr(args, field)
    if value is not None:
        changes[field] = value.strip()
```

This is why the `--name/--email/--phone/--notes` flags default to **`None`**
rather than `""`. `None` means "flag absent, leave it alone"; an explicit
`--notes ""` means "clear this field" and is therefore honoured. That
distinction is the whole reason edit does not reuse add's `or ""` idiom — it
is a partial update, not a replace.

Then three guards: at least one field must be present, a supplied name may not
become empty, and a supplied email must validate. The success message names the
fields that changed — `Updated contact #1 (phone).` — which doubles as the only
confirmation the user gets that they did not accidentally overwrite a column.

### `delete`

```python
if not delete_contact(conn, args.id):
    return not_found(args.id)
```

No "are you sure?" prompt. A prompt makes the tool unusable from a script or a
loop, and this is a local address book; the cost of a mistake is one lost
record. Users who want safety can `show` first.

---

## 8. The command line

`build_parser()` assembles one top-level parser and one subparser per command.

```python
parser.add_argument("--db", default=DEFAULT_DB, metavar="PATH", help=...)
subparsers = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
```

- **Dispatch without `if/elif`:** each subparser calls
  `set_defaults(func=cmd_x)`, so `main()` does
  `args.func(conn, args)`. Adding a command means adding a subparser, a
  handler, and nothing else — no change to `main`.
- **`required=True` on the subparsers** turns a bare `contacts.py` into a
  usage error (argparse exit code 2) instead of an implicit no-op, and
  `metavar="COMMAND"` keeps that message readable.
- **`--db` lives on the top-level parser**, so it must appear *before* the
  subcommand: `contacts.py --db other.db list`. It is a property of the whole
  invocation, not of one command. Allowing it after the subcommand would
  require duplicating the option on every subparser.
- **`RawDescriptionHelpFormatter` plus an `epilog` of example commands**
  preserves line breaks in examples, which is most of what a user wants from
  `--help`.
- `prog="contacts.py"` is set explicitly so usage lines are correct even when
  the module is imported and `main()` is called from a test.

`solve` of the flow: `parse_args` → connection → handler. The connection is
opened **after** parsing, so a typo in the arguments never creates a stray
`contacts.db`.

---

## 9. End-to-end walkthrough: `contacts.py edit 1 --phone 555-0199`

1. `__main__` calls `main(["edit", "1", "--phone", "555-0199"])` — via
   `sys.argv` in normal use, directly in tests.
2. `build_parser().parse_args(...)` sets `command="edit"`, `id=1`
   (`type=int`), `phone="555-0199"`, and `name`/`email`/`notes` to `None`.
3. `main` opens `contacts.db`, which is created with the schema if missing.
4. `args.func` is `cmd_edit`. It calls `get_contact(conn, 1)`.
   - `None` → `not_found(1)` prints to stderr, returns 1.
5. Otherwise it walks `FIELDS`; only `phone` is not `None`, so
   `changes = {"phone": "555-0199"}`. `"phone" in changes`, so the name guard
   is skipped; `"email" not in changes`, so validation is skipped.
6. `update_contact` builds `SET phone = ?` and executes
   `UPDATE contacts SET phone = ? WHERE id = ?` with `("555-0199", 1)`,
   then commits.
7. Back in `cmd_edit`: prints `Updated contact #1 (phone).`, returns 0.
8. `finally` closes the connection; `sys.exit(0)`.

Failure at step 4 short-circuits before any write, which is why a missing id
never errors out as a database exception.

---

## 10. How the tests are built

`test_contacts.py` has no fixtures beyond a temp directory, and no mocking.

```python
def run_cli(self, *argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = contacts.main(["--db", self.db, *argv])
    return code, out.getvalue(), err.getvalue()
```

Key decisions:

- **Tests drive `main()`, not the handlers.** They therefore cover argument
  parsing, dispatch, schema bootstrap, SQL, formatting, stream choice and exit
  codes in one hop. A unit test of `cmd_add` would not catch, say, a flag
  renamed in the parser but not in the handler.
- **The real I/O boundary is exercised; only the path is swapped.** Each test
  gets its own SQLite file via `tempfile.TemporaryDirectory` and
  `addCleanup`, so tests are order-independent and leave nothing behind. No
  in-memory `:memory:` shortcut, because the CLI opens its own connection by
  path.
- **`add_contact()` parses the id back out of the printed message**, exactly
  the way a user would. That makes the id in the output part of the tested
  contract.
- **Assertions check user-visible results** (`Ada Lovelace` and
  `ada@example.com` appear in the table; a deleted contact disappears; the
  unchanged email survives an edit) rather than internal state, so the table
  formatter and the SQL are covered together.
- Coverage: one test per command, both empty states, both failure codes,
  validation (empty name, malformed email), and persistence across a second
  `connect()`.

`python3 -m unittest -v` runs 11 tests, all passing.

---

## 11. Design decisions and trade-offs

| Decision | Why | What it costs |
| --- | --- | --- |
| Single module, not a package | The whole app is 278 lines; import ceremony would add files without adding clarity | No namespacing as it grows |
| SQLite, not JSON/CSV | Atomic writes, `LIKE`, collation, no rewrite-whole-file | A binary file in the folder; not hand-editable |
| `''` instead of `NULL` | One empty representation, no `None` branches | `NULL`-specific SQL (`COALESCE`, `IS NULL`) unavailable |
| Column whitelist + bound parameters | The only dynamic SQL cannot be injected | Adds one tuple to keep in sync when fields change |
| `None` sentinel for edit flags | Distinguishes "leave alone" from "clear it" | Differs from add's `or ""` default, a subtle asymmetry |
| Exit 1 vs 2 | Scripts can separate "missing" from "malformed" | Documented in README, so nobody guesses |
| No confirmation on delete | Scriptable, non-interactive | No undo for a slip |
| Truncated notes in `list` | Table stays readable at terminal width | Full text requires a second command (`show`) |
| Handler returns `int` | `sys.exit(main())` works and tests assert codes | Slightly more plumbing than `sys.exit` inside handlers |

---

## 12. Known limitations

Deliberate, in rough order of how likely they are to matter:

- **No pagination.** `list` loads every row into memory and prints it. Fine
  for hundreds of contacts; a phone book of 100k would want `LIMIT`/`OFFSET`.
- **Search is substring-only**, case-insensitive for ASCII. No fuzzy matching,
  no prefix index, no Unicode case folding (`LIKE` does not fold `É`/`é`).
- **`--db` must precede the subcommand.** `contacts.py list --db x.db` is a
  usage error, an argparse consequence explained in §8.
- **No schema migration path.** `CREATE TABLE IF NOT EXISTS` creates a missing
  table but will not alter an existing one, so adding a column to `SCHEMA`
  leaves old database files unchanged — see §13 for the manual fix.
- **No `updated_at`.** Edits are not timestamped; only creation is.
- **No bulk operations or import/export.** One record per invocation.
- **Concurrency is whatever SQLite defaults to.** Two simultaneous writers may
  get "database is locked" rather than waiting; no `busy_timeout` is set.
- **Timestamps are UTC** with no timezone conversion for display.

---

## 13. Extending it

**Add a field (say `company`):**

1. Add the column to `SCHEMA`.
2. Add `"company"` to `FIELDS` — this automatically flows into add, edit and
   the update whitelist.
3. Add `--company` to the `add` and `edit` subparsers (`add` with
   `default=""`, `edit` with the `None` default).
4. Add a column to `cmd_list`'s `headers`/`body` and a row to the label loop
   in `cmd_show`.
5. For an existing `contacts.db`, run
   `ALTER TABLE contacts ADD COLUMN company TEXT NOT NULL DEFAULT '';`
   once — `CREATE TABLE IF NOT EXISTS` will not do it.

**Add a command (say `count`):** write `cmd_count(conn, args) -> int`, add a
subparser with `set_defaults(func=cmd_count)`. `main()` needs no change.

**Swap the storage layer** (e.g. to JSON or Postgres): the six functions in
§4 are the entire boundary. Handlers and tests only need `connect` to return
something that supports the same calls.
