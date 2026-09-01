"""Project lifecycle application service."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.text_segment_service import TextSegmentService
from lexbundler.application.whisper_import_service import WhisperImportService
from lexbundler.domain.errors import (
    InvalidProjectMetadataError,
    ProjectAlreadyOpenError,
)
from lexbundler.domain.project import ProjectMetadata
from lexbundler.persistence.project_store import ProjectStore, ProjectStoreFactory

PROJECT_EXTENSION = ".lexbundler"


class ProjectService:
    """Coordinate explicit single-project lifecycle operations."""

    def __init__(self, store_factory: ProjectStoreFactory) -> None:
        self._store_factory = store_factory
        self._current_store: ProjectStore | None = None
        self._corpus_service = CorpusService()
        self._text_segment_service = TextSegmentService()
        self._whisper_import_service = WhisperImportService(
            self._corpus_service, self._text_segment_service
        )

    @property
    def corpus(self) -> CorpusService:
        """Return corpus operations scoped to the current project."""
        return self._corpus_service

    @property
    def text_segments(self) -> TextSegmentService:
        """Return text and segmentation operations for the current project."""
        return self._text_segment_service

    @property
    def whisper_imports(self) -> WhisperImportService:
        """Return the manual whisper.cpp JSON import workflow."""
        return self._whisper_import_service

    @property
    def current_project(self) -> ProjectMetadata | None:
        """Return metadata for the open project, if any."""
        if self._current_store is None:
            return None
        return self._current_store.metadata

    def create_project(
        self,
        destination: Path,
        *,
        name: str,
        primary_language_tag: str | None = None,
        primary_language_name: str | None = None,
    ) -> ProjectMetadata:
        """Create and open a project at *destination*."""
        self._require_closed()
        normalized_name = name.strip()
        if not normalized_name:
            raise InvalidProjectMetadataError("Project name must not be empty.")

        path = Path(destination)
        if path.suffix.lower() != PROJECT_EXTENSION:
            path = Path(f"{path}{PROJECT_EXTENSION}")

        now = datetime.now(UTC).replace(microsecond=0)
        metadata = ProjectMetadata(
            project_uuid=uuid4(),
            name=normalized_name,
            primary_language_tag=_optional_text(primary_language_tag),
            primary_language_name=_optional_text(primary_language_name),
            created_at=now,
            updated_at=now,
        )
        self._current_store = self._store_factory.create(path, metadata)
        self._corpus_service._attach(self._current_store)
        self._text_segment_service._attach(self._current_store)
        return metadata

    def open_project(self, location: Path) -> ProjectMetadata:
        """Validate and open an existing project."""
        self._require_closed()
        store = self._store_factory.open(Path(location))
        self._current_store = store
        self._corpus_service._attach(store)
        self._text_segment_service._attach(store)
        return store.metadata

    def close_project(self) -> None:
        """Close the current project; do nothing when already closed."""
        if self._current_store is None:
            return
        store = self._current_store
        self._current_store = None
        self._corpus_service._detach()
        self._text_segment_service._detach()
        store.close()

    def _require_closed(self) -> None:
        if self._current_store is not None:
            raise ProjectAlreadyOpenError(
                "Close the current project before creating or opening another."
            )


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
