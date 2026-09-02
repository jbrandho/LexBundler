"""SQLite persistence for text representations and analytical segments."""

import sqlite3
from datetime import datetime

from lexbundler.domain.corpus import JsonObject
from lexbundler.domain.errors import (
    CorpusEntityNotFoundError,
    CorpusIntegrityError,
    InvalidSpanError,
)
from lexbundler.domain.text_segments import (
    AlignmentGraph,
    AlignmentGraphSpec,
    FlatSegmentGraphSpec,
    FlatSegmentSpec,
    Segment,
    SegmentLayer,
    SegmentMediaSpan,
    SegmentSpeaker,
    SegmentTextSpan,
    Speaker,
    TextRepresentation,
    TextOnlySegmentGraphSpec,
    TextSegmentGraph,
)
from lexbundler.persistence.sqlite.serialization import (
    dump_json,
    format_utc,
    load_json,
    parse_utc,
)
from lexbundler.persistence.sqlite.store_base import SQLiteStoreBase


def _insert_layer(
    connection: sqlite3.Connection, *, source_id: int,
    source_unit_id: int | None, name: str, layer_kind: str,
    language_tag: str | None, created_by_run_id: int | None,
    metadata: JsonObject, timestamp: str,
) -> SegmentLayer:
    cursor = connection.execute(
        """INSERT INTO segment_layer (
            source_id, source_unit_id, name, layer_kind, language_tag,
            created_by_run_id, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (source_id, source_unit_id, name, layer_kind, language_tag,
         created_by_run_id, dump_json(metadata), timestamp),
    )
    return _layer_from_row(_row_by_id(connection, "segment_layer", cursor.lastrowid))


def _insert_segment(
    connection: sqlite3.Connection, *, layer_id: int, kind: str,
    label: str | None, sequence: int, created_by_run_id: int | None,
    timestamp: str,
) -> Segment:
    cursor = connection.execute(
        """INSERT INTO segment (
            layer_id, parent_id, kind, label, sequence, external_id,
            confidence, created_by_run_id, metadata_json, created_at
        ) VALUES (?, NULL, ?, ?, ?, NULL, NULL, ?, '{}', ?)""",
        (layer_id, kind, label, sequence, created_by_run_id, timestamp),
    )
    return _segment_from_row(_row_by_id(connection, "segment", cursor.lastrowid))


def _insert_text_span(
    connection: sqlite3.Connection, *, segment_id: int,
    representation_id: int, start: int, end: int, role: str | None,
    created_by_run_id: int | None, timestamp: str,
) -> SegmentTextSpan:
    cursor = connection.execute(
        """INSERT INTO segment_text_span (
            segment_id, text_representation_id, start_offset, end_offset,
            role, confidence, created_by_run_id, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, '{}', ?)""",
        (segment_id, representation_id, start, end, role,
         created_by_run_id, timestamp),
    )
    return _text_span_from_row(
        _row_by_id(connection, "segment_text_span", cursor.lastrowid)
    )


def _insert_media_span(
    connection: sqlite3.Connection, *, segment_id: int, asset_id: int,
    start: int, end: int, role: str | None,
    created_by_run_id: int | None, timestamp: str,
) -> SegmentMediaSpan:
    cursor = connection.execute(
        """INSERT INTO segment_media_span (
            segment_id, asset_id, start_ms, end_ms, role, confidence,
            created_by_run_id, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, '{}', ?)""",
        (segment_id, asset_id, start, end, role, created_by_run_id, timestamp),
    )
    return _media_span_from_row(
        _row_by_id(connection, "segment_media_span", cursor.lastrowid)
    )


def _insert_flat_segment(
    connection: sqlite3.Connection,
    *,
    item: FlatSegmentSpec,
    layer_id: int,
    representation_id: int,
    segment_kind: str,
    text_span_role: str | None,
    media_span_role: str | None,
    created_by_run_id: int | None,
    timestamp: str,
) -> tuple[Segment, SegmentTextSpan | None, SegmentMediaSpan]:
    """Insert one item in a caller-owned graph transaction."""
    segment_cursor = connection.execute(
        """
        INSERT INTO segment (
            layer_id, parent_id, kind, label, sequence, external_id,
            confidence, created_by_run_id, metadata_json, created_at
        ) VALUES (?, NULL, ?, NULL, ?, ?, NULL, ?, '{}', ?)
        """,
        (
            layer_id,
            segment_kind,
            item.sequence,
            item.external_id,
            created_by_run_id,
            timestamp,
        ),
    )
    segment = _segment_from_row(
        _row_by_id(connection, "segment", segment_cursor.lastrowid)
    )

    text_span = None
    if item.text_start is not None and item.text_end is not None:
        text_cursor = connection.execute(
            """
            INSERT INTO segment_text_span (
                segment_id, text_representation_id, start_offset, end_offset,
                role, confidence, created_by_run_id, metadata_json, created_at
            ) VALUES (?, ?, ?, ?, ?, NULL, ?, '{}', ?)
            """,
            (
                segment.id,
                representation_id,
                item.text_start,
                item.text_end,
                text_span_role,
                created_by_run_id,
                timestamp,
            ),
        )
        text_span = _text_span_from_row(
            _row_by_id(connection, "segment_text_span", text_cursor.lastrowid)
        )

    media_cursor = connection.execute(
        """
        INSERT INTO segment_media_span (
            segment_id, asset_id, start_ms, end_ms, role, confidence,
            created_by_run_id, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, NULL, ?, '{}', ?)
        """,
        (
            segment.id,
            item.media_asset_id,
            item.media_start_ms,
            item.media_end_ms,
            media_span_role,
            created_by_run_id,
            timestamp,
        ),
    )
    media_span = _media_span_from_row(
        _row_by_id(connection, "segment_media_span", media_cursor.lastrowid)
    )
    return segment, text_span, media_span


class SQLiteTextSegmentStore(SQLiteStoreBase):
    """Focused SQLite operations for immutable text and segmentation."""

    def create_text_only_segment_graph(
        self, spec: TextOnlySegmentGraphSpec, *, created_at: datetime
    ) -> TextSegmentGraph:
        timestamp = format_utc(created_at)
        with self._connection(write=True) as connection:
            representation_cursor = connection.execute(
                """
                INSERT INTO text_representation (
                    source_id, source_unit_id, representation_kind, language_tag,
                    content, source_asset_id, derived_from_id, created_by_run_id,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    spec.source_id, spec.source_unit_id, spec.representation_kind,
                    spec.language_tag, spec.content, spec.source_asset_id,
                    spec.created_by_run_id, dump_json(spec.representation_metadata),
                    timestamp,
                ),
            )
            representation = _text_representation_from_row(
                _row_by_id(connection, "text_representation", representation_cursor.lastrowid)
            )
            layer = _insert_layer(
                connection, source_id=spec.source_id,
                source_unit_id=spec.source_unit_id, name=spec.layer_name,
                layer_kind=spec.layer_kind, language_tag=spec.language_tag,
                created_by_run_id=spec.created_by_run_id,
                metadata=spec.layer_metadata, timestamp=timestamp,
            )
            segments: list[Segment] = []
            spans: list[SegmentTextSpan] = []
            for item in spec.segments:
                segment = _insert_segment(
                    connection, layer_id=layer.id, kind=spec.segment_kind,
                    label=None, sequence=item.sequence,
                    created_by_run_id=spec.created_by_run_id, timestamp=timestamp,
                )
                span = _insert_text_span(
                    connection, segment_id=segment.id,
                    representation_id=representation.id,
                    start=item.start_offset, end=item.end_offset,
                    role=spec.text_span_role,
                    created_by_run_id=spec.created_by_run_id, timestamp=timestamp,
                )
                segments.append(segment)
                spans.append(span)
            return TextSegmentGraph(
                representation, layer, tuple(segments), tuple(spans), ()
            )

    def create_alignment_graph(
        self, spec: AlignmentGraphSpec, *, created_at: datetime
    ) -> AlignmentGraph:
        timestamp = format_utc(created_at)
        with self._connection(write=True) as connection:
            representation = _row_by_id(
                connection, "text_representation", spec.text_representation_id
            )
            if representation["source_id"] != spec.source_id:
                raise CorpusIntegrityError(
                    "Alignment representation and layers must share a source."
                )
            _row_by_id(connection, "asset", spec.media_asset_id)
            layers: list[SegmentLayer] = []
            segments: list[Segment] = []
            text_spans: list[SegmentTextSpan] = []
            media_spans: list[SegmentMediaSpan] = []
            for layer_spec in spec.layers:
                layer = _insert_layer(
                    connection, source_id=spec.source_id,
                    source_unit_id=spec.source_unit_id, name=layer_spec.name,
                    layer_kind=layer_spec.layer_kind,
                    language_tag=spec.language_tag,
                    created_by_run_id=spec.created_by_run_id,
                    metadata=layer_spec.metadata, timestamp=timestamp,
                )
                layers.append(layer)
                for item in layer_spec.segments:
                    segment = _insert_segment(
                        connection, layer_id=layer.id, kind=layer_spec.segment_kind,
                        label=item.label, sequence=item.sequence,
                        created_by_run_id=spec.created_by_run_id, timestamp=timestamp,
                    )
                    segments.append(segment)
                    if item.text_start is not None and item.text_end is not None:
                        if item.text_end > len(representation["content"]):
                            raise InvalidSpanError(
                                "Text span ends beyond the referenced representation."
                            )
                        text_spans.append(_insert_text_span(
                            connection, segment_id=segment.id,
                            representation_id=spec.text_representation_id,
                            start=item.text_start, end=item.text_end,
                            role=spec.text_span_role,
                            created_by_run_id=spec.created_by_run_id,
                            timestamp=timestamp,
                        ))
                    media_spans.append(_insert_media_span(
                        connection, segment_id=segment.id,
                        asset_id=spec.media_asset_id, start=item.start_ms,
                        end=item.end_ms, role=spec.media_span_role,
                        created_by_run_id=spec.created_by_run_id,
                        timestamp=timestamp,
                    ))
            return AlignmentGraph(
                tuple(layers), tuple(segments), tuple(text_spans), tuple(media_spans)
            )

    def create_flat_segment_graph(
        self, spec: FlatSegmentGraphSpec, *, created_at: datetime
    ) -> TextSegmentGraph:
        timestamp = format_utc(created_at)
        with self._connection(write=True) as connection:
            representation_cursor = connection.execute(
                """
                INSERT INTO text_representation (
                    source_id, source_unit_id, representation_kind, language_tag,
                    content, source_asset_id, derived_from_id, created_by_run_id,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    spec.source_id,
                    spec.source_unit_id,
                    spec.representation_kind,
                    spec.language_tag,
                    spec.content,
                    spec.source_asset_id,
                    spec.created_by_run_id,
                    dump_json(spec.representation_metadata),
                    timestamp,
                ),
            )
            representation = _text_representation_from_row(
                _row_by_id(
                    connection, "text_representation", representation_cursor.lastrowid
                )
            )
            layer_cursor = connection.execute(
                """
                INSERT INTO segment_layer (
                    source_id, source_unit_id, name, layer_kind, language_tag,
                    created_by_run_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec.source_id,
                    spec.source_unit_id,
                    spec.layer_name,
                    spec.layer_kind,
                    spec.language_tag,
                    spec.created_by_run_id,
                    dump_json(spec.layer_metadata),
                    timestamp,
                ),
            )
            layer = _layer_from_row(
                _row_by_id(connection, "segment_layer", layer_cursor.lastrowid)
            )

            segments: list[Segment] = []
            text_spans: list[SegmentTextSpan] = []
            media_spans: list[SegmentMediaSpan] = []
            for item in spec.segments:
                segment, text_span, media_span = _insert_flat_segment(
                    connection,
                    item=item,
                    layer_id=layer.id,
                    representation_id=representation.id,
                    segment_kind=spec.segment_kind,
                    text_span_role=spec.text_span_role,
                    media_span_role=spec.media_span_role,
                    created_by_run_id=spec.created_by_run_id,
                    timestamp=timestamp,
                )
                segments.append(segment)
                if text_span is not None:
                    text_spans.append(text_span)
                media_spans.append(media_span)
            return TextSegmentGraph(
                representation=representation,
                layer=layer,
                segments=tuple(segments),
                text_spans=tuple(text_spans),
                media_spans=tuple(media_spans),
            )

    def create_text_representation(
        self,
        *,
        source_id: int,
        source_unit_id: int | None,
        representation_kind: str,
        language_tag: str | None,
        content: str,
        source_asset_id: int | None,
        derived_from_id: int | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> TextRepresentation:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO text_representation (
                    source_id, source_unit_id, representation_kind, language_tag,
                    content, source_asset_id, derived_from_id, created_by_run_id,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    source_unit_id,
                    representation_kind,
                    language_tag,
                    content,
                    source_asset_id,
                    derived_from_id,
                    created_by_run_id,
                    dump_json(metadata),
                    format_utc(created_at),
                ),
            )
            return _text_representation_from_row(
                _row_by_id(connection, "text_representation", cursor.lastrowid)
            )

    def get_text_representation(self, representation_id: int) -> TextRepresentation:
        with self._connection() as connection:
            return _text_representation_from_row(
                _row_by_id(connection, "text_representation", representation_id)
            )

    def list_text_representations(self, source_id: int) -> list[TextRepresentation]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM text_representation WHERE source_id = ? ORDER BY id",
                (source_id,),
            ).fetchall()
            return [_text_representation_from_row(row) for row in rows]

    def create_segment_layer(
        self,
        *,
        source_id: int,
        source_unit_id: int | None,
        name: str,
        layer_kind: str,
        language_tag: str | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> SegmentLayer:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO segment_layer (
                    source_id, source_unit_id, name, layer_kind, language_tag,
                    created_by_run_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    source_unit_id,
                    name,
                    layer_kind,
                    language_tag,
                    created_by_run_id,
                    dump_json(metadata),
                    format_utc(created_at),
                ),
            )
            return _layer_from_row(
                _row_by_id(connection, "segment_layer", cursor.lastrowid)
            )

    def get_segment_layer(self, layer_id: int) -> SegmentLayer:
        with self._connection() as connection:
            return _layer_from_row(_row_by_id(connection, "segment_layer", layer_id))

    def list_segment_layers(self, source_id: int) -> list[SegmentLayer]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM segment_layer WHERE source_id = ? ORDER BY id",
                (source_id,),
            ).fetchall()
            return [_layer_from_row(row) for row in rows]

    def create_segment(
        self,
        *,
        layer_id: int,
        parent_id: int | None,
        kind: str,
        label: str | None,
        sequence: int | None,
        external_id: str | None,
        confidence: float | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> Segment:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO segment (
                    layer_id, parent_id, kind, label, sequence, external_id,
                    confidence, created_by_run_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    layer_id,
                    parent_id,
                    kind,
                    label,
                    sequence,
                    external_id,
                    confidence,
                    created_by_run_id,
                    dump_json(metadata),
                    format_utc(created_at),
                ),
            )
            return _segment_from_row(
                _row_by_id(connection, "segment", cursor.lastrowid)
            )

    def get_segment(self, segment_id: int) -> Segment:
        with self._connection() as connection:
            return _segment_from_row(_row_by_id(connection, "segment", segment_id))

    def list_segments(self, layer_id: int) -> list[Segment]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT * FROM segment WHERE layer_id = ?
                ORDER BY CASE WHEN sequence IS NULL THEN 1 ELSE 0 END, sequence, id
                """,
                (layer_id,),
            ).fetchall()
            return [_segment_from_row(row) for row in rows]

    def add_segment_text_span(
        self,
        *,
        segment_id: int,
        text_representation_id: int,
        start_offset: int,
        end_offset: int,
        role: str | None,
        confidence: float | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> SegmentTextSpan:
        with self._connection(write=True) as connection:
            segment_source = _segment_source(connection, segment_id)
            representation = _row_by_id(
                connection, "text_representation", text_representation_id
            )
            if representation["source_id"] != segment_source:
                raise CorpusIntegrityError(
                    "Text span representation and segment must share a source."
                )
            if end_offset > len(representation["content"]):
                raise InvalidSpanError(
                    "Text span ends beyond the referenced representation."
                )
            cursor = connection.execute(
                """
                INSERT INTO segment_text_span (
                    segment_id, text_representation_id, start_offset, end_offset,
                    role, confidence, created_by_run_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    text_representation_id,
                    start_offset,
                    end_offset,
                    role,
                    confidence,
                    created_by_run_id,
                    dump_json(metadata),
                    format_utc(created_at),
                ),
            )
            return _text_span_from_row(
                _row_by_id(connection, "segment_text_span", cursor.lastrowid)
            )

    def get_segment_text_span(self, span_id: int) -> SegmentTextSpan:
        with self._connection() as connection:
            return _text_span_from_row(
                _row_by_id(connection, "segment_text_span", span_id)
            )

    def list_segment_text_spans(self, segment_id: int) -> list[SegmentTextSpan]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM segment_text_span WHERE segment_id = ? ORDER BY id",
                (segment_id,),
            ).fetchall()
            return [_text_span_from_row(row) for row in rows]

    def add_segment_media_span(
        self,
        *,
        segment_id: int,
        asset_id: int,
        start_ms: int,
        end_ms: int,
        role: str | None,
        confidence: float | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> SegmentMediaSpan:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO segment_media_span (
                    segment_id, asset_id, start_ms, end_ms, role, confidence,
                    created_by_run_id, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    asset_id,
                    start_ms,
                    end_ms,
                    role,
                    confidence,
                    created_by_run_id,
                    dump_json(metadata),
                    format_utc(created_at),
                ),
            )
            return _media_span_from_row(
                _row_by_id(connection, "segment_media_span", cursor.lastrowid)
            )

    def get_segment_media_span(self, span_id: int) -> SegmentMediaSpan:
        with self._connection() as connection:
            return _media_span_from_row(
                _row_by_id(connection, "segment_media_span", span_id)
            )

    def list_segment_media_spans(self, segment_id: int) -> list[SegmentMediaSpan]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM segment_media_span WHERE segment_id = ? ORDER BY id",
                (segment_id,),
            ).fetchall()
            return [_media_span_from_row(row) for row in rows]

    def create_speaker(
        self,
        *,
        source_id: int,
        name: str,
        external_id: str | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> Speaker:
        with self._connection(write=True) as connection:
            cursor = connection.execute(
                """
                INSERT INTO speaker (
                    source_id, name, external_id, created_by_run_id,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    name,
                    external_id,
                    created_by_run_id,
                    dump_json(metadata),
                    format_utc(created_at),
                ),
            )
            return _speaker_from_row(
                _row_by_id(connection, "speaker", cursor.lastrowid)
            )

    def get_speaker(self, speaker_id: int) -> Speaker:
        with self._connection() as connection:
            return _speaker_from_row(_row_by_id(connection, "speaker", speaker_id))

    def list_speakers(self, source_id: int) -> list[Speaker]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM speaker WHERE source_id = ? ORDER BY id", (source_id,)
            ).fetchall()
            return [_speaker_from_row(row) for row in rows]

    def add_segment_speaker(
        self,
        *,
        segment_id: int,
        speaker_id: int,
        role: str | None,
        confidence: float | None,
        created_by_run_id: int | None,
        metadata: JsonObject,
        created_at: datetime,
    ) -> SegmentSpeaker:
        with self._connection(write=True) as connection:
            segment_source = _segment_source(connection, segment_id)
            speaker = _row_by_id(connection, "speaker", speaker_id)
            if speaker["source_id"] != segment_source:
                raise CorpusIntegrityError(
                    "Speaker and segment must belong to the same source."
                )
            cursor = connection.execute(
                """
                INSERT INTO segment_speaker (
                    segment_id, speaker_id, role, confidence, created_by_run_id,
                    metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    segment_id,
                    speaker_id,
                    role,
                    confidence,
                    created_by_run_id,
                    dump_json(metadata),
                    format_utc(created_at),
                ),
            )
            return _segment_speaker_from_row(
                _row_by_id(connection, "segment_speaker", cursor.lastrowid)
            )

    def list_segment_speakers(self, segment_id: int) -> list[SegmentSpeaker]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM segment_speaker WHERE segment_id = ? ORDER BY id",
                (segment_id,),
            ).fetchall()
            return [_segment_speaker_from_row(row) for row in rows]


def _segment_source(connection: sqlite3.Connection, segment_id: int) -> int:
    row = connection.execute(
        """
        SELECT segment_layer.source_id
        FROM segment
        JOIN segment_layer ON segment_layer.id = segment.layer_id
        WHERE segment.id = ?
        """,
        (segment_id,),
    ).fetchone()
    if row is None:
        raise CorpusEntityNotFoundError(f"Segment {segment_id} does not exist.")
    return row["source_id"]


def _row_by_id(
    connection: sqlite3.Connection, table: str, entity_id: object
) -> sqlite3.Row:
    # Table names are internal constants supplied only by this module.
    row = connection.execute(
        f"SELECT * FROM {table} WHERE id = ?", (entity_id,)
    ).fetchone()
    if row is None:
        raise CorpusEntityNotFoundError(f"{table} record {entity_id} does not exist.")
    return row


def _text_representation_from_row(row: sqlite3.Row) -> TextRepresentation:
    return TextRepresentation(
        id=row["id"],
        source_id=row["source_id"],
        source_unit_id=row["source_unit_id"],
        representation_kind=row["representation_kind"],
        language_tag=row["language_tag"],
        content=row["content"],
        source_asset_id=row["source_asset_id"],
        derived_from_id=row["derived_from_id"],
        created_by_run_id=row["created_by_run_id"],
        metadata=load_json(row["metadata_json"]),
        created_at=parse_utc(row["created_at"]),
    )


def _layer_from_row(row: sqlite3.Row) -> SegmentLayer:
    return SegmentLayer(
        id=row["id"],
        source_id=row["source_id"],
        source_unit_id=row["source_unit_id"],
        name=row["name"],
        layer_kind=row["layer_kind"],
        language_tag=row["language_tag"],
        created_by_run_id=row["created_by_run_id"],
        metadata=load_json(row["metadata_json"]),
        created_at=parse_utc(row["created_at"]),
    )


def _segment_from_row(row: sqlite3.Row) -> Segment:
    return Segment(
        id=row["id"],
        layer_id=row["layer_id"],
        parent_id=row["parent_id"],
        kind=row["kind"],
        label=row["label"],
        sequence=row["sequence"],
        external_id=row["external_id"],
        confidence=row["confidence"],
        created_by_run_id=row["created_by_run_id"],
        metadata=load_json(row["metadata_json"]),
        created_at=parse_utc(row["created_at"]),
    )


def _text_span_from_row(row: sqlite3.Row) -> SegmentTextSpan:
    return SegmentTextSpan(
        id=row["id"],
        segment_id=row["segment_id"],
        text_representation_id=row["text_representation_id"],
        start_offset=row["start_offset"],
        end_offset=row["end_offset"],
        role=row["role"],
        confidence=row["confidence"],
        created_by_run_id=row["created_by_run_id"],
        metadata=load_json(row["metadata_json"]),
        created_at=parse_utc(row["created_at"]),
    )


def _media_span_from_row(row: sqlite3.Row) -> SegmentMediaSpan:
    return SegmentMediaSpan(
        id=row["id"],
        segment_id=row["segment_id"],
        asset_id=row["asset_id"],
        start_ms=row["start_ms"],
        end_ms=row["end_ms"],
        role=row["role"],
        confidence=row["confidence"],
        created_by_run_id=row["created_by_run_id"],
        metadata=load_json(row["metadata_json"]),
        created_at=parse_utc(row["created_at"]),
    )


def _speaker_from_row(row: sqlite3.Row) -> Speaker:
    return Speaker(
        id=row["id"],
        source_id=row["source_id"],
        name=row["name"],
        external_id=row["external_id"],
        created_by_run_id=row["created_by_run_id"],
        metadata=load_json(row["metadata_json"]),
        created_at=parse_utc(row["created_at"]),
    )


def _segment_speaker_from_row(row: sqlite3.Row) -> SegmentSpeaker:
    return SegmentSpeaker(
        id=row["id"],
        segment_id=row["segment_id"],
        speaker_id=row["speaker_id"],
        role=row["role"],
        confidence=row["confidence"],
        created_by_run_id=row["created_by_run_id"],
        metadata=load_json(row["metadata_json"]),
        created_at=parse_utc(row["created_at"]),
    )
