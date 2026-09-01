"""Tests for synchronous whisper.cpp execution and integrated import."""

import base64
import json
import sqlite3
from pathlib import Path

import pytest

from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import WhisperCppFormatError, WhisperExecutionError
from lexbundler.external_tools.whisper_cpp import (
    WhisperCppExecutionRequest,
    WhisperCppRunner,
)
from lexbundler.persistence.sqlite.project_store import SQLiteProjectStoreFactory
import lexbundler.persistence.sqlite.text_segment_store as sqlite_text_store


@pytest.fixture
def service(tmp_path: Path) -> ProjectService:
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(tmp_path / "execution.lexbundler", name="Execution")
    return project


def _valid_json() -> bytes:
    document = {
        "params": {"language": "zh", "translate": False},
        "result": {"language": "zh"},
        "transcription": [
            {"text": "你好", "offsets": {"from": 100, "to": 900}},
            {"text": " world", "offsets": {"from": 850, "to": 1400}},
        ],
    }
    return json.dumps(document, ensure_ascii=False).encode()


def _fake_tool(
    tmp_path: Path,
    *,
    mode: str = "success",
    payload: bytes | None = None,
) -> tuple[Path, Path]:
    tool_directory = tmp_path / "tool directory with spaces"
    tool_directory.mkdir(exist_ok=True)
    executable = tool_directory / "fake whisper cli"
    capture = tmp_path / f"arguments-{mode}.json"
    encoded_payload = base64.b64encode(payload or _valid_json()).decode()
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import base64, json, pathlib, sys\n"
        f"mode = {mode!r}\n"
        f"capture = pathlib.Path({str(capture)!r})\n"
        "capture.write_text(json.dumps(sys.argv[1:]))\n"
        "print('fake diagnostic output')\n"
        "if mode == 'nonzero':\n"
        "    print('bounded failure detail')\n"
        "    raise SystemExit(7)\n"
        "output = pathlib.Path(sys.argv[sys.argv.index('-of') + 1] + '.json')\n"
        "if mode == 'missing':\n"
        "    raise SystemExit(0)\n"
        "if mode == 'empty':\n"
        "    output.touch()\n"
        "else:\n"
        f"    output.write_bytes(base64.b64decode({encoded_payload!r}))\n"
    )
    executable.chmod(0o755)
    return executable, capture


def _inputs(tmp_path: Path) -> tuple[Path, Path]:
    model = tmp_path / "model path with spaces" / "model.bin"
    media = tmp_path / "media path with spaces" / "source audio.bin"
    model.parent.mkdir(exist_ok=True)
    media.parent.mkdir(exist_ok=True)
    model.write_bytes(b"model")
    media.write_bytes(b"media")
    return model, media


def _run_rows(project_path: Path) -> list[tuple[str, str | None, str]]:
    with sqlite3.connect(project_path) as connection:
        return connection.execute(
            "SELECT process_type, tool_name, status FROM processing_run ORDER BY id"
        ).fetchall()


def _graph_counts(project_path: Path) -> tuple[int, int, int, int, int]:
    with sqlite3.connect(project_path) as connection:
        return tuple(
            connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "text_representation",
                "segment_layer",
                "segment",
                "segment_text_span",
                "segment_media_span",
            )
        )


def test_runner_uses_exact_argv_and_handles_paths_with_spaces(tmp_path: Path) -> None:
    executable, capture = _fake_tool(tmp_path)
    model, media = _inputs(tmp_path)
    output_base = tmp_path / "staging path with spaces" / "output"

    result = WhisperCppRunner().run(
        WhisperCppExecutionRequest(
            executable_path=executable,
            model_path=model,
            media_path=media,
            language="zh",
            output_base=output_base,
        )
    )

    assert result.produced_json_path.read_bytes() == _valid_json()
    assert json.loads(capture.read_text()) == [
        "-m",
        str(model.resolve()),
        "-l",
        "zh",
        "-f",
        str(media.resolve()),
        "-ojf",
        "-of",
        str(output_base.resolve()),
    ]


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("nonzero", "bounded failure detail"),
        ("missing", "produced no JSON"),
        ("empty", "empty JSON"),
    ],
)
def test_runner_rejects_failed_output_contract(
    tmp_path: Path, mode: str, message: str
) -> None:
    executable, _ = _fake_tool(tmp_path, mode=mode)
    model, media = _inputs(tmp_path)

    with pytest.raises(WhisperExecutionError, match=message):
        WhisperCppRunner().run(
            WhisperCppExecutionRequest(
                executable, model, media, "zh", tmp_path / "stage" / "output"
            )
        )


def test_runner_rejects_invalid_inputs_without_raw_os_errors(tmp_path: Path) -> None:
    executable, _ = _fake_tool(tmp_path)
    model, media = _inputs(tmp_path)
    executable.chmod(0o644)

    with pytest.raises(WhisperExecutionError, match="not executable"):
        WhisperCppRunner().run(
            WhisperCppExecutionRequest(
                executable, model, media, "zh", tmp_path / "output"
            )
        )
    executable.chmod(0o755)
    with pytest.raises(WhisperExecutionError, match="Whisper model"):
        WhisperCppRunner().run(
            WhisperCppExecutionRequest(
                executable,
                model.with_name("missing"),
                media,
                "zh",
                tmp_path / "output",
            )
        )


def test_success_preserves_artifact_provenance_and_imports_existing_format(
    service: ProjectService, tmp_path: Path
) -> None:
    executable, capture = _fake_tool(tmp_path)
    model, media = _inputs(tmp_path)
    source = service.corpus.create_source("Execution source")
    unit = service.corpus.create_source_unit(source.id, kind="part", label="A")
    durable_json = tmp_path / "durable output" / "analysis.json"

    result = service.whisper_execution.run_and_import(
        media_path=media,
        json_output_path=durable_json,
        executable_path=executable,
        model_path=model,
        language="zh",
        source_id=source.id,
        source_unit_id=unit.id,
    )

    assert durable_json.read_bytes() == _valid_json()
    staging_base = Path(json.loads(capture.read_text())[-1])
    assert not staging_base.parent.exists()
    assert result.asr_run.process_type == "asr"
    assert result.asr_run.tool_name == "whisper.cpp"
    assert result.asr_run.tool_version is None
    assert result.asr_run.status == "succeeded"
    assert result.asr_run.parameters == {
        "language": "zh",
        "executable_path": str(executable.resolve()),
        "model_path": str(model.resolve()),
        "output_format": "whisper.cpp-json-full",
    }
    assert result.import_result.processing_run.process_type == "import"
    assert result.import_result.processing_run.tool_name == "LexBundler"
    assert result.import_result.processing_run.status == "succeeded"
    assert result.asr_run.id != result.import_result.processing_run.id
    assert result.json_asset.created_by_run_id == result.asr_run.id
    assert result.import_result.graph.representation.source_asset_id == result.json_asset.id
    assert all(
        span.asset_id == result.media_asset.id
        for span in result.import_result.graph.media_spans
    )
    locations = service.corpus.list_asset_locations(result.json_asset.id)
    assert [location.location for location in locations] == [str(durable_json.resolve())]
    bindings = service.corpus.list_asset_bindings(source.id)
    assert {(item.role, item.processing_run_id) for item in bindings} == {
        ("asr_input", result.asr_run.id),
        ("asr_output", result.asr_run.id),
    }
    assert _run_rows(tmp_path / "execution.lexbundler") == [
        ("asr", "whisper.cpp", "succeeded"),
        ("import", "LexBundler", "succeeded"),
    ]


def test_output_collision_prevents_launch_and_run(
    service: ProjectService, tmp_path: Path
) -> None:
    executable, capture = _fake_tool(tmp_path)
    model, media = _inputs(tmp_path)
    source = service.corpus.create_source("Collision source")
    durable_json = tmp_path / "existing.json"
    durable_json.write_bytes(b"keep me")

    with pytest.raises(WhisperExecutionError, match="already exists"):
        service.whisper_execution.run_and_import(
            media_path=media,
            json_output_path=durable_json,
            executable_path=executable,
            model_path=model,
            language="zh",
            source_id=source.id,
        )

    assert durable_json.read_bytes() == b"keep me"
    assert not capture.exists()
    assert _run_rows(tmp_path / "execution.lexbundler") == []


def test_process_failure_marks_asr_failed_and_cleans_staging(
    service: ProjectService, tmp_path: Path
) -> None:
    executable, capture = _fake_tool(tmp_path, mode="nonzero")
    model, media = _inputs(tmp_path)
    source = service.corpus.create_source("Failed source")
    durable_json = tmp_path / "failed.json"

    with pytest.raises(WhisperExecutionError, match="status 7"):
        service.whisper_execution.run_and_import(
            media_path=media,
            json_output_path=durable_json,
            executable_path=executable,
            model_path=model,
            language="zh",
            source_id=source.id,
        )

    staging_base = Path(json.loads(capture.read_text())[-1])
    assert not staging_base.parent.exists()
    assert not durable_json.exists()
    assert _run_rows(tmp_path / "execution.lexbundler") == [
        ("asr", "whisper.cpp", "failed")
    ]
    assert _graph_counts(tmp_path / "execution.lexbundler") == (0, 0, 0, 0, 0)


def test_keyboard_interrupt_cancels_active_asr_without_importing(
    service: ProjectService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, _ = _fake_tool(tmp_path)
    model, media = _inputs(tmp_path)
    source = service.corpus.create_source("Interrupted source")
    interruption = KeyboardInterrupt("synthetic interruption")

    def interrupt_runner(request: object) -> object:
        raise interruption

    monkeypatch.setattr(service.whisper_execution._runner, "run", interrupt_runner)

    with pytest.raises(KeyboardInterrupt) as caught:
        service.whisper_execution.run_and_import(
            media_path=media,
            json_output_path=tmp_path / "interrupted.json",
            executable_path=executable,
            model_path=model,
            language="zh",
            source_id=source.id,
        )

    assert caught.value is interruption
    assert _run_rows(tmp_path / "execution.lexbundler") == [
        ("asr", "whisper.cpp", "cancelled")
    ]
    assert _graph_counts(tmp_path / "execution.lexbundler") == (0, 0, 0, 0, 0)


def test_asr_stays_succeeded_when_preserved_json_is_invalid(
    service: ProjectService, tmp_path: Path
) -> None:
    executable, _ = _fake_tool(tmp_path, payload=b"{}")
    model, media = _inputs(tmp_path)
    source = service.corpus.create_source("Invalid artifact source")
    durable_json = tmp_path / "invalid.json"

    with pytest.raises(WhisperCppFormatError, match="transcription"):
        service.whisper_execution.run_and_import(
            media_path=media,
            json_output_path=durable_json,
            executable_path=executable,
            model_path=model,
            language="zh",
            source_id=source.id,
        )

    assert durable_json.read_bytes() == b"{}"
    assert _run_rows(tmp_path / "execution.lexbundler") == [
        ("asr", "whisper.cpp", "succeeded")
    ]
    assert len(service.corpus.list_asset_bindings(source.id)) == 2
    assert _graph_counts(tmp_path / "execution.lexbundler") == (0, 0, 0, 0, 0)


def test_graph_failure_keeps_asr_success_and_fails_atomic_import(
    service: ProjectService, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, _ = _fake_tool(tmp_path)
    model, media = _inputs(tmp_path)
    source = service.corpus.create_source("Graph failure source")
    original = sqlite_text_store._insert_flat_segment
    calls = 0

    def fail_second(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic graph failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(sqlite_text_store, "_insert_flat_segment", fail_second)
    with pytest.raises(RuntimeError, match="graph failure"):
        service.whisper_execution.run_and_import(
            media_path=media,
            json_output_path=tmp_path / "graph.json",
            executable_path=executable,
            model_path=model,
            language="zh",
            source_id=source.id,
        )

    assert _run_rows(tmp_path / "execution.lexbundler") == [
        ("asr", "whisper.cpp", "succeeded"),
        ("import", "LexBundler", "failed"),
    ]
    assert _graph_counts(tmp_path / "execution.lexbundler") == (0, 0, 0, 0, 0)
