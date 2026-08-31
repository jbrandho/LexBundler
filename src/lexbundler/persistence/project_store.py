"""Application-facing contracts for project persistence."""

from pathlib import Path
from typing import Protocol

from lexbundler.domain.project import ProjectMetadata


class ProjectStore(Protocol):
    """An opened project persistence session."""

    @property
    def metadata(self) -> ProjectMetadata:
        """Return the project's domain metadata."""
        ...

    def close(self) -> None:
        """Release resources owned by this store."""
        ...


class ProjectStoreFactory(Protocol):
    """Create and open projects using a configured persistence backend."""

    def create(self, destination: Path, metadata: ProjectMetadata) -> ProjectStore:
        """Create a project without replacing an existing destination."""
        ...

    def open(self, location: Path) -> ProjectStore:
        """Validate and open an existing project."""
        ...

