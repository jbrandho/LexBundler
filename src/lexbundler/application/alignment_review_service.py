"""Read-only application projections for reviewing authoritative text and alignment."""

from dataclasses import dataclass
from pathlib import Path

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.text_segment_service import TextSegmentService
from lexbundler.domain.text_segments import SegmentLayer, TextRepresentation
from lexbundler.application.pedagogical_review_service import (
    APPROVED_MEDIA_ROLE,
    REVIEW_LAYER_KIND,
    REVIEW_SEGMENT_KIND,
    REVIEW_TEXT_ROLE,
)


@dataclass(frozen=True, slots=True)
class ReviewSource:
    id: int
    label: str


@dataclass(frozen=True, slots=True)
class ReviewUnit:
    id: int
    source_id: int
    label: str


@dataclass(frozen=True, slots=True)
class ReviewAlignment:
    layer_id: int
    label: str


@dataclass(frozen=True, slots=True)
class ReviewWord:
    label: str
    sequence: int
    text_start: int
    text_end: int
    start_ms: int
    end_ms: int


@dataclass(frozen=True, slots=True)
class ReviewApproval:
    layer_id: int
    segment_id: int
    text_span_id: int
    source_span_id: int
    processing_run_id: int
    start_ms: int
    end_ms: int
    transcript_segment_id: int | None = None
    authoritative_text_span_id: int | None = None


@dataclass(frozen=True, slots=True)
class ReviewUtterance:
    segment_id: int
    sequence: int
    text: str
    text_start: int
    text_end: int
    source_id: int
    source_label: str
    source_unit_id: int | None
    source_unit_label: str | None
    alignment_layer_id: int | None
    audio_asset_id: int | None
    audio_path: Path | None
    speech_start_ms: int | None
    speech_end_ms: int | None
    preceding_silence_start_ms: int | None
    following_silence_end_ms: int | None
    words: tuple[ReviewWord, ...]
    playback_available: bool
    playback_unavailable_reason: str | None
    text_representation_id: int | None = None
    text_span_id: int | None = None
    approval: ReviewApproval | None = None


@dataclass(frozen=True, slots=True)
class ReviewSelection:
    alignments: tuple[ReviewAlignment, ...]
    selected_alignment_layer_id: int | None
    utterances: tuple[ReviewUtterance, ...]


class AlignmentReviewService:
    """Build GUI-ready review data without exposing persistence details."""

    def __init__(self, corpus: CorpusService, text_segments: TextSegmentService) -> None:
        self._corpus = corpus
        self._text_segments = text_segments

    def list_sources(self) -> tuple[ReviewSource, ...]:
        return tuple(ReviewSource(item.id, item.name) for item in self._corpus.list_sources())

    def list_units(self, source_id: int) -> tuple[ReviewUnit, ...]:
        return tuple(
            ReviewUnit(item.id, item.source_id, item.label)
            for item in self._corpus.list_source_units(source_id)
        )

    def load(
        self,
        source_id: int,
        source_unit_id: int | None,
        *,
        alignment_layer_id: int | None = None,
    ) -> ReviewSelection:
        source = self._corpus.get_source(source_id)
        unit = (
            self._corpus.get_source_unit(source_unit_id)
            if source_unit_id is not None
            else None
        )
        if unit is not None and unit.source_id != source_id:
            return ReviewSelection((), None, ())

        transcript_layer = self._latest_transcript_layer(source_id, source_unit_id)
        if transcript_layer is None:
            return ReviewSelection((), None, ())
        transcript_segments = self._text_segments.list_segments(transcript_layer.id)
        transcript_rows: list[tuple[object, object, TextRepresentation]] = []
        representation_ids: set[int] = set()
        for segment in transcript_segments:
            spans = self._text_segments.list_segment_text_spans(segment.id)
            authoritative = next((span for span in spans if span.role == "authoritative"), None)
            if authoritative is None:
                continue
            representation = self._text_segments.get_text_representation(
                authoritative.text_representation_id
            )
            transcript_rows.append((segment, authoritative, representation))
            representation_ids.add(representation.id)

        candidates = self._alignment_candidates(
            source_id, source_unit_id, representation_ids
        )
        alignments = tuple(
            ReviewAlignment(layer.id, _alignment_label(layer)) for layer in candidates
        )
        candidate_ids = {layer.id for layer in candidates}
        selected_id = alignment_layer_id if alignment_layer_id in candidate_ids else None
        if selected_id is None and candidates:
            selected_id = candidates[0].id
        selected_layer = next(
            (layer for layer in candidates if layer.id == selected_id), None
        )
        alignment_items = self._alignment_items(selected_layer)
        approvals = self._review_approvals(source_id, source_unit_id)

        utterances = tuple(
            self._utterance(
                segment,
                span,
                representation,
                source.name,
                unit.label if unit else None,
                selected_layer,
                alignment_items,
                approvals,
            )
            for segment, span, representation in transcript_rows
        )
        return ReviewSelection(alignments, selected_id, utterances)

    def _latest_transcript_layer(
        self, source_id: int, source_unit_id: int | None
    ) -> SegmentLayer | None:
        layers = [
            layer
            for layer in self._text_segments.list_segment_layers(source_id)
            if layer.source_unit_id == source_unit_id
            and layer.layer_kind == "transcript_line"
            and self._run_succeeded(layer.created_by_run_id)
        ]
        return max(layers, key=lambda layer: layer.id, default=None)

    def _alignment_candidates(
        self,
        source_id: int,
        source_unit_id: int | None,
        representation_ids: set[int],
    ) -> list[SegmentLayer]:
        candidates: list[SegmentLayer] = []
        for layer in self._text_segments.list_segment_layers(source_id):
            if (
                layer.source_unit_id != source_unit_id
                or layer.layer_kind != "forced_alignment"
                or layer.metadata.get("tier") != "words"
                or not self._run_succeeded(layer.created_by_run_id)
            ):
                continue
            lexical_representation_ids: set[int] = set()
            valid_kind = True
            for segment in self._text_segments.list_segments(layer.id):
                valid_kind &= segment.kind == "alignment_word"
                lexical_representation_ids.update(
                    span.text_representation_id
                    for span in self._text_segments.list_segment_text_spans(segment.id)
                )
            if valid_kind and lexical_representation_ids & representation_ids:
                candidates.append(layer)
        candidates.sort(key=self._alignment_sort_key, reverse=True)
        return candidates

    def _alignment_sort_key(self, layer: SegmentLayer) -> tuple[object, int, int]:
        if layer.created_by_run_id is None:
            return (layer.created_at, -1, layer.id)
        run = self._corpus.get_processing_run(layer.created_by_run_id)
        return (run.completed_at or run.started_at, run.id, layer.id)

    def _run_succeeded(self, run_id: int | None) -> bool:
        return run_id is None or self._corpus.get_processing_run(run_id).status == "succeeded"

    def _alignment_items(self, layer: SegmentLayer | None) -> tuple[dict[str, object], ...]:
        if layer is None:
            return ()
        items: list[dict[str, object]] = []
        for segment in self._text_segments.list_segments(layer.id):
            media_spans = self._text_segments.list_segment_media_spans(segment.id)
            if not media_spans:
                continue
            text_spans = self._text_segments.list_segment_text_spans(segment.id)
            items.append({"segment": segment, "media": media_spans[0], "text": text_spans[0] if text_spans else None})
        return tuple(items)

    def _utterance(
        self, segment, span, representation, source_label, unit_label,
        selected_layer, alignment_items, approvals,
    ) -> ReviewUtterance:
        lexical: list[ReviewWord] = []
        matching_items: list[dict[str, object]] = []
        for item in alignment_items:
            text_span = item["text"]
            if text_span is None or text_span.text_representation_id != representation.id:
                continue
            if text_span.start_offset < span.end_offset and span.start_offset < text_span.end_offset:
                media = item["media"]
                word_segment = item["segment"]
                lexical.append(ReviewWord(
                    word_segment.label or "", word_segment.sequence or 0,
                    text_span.start_offset, text_span.end_offset,
                    media.start_ms, media.end_ms,
                ))
                matching_items.append(item)
        lexical.sort(key=lambda word: (word.sequence, word.start_ms))
        starts = [word.start_ms for word in lexical]
        ends = [word.end_ms for word in lexical]
        speech_start = min(starts) if starts else None
        speech_end = max(ends) if ends else None
        asset_ids = {item["media"].asset_id for item in matching_items}
        audio_asset_id = next(iter(asset_ids)) if len(asset_ids) == 1 else None
        audio_path = self._local_file(audio_asset_id) if audio_asset_id is not None else None
        preceding, following = self._neighboring_silence(
            alignment_items, lexical, audio_asset_id
        )
        if not lexical:
            reason = "No MFA word alignment is available for this transcript turn."
        elif audio_asset_id is None:
            reason = "Aligned words do not identify one source audio asset."
        elif audio_path is None:
            reason = "The aligned source audio has no usable local file."
        else:
            reason = None
        matching_approvals = [
            approval for approval, approval_span in approvals
            if approval.transcript_segment_id == segment.id
            and approval_span.text_representation_id == representation.id
            and approval_span.start_offset == span.start_offset
            and approval_span.end_offset == span.end_offset
        ]
        approval = matching_approvals[0] if matching_approvals else None
        return ReviewUtterance(
            segment.id, segment.sequence or 0,
            representation.content[span.start_offset:span.end_offset],
            span.start_offset, span.end_offset, representation.source_id,
            source_label, representation.source_unit_id, unit_label,
            selected_layer.id if selected_layer else None, audio_asset_id, audio_path,
            speech_start, speech_end, preceding, following, tuple(lexical),
            reason is None, reason, representation.id, span.id, approval,
        )

    def list_review_history(
        self, source_id: int, source_unit_id: int | None,
        transcript_segment_id: int,
    ) -> tuple[ReviewApproval, ...]:
        return tuple(
            approval for approval, _span in self._review_approvals(
                source_id, source_unit_id
            )
            if approval.transcript_segment_id == transcript_segment_id
        )

    def _review_approvals(self, source_id: int, source_unit_id: int | None):
        runs = {run.id: run for run in self._corpus.list_processing_runs()}
        results = []
        for layer in self._text_segments.list_segment_layers(source_id):
            run = runs.get(layer.created_by_run_id)
            if (
                layer.source_unit_id != source_unit_id
                or layer.layer_kind != REVIEW_LAYER_KIND
                or run is None
                or run.status != "succeeded"
            ):
                continue
            segments = self._text_segments.list_segments(layer.id)
            if len(segments) != 1 or segments[0].kind != REVIEW_SEGMENT_KIND:
                continue
            segment = segments[0]
            text_spans = [
                span for span in self._text_segments.list_segment_text_spans(segment.id)
                if span.role == REVIEW_TEXT_ROLE
            ]
            source_spans = [
                span for span in self._text_segments.list_segment_media_spans(segment.id)
                if span.role == APPROVED_MEDIA_ROLE
            ]
            if len(text_spans) != 1 or len(source_spans) != 1:
                continue
            results.append((ReviewApproval(
                layer.id, segment.id, text_spans[0].id, source_spans[0].id,
                run.id, source_spans[0].start_ms, source_spans[0].end_ms,
                layer.metadata.get("authoritative_transcript_segment_id"),
                layer.metadata.get("authoritative_text_span_id"),
            ), text_spans[0], run))
        results.sort(
            key=lambda item: (
                item[2].completed_at or item[2].started_at,
                item[2].id,
                item[0].layer_id,
            ),
            reverse=True,
        )
        return tuple((approval, span) for approval, span, _run in results)

    def _neighboring_silence(self, items, words, asset_id):
        if not words or asset_id is None:
            return None, None
        first_sequence = min(word.sequence for word in words)
        last_sequence = max(word.sequence for word in words)
        before = [item for item in items if (item["segment"].sequence or 0) < first_sequence]
        after = [item for item in items if (item["segment"].sequence or 0) > last_sequence]
        previous_item = max(
            before, key=lambda item: item["segment"].sequence or 0, default=None
        )
        following_item = min(
            after, key=lambda item: item["segment"].sequence or 0, default=None
        )
        previous = (
            previous_item["media"]
            if previous_item is not None
            and previous_item["text"] is None
            and previous_item["segment"].label == "<eps>"
            and previous_item["media"].asset_id == asset_id
            else None
        )
        following = (
            following_item["media"]
            if following_item is not None
            and following_item["text"] is None
            and following_item["segment"].label == "<eps>"
            and following_item["media"].asset_id == asset_id
            else None
        )
        return (
            previous.start_ms if previous and previous.end_ms <= words[0].start_ms else None,
            following.end_ms if following and following.start_ms >= words[-1].end_ms else None,
        )

    def _local_file(self, asset_id: int) -> Path | None:
        for location in self._corpus.list_asset_locations(asset_id):
            if location.location_kind == "filesystem":
                path = Path(location.location)
                if path.is_file():
                    return path.resolve()
        return None


def _alignment_label(layer: SegmentLayer) -> str:
    suffix = layer.created_at.astimezone().strftime("%Y-%m-%d %H:%M")
    run = f"run {layer.created_by_run_id}" if layer.created_by_run_id else f"layer {layer.id}"
    return f"{layer.name} — {suffix} ({run})"
