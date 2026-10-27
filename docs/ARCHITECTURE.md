# Architecture

Tidy is split into a headless **engine** (`tidy.core`, `tidy.ai`, `tidy.database`,
`tidy.models`, `tidy.utils`) and a **PySide6 UI** (`tidy.ui`). The engine has no Qt
dependency, so it is unit-tested directly and also powers the CLI.

```
src/tidy/
├── app.py                  GUI entry point (logging, recovery, window)
├── cli.py                  tidy-cli
├── settings.py             AppSettings dataclass persisted in SQLite
├── models/                 FileItem, Classification, PlannedMove, Plan
├── core/
│   ├── scanner.py          streaming, link-safe filesystem walk
│   ├── projects.py         project detection (markers / dominant code)
│   ├── categories.py       configurable category tree (stable keys → folders)
│   ├── rules.py            layer 1: extension + filename rules
│   ├── content_analyser.py layer 2: bounded local content inspection
│   ├── classifier.py       learned → rules → content pipeline
│   ├── grouping.py         related files (same stem, sidecars, series)
│   ├── learning.py         learned rules from user corrections
│   ├── planner.py          dry-run plan with safe, unique destinations
│   ├── pipeline.py         scan → classify → (AI) → group → plan
│   ├── fileops.py          no-overwrite rename / verified cross-volume copy
│   ├── organiser.py        execute, undo, crash recovery
│   ├── history.py          operation journal
│   └── duplicates.py       size → partial hash → SHA-256
├── ai/anthropic_classifier.py   layer 3: optional Claude classification
├── database/db.py          SQLite connection + forward-only migrations
├── utils/                  paths (safety primitives), hashing, logging, formatting
└── ui/                     main window, pages, Qt models, workers, theme
```

## Data flow

```
Scanner ──► FileItems ──► Classifier ──► (AI refine) ──► grouping ──► Planner ──► Plan
                           │ learned rules                                   │
                           │ rules                                           ▼
                           └ content                         user review / edits (UI)
                                                                             │
                                                     Organiser.execute ◄─────┘
                                                             │
                                                     Journal (SQLite) ──► History / Undo
```

## Classification

Each layer returns a `Classification(category, confidence, source, reasons)`.

1. **Learned rules** (0.95) — globs generalised from approved corrections.
2. **Rules** — an extension table assigns a base category and *family* (document, image,
   audio…). Keyword rules then refine within the same family, so "receipt.png" can become
   Finance but "statement.mp4" stays Video. Folder units vote using sampled file names.
3. **Content** — overrides rules only when at least as confident. Text is scored against
   weighted phrase profiles; confidence depends on both the score and its margin over the
   runner-up, so ambiguous documents stay low-confidence.
4. **AI** — only for items still below the threshold; capped at 0.9 so it can never outrank
   learned rules or user choices.

Anything below the user's threshold is routed to `Review/` with an explanation.

## Safety invariants

| Invariant | Where enforced |
|---|---|
| Destinations stay inside the root | `paths.ensure_within` in planner *and* organiser |
| No writes through links/junctions | `paths.assert_no_links_between`, scanner `is_link_like` |
| Never overwrite | `unique_destination` + `rename_no_overwrite` (atomic on Windows; `link`+`unlink` on POSIX) |
| Never delete user data | only verified-copy originals, Tidy's own temp files, and empty folders Tidy created are removed |
| Stale plans can't move changed files | organiser re-fingerprints every source |
| Crash-consistent | write-ahead journal + `Organiser.recover()` on every start |
| Unsafe settings can't produce unsafe paths | `CategoryTree` validates every folder; invalid ones revert to defaults |

### Cross-volume moves

```
copy  src → dst.tidy-partial        (journal: temp_path recorded first)
verify SHA-256(src) == SHA-256(temp)
rename temp → dst                   (atomic, no overwrite)
remove src                          (file: if this fails, dst is removed again → exactly one copy)
```

Recovery decision table for a move left `in_progress`:

| source exists | destination exists | temp exists | action |
|---|---|---|---|
| no | yes | – | mark done |
| yes | no | maybe | discard temp, mark failed (original untouched) |
| yes | yes | – | if identical: remove source, mark done; else leave both |
| no | no | yes | rename verified temp into place, mark done |

## Threading

Long work runs on `QThreadPool` via `ui.workers.Worker`, which passes a `threading.Event`
for cancellation and a progress callback. Results come back through Qt signals (queued to the
GUI thread). A single SQLite connection guarded by a re-entrant lock is shared; SQLite
serialises writers anyway.

## Persistence

SQLite (`%LOCALAPPDATA%\Tidy\tidy.sqlite3`; `TIDY_DATA_DIR` overrides) holds settings, the
operation journal, created folders, the hash cache, the AI cache and learned rules. Schema
changes are appended to `MIGRATIONS` and applied in order via `PRAGMA user_version`.

## Cross-platform notes

Windows-specific behaviour is isolated in `utils/paths.py` (Known Folder lookup, reparse-point
detection, reserved names) and `core/fileops.py` (creation-time preservation). Linux uses XDG
user dirs; macOS uses `~/Downloads`.
