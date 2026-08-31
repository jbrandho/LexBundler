import sqlite3
from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.persistence.sqlite import SQLiteProjectStoreFactory


def test_direct_text_and_segment_self_parenting_are_rejected(
    tmp_path: Path,
) -> None:
    path = tmp_path / "constraints.lexbundler"
    service = ProjectService(SQLiteProjectStoreFactory())
    service.create_project(path, name="Constraints")
    source = service.corpus.create_source("Source")
    layer = service.text_segments.create_segment_layer(
        source.id, name="Layer", layer_kind="manual"
    )
    service.close_project()

    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO text_representation (
                    id, source_id, representation_kind, content, derived_from_id,
                    metadata_json, created_at
                ) VALUES (100, ?, 'derived', 'text', 100, '{}',
                          '2026-08-31T10:00:00Z')
                """,
                (source.id,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO segment (
                    id, layer_id, parent_id, kind, metadata_json, created_at
                ) VALUES (200, ?, 200, 'unit', '{}', '2026-08-31T10:00:00Z')
                """,
                (layer.id,),
            )

