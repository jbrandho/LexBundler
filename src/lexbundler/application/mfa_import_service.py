"""Application workflow for importing existing MFA HF JSON alignment evidence."""

import unicodedata
from dataclasses import dataclass
from pathlib import Path

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.text_segment_service import TextSegmentService
from lexbundler.domain.corpus import Asset, ProcessingRun
from lexbundler.domain.errors import MfaImportError, MfaTextMismatchError
from lexbundler.domain.text_segments import (
    AlignmentGraph,
    AlignmentGraphSpec,
    AlignmentLayerSpec,
    AlignmentSegmentSpec,
    TextRepresentation,
)
from lexbundler.importers.mfa_hf_json import (
    MfaHfResult,
    MfaInterval,
    load_mfa_hf_json,
)


@dataclass(frozen=True, slots=True)
class MfaImportResult:
    parsed: MfaHfResult
    json_asset: Asset
    media_asset: Asset
    authoritative_text: TextRepresentation
    processing_run: ProcessingRun
    graph: AlignmentGraph


class MfaImportService:
    def __init__(self, corpus: CorpusService, text_segments: TextSegmentService) -> None:
        self._corpus = corpus
        self._text_segments = text_segments

    def import_json(
        self, json_path: Path, *, media_asset: Asset,
        authoritative_text: TextRepresentation, source_id: int,
        source_unit_id: int | None = None,
    ) -> MfaImportResult:
        parsed = load_mfa_hf_json(Path(json_path))
        stored_media = self._corpus.get_asset(media_asset.id)
        stored_text = self._text_segments.get_text_representation(authoritative_text.id)
        if stored_media.sha256 != media_asset.sha256:
            raise MfaImportError("The selected media Asset does not match this project.")
        if (
            stored_text != authoritative_text
            or stored_text.source_id != source_id
            or stored_text.source_unit_id != source_unit_id
        ):
            raise MfaImportError(
                "The authoritative TextRepresentation does not match the selected source."
            )
        word_spans = match_mfa_words(stored_text.content, parsed.words)
        layers = _layer_specs(parsed, word_spans)
        _validate_rounded_durations(layers)

        json_asset = self._corpus.register_local_asset(
            Path(json_path), asset_kind="document", mime_type="application/json"
        )
        run = self._corpus.start_processing_run(
            "import", tool_name="LexBundler",
            parameters={
                "format": "mfa-3.4-hf-json",
                "producer": "Montreal Forced Aligner",
                "producer_version": "3.4",
                "operation": "normalize_existing_artifact",
                "seconds_to_milliseconds": "round(seconds * 1000)",
            },
        )
        try:
            self._bind(source_id, source_unit_id, json_asset.id,
                       "forced_alignment_output", run.id)
            self._bind(source_id, source_unit_id, stored_media.id,
                       "aligned_media", run.id)
            graph = self._text_segments.create_alignment_graph(
                AlignmentGraphSpec(
                    source_id=source_id, source_unit_id=source_unit_id,
                    text_representation_id=stored_text.id,
                    media_asset_id=stored_media.id,
                    language_tag=stored_text.language_tag,
                    created_by_run_id=run.id,
                    text_span_role="authoritative_alignment",
                    media_span_role="aligned_source",
                    layers=layers,
                )
            )
        except Exception:
            self._corpus.finish_processing_run(run.id, status="failed")
            raise
        completed = self._corpus.finish_processing_run(run.id, status="succeeded")
        return MfaImportResult(
            parsed, json_asset, stored_media, stored_text, completed, graph
        )

    def _bind(
        self, source_id: int, source_unit_id: int | None, asset_id: int,
        role: str, run_id: int,
    ) -> None:
        kwargs = dict(role=role, assignment_method="manual_import",
                      processing_run_id=run_id)
        if source_unit_id is None:
            self._corpus.bind_asset_to_source(source_id, asset_id, **kwargs)
        else:
            self._corpus.bind_asset_to_source_unit(
                source_id, source_unit_id, asset_id, **kwargs
            )


def match_mfa_words(
    authoritative: str, words: tuple[MfaInterval, ...]
) -> tuple[tuple[int, int] | None, ...]:
    """Match lexical labels in order, ignoring only intervening punctuation/space."""
    cursor = 0
    spans: list[tuple[int, int] | None] = []
    for index, interval in enumerate(words):
        if interval.is_silence:
            spans.append(None)
            continue
        while cursor < len(authoritative) and _alignment_ignorable(authoritative[cursor]):
            cursor += 1
        end = cursor + len(interval.label)
        if not interval.label or authoritative[cursor:end] != interval.label:
            found = authoritative[cursor:end]
            raise MfaTextMismatchError(
                f"MFA word {index} {interval.label!r} does not match "
                f"authoritative text at offset {cursor}: {found!r}."
            )
        spans.append((cursor, end))
        cursor = end
    while cursor < len(authoritative) and _alignment_ignorable(authoritative[cursor]):
        cursor += 1
    if cursor != len(authoritative):
        raise MfaTextMismatchError(
            f"Authoritative text has unmatched lexical content at offset {cursor}."
        )
    return tuple(spans)


def _alignment_ignorable(character: str) -> bool:
    return character.isspace() or unicodedata.category(character)[0] in {"P", "Z"}


def _layer_specs(
    parsed: MfaHfResult,
    word_spans: tuple[tuple[int, int] | None, ...],
) -> tuple[AlignmentLayerSpec, ...]:
    word_items = tuple(
        AlignmentSegmentSpec(
            sequence=index, label=interval.label, start_ms=interval.start_ms,
            end_ms=interval.end_ms,
            text_start=None if span is None else span[0],
            text_end=None if span is None else span[1],
        )
        for index, (interval, span) in enumerate(zip(parsed.words, word_spans, strict=True))
    )
    phone_items = tuple(
        AlignmentSegmentSpec(
            sequence=index, label=interval.label, start_ms=interval.start_ms,
            end_ms=interval.end_ms,
        )
        for index, interval in enumerate(parsed.phones)
    )
    metadata = {"format": "mfa-3.4-hf-json", "evidence": "derived_timing"}
    return (
        AlignmentLayerSpec(
            "MFA word alignment", "forced_alignment", "alignment_word",
            {**metadata, "tier": "words", "tokens_are_canonical": False}, word_items,
        ),
        AlignmentLayerSpec(
            "MFA phone alignment", "forced_alignment", "alignment_phone",
            {**metadata, "tier": "phones"}, phone_items,
        ),
    )


def _validate_rounded_durations(layers: tuple[AlignmentLayerSpec, ...]) -> None:
    for layer in layers:
        for item in layer.segments:
            if item.end_ms <= item.start_ms:
                raise MfaImportError(
                    f"Rounded MFA {layer.name} interval {item.sequence} has no "
                    "positive millisecond duration."
                )
