# LexBundler Architecture Guide

- LexBundler is a PySide6 native desktop application.
- The core application must remain language- and source-agnostic.
- SQLite will be the canonical project datastore.
- Store timestamps internally as integer milliseconds.
- Raw imported, ASR, and source data must remain distinct from canonical reviewed utterance data.
- Preserve data provenance.
- Media files remain on disk; SQLite stores paths, metadata, and hashes rather than embedding media blobs.
- UI widgets must not directly query SQLite.
- Persistence should sit behind repository and service boundaries.
- Prefer Qt Model/View architecture for substantial tabular data.
- Long-running operations must not block the Qt UI thread.
- Source-specific functionality such as Mandarin, HSK, Whisper, or Anki must not contaminate the generic core architecture.
- Copyrighted corpus or source material must never be committed to the repository.
- Prefer straightforward, maintainable Python over premature abstractions.

