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

