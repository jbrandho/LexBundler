"""Tests for ffmpeg clip execution and durable media-span rendering."""

import base64
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import MediaRenderError
from lexbundler.domain.text_segments import SegmentMediaSpan
from lexbundler.external_tools.ffmpeg import FfmpegClipRequest, FfmpegClipRunner
from lexbundler.persistence.sqlite.project_store import SQLiteProjectStoreFactory


CLIP_BYTES = b"synthetic mp3-like clip bytes\x00\x01"


@dataclass(frozen=True, slots=True)
class RenderContext:
    project: ProjectService
    project_path: Path
    source_id: int
    source_unit_id: int
    source_path: Path
    source_span: SegmentMediaSpan


@pytest.fixture
def context(tmp_path: Path) -> RenderContext:
    project_path = tmp_path / "render.lexbundler"
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(project_path, name="Render")
    source = project.corpus.create_source("Synthetic source")
    unit = project.corpus.create_source_unit(source.id, kind="part", label="Part")
    source_path = tmp_path / "source media with spaces" / "source.bin"
    source_path.parent.mkdir()
    source_path.write_bytes(b"immutable source bytes")
    asset = project.corpus.register_local_asset(source_path, asset_kind="audio")
    project.corpus.bind_asset_to_source_unit(
        source.id, unit.id, asset.id, role="source_media"
    )
    layer = project.text_segments.create_segment_layer(
        source.id, name="Raw", layer_kind="asr", source_unit_id=unit.id
    )
    segment = project.text_segments.create_segment(layer.id, kind="asr_segment")
    source_span = project.text_segments.add_segment_media_span(
        segment.id, asset.id, 25880, 31240, role="source"
    )
    return RenderContext(
        project, project_path, source.id, unit.id, source_path, source_span
    )


def _fake_ffmpeg(
    tmp_path: Path, *, mode: str = "success", payload: bytes = CLIP_BYTES
) -> tuple[Path, Path]:
    directory = tmp_path / "ffmpeg directory with spaces"
    directory.mkdir(exist_ok=True)
    executable = directory / "fake ffmpeg"
    capture = tmp_path / f"ffmpeg-arguments-{mode}.json"
    encoded = base64.b64encode(payload).decode()
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import base64, json, pathlib, sys\n"
        f"mode = {mode!r}\n"
        f"capture = pathlib.Path({str(capture)!r})\n"
        "capture.write_text(json.dumps(sys.argv[1:]))\n"
        "print('fake ffmpeg diagnostic')\n"
        "if mode == 'nonzero':\n"
        "    print('render failure detail')\n"
        "    raise SystemExit(9)\n"
        "output = pathlib.Path(sys.argv[-1])\n"
        "if mode == 'missing':\n"
        "    raise SystemExit(0)\n"
        "if mode == 'empty':\n"
        "    output.touch()\n"
        "else:\n"
        f"    output.write_bytes(base64.b64decode({encoded!r}))\n"
    )
    executable.chmod(0o755)
    return executable, capture


def _run_rows(path: Path) -> list[tuple[str, str | None, str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT process_type, tool_name, status FROM processing_run ORDER BY id"
        ).fetchall()


def test_runner_uses_exact_argv_and_paths_with_spaces(tmp_path: Path) -> None:
    executable, capture = _fake_ffmpeg(tmp_path)
    source = tmp_path / "input with spaces" / "audio.bin"
    source.parent.mkdir()
    source.write_bytes(b"audio")
    output = tmp_path / "staging with spaces" / "clip.mp3"

    result = FfmpegClipRunner().run(
        FfmpegClipRequest(executable, source, 25830, 31490, output)
    )

    assert result.output_path.read_bytes() == CLIP_BYTES
    assert json.loads(capture.read_text()) == [
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "warning",
        "-i",
        str(source.resolve()),
        "-ss",
        "25.830",
        "-t",
        "5.660",
        "-vn",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(output.resolve()),
    ]


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("nonzero", "status 9.*render failure detail"),
        ("missing", "produced no clip"),
        ("empty", "empty clip"),
    ],
)
def test_runner_rejects_failed_output_contract(
    tmp_path: Path, mode: str, message: str
) -> None:
    executable, _ = _fake_ffmpeg(tmp_path, mode=mode)
    source = tmp_path / "source.bin"
    source.write_bytes(b"audio")
    with pytest.raises(MediaRenderError, match=message.replace(".*", "[\\s\\S]*")):
        FfmpegClipRunner().run(
            FfmpegClipRequest(executable, source, 0, 1000, tmp_path / "clip.mp3")
        )


def test_runner_rejects_invalid_executable_and_source(tmp_path: Path) -> None:
    executable, _ = _fake_ffmpeg(tmp_path)
    source = tmp_path / "source.bin"
    source.write_bytes(b"audio")
    executable.chmod(0o644)
    with pytest.raises(MediaRenderError, match="not executable"):
        FfmpegClipRunner().run(
            FfmpegClipRequest(executable, source, 0, 10, tmp_path / "clip.mp3")
        )
    executable.chmod(0o755)
    with pytest.raises(MediaRenderError, match="Source media"):
        FfmpegClipRunner().run(
            FfmpegClipRequest(
                executable, source.with_name("missing"), 0, 10, tmp_path / "clip.mp3"
            )
        )


def test_render_preserves_source_and_creates_clip_relative_span(
    context: RenderContext, tmp_path: Path
) -> None:
    executable, capture = _fake_ffmpeg(tmp_path)
    durable = tmp_path / "durable clips" / "card audio.mp3"
    original_bytes = context.source_path.read_bytes()

    result = context.project.audio_clips.render(
        segment_media_span_id=context.source_span.id,
        output_path=durable,
        ffmpeg_path=executable,
        pre_padding_ms=50,
        post_padding_ms=250,
    )

    assert context.source_path.read_bytes() == original_bytes
    assert durable.read_bytes() == CLIP_BYTES
    staging_output = Path(json.loads(capture.read_text())[-1])
    assert not staging_output.parent.exists()
    assert result.render_start_ms == 25830
    assert result.requested_render_end_ms == 31490
    assert (result.rendered_span.start_ms, result.rendered_span.end_ms) == (50, 5410)
    assert result.rendered_span.segment_id == context.source_span.segment_id
    assert result.rendered_span.role == "rendered_clip"
    assert result.rendered_span.created_by_run_id == result.processing_run.id
    assert context.project.text_segments.get_segment_media_span(
        context.source_span.id
    ) == context.source_span
    spans = context.project.text_segments.list_segment_media_spans(
        context.source_span.segment_id
    )
    assert spans == [context.source_span, result.rendered_span]

    assert result.processing_run.process_type == "media_render"
    assert result.processing_run.tool_name == "ffmpeg"
    assert result.processing_run.status == "succeeded"
    assert result.processing_run.parameters == {
        "source_segment_media_span_id": context.source_span.id,
        "requested_pre_padding_ms": 50,
        "requested_post_padding_ms": 250,
        "source_render_start_ms": 25830,
        "source_render_end_ms": 31490,
        "output_format": "mp3",
        "audio_codec": "libmp3lame",
        "audio_quality": 2,
    }
    assert result.clip_asset.created_by_run_id == result.processing_run.id
    locations = context.project.corpus.list_asset_locations(result.clip_asset.id)
    assert [item.location for item in locations] == [str(durable.resolve())]
    bindings = context.project.corpus.list_asset_bindings(context.source_id)
    rendered_binding = next(item for item in bindings if item.role == "rendered_clip")
    assert rendered_binding.asset_id == result.clip_asset.id
    assert rendered_binding.source_unit_id == context.source_unit_id
    assert rendered_binding.processing_run_id == result.processing_run.id


def test_pre_padding_clamps_at_zero(context: RenderContext, tmp_path: Path) -> None:
    executable, capture = _fake_ffmpeg(tmp_path)
    segment = context.project.text_segments.get_segment(context.source_span.segment_id)
    near_zero = context.project.text_segments.add_segment_media_span(
        segment.id, context.source_span.asset_id, 30, 100, role="source"
    )

    result = context.project.audio_clips.render(
        segment_media_span_id=near_zero.id,
        output_path=tmp_path / "clamped.mp3",
        ffmpeg_path=executable,
        pre_padding_ms=50,
        post_padding_ms=20,
    )

    argv = json.loads(capture.read_text())
    assert argv[argv.index("-ss") + 1] == "0.000"
    assert argv[argv.index("-t") + 1] == "0.120"
    assert (result.rendered_span.start_ms, result.rendered_span.end_ms) == (30, 100)


@pytest.mark.parametrize("padding", [-1, True, 1.5, "10"])
def test_invalid_padding_is_rejected_before_run(
    context: RenderContext, tmp_path: Path, padding: object
) -> None:
    executable, capture = _fake_ffmpeg(tmp_path)
    with pytest.raises(MediaRenderError, match="nonnegative integer"):
        context.project.audio_clips.render(
            segment_media_span_id=context.source_span.id,
            output_path=tmp_path / "invalid.mp3",
            ffmpeg_path=executable,
            pre_padding_ms=padding,  # type: ignore[arg-type]
        )
    assert not capture.exists()
    assert _run_rows(context.project_path) == []


def test_existing_output_prevents_launch(context: RenderContext, tmp_path: Path) -> None:
    executable, capture = _fake_ffmpeg(tmp_path)
    output = tmp_path / "existing.mp3"
    output.write_bytes(b"keep")
    with pytest.raises(MediaRenderError, match="already exists"):
        context.project.audio_clips.render(
            segment_media_span_id=context.source_span.id,
            output_path=output,
            ffmpeg_path=executable,
        )
    assert output.read_bytes() == b"keep"
    assert not capture.exists()
    assert _run_rows(context.project_path) == []


def test_missing_local_asset_location_is_rejected_before_run(
    context: RenderContext, tmp_path: Path
) -> None:
    executable, capture = _fake_ffmpeg(tmp_path)
    context.source_path.unlink()
    with pytest.raises(MediaRenderError, match="no currently usable local"):
        context.project.audio_clips.render(
            segment_media_span_id=context.source_span.id,
            output_path=tmp_path / "missing.mp3",
            ffmpeg_path=executable,
        )
    assert not capture.exists()
    assert _run_rows(context.project_path) == []


def test_process_failure_marks_run_failed_and_cleans_staging(
    context: RenderContext, tmp_path: Path
) -> None:
    executable, capture = _fake_ffmpeg(tmp_path, mode="nonzero")
    output = tmp_path / "failed.mp3"
    with pytest.raises(MediaRenderError, match="status 9"):
        context.project.audio_clips.render(
            segment_media_span_id=context.source_span.id,
            output_path=output,
            ffmpeg_path=executable,
        )
    staging_output = Path(json.loads(capture.read_text())[-1])
    assert not staging_output.parent.exists()
    assert not output.exists()
    assert _run_rows(context.project_path) == [("media_render", "ffmpeg", "failed")]
    assert context.project.text_segments.list_segment_media_spans(
        context.source_span.segment_id
    ) == [context.source_span]


def test_keyboard_interrupt_cancels_run_and_reaches_caller(
    context: RenderContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, _ = _fake_ffmpeg(tmp_path)
    interruption = KeyboardInterrupt("synthetic render interruption")

    def interrupt_runner(request: object) -> object:
        raise interruption

    monkeypatch.setattr(context.project.audio_clips._runner, "run", interrupt_runner)
    with pytest.raises(KeyboardInterrupt) as caught:
        context.project.audio_clips.render(
            segment_media_span_id=context.source_span.id,
            output_path=tmp_path / "interrupted.mp3",
            ffmpeg_path=executable,
        )
    assert caught.value is interruption
    assert _run_rows(context.project_path) == [
        ("media_render", "ffmpeg", "cancelled")
    ]
    assert context.project.text_segments.list_segment_media_spans(
        context.source_span.segment_id
    ) == [context.source_span]
