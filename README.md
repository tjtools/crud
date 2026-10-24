# Contacts

A small CRUD address book that runs on the Python standard library alone —
`sqlite3` for storage, `argparse` for the command line. No pip installs, no
server, no config files.

## Usage

```bash
python3 contacts.py add "Ada Lovelace" --email ada@example.com --phone 555-0100
python3 contacts.py list
python3 contacts.py list --search ada
python3 contacts.py show 1
python3 contacts.py edit 1 --phone 555-0199
python3 contacts.py delete 1
```

Data is stored in `contacts.db` in the current directory. Point somewhere else
with `--db path/to/other.db` (the option goes before the command).

Run `python3 contacts.py --help` or `python3 contacts.py <command> --help` for
details.

## Exit codes

| Code | Meaning                                        |
| ---- | ---------------------------------------------- |
| 0    | success                                        |
| 1    | no contact with that id                        |
| 2    | invalid input (empty name, malformed email, …) |

## Tests

```bash
python3 -m unittest -v
```

## How it works

See [IMPLEMENTATION.md](IMPLEMENTATION.md) for the architecture, data model,
error/exit-code contract, design trade-offs and extension notes.
