"""Application workflow for importing existing whisper.cpp JSON artifacts."""

from dataclasses import dataclass
from pathlib import Path

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.text_segment_service import TextSegmentService
from lexbundler.domain.corpus import Asset, ProcessingRun
from lexbundler.domain.text_segments import (
    FlatSegmentGraphSpec,
    FlatSegmentSpec,
    TextSegmentGraph,
)
from lexbundler.importers.whisper_cpp_json import (
    WhisperCppResult,
    load_whisper_cpp_json,
)


@dataclass(frozen=True, slots=True)
class WhisperImportResult:
    parsed: WhisperCppResult
    json_asset: Asset
    media_asset: Asset
    processing_run: ProcessingRun
    graph: TextSegmentGraph


class WhisperImportService:
    """Coordinate preservation and normalization of existing tool output."""

    def __init__(
        self, corpus: CorpusService, text_segments: TextSegmentService
    ) -> None:
        self._corpus = corpus
        self._text_segments = text_segments

    def import_json(
        self,
        json_path: Path,
        media_path: Path,
        *,
        source_id: int,
        source_unit_id: int | None = None,
    ) -> WhisperImportResult:
        """Import a fully parsed artifact into one atomic analytical graph."""
        parsed = load_whisper_cpp_json(Path(json_path))
        media_asset = self._corpus.register_local_asset(Path(media_path))
        json_asset = self._corpus.register_local_asset(
            Path(json_path), asset_kind="document", mime_type="application/json"
        )
        return self._import_parsed(
            parsed,
            json_asset=json_asset,
            media_asset=media_asset,
            source_id=source_id,
            source_unit_id=source_unit_id,
            manual_bindings=(
                ("asr_output", json_asset.id),
                ("source_media", media_asset.id),
            ),
        )

    def import_registered_json(
        self,
        json_path: Path,
        *,
        json_asset: Asset,
        media_asset: Asset,
        source_id: int,
        source_unit_id: int | None = None,
    ) -> WhisperImportResult:
        """Normalize durable artifacts already registered by an execution workflow."""
        parsed = load_whisper_cpp_json(Path(json_path))
        return self._import_parsed(
            parsed,
            json_asset=json_asset,
            media_asset=media_asset,
            source_id=source_id,
            source_unit_id=source_unit_id,
            manual_bindings=(),
        )

    def _import_parsed(
        self,
        parsed: WhisperCppResult,
        *,
        json_asset: Asset,
        media_asset: Asset,
        source_id: int,
        source_unit_id: int | None,
        manual_bindings: tuple[tuple[str, int], ...],
    ) -> WhisperImportResult:
        content, offsets = _flatten_segments(parsed)
        run = self._corpus.start_processing_run(
            "import",
            tool_name="LexBundler",
            parameters=_run_parameters(parsed),
        )
        try:
            for role, asset_id in manual_bindings:
                self._bind_manual_asset(
                    source_id,
                    source_unit_id,
                    asset_id,
                    role,
                    run_id=run.id,
                )
            items = tuple(
                FlatSegmentSpec(
                    sequence=segment.index,
                    external_id=str(segment.index),
                    text_start=text_start,
                    text_end=text_end,
                    media_asset_id=media_asset.id,
                    media_start_ms=segment.start_ms,
                    media_end_ms=segment.end_ms,
                )
                for segment, (text_start, text_end) in zip(
                    parsed.segments, offsets, strict=True
                )
            )
            graph = self._text_segments.create_flat_segment_graph(
                FlatSegmentGraphSpec(
                    source_id=source_id,
                    source_unit_id=source_unit_id,
                    representation_kind="asr_raw",
                    language_tag=parsed.language,
                    content=content,
                    source_asset_id=json_asset.id,
                    created_by_run_id=run.id,
                    representation_metadata={"format": "whisper.cpp-json"},
                    layer_name="whisper.cpp raw ASR",
                    layer_kind="asr",
                    layer_metadata={"format": "whisper.cpp-json"},
                    segment_kind="asr_segment",
                    text_span_role="asr",
                    media_span_role="source",
                    segments=items,
                )
            )
        except Exception:
            self._corpus.finish_processing_run(run.id, status="failed")
            raise

        completed_run = self._corpus.finish_processing_run(
            run.id, status="succeeded"
        )
        return WhisperImportResult(
            parsed=parsed,
            json_asset=json_asset,
            media_asset=media_asset,
            processing_run=completed_run,
            graph=graph,
        )

    def _bind_manual_asset(
        self,
        source_id: int,
        source_unit_id: int | None,
        asset_id: int,
        role: str,
        *,
        run_id: int,
    ) -> None:
        arguments = {
            "role": role,
            "assignment_method": "manual_import",
            "processing_run_id": run_id,
        }
        if source_unit_id is None:
            self._corpus.bind_asset_to_source(source_id, asset_id, **arguments)
        else:
            self._corpus.bind_asset_to_source_unit(
                source_id, source_unit_id, asset_id, **arguments
            )


def _flatten_segments(
    parsed: WhisperCppResult,
) -> tuple[str, tuple[tuple[int | None, int | None], ...]]:
    parts: list[str] = []
    spans: list[tuple[int | None, int | None]] = []
    cursor = 0
    for segment in parsed.segments:
        parts.append(segment.text)
        end = cursor + len(segment.text)
        spans.append((cursor, end) if end > cursor else (None, None))
        cursor = end
    return "".join(parts), tuple(spans)


def _run_parameters(parsed: WhisperCppResult) -> dict[str, object]:
    parameters: dict[str, object] = {
        "format": "whisper.cpp-json",
        "producer": "whisper.cpp",
    }
    if parsed.language is not None:
        parameters["language"] = parsed.language
    if parsed.translate is not None:
        parameters["translate"] = parsed.translate
    if parsed.requested_language is not None:
        parameters["requested_language"] = parsed.requested_language
    if parsed.model_metadata is not None:
        parameters["producer_model_metadata"] = parsed.model_metadata
    if parsed.model_path is not None:
        parameters["producer_model_path"] = parsed.model_path
    return parameters
