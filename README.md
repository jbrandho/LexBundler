# LexBundler

A desktop tool for building, aligning, and analyzing linguistic corpora from text and audio.

LexBundler is in early development. It can currently create, validate, close, and reopen project files; corpus importing, audio, alignment, analysis, and export features are planned but not yet implemented.

## Projects and persistence

A LexBundler project is a corpus workspace, typically centered on one primary language. The primary language is descriptive metadata: translations, multilingual annotations, bilingual content, and code-switching are allowed within any project.

At present, one project is one SQLite database file with a `.lexbundler` extension. The file contains project identity, descriptive metadata, a LexBundler format identifier, and one authoritative schema version. Opening a project validates those values rather than trusting its filename. Project databases may contain private or copyrighted derived data and are ignored by Git.

Future material such as HSK courses, ChinesePod content, CALLHOME data, podcasts, or other sources will be imported as corpus sources within a project. They do not need to become separate application databases.

UI code delegates lifecycle operations to `ProjectService`, which depends on a meaningful project-store interface. SQLite-specific SQL, validation, transactions, migrations, and connections remain inside the SQLite persistence implementation. Production schemas use ordered integer versions; project creation writes the current schema directly, while older supported schemas will be migrated sequentially and transactionally through the migration registry.

SQLite is the only implemented backend. A PostgreSQL store may be considered later, but PostgreSQL support does not currently exist.

## Development setup

Python 3.13 or newer is required. Create and activate a virtual environment:

```shell
python3.13 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

Install LexBundler in editable mode with its test dependencies:

```shell
python -m pip install -e '.[dev]'
```

## Run the application

Use either entry point:

```shell
lexbundler
python -m lexbundler
```

## Run tests

```shell
pytest
```

## Planned workflow

Future milestones will let users import text and audio sources, review source-derived utterances, align reviewed text with media, analyze the resulting corpus, and export selected results. These corpus capabilities are not implemented yet.
