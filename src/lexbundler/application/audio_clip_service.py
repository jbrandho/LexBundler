"""Application workflow for rendering durable audio clips from media spans."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.text_segment_service import TextSegmentService
from lexbundler.domain.corpus import Asset, ProcessingRun
from lexbundler.domain.errors import MediaRenderError
from lexbundler.domain.text_segments import SegmentMediaSpan
from lexbundler.external_tools.ffmpeg import (
    FfmpegClipRequest,
    FfmpegClipRunner,
    validate_ffmpeg_inputs,
)


@dataclass(frozen=True, slots=True)
class AudioClipResult:
    processing_run: ProcessingRun
    source_span: SegmentMediaSpan
    rendered_span: SegmentMediaSpan
    clip_asset: Asset
    output_path: Path
    render_start_ms: int
    requested_render_end_ms: int


class AudioClipService:
    """Resolve and render one caller-selected SegmentMediaSpan."""

    def __init__(
        self,
        corpus: CorpusService,
        text_segments: TextSegmentService,
        runner: FfmpegClipRunner | None = None,
    ) -> None:
        self._corpus = corpus
        self._text_segments = text_segments
        self._runner = runner or FfmpegClipRunner()

    def render(
        self,
        *,
        segment_media_span_id: int,
        output_path: Path,
        ffmpeg_path: Path,
        pre_padding_ms: int = 0,
        post_padding_ms: int = 0,
    ) -> AudioClipResult:
        _validate_padding(pre_padding_ms, "Pre-padding")
        _validate_padding(post_padding_ms, "Post-padding")
        source_span = self._text_segments.get_segment_media_span(
            segment_media_span_id
        )
        segment = self._text_segments.get_segment(source_span.segment_id)
        layer = self._text_segments.get_segment_layer(segment.layer_id)
        source_path = self._resolve_local_asset(source_span.asset_id)
        executable, source_path = validate_ffmpeg_inputs(ffmpeg_path, source_path)
        durable_output = _prepare_output_path(output_path)

        render_start = max(0, source_span.start_ms - pre_padding_ms)
        requested_render_end = source_span.end_ms + post_padding_ms
        clip_start = source_span.start_ms - render_start
        clip_end = clip_start + (source_span.end_ms - source_span.start_ms)
        run = self._corpus.start_processing_run(
            "media_render",
            tool_name="ffmpeg",
            parameters={
                "source_segment_media_span_id": source_span.id,
                "requested_pre_padding_ms": pre_padding_ms,
                "requested_post_padding_ms": post_padding_ms,
                "source_render_start_ms": render_start,
                "source_render_end_ms": requested_render_end,
                "output_format": "mp3",
                "audio_codec": "libmp3lame",
                "audio_quality": 2,
            },
        )
        try:
            with TemporaryDirectory(prefix="lexbundler-ffmpeg-") as staging:
                staged_output = Path(staging) / "clip.mp3"
                execution = self._runner.run(
                    FfmpegClipRequest(
                        executable_path=executable,
                        input_media_path=source_path,
                        source_start_ms=render_start,
                        source_end_ms=requested_render_end,
                        output_path=staged_output,
                    )
                )
                _publish_clip(execution.output_path, durable_output)
            clip_asset = self._corpus.register_local_asset(
                durable_output,
                asset_kind="audio",
                mime_type="audio/mpeg",
                created_by_run_id=run.id,
            )
            self._bind_clip(
                layer.source_id,
                layer.source_unit_id,
                clip_asset.id,
                run.id,
            )
            rendered_span = self._text_segments.add_segment_media_span(
                segment.id,
                clip_asset.id,
                clip_start,
                clip_end,
                role="rendered_clip",
                created_by_run_id=run.id,
                metadata={"source_segment_media_span_id": source_span.id},
            )
        except KeyboardInterrupt:
            self._corpus.finish_processing_run(run.id, status="cancelled")
            raise
        except OSError as error:
            self._corpus.finish_processing_run(run.id, status="failed")
            raise MediaRenderError(
                "Could not create or clean the ffmpeg staging workspace."
            ) from error
        except Exception:
            self._corpus.finish_processing_run(run.id, status="failed")
            raise

        completed_run = self._corpus.finish_processing_run(run.id, status="succeeded")
        return AudioClipResult(
            processing_run=completed_run,
            source_span=source_span,
            rendered_span=rendered_span,
            clip_asset=clip_asset,
            output_path=durable_output,
            render_start_ms=render_start,
            requested_render_end_ms=requested_render_end,
        )

    def _resolve_local_asset(self, asset_id: int) -> Path:
        for location in self._corpus.list_asset_locations(asset_id):
            if location.location_kind != "filesystem":
                continue
            candidate = Path(location.location)
            if candidate.is_file():
                return candidate.resolve()
        raise MediaRenderError(
            f"Asset {asset_id} has no currently usable local file location."
        )

    def _bind_clip(
        self,
        source_id: int,
        source_unit_id: int | None,
        asset_id: int,
        run_id: int,
    ) -> None:
        arguments = {
            "role": "rendered_clip",
            "assignment_method": "media_render",
            "processing_run_id": run_id,
        }
        if source_unit_id is None:
            self._corpus.bind_asset_to_source(source_id, asset_id, **arguments)
        else:
            self._corpus.bind_asset_to_source_unit(
                source_id, source_unit_id, asset_id, **arguments
            )


def _validate_padding(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise MediaRenderError(f"{name} must be a nonnegative integer number of ms.")


def _prepare_output_path(path: Path) -> Path:
    output = Path(path).resolve()
    if output.exists():
        raise MediaRenderError(f"The durable rendered clip already exists: {output}")
    if output.suffix.lower() != ".mp3":
        raise MediaRenderError("The durable rendered clip must use an .mp3 extension.")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise MediaRenderError(
            f"Could not create the durable clip directory: {output.parent}"
        ) from error
    if not output.parent.is_dir():
        raise MediaRenderError(
            f"The durable clip parent is not a directory: {output.parent}"
        )
    return output


def _publish_clip(staged_clip: Path, durable_clip: Path) -> None:
    created = False
    try:
        with Path(staged_clip).open("rb") as source:
            with durable_clip.open("xb") as destination:
                created = True
                shutil.copyfileobj(source, destination)
    except FileExistsError as error:
        raise MediaRenderError(
            f"The durable rendered clip already exists: {durable_clip}"
        ) from error
    except OSError as error:
        if created:
            durable_clip.unlink(missing_ok=True)
        raise MediaRenderError(
            f"Could not publish rendered clip to: {durable_clip}"
        ) from error
