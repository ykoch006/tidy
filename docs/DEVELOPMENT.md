# Developer guide

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate               # source .venv/bin/activate on Linux/macOS
pip install -e ".[dev,build]"
```

Python 3.12+ is required (the code uses `Path.is_junction`, `datetime.UTC` and `StrEnum`).

## Running

```bash
python -m tidy                       # desktop app
tidy-cli plan path/to/folder         # CLI dry run
```

### Never touch your real Downloads while developing

Point Tidy at a sandbox:

```powershell
$env:TIDY_DOWNLOADS_DIR = "C:\dev\tidy-sandbox"   # default folder
$env:TIDY_DATA_DIR      = "C:\dev\tidy-data"      # separate database and logs
python -m tidy
```

The test suite sets both variables to temporary directories automatically, and
`tests/conftest.py` fails any test whose temp directory resolves inside the real Downloads
folder.

## Tests

```bash
pytest                       # everything (GUI tests run with QT_QPA_PLATFORM=offscreen)
pytest -m "not slow"         # skip the large-collection tests
pytest tests/test_organiser.py -k recovery
```

| File | Covers |
|---|---|
| `test_paths.py` | sanitising, traversal rejection, junction/symlink detection, unique names |
| `test_scanner.py` | scanning, projects, skipped items, unusual names, permissions, cancel, 5,000 files |
| `test_classification_rules.py` | extension/keyword rules, categories, learning, grouping |
| `test_content.py` | PDF/DOCX/PPTX/XLSX/ZIP/EXIF analysis, corrupt and encrypted files |
| `test_planner.py` | dry run, collisions, review routing, edits |
| `test_organiser.py` | execution, change detection, locks, junctions, cross-volume, undo, crash recovery |
| `test_duplicates.py` | hashing passes, cache invalidation, look-alike names |
| `test_ai.py` | consent, payload privacy, caching, validation, error handling (fake client) |
| `test_cli.py` | CLI round-trip |
| `test_gui.py` | end-to-end GUI flows, offscreen |

Crash recovery is tested by raising a `BaseException` subclass from inside the move
primitives (simulating the process dying at a precise point), then running `recover()`.
Cross-volume behaviour is tested by forcing `same_volume` to return `False`.

GUI tests auto-answer modal dialogs (see the `no_modal_dialogs` fixture) — a real modal box
would otherwise block an offscreen run forever.

## Code style

- `ruff check` and `ruff format` (line length 100).
- Type hints throughout; engine modules must not import Qt.
- Logging: `get_logger(__name__)`; pass structured context with `extra={"ctx": {...}}`.
  Logs are JSON lines in `<data dir>/logs/tidy.log`.

## Adding things

- **A category**: add it to `DEFAULT_CATEGORIES` in `core/categories.py`, and rules in
  `core/rules.py`. Keys are stable identifiers — never rename a released key.
- **A content analyser**: extend `ContentAnalyser.analyse`; keep reads bounded and catch
  parser errors (the classifier also isolates crashes).
- **A schema change**: append a new SQL string to `MIGRATIONS` in `database/db.py`. Never
  edit a released migration.
- **A page**: build a `Page` subclass and register it in `ui/features.py` with
  `window.add_page(...)`.

## Screenshots

Run the app against a demo folder (see the sandbox instructions above), resize the window to
about 1400×860, and save PNGs as `docs/screenshots/{dashboard,explorer,preview,duplicates}.png`.
Make sure no personal paths or file names are visible.

## Releasing

1. Bump `version` in `pyproject.toml` and `src/tidy/__init__.py`.
2. Tag `vX.Y.Z` and push — CI runs the tests and builds `Tidy-windows`.
