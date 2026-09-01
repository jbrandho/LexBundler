"""Application workflow for whisper.cpp execution and artifact import."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.whisper_import_service import (
    WhisperImportResult,
    WhisperImportService,
)
from lexbundler.domain.corpus import Asset, ProcessingRun
from lexbundler.domain.errors import WhisperExecutionError
from lexbundler.external_tools.whisper_cpp import (
    WhisperCppExecutionRequest,
    WhisperCppRunner,
    validate_whisper_cpp_inputs,
)


@dataclass(frozen=True, slots=True)
class WhisperTranscriptionResult:
    asr_run: ProcessingRun
    media_asset: Asset
    json_asset: Asset
    json_output_path: Path
    import_result: WhisperImportResult


class WhisperExecutionService:
    """Run whisper.cpp, preserve its native artifact, then normalize it."""

    def __init__(
        self,
        corpus: CorpusService,
        imports: WhisperImportService,
        runner: WhisperCppRunner | None = None,
    ) -> None:
        self._corpus = corpus
        self._imports = imports
        self._runner = runner or WhisperCppRunner()

    def run_and_import(
        self,
        *,
        media_path: Path,
        json_output_path: Path,
        executable_path: Path,
        model_path: Path,
        language: str,
        source_id: int,
        source_unit_id: int | None = None,
    ) -> WhisperTranscriptionResult:
        executable, model, media, normalized_language = validate_whisper_cpp_inputs(
            executable_path, model_path, media_path, language
        )
        durable_json = _prepare_output_path(json_output_path)
        media_asset = self._corpus.register_local_asset(media)
        asr_run = self._corpus.start_processing_run(
            "asr",
            tool_name="whisper.cpp",
            parameters={
                "language": normalized_language,
                "executable_path": str(executable),
                "model_path": str(model),
                "output_format": "whisper.cpp-json-full",
            },
        )
        try:
            self._bind_asset(
                source_id,
                source_unit_id,
                media_asset.id,
                role="asr_input",
                run_id=asr_run.id,
            )
            with TemporaryDirectory(prefix="lexbundler-whisper-") as staging:
                output_base = Path(staging) / "output"
                execution = self._runner.run(
                    WhisperCppExecutionRequest(
                        executable_path=executable,
                        model_path=model,
                        media_path=media,
                        language=normalized_language,
                        output_base=output_base,
                    )
                )
                _publish_json(execution.produced_json_path, durable_json)
            json_asset = self._corpus.register_local_asset(
                durable_json,
                asset_kind="document",
                mime_type="application/json",
                created_by_run_id=asr_run.id,
            )
            self._bind_asset(
                source_id,
                source_unit_id,
                json_asset.id,
                role="asr_output",
                run_id=asr_run.id,
            )
        except KeyboardInterrupt:
            self._corpus.finish_processing_run(asr_run.id, status="cancelled")
            raise
        except OSError as error:
            self._corpus.finish_processing_run(asr_run.id, status="failed")
            raise WhisperExecutionError(
                "Could not create or clean the whisper.cpp staging workspace."
            ) from error
        except Exception:
            self._corpus.finish_processing_run(asr_run.id, status="failed")
            raise

        completed_asr_run = self._corpus.finish_processing_run(
            asr_run.id, status="succeeded"
        )
        import_result = self._imports.import_registered_json(
            durable_json,
            json_asset=json_asset,
            media_asset=media_asset,
            source_id=source_id,
            source_unit_id=source_unit_id,
        )
        return WhisperTranscriptionResult(
            asr_run=completed_asr_run,
            media_asset=media_asset,
            json_asset=json_asset,
            json_output_path=durable_json,
            import_result=import_result,
        )

    def _bind_asset(
        self,
        source_id: int,
        source_unit_id: int | None,
        asset_id: int,
        *,
        role: str,
        run_id: int,
    ) -> None:
        arguments = {
            "role": role,
            "assignment_method": "tool_execution",
            "processing_run_id": run_id,
        }
        if source_unit_id is None:
            self._corpus.bind_asset_to_source(source_id, asset_id, **arguments)
        else:
            self._corpus.bind_asset_to_source_unit(
                source_id, source_unit_id, asset_id, **arguments
            )


def _prepare_output_path(path: Path) -> Path:
    output = Path(path).resolve()
    if output.exists():
        raise WhisperExecutionError(
            f"The durable whisper.cpp JSON output already exists: {output}"
        )
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise WhisperExecutionError(
            f"Could not create the durable output directory: {output.parent}"
        ) from error
    if not output.parent.is_dir():
        raise WhisperExecutionError(
            f"The durable output parent is not a directory: {output.parent}"
        )
    return output


def _publish_json(staged_json: Path, durable_json: Path) -> None:
    created = False
    try:
        with Path(staged_json).open("rb") as source:
            with durable_json.open("xb") as destination:
                created = True
                shutil.copyfileobj(source, destination)
    except FileExistsError as error:
        raise WhisperExecutionError(
            f"The durable whisper.cpp JSON output already exists: {durable_json}"
        ) from error
    except OSError as error:
        if created:
            durable_json.unlink(missing_ok=True)
        raise WhisperExecutionError(
            f"Could not publish whisper.cpp JSON to: {durable_json}"
        ) from error
