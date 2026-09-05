"""Explicit approval of reviewed pedagogical media selections."""

from dataclasses import dataclass

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.text_segment_service import TextSegmentService
from lexbundler.domain.corpus import ProcessingRun
from lexbundler.domain.errors import PedagogicalReviewError
from lexbundler.domain.text_segments import (
    AlignmentGraph,
    AlignmentGraphSpec,
    AlignmentLayerSpec,
    AlignmentSegmentSpec,
    Segment,
    SegmentMediaSpan,
    SegmentTextSpan,
)


REVIEW_LAYER_KIND = "reviewed_pedagogical_selection"
REVIEW_SEGMENT_KIND = "reviewed_pedagogical_utterance"
REVIEW_TEXT_ROLE = "authoritative_review_basis"
APPROVED_MEDIA_ROLE = "approved_source_selection"


@dataclass(frozen=True, slots=True)
class PedagogicalReviewRequest:
    transcript_segment_id: int
    transcript_text_span_id: int
    source_audio_asset_id: int
    approved_start_ms: int
    approved_end_ms: int
    alignment_layer_id: int | None
    mfa_speech_start_ms: int | None
    mfa_speech_end_ms: int | None
    manually_edited: bool


@dataclass(frozen=True, slots=True)
class PedagogicalApproval:
    processing_run: ProcessingRun
    graph: AlignmentGraph
    reviewed_segment: Segment
    authoritative_span: SegmentTextSpan
    approved_source_span: SegmentMediaSpan


class PedagogicalReviewService:
    """Persist append-only human review as logical spans over source media."""

    def __init__(
        self,
        corpus: CorpusService,
        text_segments: TextSegmentService,
    ) -> None:
        self._corpus = corpus
        self._text_segments = text_segments

    def approve(self, request: PedagogicalReviewRequest) -> PedagogicalApproval:
        transcript = self._text_segments.get_segment(request.transcript_segment_id)
        transcript_layer = self._text_segments.get_segment_layer(transcript.layer_id)
        text_span = self._text_segments.get_segment_text_span(
            request.transcript_text_span_id
        )
        representation = self._text_segments.get_text_representation(
            text_span.text_representation_id
        )
        audio = self._corpus.get_asset(request.source_audio_asset_id)
        if transcript_layer.layer_kind != "transcript_line":
            raise PedagogicalReviewError(
                "The approval basis must be an authoritative transcript line."
            )
        if text_span.segment_id != transcript.id or text_span.role != "authoritative":
            raise PedagogicalReviewError(
                "The selected authoritative text span does not belong to the transcript line."
            )
        if representation.representation_kind != "authoritative_source":
            raise PedagogicalReviewError(
                "The approval basis must be authoritative source text."
            )
        if (
            representation.source_id != transcript_layer.source_id
            or representation.source_unit_id != transcript_layer.source_unit_id
        ):
            raise PedagogicalReviewError(
                "The authoritative text and transcript layer do not share a source."
            )
        if audio.asset_kind != "audio":
            raise PedagogicalReviewError(
                "The reviewed selection must reference a source audio asset."
            )
        if (
            type(request.approved_start_ms) is not int
            or type(request.approved_end_ms) is not int
            or request.approved_start_ms < 0
            or request.approved_end_ms <= request.approved_start_ms
        ):
            raise PedagogicalReviewError(
                "Approved bounds must be a non-empty nonnegative millisecond interval."
            )
        if request.mfa_speech_start_ms is None or request.mfa_speech_end_ms is None:
            raise PedagogicalReviewError(
                "Valid MFA speech bounds are required for approval."
            )
        if not 0 <= request.mfa_speech_start_ms < request.mfa_speech_end_ms:
            raise PedagogicalReviewError("MFA speech bounds are invalid.")
        if (
            request.approved_start_ms
            < max(0, request.mfa_speech_start_ms - 1000)
            or request.approved_end_ms > request.mfa_speech_end_ms + 1000
        ):
            raise PedagogicalReviewError(
                "Approved bounds must remain inside the review context window."
            )
        if request.alignment_layer_id is None:
            raise PedagogicalReviewError(
                "A forced-alignment layer is required for approval."
            )
        alignment = self._text_segments.get_segment_layer(request.alignment_layer_id)
        if (
            alignment.layer_kind != "forced_alignment"
            or alignment.source_id != transcript_layer.source_id
            or alignment.source_unit_id != transcript_layer.source_unit_id
        ):
            raise PedagogicalReviewError(
                "The selected alignment evidence does not match the transcript."
            )
        aligned_assets = {
            span.asset_id
            for segment in self._text_segments.list_segments(alignment.id)
            for span in self._text_segments.list_segment_media_spans(segment.id)
        }
        if audio.id not in aligned_assets:
            raise PedagogicalReviewError(
                "The source audio does not match the selected alignment evidence."
            )
        run = self._corpus.start_processing_run(
            "review",
            tool_name="LexBundler",
            parameters={
                "operation": "approve_pedagogical_selection",
                "authoritative_transcript_segment_id": transcript.id,
                "authoritative_text_span_id": text_span.id,
                "authoritative_text_representation_id": representation.id,
                "source_audio_asset_id": audio.id,
                "approved_start_ms": request.approved_start_ms,
                "approved_end_ms": request.approved_end_ms,
                "alignment_layer_id": request.alignment_layer_id,
                "mfa_speech_start_ms": request.mfa_speech_start_ms,
                "mfa_speech_end_ms": request.mfa_speech_end_ms,
                "manually_edited": request.manually_edited,
            },
        )
        try:
            graph = self._text_segments.create_alignment_graph(AlignmentGraphSpec(
                source_id=transcript_layer.source_id,
                source_unit_id=transcript_layer.source_unit_id,
                text_representation_id=representation.id,
                media_asset_id=audio.id,
                language_tag=representation.language_tag,
                created_by_run_id=run.id,
                text_span_role=REVIEW_TEXT_ROLE,
                media_span_role=APPROVED_MEDIA_ROLE,
                layers=(AlignmentLayerSpec(
                    name="Reviewed pedagogical selection",
                    layer_kind=REVIEW_LAYER_KIND,
                    segment_kind=REVIEW_SEGMENT_KIND,
                    metadata={
                        "authoritative_transcript_segment_id": transcript.id,
                        "authoritative_text_span_id": text_span.id,
                        "review_status": "approved",
                    },
                    segments=(AlignmentSegmentSpec(
                        sequence=0,
                        label="approved",
                        start_ms=request.approved_start_ms,
                        end_ms=request.approved_end_ms,
                        text_start=text_span.start_offset,
                        text_end=text_span.end_offset,
                    ),),
                ),),
            ))
        except Exception:
            self._corpus.finish_processing_run(run.id, status="failed")
            raise
        completed = self._corpus.finish_processing_run(run.id, status="succeeded")
        return PedagogicalApproval(
            completed, graph, graph.segments[0], graph.text_spans[0],
            graph.media_spans[0],
        )
