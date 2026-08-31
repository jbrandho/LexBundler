"""Domain model for a LexBundler corpus project."""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ProjectMetadata:
    """User-facing identity and descriptive metadata for a corpus workspace."""

    project_uuid: UUID
    name: str
    primary_language_tag: str | None
    primary_language_name: str | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Project name must not be empty.")
        for field_name in ("created_at", "updated_at"):
            value = getattr(self, field_name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field_name} must be timezone-aware.")
            if value.utcoffset() != UTC.utcoffset(value):
                raise ValueError(f"{field_name} must use UTC.")

