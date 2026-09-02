# LexBundler

A desktop tool for building, aligning, and analyzing linguistic corpora from text and audio.

LexBundler is in early development. It can manage project files, preserve source assets, import authoritative UTF-8 transcripts, import or produce MFA and whisper.cpp analysis, render selected media spans as durable MP3 clips, and package explicitly selected Chinese text/audio pairs as Anki listening cards. Audio playback, alignment review, broader import workflows, and analysis are not yet implemented.

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

## Manual whisper.cpp JSON import

LexBundler can normalize an already-existing whisper.cpp JSON file into the schema-v3 model through its application service. This manual-import entry point does not execute `whisper-cli`; integrated execution is described separately below.

The original JSON and source media remain immutable Assets. The JSON is retained as the direct evidence for one exact TextRepresentation; its raw transcription entries become an unreviewed ASR SegmentLayer with exact text and integer-millisecond media spans. Producer token arrays are deliberately not normalized into LexBundler records—the complete token output remains preserved in the original JSON Asset. Raw tool segments are not reinterpreted as sentences, utterances, speaker turns, or pedagogical units.

## Authoritative transcript and MFA alignment import

The backend can import an existing UTF-8 transcript as authoritative source text without normalizing its Unicode, punctuation, whitespace, or line endings. The TXT file remains an immutable Asset, and the exact decoded string becomes an immutable TextRepresentation distinct from ASR. When explicitly requested, each non-empty source line also becomes a segment whose text span excludes only its line terminator; this is a narrow import option, not a global claim that lines are linguistic units.

LexBundler can also import already-produced Montreal Forced Aligner 3.4 HF JSON against an explicitly selected media Asset and authoritative TextRepresentation. The native JSON is preserved, while word and phone tiers become separate derived timing layers. Word labels are acoustic alignment units rather than canonical linguistic tokens: lexical labels are matched sequentially to exact authoritative text spans while intervening Unicode punctuation and whitespace are skipped, and mismatches fail instead of rewriting authoritative text. Silence labels (`<eps>` and `sil`) remain explicit timing segments. Phone labels are retained as generic segment labels and are never attached to invented character spans.

MFA seconds are converted to canonical integer milliseconds with `round(seconds * 1000)`.

The backend can execute a caller-configured MFA executable using `align_one_hf`; LexBundler does not discover or manage Conda environments, MFA installations, models, downloads, or authentication. The caller explicitly selects the media Asset and authoritative TextRepresentation. LexBundler materializes the exact stored text temporarily as UTF-8, validates MFA's staged native JSON, publishes it exclusively to the caller's durable path, and preserves it as an Asset before invoking the existing normalization workflow. External MFA execution and LexBundler normalization are separate ProcessingRuns with independent success or failure provenance.

## whisper.cpp execution foundation

The backend can also invoke a caller-supplied `whisper-cli` executable using a caller-supplied model path and explicit language. Execution occurs in temporary staging, and the caller must choose a new durable JSON output path. LexBundler preserves the native whisper.cpp JSON there before staging is removed, then normalizes that same artifact through the existing manual importer.

Tool execution and normalization are recorded separately: whisper.cpp produces an `asr` ProcessingRun and the JSON Asset, while LexBundler creates a subsequent `import` ProcessingRun and the analytical graph. This synchronous capability has no GUI, progress reporting, cancellation UI, executable discovery, model management, or download support yet; future UI calls must run it outside the Qt main thread.

## Audio clip rendering foundation

The backend can invoke a caller-supplied ffmpeg executable to render a selected SegmentMediaSpan as a durable MP3. The generated clip is registered as a derived Asset and receives an additional `rendered_clip` MediaSpan on the same Segment; the original source Asset and MediaSpan remain unchanged. Optional pre/post padding changes the rendered file boundaries, while the new clip-relative span identifies only where the original linguistic segment occurs inside that padded file.

Rendering is synchronous and backend-only. LexBundler does not discover or install ffmpeg, inspect media with ffprobe, or provide rendering UI.

## Anki listening-deck export

The backend can pair caller-selected SegmentTextSpans with caller-selected `rendered_clip` SegmentMediaSpans and export them as a durable Anki `.apkg`. The Listening v1 card front contains only Chinese audio. Its answer shows the exact selected Simplified Chinese text, retains the front audio for replay, and includes unobtrusive source information. Pinyin and English fields are part of the stable note model but remain empty for now.

Selection is entirely caller-supplied. LexBundler does not yet automatically select pedagogical material, generate Pinyin, translate text, or provide an Anki export UI; this is a narrow backend listening-comprehension path, not a curriculum generator.

## Read-only alignment review

The desktop review workspace consumes immutable application-level projections; Qt
widgets do not reconstruct joins or access SQLite. It browses sources and source
units, shows exact authoritative transcript lines, and projects matching MFA word
timing evidence onto each line. Authoritative linguistic line segmentation and MFA
acoustic word segmentation remain distinct layers.

Speech and preview playback ranges are calculated at read time from lexical MFA
media spans. Silence-aware study padding and wider context bounds are provisional
playback values only: they are not persisted as reviewed or pedagogical boundaries.
Boundary editing, review, and approval remain separate future workflows.

The waveform is a bounded, transient projection decoded from immutable source media;
it is not canonical project data. Editable proposed clip boundaries are likewise
in-memory review state. They remain distinct from immutable MFA evidence, and only
an explicit future approval workflow may persist reviewed pedagogical boundaries.
Waveform extraction invokes `ffmpeg` from `PATH` asynchronously and decodes only the
visible window to reduced-rate mono analysis PCM; source-audio playback remains Qt.

Source hierarchy is also distinct from future user-defined organizational
collections. Assets retain their independent content identity and attach to the
source hierarchy through roles and bindings; navigation alone does not justify a
collection model.

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

Future milestones will add user-facing import and execution flows, boundary review,
corpus analysis, and export controls. The current alignment workspace is read-only.
