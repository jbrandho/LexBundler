# LexBundler

A desktop tool for building, aligning, and analyzing linguistic corpora from text and audio.

LexBundler is in early development. It can currently create, validate, close, and reopen project files; corpus importing, audio, alignment, analysis, and export features are planned but not yet implemented.

## Projects and persistence

A LexBundler project is a corpus workspace, typically centered on one primary language. The primary language is descriptive metadata: translations, multilingual annotations, bilingual content, and code-switching are allowed within any project.

At present, one project is one SQLite database file with a `.lexbundler` extension. The file contains project identity, descriptive metadata, a LexBundler format identifier, and one authoritative schema version. Opening a project validates those values rather than trusting its filename. Project databases may contain private or copyrighted derived data and are ignored by Git.

Future material such as HSK courses, ChinesePod content, CALLHOME data, podcasts, or other sources will be imported as corpus sources within a project. They do not need to become separate application databases.

UI code delegates lifecycle operations to `ProjectService`, which depends on a meaningful project-store interface. SQLite-specific SQL, validation, transactions, migrations, and connections remain inside the SQLite persistence implementation. Production schemas use ordered integer versions; project creation writes the current schema directly, while older supported schemas will be migrated sequentially and transactionally through the migration registry.

SQLite is the only implemented backend. A PostgreSQL store may be considered later, but PostgreSQL support does not currently exist.

## Corpus foundation

Schema v2 introduces the first generic corpus-storage model:

- A **Source** is a logical external corpus or material collection.
- A **SourceUnit** is an optional, arbitrarily deep hierarchy inside a source. Unit kinds and labels are ordinary data, so the core does not hard-code books, lessons, episodes, or other provider concepts.
- An **Asset** is immutable imported file content identified by its SHA-256 digest and exact byte size.
- An **AssetLocation** records where those exact bytes were observed. A filesystem path is historical evidence, not asset identity or a guarantee that the path remains valid.
- An **AssetBinding** records evidence relating an asset to a whole source or a particular source unit, including optional method, confidence, and processing provenance.
- A **ProcessingRun** records generic import or processing activity and its reproducibility parameters.

LexBundler hashes files incrementally and never changes or copies the original file. File and media bytes are not embedded as SQLite blobs. Identical bytes observed at different paths deduplicate to one Asset while retaining each distinct location observation.

Future extracted text, segments, annotations, and reviewed representations will be additive layers traceable to original evidence rather than destructive replacements. Text storage, segmentation, NLP, and source-specific importers are not implemented yet.

## Text and segmentation foundation

Schema v3 separates immutable evidence, textual interpretations, and analytical structure:

- An **Asset** remains immutable external evidence.
- A **TextRepresentation** is an immutable Unicode snapshot, such as extracted, raw, normalized, reviewed, translated, or romanized text. Transformations create additional representations rather than overwriting earlier content, and equal strings are not automatically deduplicated.
- A **SegmentLayer** is one interpretation of structure over a source or source unit.
- A **Segment** is a conceptual unit inside one layer. Segments do not store or duplicate transcript text.
- A **SegmentTextSpan** points into a TextRepresentation using a zero-based, half-open Python Unicode code-point range. These are Python string offsets, not UTF-8 byte offsets.
- A **SegmentMediaSpan** points into an Asset using a zero-based, half-open range of integer milliseconds.
- A **Speaker** is scoped to one source, while **SegmentSpeaker** supports zero, one, or multiple speaker attributions per segment.

Multiple representations and segmentation layers may coexist, and text or media spans may overlap. Segment hierarchy expresses analytical parentage rather than a non-overlapping interval partition. OCR, ASR, forced alignment, and NLP engines are not implemented yet.

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
