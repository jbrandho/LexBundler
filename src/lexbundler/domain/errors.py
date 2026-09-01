"""Application-facing project lifecycle errors."""


class ProjectError(Exception):
    """Base class for project lifecycle failures."""


class ProjectAlreadyExistsError(ProjectError):
    """Raised when project creation would overwrite an existing path."""


class ProjectNotFoundError(ProjectError):
    """Raised when the requested project does not exist."""


class ProjectAlreadyOpenError(ProjectError):
    """Raised when a lifecycle operation requires no open project."""


class InvalidProjectError(ProjectError):
    """Raised when input is not a valid LexBundler project."""


class UnsupportedSchemaVersionError(ProjectError):
    """Raised when a project schema is newer than this application supports."""

    def __init__(self, found: int, supported: int) -> None:
        self.found = found
        self.supported = supported
        super().__init__(
            f"Project schema version {found} is newer than supported version "
            f"{supported}."
        )


class ProjectMigrationError(ProjectError):
    """Raised when a project cannot be migrated safely."""


class InvalidMigrationStateError(ProjectMigrationError):
    """Raised when migration versions or registry state are inconsistent."""


class InvalidProjectMetadataError(ProjectError):
    """Raised when supplied project metadata is incomplete or invalid."""


class CorpusError(Exception):
    """Base class for source, asset, and provenance operation failures."""


class NoOpenProjectError(CorpusError):
    """Raised when a corpus operation requires an open project."""


class CorpusEntityNotFoundError(CorpusError):
    """Raised when a requested corpus entity does not exist."""


class CorpusIntegrityError(CorpusError):
    """Raised when a corpus relationship or constraint is invalid."""


class InvalidCorpusDataError(CorpusError):
    """Raised when application input is malformed."""


class AssetFileError(CorpusError):
    """Raised when a local asset path cannot be safely read."""


class CorpusStorageError(CorpusError):
    """Raised when corpus persistence fails unexpectedly."""


class InvalidSpanError(InvalidCorpusDataError):
    """Raised when a text or media range is malformed or out of bounds."""


class WhisperImportError(CorpusError):
    """Base class for manual whisper.cpp JSON import failures."""


class WhisperCppFormatError(WhisperImportError):
    """Raised when a whisper.cpp JSON artifact lacks required valid data."""


class WhisperExecutionError(CorpusError):
    """Raised when whisper.cpp configuration or execution fails."""


class MediaRenderError(CorpusError):
    """Raised when a durable derived-media rendering operation fails."""
