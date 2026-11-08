import re
import unicodedata
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.locations import normalize_name
from app.models import Claim, Document, Location, LocationAlias
from app.models.document import DocumentStatus
from app.models.location import LocationType

RESOLUTION_VERSION = "location-v3"


class LocationNode(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    type: LocationType
    country_code: str | None
    is_provisional: bool


class LocationResolution(BaseModel):
    version: str = RESOLUTION_VERSION
    text: str | None
    normalized_name: str | None = None
    status: Literal["resolved", "provisional", "ambiguous", "unresolved"]
    location_id: UUID | None = None
    candidates: list[UUID] = Field(default_factory=list)
    total: int = 0
    hierarchy: list[LocationNode] = Field(default_factory=list)
    reason: str | None = None
    created: bool = False
    method: Literal["exact", "qualified"] = "exact"
    component_text: str | None = None
    qualifier_text: str | None = None


def _display_name(value: str) -> str:
    name = " ".join(unicodedata.normalize("NFKC", value).split())
    if not name or len(name) > 200 or len(normalize_name(name)) > 200:
        raise ValueError("name must contain 1–200 normalized characters")
    if any(unicodedata.category(char).startswith("C") for char in name):
        raise ValueError("name contains control characters")
    return name


def normalize_entities(values: list[str]) -> list[dict[str, str]]:
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError("entities must be a bounded list of names")
    result, seen = [], set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("entity names must be strings")
        name = _display_name(value)
        normalized = normalize_name(name)
        if normalized not in seen:
            result.append({"name": name, "normalized_name": normalized})
            seen.add(normalized)
    return result


async def get_location_path(session: AsyncSession, location_id: UUID) -> list[Location]:
    path, seen = [], set()
    current = location_id
    while current is not None:
        if current in seen or len(path) >= 32:
            raise ValueError("cyclic_or_excessive_location_hierarchy")
        seen.add(current)
        row = await session.get(Location, current)
        if row is None:
            raise ValueError("location_not_found")
        path.append(row)
        current = row.parent_id
    countries = {row.country_code for row in path if row.country_code is not None}
    if len(countries) > 1:
        raise ValueError("conflicting_country_hierarchy")
    ordered = list(reversed(path))
    for parent, child in zip(ordered, ordered[1:], strict=False):
        if (
            parent.type == LocationType.POINT_OF_INTEREST
            and child.type != LocationType.POINT_OF_INTEREST
        ):
            raise ValueError("incompatible_location_hierarchy")
    return ordered


def _descendants(root_id: UUID):
    scope = select(Location.id).where(Location.id == root_id).cte(recursive=True)
    # UNION, rather than UNION ALL, also bounds traversal of malformed legacy cycles.
    return scope.union(select(Location.id).join(scope, Location.parent_id == scope.c.id))


async def _resolved(
    session: AsyncSession, row: Location, text: str, normalized: str, *, created: bool = False
) -> LocationResolution:
    path = await get_location_path(session, row.id)
    return LocationResolution(
        text=text,
        normalized_name=normalized,
        status="provisional" if row.is_provisional else "resolved",
        location_id=row.id,
        candidates=[row.id],
        total=1,
        hierarchy=[LocationNode.model_validate(item) for item in path],
        created=created,
    )


async def _resolve_name(
    session: AsyncSession,
    text: str | None,
    *,
    parent_id: UUID | None = None,
    country_code: str | None = None,
    location_type: LocationType | None = None,
    create_provisional: bool = True,
) -> LocationResolution:
    if country_code is not None:
        country_code = country_code.upper()
        if not re.fullmatch("[A-Z]{2}", country_code):
            raise ValueError("invalid_country_code")
    if location_type is not None:
        location_type = LocationType(location_type)
    parent_path = await get_location_path(session, parent_id) if parent_id else []
    inherited_code = next((row.country_code for row in parent_path if row.country_code), None)
    if country_code and inherited_code and country_code != inherited_code:
        raise ValueError("country_context_conflict")
    if country_code and parent_path and inherited_code is None:
        raise ValueError("country_context_unverified")
    if text is None:
        return LocationResolution(text=None, status="unresolved", reason="missing_location")
    name = _display_name(text)
    normalized = normalize_name(name)
    alias = (
        select(LocationAlias.id)
        .where(
            LocationAlias.location_id == Location.id, LocationAlias.normalized_alias == normalized
        )
        .exists()
    )
    name_filter = or_(Location.canonical_name == normalized, alias)
    global_total = await session.scalar(
        select(func.count()).select_from(Location).where(name_filter)
    )
    filters = [name_filter]
    if location_type is not None:
        filters.append(Location.type == location_type)
    country = None
    if country_code is not None:
        countries = list(
            await session.scalars(
                select(Location)
                .where(Location.type == LocationType.COUNTRY, Location.country_code == country_code)
                .limit(2)
            )
        )
        if len(countries) != 1:
            return LocationResolution(
                text=text,
                normalized_name=normalized,
                status="unresolved",
                reason="unknown_or_ambiguous_country",
            )
        country = countries[0]
        await get_location_path(session, country.id)
        scope = _descendants(country.id)
        filters.append(Location.id.in_(select(scope.c.id)))
    if parent_id is not None:
        scope = _descendants(parent_id)
        filters.append(
            or_(
                Location.id.in_(select(scope.c.id)),
                Location.id.in_([row.id for row in parent_path]),
            )
        )
    total = await session.scalar(select(func.count()).select_from(Location).where(*filters))
    matches = list(
        await session.scalars(select(Location).where(*filters).order_by(Location.id).limit(20))
    )
    if total == 1:
        return await _resolved(session, matches[0], text, normalized)
    if total:
        return LocationResolution(
            text=text,
            normalized_name=normalized,
            status="ambiguous",
            candidates=[row.id for row in matches],
            total=total,
            reason="multiple_matches",
        )
    if global_total:
        return LocationResolution(
            text=text,
            normalized_name=normalized,
            status="unresolved",
            reason="conflicting_context_or_type",
        )
    creation_parent = parent_path[-1] if parent_path else country
    if not create_provisional or location_type is None or creation_parent is None:
        return LocationResolution(
            text=text,
            normalized_name=normalized,
            status="unresolved",
            reason="insufficient_context_or_creation_disabled",
        )
    if location_type == LocationType.COUNTRY:
        return LocationResolution(
            text=text,
            normalized_name=normalized,
            status="unresolved",
            reason="country_requires_root_review",
        )
    if (
        creation_parent.type == LocationType.POINT_OF_INTEREST
        and location_type != LocationType.POINT_OF_INTEREST
    ):
        return LocationResolution(
            text=text,
            normalized_name=normalized,
            status="unresolved",
            reason="incompatible_parent_type",
        )
    creation_path = await get_location_path(session, creation_parent.id)
    code = next((row.country_code for row in creation_path if row.country_code), None)
    identity = await session.scalar(
        insert(Location)
        .values(
            name=name,
            canonical_name=normalized,
            type=location_type,
            parent_id=creation_parent.id,
            country_code=code,
            is_provisional=True,
        )
        .on_conflict_do_nothing(constraint="uq_location_identity")
        .returning(Location.id)
    )
    created = identity is not None
    if identity is None:
        identity = await session.scalar(
            select(Location.id).where(
                Location.parent_id == creation_parent.id,
                Location.canonical_name == normalized,
                Location.type == location_type,
            )
        )
    row = await session.get(Location, identity)
    return await _resolved(session, row, text, normalized, created=created)


def qualified_parts(value: str) -> tuple[str, str] | None:
    """A deliberately narrow English grammar, not fuzzy matching or a geocoder."""
    name = _display_name(value)
    name = re.sub(r"^(?:at|near|outside|around|in)\s+", "", name, flags=re.I)
    parts = re.split(r"\s+in\s+|,\s*", name, flags=re.I)
    if len(parts) != 2 or not all(part.strip() for part in parts):
        return None
    return tuple(part.strip() for part in parts)


def _landmark_name(value: str) -> bool:
    # Unknown generic references and administrative names must not become POIs.
    if re.search(
        r"\b(?:city|country|state|province|district|neighborhood|and|or)\b|[;/]", value, re.I
    ):
        return False
    words = re.findall(r"[^\W\d_]+", value, flags=re.UNICODE)
    generic = {"the", "a", "an", "old", "new", "local", "nearby", "main", "central"}
    kinds = {
        "palace",
        "temple",
        "museum",
        "airport",
        "station",
        "market",
        "bridge",
        "cathedral",
        "monument",
        "park",
        "garden",
        "gardens",
        "castle",
        "fort",
    }
    lower = {word.casefold() for word in words}
    named = any(word[0].isupper() and word.casefold() not in generic | kinds for word in words)
    return bool(lower & kinds) and named and len(words) <= 8


async def resolve_location(
    session: AsyncSession,
    text: str | None,
    *,
    parent_id: UUID | None = None,
    country_code: str | None = None,
    location_type: LocationType | None = None,
    create_provisional: bool = True,
    allow_qualified: bool = True,
) -> LocationResolution:
    context = dict(parent_id=parent_id, country_code=country_code, location_type=location_type)
    exact = await _resolve_name(session, text, **context, create_provisional=False)
    if exact.status != "unresolved" or exact.reason != "insufficient_context_or_creation_disabled":
        return exact
    parts = qualified_parts(text) if allow_qualified and text is not None else None
    if allow_qualified and text and parts is None and re.search(r"\s+in\s+|,", text, re.I):
        return exact.model_copy(update={"reason": "unsupported_location_qualification"})
    if parts is not None:
        component, qualifier = parts
        anchor = await _resolve_name(
            session,
            qualifier,
            parent_id=parent_id,
            country_code=country_code,
            create_provisional=False,
        )
        if anchor.location_id is None:
            return exact.model_copy(
                update={
                    "method": "qualified",
                    "component_text": component,
                    "qualifier_text": qualifier,
                    "reason": "qualified_parent_" + anchor.status,
                }
            )
        anchor_type = anchor.hierarchy[-1].type
        if anchor_type not in {LocationType.CITY, LocationType.COUNTRY}:
            return exact.model_copy(update={"reason": "unsupported_qualified_parent"})
        leaf = await _resolve_name(
            session,
            component,
            parent_id=anchor.location_id,
            location_type=location_type,
            create_provisional=False,
        )
        # First honor literal names/aliases, including place names beginning with 'The'.
        if leaf.status == "unresolved" and component.casefold().startswith("the "):
            component = component[4:]
            leaf = await _resolve_name(
                session,
                component,
                parent_id=anchor.location_id,
                location_type=location_type,
                create_provisional=False,
            )
        may_create_in_scope = parent_id is None or parent_id in {
            node.id for node in anchor.hierarchy
        }
        inferred_type = location_type
        if inferred_type is None and anchor_type == LocationType.CITY and _landmark_name(component):
            inferred_type = LocationType.POINT_OF_INTEREST
        if (
            leaf.reason == "insufficient_context_or_creation_disabled"
            and create_provisional
            and may_create_in_scope
            and inferred_type is not None
            and inferred_type != LocationType.COUNTRY
        ):
            leaf = await _resolve_name(
                session,
                component,
                parent_id=anchor.location_id,
                location_type=inferred_type,
            )
        if leaf.location_id is not None:
            allowed = anchor.location_id in {node.id for node in leaf.hierarchy}
            if parent_id is not None:
                parent_path = await get_location_path(session, parent_id)
                allowed &= parent_id in {
                    node.id for node in leaf.hierarchy
                } or leaf.location_id in {node.id for node in parent_path}
            if not allowed:
                leaf = LocationResolution(
                    text=component, status="unresolved", reason="conflicting_qualified_hierarchy"
                )
        return leaf.model_copy(
            update={
                "text": text,
                "normalized_name": normalize_name(text),
                "method": "qualified",
                "component_text": component,
                "qualifier_text": qualifier,
            }
        )
    if create_provisional:
        return await _resolve_name(session, text, **context, create_provisional=True)
    return exact


async def resolve_claim(
    session: AsyncSession, claim_id: UUID, **context
) -> LocationResolution | None:
    claim = await session.scalar(select(Claim).where(Claim.id == claim_id).with_for_update())
    if claim is None:
        return None
    metadata = claim.structured_data
    entities = normalize_entities(metadata.get("entities", []))
    text = metadata.get("unresolved_location", metadata.get("location"))
    if text is not None and not isinstance(text, str):
        raise ValueError("location text must be a string")
    quote = metadata.get("evidence_quote")
    grounded = isinstance(quote, str) and isinstance(text, str) and text in quote
    # Qualified claim geography must come from a retained source quote, never the
    # search label. Legacy exact-name claims without a quote retain exact resolution.
    if grounded:
        document = await session.get(Document, claim.document_id)
        grounded = bool(
            document
            and document.processing_status == DocumentStatus.PROCESSED
            and document.clean_text
            and quote in document.clean_text
        )
        lineage = metadata.get("extraction", {})
        if lineage.get("clean_text_hash") and document:
            grounded = grounded and lineage["clean_text_hash"] == document.clean_text_hash
        if "quote_start" in lineage or "quote_end" in lineage:
            start, end = lineage.get("quote_start"), lineage.get("quote_end")
            grounded = grounded and (
                isinstance(start, int)
                and isinstance(end, int)
                and 0 <= start < end <= len(document.clean_text)
                and document.clean_text[start:end] == quote
            )
    if quote is not None and not grounded and text is not None:
        resolution = LocationResolution(
            text=text, status="unresolved", reason="ungrounded_location"
        )
    else:
        # Remove only a source-copied locative prefix, never supply the search city.
        component = re.sub(r"^(?:in|at)\s+", "", text, count=1, flags=re.I) if grounded else text
        resolution = await resolve_location(session, component, allow_qualified=grounded, **context)
        if component != text:
            resolution = resolution.model_copy(
                update={"text": text, "component_text": resolution.component_text or component}
            )
    await session.execute(
        update(Claim)
        .where(Claim.id == claim_id)
        .values(
            location_id=resolution.location_id,
            structured_data=Claim.structured_data.op("||")(
                {
                    "location_resolution": resolution.model_dump(mode="json"),
                    "normalized_entities": entities,
                }
            ),
        )
    )
    return resolution
