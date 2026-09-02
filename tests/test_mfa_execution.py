import base64
import json
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

from lexbundler.application.forced_alignment_service import _materialize_transcript
from lexbundler.application.project_service import ProjectService
from lexbundler.domain.errors import MfaExecutionError
from lexbundler.external_tools.mfa import (
    MfaAlignmentRequest,
    MfaRunner,
    _subprocess_environment,
)
from lexbundler.persistence.sqlite.project_store import SQLiteProjectStoreFactory
import lexbundler.persistence.sqlite.text_segment_store as sqlite_text_store


def _valid_mfa() -> bytes:
    return json.dumps({
        "start": 0, "end": 1,
        "tiers": {
            "words": {"type": "IntervalTier", "entries": [
                [0, .1, "<eps>"], [.1, .5, "你好"], [.5, 1, "<eps>"],
            ]},
            "phones": {"type": "IntervalTier", "entries": [
                [0, .1, "sil"], [.1, .3, "n"], [.3, .5, "i"], [.5, 1, "sil"],
            ]},
        },
    }, ensure_ascii=False).encode()


def _fake_mfa(
    tmp_path: Path, *, mode: str = "success", payload: bytes | None = None,
    long_log: bool = False, invoke_sibling: bool = False,
) -> tuple[Path, Path]:
    directory = tmp_path / "MFA tools with spaces"
    directory.mkdir(exist_ok=True)
    executable = directory / "fake mfa"
    capture = tmp_path / f"mfa-capture-{mode}.json"
    encoded = base64.b64encode(_valid_mfa() if payload is None else payload).decode()
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import base64, json, pathlib, subprocess, sys\n"
        f"mode = {mode!r}\n"
        f"capture = pathlib.Path({str(capture)!r})\n"
        "transcript = pathlib.Path(sys.argv[3]).read_bytes()\n"
        "capture.write_text(json.dumps({'argv': sys.argv[1:], "
        "'transcript_b64': base64.b64encode(transcript).decode()}))\n"
        f"print('x' * {20000 if long_log else 1})\n"
        "print('MFA diagnostic marker')\n"
        f"invoke_sibling = {invoke_sibling!r}\n"
        "if invoke_sibling: subprocess.run(['mfa-sibling-helper'], check=True)\n"
        "if mode == 'nonzero': raise SystemExit(7)\n"
        "output = pathlib.Path(sys.argv[5])\n"
        "if mode == 'missing': raise SystemExit(0)\n"
        "if mode == 'empty': output.touch()\n"
        f"else: output.write_bytes(base64.b64decode({encoded!r}))\n"
    )
    executable.chmod(0o755)
    return executable, capture


def _runner_inputs(tmp_path: Path) -> tuple[Path, Path]:
    audio = tmp_path / "source audio with spaces.wav"
    transcript = tmp_path / "authoritative text with spaces.txt"
    audio.write_bytes(b"wave")
    transcript.write_bytes("你好\r\n".encode())
    return audio, transcript


def _request(
    executable: Path, audio: Path, transcript: Path, output: Path,
    *, use_g2p: bool = True,
) -> MfaAlignmentRequest:
    return MfaAlignmentRequest(
        executable, audio, transcript, "model/id with space", output,
        "dialect value", use_g2p, "json",
    )


@pytest.mark.parametrize("use_g2p", [True, False])
def test_runner_exact_argv_paths_and_optional_g2p(
    tmp_path: Path, use_g2p: bool
) -> None:
    executable, capture = _fake_mfa(tmp_path)
    audio, transcript = _runner_inputs(tmp_path)
    output = tmp_path / "staging with spaces" / "output.json"
    result = MfaRunner().run(
        _request(executable, audio, transcript, output, use_g2p=use_g2p)
    )
    expected = [
        "align_one_hf", str(audio.resolve()), str(transcript.resolve()),
        "model/id with space", str(output.resolve()), "--dialect", "dialect value",
    ]
    if use_g2p:
        expected.append("--use_g2p")
    expected.extend(["--output_format", "json"])
    assert json.loads(capture.read_text())["argv"] == expected
    assert result.produced_json_path.read_bytes() == _valid_mfa()


def test_runner_explicitly_disables_shell(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    executable, _ = _fake_mfa(tmp_path)
    audio, transcript = _runner_inputs(tmp_path)
    original = subprocess.Popen
    observed: list[tuple[object, object]] = []

    def checked_popen(*args: object, **kwargs: object) -> object:
        observed.append((kwargs.get("shell"), kwargs.get("env")))
        return original(*args, **kwargs)

    monkeypatch.setattr("lexbundler.external_tools.mfa.subprocess.Popen", checked_popen)
    MfaRunner().run(_request(executable, audio, transcript, tmp_path / "out.json"))
    assert observed[0][0] is False
    environment = observed[0][1]
    assert isinstance(environment, dict)
    assert environment["PATH"].split(os.pathsep)[0] == str(executable.parent.resolve())


@pytest.mark.parametrize("original_path", [None, "", "/inherited/bin:/another/bin"])
def test_subprocess_environment_prepends_without_mutating_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, original_path: str | None,
) -> None:
    executable = tmp_path / "suite directory with spaces" / "mfa"
    executable.parent.mkdir()
    if original_path is None:
        monkeypatch.delenv("PATH", raising=False)
    else:
        monkeypatch.setenv("PATH", original_path)
    monkeypatch.setenv("MFA_ENV_SENTINEL", "preserved")
    before = os.environ.copy()
    environment = _subprocess_environment(executable)
    assert os.environ.copy() == before
    assert environment is not os.environ
    assert environment["MFA_ENV_SENTINEL"] == "preserved"
    expected = str(executable.parent)
    if original_path:
        expected += os.pathsep + original_path
    assert environment["PATH"] == expected


def test_runner_exposes_sibling_executable_by_bare_name(tmp_path: Path) -> None:
    executable, _ = _fake_mfa(tmp_path, invoke_sibling=True)
    sibling = executable.parent / "mfa-sibling-helper"
    marker = tmp_path / "sibling-ran"
    sibling.write_text(
        "#!/bin/sh\n"
        f"touch {str(marker)!r}\n"
    )
    sibling.chmod(0o755)
    audio, transcript = _runner_inputs(tmp_path)
    MfaRunner().run(_request(executable, audio, transcript, tmp_path / "out.json"))
    assert marker.is_file()


@pytest.mark.parametrize(
    ("mode", "payload", "message"),
    [
        ("nonzero", None, "status 7"), ("missing", None, "produced no JSON"),
        ("empty", None, "empty JSON"), ("success", b"{", "invalid MFA JSON"),
        ("success", b"{}", "invalid MFA JSON"),
    ],
)
def test_runner_rejects_failed_or_invalid_output(
    tmp_path: Path, mode: str, payload: bytes | None, message: str
) -> None:
    executable, _ = _fake_mfa(tmp_path, mode=mode, payload=payload)
    audio, transcript = _runner_inputs(tmp_path)
    with pytest.raises(MfaExecutionError, match=message):
        MfaRunner().run(_request(
            executable, audio, transcript, tmp_path / mode / "out.json"
        ))


def test_diagnostic_capture_is_bounded_to_tail(tmp_path: Path) -> None:
    executable, _ = _fake_mfa(tmp_path, mode="nonzero", long_log=True)
    audio, transcript = _runner_inputs(tmp_path)
    with pytest.raises(MfaExecutionError) as caught:
        MfaRunner().run(_request(executable, audio, transcript, tmp_path / "out.json"))
    assert "MFA diagnostic marker" in str(caught.value)
    assert len(str(caught.value).encode()) < 17 * 1024


def test_runner_interruption_terminates_then_kills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable, _ = _fake_mfa(tmp_path)
    audio, transcript = _runner_inputs(tmp_path)

    class Process:
        terminated = False
        killed = False
        waits = 0
        def wait(self, timeout: float | None = None) -> int:
            self.waits += 1
            if self.waits == 1:
                raise KeyboardInterrupt
            if timeout is not None:
                raise subprocess.TimeoutExpired("mfa", timeout)
            return 0
        def poll(self) -> None:
            return None
        def terminate(self) -> None:
            self.terminated = True
        def kill(self) -> None:
            self.killed = True

    process = Process()
    monkeypatch.setattr("lexbundler.external_tools.mfa.subprocess.Popen",
                        lambda *args, **kwargs: process)
    with pytest.raises(KeyboardInterrupt):
        MfaRunner().run(_request(executable, audio, transcript, tmp_path / "out.json"))
    assert process.terminated and process.killed


@pytest.mark.parametrize("content", ["中文，标点！\n", "中文\r\n末行\r\n", "e\u0301é"])
def test_transcript_materialization_is_exact(tmp_path: Path, content: str) -> None:
    path = tmp_path / "materialized.txt"
    _materialize_transcript(path, content)
    assert path.read_bytes() == content.encode("utf-8")


def _context(tmp_path: Path) -> tuple[ProjectService, Path, object, object, Path]:
    project_path = tmp_path / "execution.lexbundler"
    project = ProjectService(SQLiteProjectStoreFactory())
    project.create_project(project_path, name="MFA execution")
    source = project.corpus.create_source("S", language_tag="zh")
    transcript_path = tmp_path / "source.txt"
    transcript_path.write_text("你好", encoding="utf-8")
    text = project.transcript_imports.import_utf8(
        transcript_path, source_id=source.id, language_tag="zh"
    ).graph.representation
    audio_path = tmp_path / "source.wav"
    audio_path.write_bytes(b"wave")
    media = project.corpus.register_local_asset(audio_path, asset_kind="audio")
    return project, project_path, source, text, audio_path


def _run_rows(path: Path) -> list[tuple[str, str | None, str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute(
            "SELECT process_type, tool_name, status FROM processing_run ORDER BY id"
        ).fetchall()


def test_service_preserves_artifact_and_creates_separate_runs_and_layers(
    tmp_path: Path
) -> None:
    project, project_path, source, text, _ = _context(tmp_path)
    executable, capture = _fake_mfa(tmp_path)
    import hashlib
    media = project.corpus.find_asset_by_sha256(hashlib.sha256(b"wave").hexdigest())
    assert media is not None
    whisper = project.text_segments.create_segment_layer(
        source.id, name="whisper.cpp raw ASR", layer_kind="asr"
    )
    durable = tmp_path / "durable output" / "alignment.json"
    result = project.forced_alignments.align_and_import(
        media_asset=media, authoritative_text=text, json_output_path=durable,
        executable_path=executable, source_id=source.id,
        model_id="model/id with space", dialect="dialect value", tool_version="3.4.2",
    )
    captured = json.loads(capture.read_text())
    assert base64.b64decode(captured["transcript_b64"]) == "你好".encode()
    assert not Path(captured["argv"][2]).parent.exists()
    assert durable.read_bytes() == _valid_mfa()
    assert result.execution_run.status == "succeeded"
    assert result.execution_run.tool_version == "3.4.2"
    assert result.import_result.processing_run.status == "succeeded"
    assert result.execution_run.id != result.import_result.processing_run.id
    assert result.json_asset.created_by_run_id == result.execution_run.id
    assert [layer.name for layer in result.import_result.graph.layers] == [
        "MFA word alignment", "MFA phone alignment"
    ]
    assert any(layer.id == whisper.id for layer in project.text_segments.list_segment_layers(source.id))
    assert _run_rows(project_path)[-2:] == [
        ("forced_alignment", "Montreal Forced Aligner", "succeeded"),
        ("import", "LexBundler", "succeeded"),
    ]


def test_service_rejects_collision_and_missing_media_before_run(tmp_path: Path) -> None:
    project, project_path, source, text, audio_path = _context(tmp_path)
    import hashlib
    media = project.corpus.find_asset_by_sha256(hashlib.sha256(b"wave").hexdigest())
    assert media is not None
    executable, capture = _fake_mfa(tmp_path)
    output = tmp_path / "exists.json"
    output.write_bytes(b"keep")
    with pytest.raises(MfaExecutionError, match="already exists"):
        project.forced_alignments.align_and_import(
            media_asset=media, authoritative_text=text, json_output_path=output,
            executable_path=executable, source_id=source.id,
        )
    audio_path.unlink()
    with pytest.raises(MfaExecutionError, match="no currently usable"):
        project.forced_alignments.align_and_import(
            media_asset=media, authoritative_text=text,
            json_output_path=tmp_path / "new.json", executable_path=executable,
            source_id=source.id,
        )
    assert output.read_bytes() == b"keep"
    assert not capture.exists()
    assert _run_rows(project_path) == [("import", "LexBundler", "succeeded")]


def test_failed_execution_publishes_nothing_and_cleans_staging(tmp_path: Path) -> None:
    project, project_path, source, text, _ = _context(tmp_path)
    import hashlib
    media = project.corpus.find_asset_by_sha256(hashlib.sha256(b"wave").hexdigest())
    assert media is not None
    executable, capture = _fake_mfa(tmp_path, mode="nonzero")
    durable = tmp_path / "failed.json"
    with pytest.raises(MfaExecutionError, match="status 7"):
        project.forced_alignments.align_and_import(
            media_asset=media, authoritative_text=text, json_output_path=durable,
            executable_path=executable, source_id=source.id,
        )
    staged = Path(json.loads(capture.read_text())["argv"][4])
    assert not staged.parent.exists()
    assert not durable.exists()
    assert _run_rows(project_path)[-1] == (
        "forced_alignment", "Montreal Forced Aligner", "failed"
    )


def test_malformed_execution_output_is_not_published(tmp_path: Path) -> None:
    project, project_path, source, text, _ = _context(tmp_path)
    import hashlib
    media = project.corpus.find_asset_by_sha256(hashlib.sha256(b"wave").hexdigest())
    assert media is not None
    executable, _ = _fake_mfa(tmp_path, payload=b"{}")
    durable = tmp_path / "malformed.json"
    with pytest.raises(MfaExecutionError, match="invalid MFA JSON"):
        project.forced_alignments.align_and_import(
            media_asset=media, authoritative_text=text, json_output_path=durable,
            executable_path=executable, source_id=source.id,
        )
    assert not durable.exists()
    assert _run_rows(project_path)[-1][2] == "failed"


def test_service_interruption_marks_execution_cancelled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, project_path, source, text, _ = _context(tmp_path)
    import hashlib
    media = project.corpus.find_asset_by_sha256(hashlib.sha256(b"wave").hexdigest())
    assert media is not None
    executable, _ = _fake_mfa(tmp_path)
    interruption = KeyboardInterrupt("stop")
    monkeypatch.setattr(
        project.forced_alignments._runner, "run",
        lambda request: (_ for _ in ()).throw(interruption),
    )
    with pytest.raises(KeyboardInterrupt) as caught:
        project.forced_alignments.align_and_import(
            media_asset=media, authoritative_text=text,
            json_output_path=tmp_path / "cancelled.json",
            executable_path=executable, source_id=source.id,
        )
    assert caught.value is interruption
    assert _run_rows(project_path)[-1][2] == "cancelled"


def test_wrong_project_assets_and_text_are_rejected(tmp_path: Path) -> None:
    project, project_path, source, text, _ = _context(tmp_path)
    other = ProjectService(SQLiteProjectStoreFactory())
    other.create_project(tmp_path / "other.lexbundler", name="Other")
    other_source = other.corpus.create_source("Other")
    other_audio_path = tmp_path / "other.wav"
    other_audio_path.write_bytes(b"different")
    other_media = other.corpus.register_local_asset(other_audio_path)
    other_text_path = tmp_path / "other.txt"
    other_text_path.write_text("你好", encoding="utf-8")
    other_text = other.transcript_imports.import_utf8(
        other_text_path, source_id=other_source.id
    ).graph.representation
    executable, _ = _fake_mfa(tmp_path)
    with pytest.raises(MfaExecutionError, match="media Asset"):
        project.forced_alignments.align_and_import(
            media_asset=other_media, authoritative_text=text,
            json_output_path=tmp_path / "wrong-media.json",
            executable_path=executable, source_id=source.id,
        )
    import hashlib
    media = project.corpus.find_asset_by_sha256(hashlib.sha256(b"wave").hexdigest())
    assert media is not None
    with pytest.raises(MfaExecutionError, match="TextRepresentation"):
        project.forced_alignments.align_and_import(
            media_asset=media, authoritative_text=other_text,
            json_output_path=tmp_path / "wrong-text.json",
            executable_path=executable, source_id=source.id,
        )
    assert _run_rows(project_path) == [("import", "LexBundler", "succeeded")]


def test_import_failure_preserves_execution_success_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, project_path, source, text, _ = _context(tmp_path)
    import hashlib
    media = project.corpus.find_asset_by_sha256(hashlib.sha256(b"wave").hexdigest())
    assert media is not None
    executable, _ = _fake_mfa(tmp_path)
    durable = tmp_path / "preserved.json"
    monkeypatch.setattr(sqlite_text_store, "_insert_segment",
                        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("graph")))
    with pytest.raises(RuntimeError, match="graph"):
        project.forced_alignments.align_and_import(
            media_asset=media, authoritative_text=text, json_output_path=durable,
            executable_path=executable, source_id=source.id,
        )
    assert durable.read_bytes() == _valid_mfa()
    assert _run_rows(project_path)[-2:] == [
        ("forced_alignment", "Montreal Forced Aligner", "succeeded"),
        ("import", "LexBundler", "failed"),
    ]
