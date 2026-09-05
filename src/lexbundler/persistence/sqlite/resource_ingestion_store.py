"""SQLite implementation of atomic logical-resource ingestion."""

from lexbundler.domain.errors import CorpusIntegrityError
from lexbundler.persistence.resource_ingestion_store import (
    AssetAttachmentPlan, ResourceIngestionPlan, StoredAssetAttachment,
    StoredResourceIngestion,
)
from lexbundler.persistence.sqlite.serialization import dump_json, format_utc
from lexbundler.persistence.sqlite.store_base import SQLiteStoreBase


class SQLiteResourceIngestionStore(SQLiteStoreBase):
    def ingest_resource(self, plan: ResourceIngestionPlan) -> StoredResourceIngestion:
        timestamp = format_utc(plan.timestamp)
        with self._connection(write=True) as connection:
            run_cursor = connection.execute(
                """INSERT INTO processing_run (
                    run_uuid, process_type, tool_name, tool_version,
                    parameters_json, status, started_at, completed_at
                ) VALUES (?, 'import', 'LexBundler', NULL, '{}',
                          'running', ?, NULL)""",
                (str(plan.run_uuid), timestamp),
            )
            run_id = run_cursor.lastrowid

            if plan.existing_source_id is None:
                if plan.new_source_name is None:
                    raise CorpusIntegrityError("A new source name is required.")
                if connection.execute(
                    "SELECT 1 FROM corpus_source WHERE name = ?", (plan.new_source_name,)
                ).fetchone():
                    raise CorpusIntegrityError(
                        f'A source named "{plan.new_source_name}" already exists.'
                    )
                source_cursor = connection.execute(
                    """INSERT INTO corpus_source (
                        name, source_kind, language_tag, external_id, metadata_json,
                        created_by_run_id, created_at, updated_at
                    ) VALUES (?, 'corpus', ?, NULL, '{}', ?, ?, ?)""",
                    (plan.new_source_name, plan.language_tag, run_id, timestamp, timestamp),
                )
                source_id = source_cursor.lastrowid
            else:
                source_id = plan.existing_source_id
                if connection.execute(
                    "SELECT 1 FROM corpus_source WHERE id = ?", (source_id,)
                ).fetchone() is None:
                    raise CorpusIntegrityError("The selected source no longer exists.")

            parent_id = plan.existing_parent_unit_id
            if parent_id is not None:
                parent = connection.execute(
                    "SELECT source_id FROM source_unit WHERE id = ?", (parent_id,)
                ).fetchone()
                if parent is None or parent["source_id"] != source_id:
                    raise CorpusIntegrityError(
                        "The selected parent does not belong to the selected source."
                    )

            for label in plan.new_parent_labels:
                _reject_sibling_duplicate(connection, source_id, parent_id, label)
                cursor = connection.execute(
                    """INSERT INTO source_unit (
                        source_id, parent_id, kind, label, sequence, external_id,
                        metadata_json, created_by_run_id, confidence, created_at, updated_at
                    ) VALUES (?, ?, 'container', ?, NULL, NULL, '{}', ?, NULL, ?, ?)""",
                    (source_id, parent_id, label, run_id, timestamp, timestamp),
                )
                parent_id = cursor.lastrowid

            _reject_sibling_duplicate(
                connection, source_id, parent_id, plan.resource_name
            )
            resource_cursor = connection.execute(
                """INSERT INTO source_unit (
                    source_id, parent_id, kind, label, sequence, external_id,
                    metadata_json, created_by_run_id, confidence, created_at, updated_at
                ) VALUES (?, ?, 'resource', ?, NULL, NULL, ?, ?, NULL, ?, ?)""",
                (
                    source_id, parent_id, plan.resource_name,
                    dump_json({"resource_type": plan.resource_type}),
                    run_id, timestamp, timestamp,
                ),
            )
            resource_unit_id = resource_cursor.lastrowid

            asset_ids: list[int] = []
            asset_by_role: dict[str, int] = {}
            for asset in plan.assets:
                existing = connection.execute(
                    "SELECT id, byte_size FROM asset WHERE sha256 = ?", (asset.sha256,)
                ).fetchone()
                if existing is None:
                    cursor = connection.execute(
                        """INSERT INTO asset (
                            sha256, byte_size, asset_kind, mime_type,
                            created_by_run_id, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)""",
                        (
                            asset.sha256, asset.byte_size, asset.asset_kind,
                            asset.mime_type, run_id, timestamp,
                        ),
                    )
                    asset_id = cursor.lastrowid
                else:
                    if existing["byte_size"] != asset.byte_size:
                        raise CorpusIntegrityError(
                            "An existing SHA-256 asset has an inconsistent byte size."
                        )
                    asset_id = existing["id"]
                connection.execute(
                    """INSERT INTO asset_location (
                        asset_id, location_kind, location, created_by_run_id, observed_at
                    ) VALUES (?, 'filesystem', ?, ?, ?)
                    ON CONFLICT(asset_id, location_kind, location) DO NOTHING""",
                    (asset_id, asset.path, run_id, timestamp),
                )
                connection.execute(
                    """INSERT INTO asset_binding (
                        source_id, source_unit_id, asset_id, role, assignment_method,
                        confidence, processing_run_id, metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, 'manual_import', NULL, ?, ?, ?)""",
                    (
                        source_id, resource_unit_id, asset_id, asset.role, run_id,
                        dump_json({"storage": "referenced_original_location"}), timestamp,
                    ),
                )
                asset_ids.append(asset_id)
                asset_by_role[asset.role] = asset_id

            representation_id = None
            if plan.text is not None:
                text_asset_id = asset_by_role[plan.text.binding_role]
                representation_cursor = connection.execute(
                    """INSERT INTO text_representation (
                        source_id, source_unit_id, representation_kind, language_tag,
                        content, source_asset_id, derived_from_id, created_by_run_id,
                        metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
                    (
                        source_id, resource_unit_id, plan.text.representation_kind,
                        plan.language_tag, plan.text.content, text_asset_id, run_id,
                        dump_json({
                            "format": "utf-8-plain-text",
                            "authority": plan.text.authority,
                            "newline_decoding": "exact (no universal-newline conversion)",
                        }), timestamp,
                    ),
                )
                representation_id = representation_cursor.lastrowid
                layer_cursor = connection.execute(
                    """INSERT INTO segment_layer (
                        source_id, source_unit_id, name, layer_kind, language_tag,
                        created_by_run_id, metadata_json, created_at
                    ) VALUES (?, ?, ?, 'transcript_line', ?, ?, ?, ?)""",
                    (
                        source_id, resource_unit_id,
                        "imported transcript non-empty lines", plan.language_tag, run_id,
                        dump_json({
                            "segmentation_policy": "explicit_nonempty_source_lines",
                            "authority": plan.text.authority,
                        }), timestamp,
                    ),
                )
                layer_id = layer_cursor.lastrowid
                span_role = (
                    "authoritative" if plan.text.authority == "source"
                    else "machine_unreviewed"
                )
                for sequence, (start, end) in enumerate(plan.text.spans):
                    segment_cursor = connection.execute(
                        """INSERT INTO segment (
                            layer_id, parent_id, kind, label, sequence, external_id,
                            confidence, created_by_run_id, metadata_json, created_at
                        ) VALUES (?, NULL, 'transcript_line', NULL, ?, NULL,
                                  NULL, ?, '{}', ?)""",
                        (layer_id, sequence, run_id, timestamp),
                    )
                    connection.execute(
                        """INSERT INTO segment_text_span (
                            segment_id, text_representation_id, start_offset, end_offset,
                            role, confidence, created_by_run_id, metadata_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, NULL, ?, '{}', ?)""",
                        (
                            segment_cursor.lastrowid, representation_id, start, end,
                            span_role, run_id, timestamp,
                        ),
                    )

            parameters = {
                "operation": "add_resource",
                "resource_type": plan.resource_type,
                "source_id": source_id,
                "parent_unit_id": parent_id,
                "resource_unit_id": resource_unit_id,
                "asset_roles": [asset.role for asset in plan.assets],
                "text_provenance": plan.text.authority if plan.text else None,
                "storage": "referenced_original_location",
            }
            connection.execute(
                """UPDATE processing_run
                   SET parameters_json = ?, status = 'succeeded', completed_at = ?
                   WHERE id = ?""",
                (dump_json(parameters), timestamp, run_id),
            )
            return StoredResourceIngestion(
                source_id, resource_unit_id, tuple(asset_ids),
                representation_id, run_id,
            )

    def attach_asset(self, plan: AssetAttachmentPlan) -> StoredAssetAttachment:
        timestamp = format_utc(plan.timestamp)
        with self._connection(write=True) as connection:
            unit = connection.execute(
                "SELECT source_id FROM source_unit WHERE id = ?",
                (plan.resource_unit_id,),
            ).fetchone()
            if unit is None or unit["source_id"] != plan.source_id:
                raise CorpusIntegrityError("The selected resource no longer exists.")
            run_cursor = connection.execute(
                """INSERT INTO processing_run (
                    run_uuid, process_type, tool_name, tool_version,
                    parameters_json, status, started_at, completed_at
                ) VALUES (?, 'import', 'LexBundler', NULL, '{}',
                          'running', ?, NULL)""",
                (str(plan.run_uuid), timestamp),
            )
            run_id = run_cursor.lastrowid
            asset = plan.asset
            existing = connection.execute(
                "SELECT id, byte_size FROM asset WHERE sha256 = ?", (asset.sha256,)
            ).fetchone()
            if existing is None:
                cursor = connection.execute(
                    """INSERT INTO asset (
                        sha256, byte_size, asset_kind, mime_type,
                        created_by_run_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (asset.sha256, asset.byte_size, asset.asset_kind,
                     asset.mime_type, run_id, timestamp),
                )
                asset_id = cursor.lastrowid
            else:
                if existing["byte_size"] != asset.byte_size:
                    raise CorpusIntegrityError(
                        "An existing SHA-256 asset has an inconsistent byte size."
                    )
                asset_id = existing["id"]
            connection.execute(
                """INSERT INTO asset_location (
                    asset_id, location_kind, location, created_by_run_id, observed_at
                ) VALUES (?, 'filesystem', ?, ?, ?)
                ON CONFLICT(asset_id, location_kind, location) DO NOTHING""",
                (asset_id, asset.path, run_id, timestamp),
            )
            connection.execute(
                """INSERT INTO asset_binding (
                    source_id, source_unit_id, asset_id, role, assignment_method,
                    confidence, processing_run_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, 'manual_import', NULL, ?, ?, ?)""",
                (plan.source_id, plan.resource_unit_id, asset_id, asset.role,
                 run_id, dump_json({
                     "storage": "referenced_original_location"
                 }), timestamp),
            )
            representation_id = None
            if plan.text is not None:
                representation_id = _insert_text_graph(
                    connection, source_id=plan.source_id,
                    resource_unit_id=plan.resource_unit_id,
                    asset_id=asset_id, text=plan.text,
                    language_tag=plan.language_tag,
                    run_id=run_id, timestamp=timestamp,
                )
            parameters = {
                "operation": "add_asset_to_resource",
                "source_id": plan.source_id,
                "resource_unit_id": plan.resource_unit_id,
                "asset_id": asset_id,
                "asset_role": asset.role,
                "text_provenance": plan.text.authority if plan.text else None,
                "storage": "referenced_original_location",
            }
            connection.execute(
                """UPDATE processing_run
                   SET parameters_json = ?, status = 'succeeded', completed_at = ?
                   WHERE id = ?""",
                (dump_json(parameters), timestamp, run_id),
            )
            return StoredAssetAttachment(asset_id, representation_id, run_id)


def _reject_sibling_duplicate(connection, source_id: int,
                              parent_id: int | None, label: str) -> None:
    row = connection.execute(
        """SELECT 1 FROM source_unit
           WHERE source_id = ? AND label = ?
             AND ((parent_id IS NULL AND ? IS NULL) OR parent_id = ?)""",
        (source_id, label, parent_id, parent_id),
    ).fetchone()
    if row is not None:
        raise CorpusIntegrityError(
            f'A unit named "{label}" already exists at that hierarchy level.'
        )


def _insert_text_graph(connection, *, source_id: int, resource_unit_id: int,
                       asset_id: int, text, language_tag: str | None,
                       run_id: int, timestamp: str) -> int:
    representation = connection.execute(
        """INSERT INTO text_representation (
            source_id, source_unit_id, representation_kind, language_tag,
            content, source_asset_id, derived_from_id, created_by_run_id,
            metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)""",
        (source_id, resource_unit_id, text.representation_kind, language_tag,
         text.content, asset_id, run_id, dump_json({
             "format": "utf-8-plain-text", "authority": text.authority,
             "newline_decoding": "exact (no universal-newline conversion)",
         }), timestamp),
    )
    representation_id = representation.lastrowid
    layer = connection.execute(
        """INSERT INTO segment_layer (
            source_id, source_unit_id, name, layer_kind, language_tag,
            created_by_run_id, metadata_json, created_at
        ) VALUES (?, ?, 'imported transcript non-empty lines',
                  'transcript_line', ?, ?, ?, ?)""",
        (source_id, resource_unit_id, language_tag, run_id, dump_json({
            "segmentation_policy": "explicit_nonempty_source_lines",
            "authority": text.authority,
        }), timestamp),
    )
    role = "authoritative" if text.authority == "source" else "machine_unreviewed"
    for sequence, (start, end) in enumerate(text.spans):
        segment = connection.execute(
            """INSERT INTO segment (
                layer_id, parent_id, kind, label, sequence, external_id,
                confidence, created_by_run_id, metadata_json, created_at
            ) VALUES (?, NULL, 'transcript_line', NULL, ?, NULL,
                      NULL, ?, '{}', ?)""",
            (layer.lastrowid, sequence, run_id, timestamp),
        )
        connection.execute(
            """INSERT INTO segment_text_span (
                segment_id, text_representation_id, start_offset, end_offset,
                role, confidence, created_by_run_id, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, '{}', ?)""",
            (segment.lastrowid, representation_id, start, end,
             role, run_id, timestamp),
        )
    return representation_id
