"""Read projections for the corpus Explorer and resource workspace."""

from dataclasses import dataclass
from pathlib import Path

from lexbundler.application.alignment_review_service import AlignmentReviewService
from lexbundler.application.corpus_service import CorpusService
from lexbundler.application.text_segment_service import TextSegmentService


@dataclass(frozen=True, slots=True)
class ResourceIdentity:
    source_id: int
    source_unit_id: int | None

    @property
    def key(self) -> tuple[str, int]:
        return (
            ("unit", self.source_unit_id)
            if self.source_unit_id is not None
            else ("source", self.source_id)
        )


@dataclass(frozen=True, slots=True)
class ExplorerNode:
    key: tuple[str, int | None]
    label: str
    node_kind: str
    resource: ResourceIdentity | None
    is_resource: bool
    children: tuple["ExplorerNode", ...] = ()


@dataclass(frozen=True, slots=True)
class ExplorerTree:
    project: ExplorerNode
    resource_count: int


@dataclass(frozen=True, slots=True)
class AssetOverview:
    asset_id: int
    label: str
    role: str | None
    asset_kind: str | None
    mime_type: str | None
    byte_size: int
    local_path: Path | None
    local_available: bool


@dataclass(frozen=True, slots=True)
class AlignmentOverview:
    layer_id: int
    name: str
    tier: str | None
    item_count: int
    tool_name: str | None
    status: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class ProcessingOverview:
    run_id: int
    process_type: str
    tool_name: str | None
    status: str
    completed_at: str | None


@dataclass(frozen=True, slots=True)
class ResourceOverview:
    resource: ResourceIdentity
    breadcrumb: tuple[str, ...]
    label: str
    representation_kinds: tuple[str, ...]
    transcript_provenance: str | None
    transcript_source_label: str | None
    utterances: tuple[str, ...]
    alignments: tuple[AlignmentOverview, ...]
    reviewable_count: int
    approved_count: int
    assets: tuple[AssetOverview, ...]
    processing_history: tuple[ProcessingOverview, ...]


class ProjectExplorerService:
    """Compose GUI-ready Explorer and overview data from application services."""

    def __init__(
        self,
        corpus: CorpusService,
        text_segments: TextSegmentService,
        alignment_review: AlignmentReviewService,
    ) -> None:
        self._corpus = corpus
        self._text_segments = text_segments
        self._alignment_review = alignment_review

    def load_tree(self, project_name: str) -> ExplorerTree:
        sources = self._corpus.list_sources()
        nodes = tuple(self._source_node(source) for source in sources)
        project = ExplorerNode(
            ("project", None), project_name, "project", None, False, nodes
        )
        return ExplorerTree(project, _resource_count(project))

    def load_overview(self, resource: ResourceIdentity) -> ResourceOverview:
        source = self._corpus.get_source(resource.source_id)
        units = self._corpus.list_source_units(source.id)
        unit = next(
            (item for item in units if item.id == resource.source_unit_id), None
        )
        if resource.source_unit_id is not None and unit is None:
            raise ValueError("The selected source unit does not belong to the source.")
        breadcrumb = [source.name]
        if unit is not None:
            by_id = {item.id: item for item in units}
            lineage = []
            cursor = unit
            seen = set()
            while cursor is not None and cursor.id not in seen:
                seen.add(cursor.id)
                lineage.append(cursor.label)
                cursor = by_id.get(cursor.parent_id)
            breadcrumb.extend(reversed(lineage))

        representations = self._scope_representations(resource)
        layers = self._scope_layers(resource)
        bindings = self._scope_bindings(resource)
        runs = {run.id: run for run in self._corpus.list_processing_runs()}
        review = self._alignment_review.load(
            resource.source_id, resource.source_unit_id
        )
        transcript_utterances, transcript_representation, transcript_provenance = (
            self._transcript_presentation(representations, layers, runs)
        )
        representation_kinds = tuple(dict.fromkeys(
            item.representation_kind for item in representations
        ))

        alignments = []
        for layer in layers:
            if layer.layer_kind != "forced_alignment":
                continue
            run = runs.get(layer.created_by_run_id)
            alignments.append(AlignmentOverview(
                layer.id,
                layer.name,
                layer.metadata.get("tier") if isinstance(
                    layer.metadata.get("tier"), str
                ) else None,
                len(self._text_segments.list_segments(layer.id)),
                run.tool_name if run else None,
                run.status if run else "available",
                _time_text(run.completed_at if run else None),
            ))

        asset_roles: dict[int, set[str]] = {}
        for binding in bindings:
            asset_roles.setdefault(binding.asset_id, set())
            if binding.role:
                asset_roles[binding.asset_id].add(binding.role)
        for representation in representations:
            if representation.source_asset_id is not None:
                asset_roles.setdefault(representation.source_asset_id, set()).add(
                    "text source"
                )
        for layer in layers:
            for segment in self._text_segments.list_segments(layer.id):
                for span in self._text_segments.list_segment_media_spans(segment.id):
                    asset_roles.setdefault(span.asset_id, set()).add("media evidence")
        assets = tuple(
            self._asset_overview(asset_id, roles)
            for asset_id, roles in sorted(asset_roles.items())
        )

        run_ids = {
            item.created_by_run_id for item in representations + layers
            if item.created_by_run_id is not None
        }
        run_ids.update(
            run_id for run_id in (
                source.created_by_run_id,
                unit.created_by_run_id if unit else None,
            ) if run_id is not None
        )
        run_ids.update(
            item.processing_run_id for item in bindings
            if item.processing_run_id is not None
        )
        for asset_overview in assets:
            asset_run_id = self._corpus.get_asset(
                asset_overview.asset_id
            ).created_by_run_id
            if asset_run_id is not None:
                run_ids.add(asset_run_id)
        history = sorted(
            (runs[run_id] for run_id in run_ids if run_id in runs),
            key=lambda run: (run.completed_at or run.started_at, run.id),
            reverse=True,
        )
        processing = tuple(ProcessingOverview(
            run.id, run.process_type, run.tool_name, run.status,
            _time_text(run.completed_at),
        ) for run in history[:8])
        transcript_asset = next((asset for asset in assets
            if transcript_representation is not None
            and asset.asset_id == transcript_representation.source_asset_id), None)
        return ResourceOverview(
            resource,
            tuple(breadcrumb),
            unit.label if unit else source.name,
            representation_kinds,
            transcript_provenance,
            transcript_asset.label if transcript_asset else None,
            transcript_utterances,
            tuple(alignments),
            len(review.utterances),
            sum(item.approval is not None for item in review.utterances),
            assets,
            processing,
        )

    def _transcript_presentation(self, representations, layers, runs):
        candidates = [
            layer for layer in layers
            if layer.layer_kind == "transcript_line"
            and (layer.created_by_run_id is None
                 or (layer.created_by_run_id in runs
                     and runs[layer.created_by_run_id].status == "succeeded"))
        ]
        preferences = (
            ("authoritative_source", "authoritative", "authoritative"),
            ("machine_transcript", "machine_unreviewed", "machine_unreviewed"),
        )
        for representation_kind, span_role, provenance in preferences:
            by_id = {
                item.id: item for item in representations
                if item.representation_kind == representation_kind
            }
            if not by_id:
                continue
            for layer in sorted(candidates, key=lambda item: item.id, reverse=True):
                rows = []
                selected_representation = None
                for segment in self._text_segments.list_segments(layer.id):
                    for span in self._text_segments.list_segment_text_spans(segment.id):
                        representation = by_id.get(span.text_representation_id)
                        if span.role == span_role and representation is not None:
                            rows.append((
                                segment.sequence or 0,
                                representation.content[
                                    span.start_offset:span.end_offset
                                ],
                            ))
                            selected_representation = representation
                            break
                if rows:
                    rows.sort(key=lambda item: item[0])
                    return (
                        tuple(text for _sequence, text in rows),
                        selected_representation,
                        provenance,
                    )
            return (
                (), max(by_id.values(), key=lambda item: item.id), provenance
            )
        return (), None, None

    def _source_node(self, source) -> ExplorerNode:
        units = self._corpus.list_source_units(source.id)
        children_by_parent: dict[int | None, list] = {}
        for unit in units:
            parent = unit.parent_id if any(
                candidate.id == unit.parent_id for candidate in units
            ) else None
            children_by_parent.setdefault(parent, []).append(unit)

        def unit_node(unit, lineage: frozenset[int]) -> ExplorerNode:
            descendants = () if unit.id in lineage else tuple(
                unit_node(child, lineage | {unit.id})
                for child in children_by_parent.get(unit.id, ())
            )
            identity = ResourceIdentity(source.id, unit.id)
            has_evidence = self._has_scope_evidence(identity)
            return ExplorerNode(
                identity.key, unit.label, "unit", identity,
                has_evidence or not descendants, descendants,
            )

        children = tuple(
            unit_node(unit, frozenset()) for unit in children_by_parent.get(None, ())
        )
        identity = ResourceIdentity(source.id, None)
        return ExplorerNode(
            identity.key, source.name, "source", identity,
            self._has_scope_evidence(identity) or not children, children,
        )

    def _has_scope_evidence(self, resource: ResourceIdentity) -> bool:
        return bool(
            self._scope_representations(resource)
            or self._scope_layers(resource)
            or self._scope_bindings(resource)
        )

    def _scope_representations(self, resource: ResourceIdentity):
        return [
            item for item in self._text_segments.list_text_representations(
                resource.source_id
            )
            if item.source_unit_id == resource.source_unit_id
        ]

    def _scope_layers(self, resource: ResourceIdentity):
        return [
            item for item in self._text_segments.list_segment_layers(
                resource.source_id
            )
            if item.source_unit_id == resource.source_unit_id
        ]

    def _scope_bindings(self, resource: ResourceIdentity):
        return [
            item for item in self._corpus.list_asset_bindings(resource.source_id)
            if item.source_unit_id == resource.source_unit_id
        ]

    def _asset_overview(self, asset_id: int, roles: set[str]) -> AssetOverview:
        asset = self._corpus.get_asset(asset_id)
        filesystem = next((
            Path(location.location)
            for location in self._corpus.list_asset_locations(asset_id)
            if location.location_kind == "filesystem"
        ), None)
        return AssetOverview(
            asset.id,
            filesystem.name if filesystem else f"Asset {asset.id}",
            ", ".join(sorted(roles)) or None,
            asset.asset_kind,
            asset.mime_type,
            asset.byte_size,
            filesystem,
            bool(filesystem and filesystem.is_file()),
        )


def _resource_count(node: ExplorerNode) -> int:
    return int(node.is_resource) + sum(_resource_count(child) for child in node.children)


def _time_text(value) -> str | None:
    return value.astimezone().strftime("%Y-%m-%d %H:%M") if value else None
