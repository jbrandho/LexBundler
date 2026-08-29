# LexBundler

A desktop tool for building, aligning, and analyzing linguistic corpora from text and audio.

LexBundler is in early development. The current application is only a minimal PySide6 shell; corpus management, persistence, audio, alignment, analysis, and export features are planned but not yet implemented.

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

Future milestones will let users create a local project, import text and audio sources, review source-derived utterances, align reviewed text with media, analyze the resulting corpus, and export selected results. These capabilities are not part of the initial application shell.

