"""Application workflow for MFA execution and existing-artifact normalization."""

import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.mfa_import_service import MfaImportResult, MfaImportService
from lexbundler.application.text_segment_service import TextSegmentService
from lexbundler.domain.corpus import Asset, ProcessingRun
from lexbundler.domain.errors import MfaExecutionError
from lexbundler.domain.text_segments import TextRepresentation
from lexbundler.external_tools.mfa import MfaAlignmentRequest, MfaRunner


@dataclass(frozen=True, slots=True)
class ForcedAlignmentResult:
    execution_run: ProcessingRun
    json_asset: Asset
    json_output_path: Path
    import_result: MfaImportResult
    model_id: str
    dialect: str
    use_g2p: bool


class ForcedAlignmentService:
    """Run configured MFA, preserve valid native JSON, then normalize it."""

    def __init__(
        self, corpus: CorpusService, text_segments: TextSegmentService,
        imports: MfaImportService, runner: MfaRunner | None = None,
    ) -> None:
        self._corpus = corpus
        self._text_segments = text_segments
        self._imports = imports
        self._runner = runner or MfaRunner()

    def align_and_import(
        self, *, media_asset: Asset, authoritative_text: TextRepresentation,
        json_output_path: Path, executable_path: Path, source_id: int,
        source_unit_id: int | None = None,
        model_id: str = "MontrealCorpusTools/mandarin_mfa",
        dialect: str = "mandarin_china_mfa", use_g2p: bool = True,
        output_format: str = "json", tool_version: str | None = None,
    ) -> ForcedAlignmentResult:
        stored_media = self._corpus.get_asset(media_asset.id)
        stored_text = self._text_segments.get_text_representation(authoritative_text.id)
        if stored_media != media_asset:
            raise MfaExecutionError("The selected media Asset does not match this project.")
        if (
            stored_text != authoritative_text
            or stored_text.source_id != source_id
            or stored_text.source_unit_id != source_unit_id
        ):
            raise MfaExecutionError(
                "The authoritative TextRepresentation does not match the selected source."
            )
        audio_path = self._resolve_local_asset(stored_media.id)
        durable_json = _prepare_output_path(json_output_path)
        execution_run = self._corpus.start_processing_run(
            "forced_alignment", tool_name="Montreal Forced Aligner",
            tool_version=tool_version,
            parameters={
                "workflow": "align_one_hf", "model_id": model_id,
                "dialect": dialect, "use_g2p": use_g2p,
                "output_format": output_format,
                "executable_path": str(Path(executable_path).resolve()),
                "authoritative_text_representation_id": stored_text.id,
                "media_asset_id": stored_media.id,
            },
        )
        try:
            self._bind(source_id, source_unit_id, stored_media.id,
                       "forced_alignment_input", execution_run.id)
            with TemporaryDirectory(prefix="lexbundler-mfa-") as staging:
                staging_path = Path(staging)
                transcript_path = staging_path / "authoritative-transcript.txt"
                staged_json = staging_path / "alignment.json"
                _materialize_transcript(transcript_path, stored_text.content)
                execution = self._runner.run(MfaAlignmentRequest(
                    executable_path=Path(executable_path),
                    source_audio_path=audio_path,
                    transcript_path=transcript_path, model_id=model_id,
                    output_path=staged_json, dialect=dialect, use_g2p=use_g2p,
                    output_format=output_format,
                ))
                _publish_json(execution.produced_json_path, durable_json)
            json_asset = self._corpus.register_local_asset(
                durable_json, asset_kind="document", mime_type="application/json",
                created_by_run_id=execution_run.id,
            )
            self._bind(source_id, source_unit_id, json_asset.id,
                       "forced_alignment_output", execution_run.id)
        except KeyboardInterrupt:
            self._corpus.finish_processing_run(execution_run.id, status="cancelled")
            raise
        except OSError as error:
            self._corpus.finish_processing_run(execution_run.id, status="failed")
            raise MfaExecutionError(
                "Could not create or clean the MFA staging workspace."
            ) from error
        except Exception:
            self._corpus.finish_processing_run(execution_run.id, status="failed")
            raise

        completed_run = self._corpus.finish_processing_run(
            execution_run.id, status="succeeded"
        )
        import_result = self._imports.import_registered_json(
            durable_json, json_asset=json_asset, media_asset=stored_media,
            authoritative_text=stored_text, source_id=source_id,
            source_unit_id=source_unit_id,
        )
        return ForcedAlignmentResult(
            completed_run, json_asset, durable_json, import_result,
            model_id, dialect, use_g2p,
        )

    def _resolve_local_asset(self, asset_id: int) -> Path:
        for location in self._corpus.list_asset_locations(asset_id):
            if location.location_kind != "filesystem":
                continue
            candidate = Path(location.location)
            if candidate.is_file():
                return candidate.resolve()
        raise MfaExecutionError(
            f"Asset {asset_id} has no currently usable local file location."
        )

    def _bind(
        self, source_id: int, source_unit_id: int | None, asset_id: int,
        role: str, run_id: int,
    ) -> None:
        arguments = dict(
            role=role, assignment_method="tool_execution",
            processing_run_id=run_id,
        )
        if source_unit_id is None:
            self._corpus.bind_asset_to_source(source_id, asset_id, **arguments)
        else:
            self._corpus.bind_asset_to_source_unit(
                source_id, source_unit_id, asset_id, **arguments
            )


def _materialize_transcript(path: Path, content: str) -> None:
    try:
        path.write_bytes(content.encode("utf-8"))
    except (OSError, UnicodeEncodeError) as error:
        raise MfaExecutionError(
            "Could not materialize the exact authoritative transcript for MFA."
        ) from error


def _prepare_output_path(path: Path) -> Path:
    output = Path(path).resolve()
    if output.exists():
        raise MfaExecutionError(f"The durable MFA JSON output already exists: {output}")
    if output.suffix.lower() != ".json":
        raise MfaExecutionError("The durable MFA output must use a .json extension.")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise MfaExecutionError(
            f"Could not create the durable output directory: {output.parent}"
        ) from error
    if not output.parent.is_dir():
        raise MfaExecutionError(
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
        raise MfaExecutionError(
            f"The durable MFA JSON output already exists: {durable_json}"
        ) from error
    except OSError as error:
        if created:
            durable_json.unlink(missing_ok=True)
        raise MfaExecutionError(
            f"Could not publish MFA JSON to: {durable_json}"
        ) from error
