"""Application workflow for durable listening-comprehension Anki exports."""

import hashlib
import html
import shutil
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable
from uuid import UUID

from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.text_segment_service import TextSegmentService
from lexbundler.domain.corpus import Asset, ProcessingRun
from lexbundler.domain.errors import AnkiExportError
from lexbundler.exporters.anki import (
    LISTENING_CARD_VERSION,
    AnkiNote,
    listening_note_guid,
    write_listening_package,
)

APKG_MIME_TYPE = "application/vnd.anki"
ANKI_ID_FLOOR = 1 << 30
ANKI_ID_RANGE = 1 << 30


@dataclass(frozen=True, slots=True)
class AnkiExportItem:
    text_span_id: int
    media_span_id: int
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.text_span_id) is not int or self.text_span_id <= 0:
            raise AnkiExportError("Text-span ID must be a positive integer.")
        if type(self.media_span_id) is not int or self.media_span_id <= 0:
            raise AnkiExportError("Media-span ID must be a positive integer.")
        _normalize_tags(self.tags)


@dataclass(frozen=True, slots=True)
class AnkiExportResult:
    processing_run: ProcessingRun
    package_asset: Asset
    output_path: Path
    note_count: int


@dataclass(frozen=True, slots=True)
class _ResolvedItem:
    segment_id: int
    chinese_sc: str
    source: str
    audio_asset: Asset
    audio_path: Path
    tags: tuple[str, ...]


class AnkiExportService:
    """Resolve explicit corpus spans and export Listening v1 notes."""

    def __init__(
        self,
        corpus: CorpusService,
        text_segments: TextSegmentService,
        project_uuid_provider: Callable[[], UUID],
    ) -> None:
        self._corpus = corpus
        self._text_segments = text_segments
        self._project_uuid_provider = project_uuid_provider

    def export_apkg(
        self,
        *,
        deck_name: str,
        output_path: Path,
        items: list[AnkiExportItem] | tuple[AnkiExportItem, ...],
    ) -> AnkiExportResult:
        project_uuid = self._project_uuid_provider()
        normalized_name = _required_deck_name(deck_name)
        durable_output = _prepare_output_path(output_path)
        resolved = tuple(self._resolve_item(item) for item in items)
        if not resolved:
            raise AnkiExportError("At least one Anki export item is required.")
        segment_ids = [item.segment_id for item in resolved]
        if len(segment_ids) != len(set(segment_ids)):
            raise AnkiExportError(
                "Listening v1 export items must identify distinct Segments."
            )

        run = self._corpus.start_processing_run(
            "export",
            tool_name="LexBundler",
            parameters={
                "export_format": "anki-apkg",
                "deck_name": normalized_name,
                "note_model": LISTENING_CARD_VERSION,
                "item_count": len(resolved),
            },
        )
        try:
            with TemporaryDirectory(prefix="lexbundler-anki-") as staging:
                staging_path = Path(staging)
                notes, media_paths = _stage_notes(
                    project_uuid, resolved, staging_path
                )
                staged_package = staging_path / "deck.apkg"
                write_listening_package(
                    deck_id=anki_deck_id(project_uuid, normalized_name),
                    deck_name=normalized_name,
                    notes=notes,
                    media_paths=media_paths,
                    output_path=staged_package,
                )
                _verify_package(staged_package)
                _publish_package(staged_package, durable_output)
            package_asset = self._corpus.register_local_asset(
                durable_output,
                asset_kind="package",
                mime_type=APKG_MIME_TYPE,
                created_by_run_id=run.id,
            )
        except KeyboardInterrupt:
            self._corpus.finish_processing_run(run.id, status="cancelled")
            raise
        except Exception:
            self._corpus.finish_processing_run(run.id, status="failed")
            raise

        completed_run = self._corpus.finish_processing_run(run.id, status="succeeded")
        return AnkiExportResult(
            processing_run=completed_run,
            package_asset=package_asset,
            output_path=durable_output,
            note_count=len(resolved),
        )

    def _resolve_item(self, item: AnkiExportItem) -> _ResolvedItem:
        if not isinstance(item, AnkiExportItem):
            raise AnkiExportError("Every export item must be an AnkiExportItem.")
        text_span = self._text_segments.get_segment_text_span(item.text_span_id)
        media_span = self._text_segments.get_segment_media_span(item.media_span_id)
        if text_span.segment_id != media_span.segment_id:
            raise AnkiExportError(
                "The selected text and media spans must belong to the same Segment."
            )
        if media_span.role != "rendered_clip":
            raise AnkiExportError(
                "The selected media span must have role 'rendered_clip'."
            )
        segment = self._text_segments.get_segment(text_span.segment_id)
        layer = self._text_segments.get_segment_layer(segment.layer_id)
        source = self._corpus.get_source(layer.source_id)
        source_label_parts = [source.name]
        source_unit_id = layer.source_unit_id
        unit_labels: list[str] = []
        while source_unit_id is not None:
            unit = self._corpus.get_source_unit(source_unit_id)
            unit_labels.append(unit.label)
            source_unit_id = unit.parent_id
        source_label_parts.extend(reversed(unit_labels))
        audio_asset = self._corpus.get_asset(media_span.asset_id)
        audio_path = self._resolve_audio_path(audio_asset.id)
        return _ResolvedItem(
            segment_id=segment.id,
            chinese_sc=self._text_segments.resolve_text_span(text_span.id),
            source=" — ".join(source_label_parts),
            audio_asset=audio_asset,
            audio_path=audio_path,
            tags=_normalize_tags(item.tags),
        )

    def _resolve_audio_path(self, asset_id: int) -> Path:
        for location in self._corpus.list_asset_locations(asset_id):
            if location.location_kind != "filesystem":
                continue
            candidate = Path(location.location)
            if candidate.is_file():
                return candidate.resolve()
        raise AnkiExportError(
            f"Rendered audio Asset {asset_id} has no currently usable local file."
        )


def anki_deck_id(project_uuid: UUID, deck_name: str) -> int:
    """Map project UUID and deck name deterministically into genanki's ID range."""
    digest = hashlib.sha256(
        f"{project_uuid}\0{deck_name}".encode("utf-8")
    ).digest()
    return ANKI_ID_FLOOR + int.from_bytes(digest[:8], "big") % ANKI_ID_RANGE


def lexbundler_listening_id(project_uuid: UUID, segment_id: int) -> str:
    return f"{project_uuid}:{segment_id}:{LISTENING_CARD_VERSION}"


def media_basename(asset: Asset) -> str:
    return f"lb-{asset.sha256}.mp3"


def _stage_notes(
    project_uuid: UUID,
    items: tuple[_ResolvedItem, ...],
    staging_path: Path,
) -> tuple[tuple[AnkiNote, ...], tuple[Path, ...]]:
    staged_media: dict[str, Path] = {}
    notes: list[AnkiNote] = []
    for item in items:
        basename = media_basename(item.audio_asset)
        media_path = staged_media.get(basename)
        if media_path is None:
            media_path = staging_path / basename
            shutil.copyfile(item.audio_path, media_path)
            staged_media[basename] = media_path
        notes.append(
            AnkiNote(
                guid=listening_note_guid(project_uuid, item.segment_id),
                audio=f"[sound:{basename}]",
                chinese_sc=html.escape(item.chinese_sc),
                pinyin="",
                english="",
                source=html.escape(item.source),
                lexbundler_id=html.escape(
                    lexbundler_listening_id(project_uuid, item.segment_id)
                ),
                tags=item.tags,
            )
        )
    return tuple(notes), tuple(staged_media.values())


def _normalize_tags(tags: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(tags, tuple):
        raise AnkiExportError("Anki export tags must be supplied as a tuple.")
    normalized = ["lexbundler"]
    for tag in tags:
        if not isinstance(tag, str):
            raise AnkiExportError("Anki tags must be strings.")
        candidate = tag.strip()
        if not candidate or any(character.isspace() for character in candidate):
            raise AnkiExportError(
                "Anki tags must be non-empty and contain no whitespace."
            )
        if any(ord(character) < 32 or ord(character) == 127 for character in candidate):
            raise AnkiExportError("Anki tags must not contain control characters.")
        if candidate not in normalized:
            normalized.append(candidate)
    return tuple(normalized)


def _required_deck_name(deck_name: object) -> str:
    if not isinstance(deck_name, str) or not deck_name.strip():
        raise AnkiExportError("Anki deck name must not be empty.")
    return deck_name.strip()


def _prepare_output_path(path: Path) -> Path:
    output = Path(path).resolve()
    if output.suffix.lower() != ".apkg":
        raise AnkiExportError("The durable Anki package must use an .apkg extension.")
    if output.exists():
        raise AnkiExportError(f"The durable Anki package already exists: {output}")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise AnkiExportError(
            f"Could not create the durable package directory: {output.parent}"
        ) from error
    if not output.parent.is_dir():
        raise AnkiExportError(
            f"The durable package parent is not a directory: {output.parent}"
        )
    return output


def _verify_package(path: Path) -> None:
    if not path.is_file() or path.stat().st_size == 0:
        raise AnkiExportError("genanki did not produce a non-empty package.")


def _publish_package(staged: Path, durable: Path) -> None:
    created = False
    try:
        with staged.open("rb") as source:
            with durable.open("xb") as destination:
                created = True
                shutil.copyfileobj(source, destination)
    except FileExistsError as error:
        raise AnkiExportError(
            f"The durable Anki package already exists: {durable}"
        ) from error
    except OSError as error:
        if created:
            durable.unlink(missing_ok=True)
        raise AnkiExportError(
            f"Could not publish the durable Anki package: {durable}"
        ) from error
