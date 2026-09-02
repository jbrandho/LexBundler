"""Application workflow for importing authoritative UTF-8 transcripts."""

from dataclasses import dataclass
from pathlib import Path

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.text_segment_service import TextSegmentService
from lexbundler.domain.corpus import Asset, ProcessingRun
from lexbundler.domain.errors import TranscriptImportError
from lexbundler.domain.text_segments import (
    TextOnlySegmentGraphSpec,
    TextOnlySegmentSpec,
    TextSegmentGraph,
)


@dataclass(frozen=True, slots=True)
class TranscriptImportResult:
    transcript_asset: Asset
    processing_run: ProcessingRun
    graph: TextSegmentGraph


class TranscriptImportService:
    def __init__(self, corpus: CorpusService, text_segments: TextSegmentService) -> None:
        self._corpus = corpus
        self._text_segments = text_segments

    def import_utf8(
        self, transcript_path: Path, *, source_id: int,
        source_unit_id: int | None = None, language_tag: str | None = None,
        segment_nonempty_lines: bool = True,
    ) -> TranscriptImportResult:
        path = Path(transcript_path)
        try:
            content = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError) as error:
            raise TranscriptImportError(
                f"Could not read exact UTF-8 transcript: {path}"
            ) from error
        ranges = _nonempty_line_ranges(content) if segment_nonempty_lines else ()
        asset = self._corpus.register_local_asset(
            path, asset_kind="text", mime_type="text/plain"
        )
        run = self._corpus.start_processing_run(
            "import", tool_name="LexBundler",
            parameters={
                "format": "utf-8-plain-text",
                "text_authority": "authoritative_source",
                "line_segmentation": segment_nonempty_lines,
                "newline_decoding": "exact (no universal-newline conversion)",
            },
        )
        try:
            self._bind(source_id, source_unit_id, asset.id, run.id)
            graph = self._text_segments.create_text_only_segment_graph(
                TextOnlySegmentGraphSpec(
                    source_id=source_id, source_unit_id=source_unit_id,
                    representation_kind="authoritative_source",
                    language_tag=language_tag, content=content,
                    source_asset_id=asset.id, created_by_run_id=run.id,
                    representation_metadata={
                        "format": "utf-8-plain-text", "authority": "source"
                    },
                    layer_name="imported transcript non-empty lines",
                    layer_kind="transcript_line", layer_metadata={
                        "segmentation_policy": "explicit_nonempty_source_lines"
                    },
                    segment_kind="transcript_line", text_span_role="authoritative",
                    segments=tuple(
                        TextOnlySegmentSpec(index, start, end)
                        for index, (start, end) in enumerate(ranges)
                    ),
                )
            )
        except Exception:
            self._corpus.finish_processing_run(run.id, status="failed")
            raise
        completed = self._corpus.finish_processing_run(run.id, status="succeeded")
        return TranscriptImportResult(asset, completed, graph)

    def _bind(
        self, source_id: int, source_unit_id: int | None, asset_id: int, run_id: int
    ) -> None:
        kwargs = dict(
            role="authoritative_transcript", assignment_method="manual_import",
            processing_run_id=run_id,
        )
        if source_unit_id is None:
            self._corpus.bind_asset_to_source(source_id, asset_id, **kwargs)
        else:
            self._corpus.bind_asset_to_source_unit(
                source_id, source_unit_id, asset_id, **kwargs
            )


def _nonempty_line_ranges(content: str) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    cursor = 0
    for line in content.splitlines(keepends=True):
        body = line.removesuffix("\r\n").removesuffix("\n").removesuffix("\r")
        if body:
            ranges.append((cursor, cursor + len(body)))
        cursor += len(line)
    if cursor < len(content):
        ranges.append((cursor, len(content)))
    return tuple(ranges)
