"""Database operations. Keep external API JSON separate from cleaned records."""

import hashlib
import json
import re

from sqlalchemy import case, extract, func, or_, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, selectinload

from .models import (
    Author,
    IngestionRun,
    Publication,
    PublicationAuthor,
    RawPublicationRecord,
    utc_now,
)
from .schemas import SearchIntent, WorkOut, iso_date


def fingerprint(intent: SearchIntent) -> str:
    payload = intent.model_dump(mode="json")
    payload["query"] = payload["query"].lower()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_doi(value: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    return normalized if re.fullmatch(r"10\.\d{4,9}/\S+", normalized) else None


def abstract_from_index(value: dict | None) -> str | None:
    """OpenAlex stores abstract word positions, not a plain abstract string."""
    if not isinstance(value, dict):
        return None
    positioned: dict[int, str] = {}
    for word, positions in value.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            continue
        for position in positions:
            if type(position) is int and 0 <= position < 2000:
                positioned[position] = word
    if not positioned:
        return None
    return " ".join(positioned[i] for i in sorted(positioned))[:12000]


def _valid_openalex_work(record: object) -> bool:
    """Validate fields we store before making any cleaned-table changes."""
    if not isinstance(record, dict):
        return False
    work_id = record.get("id")
    if not isinstance(work_id, str) or len(work_id) > 100 or not re.fullmatch(r"https://openalex\.org/W\d+", work_id):
        return False
    for field in ("title", "doi", "type"):
        if record.get(field) is not None and not isinstance(record[field], str):
            return False
    if len(record.get("doi") or "") > 500 or len(record.get("type") or "") > 80:
        return False
    year, count = record.get("publication_year"), record.get("cited_by_count")
    if record.get("publication_date") is not None:
        try:
            if not isinstance(record["publication_date"], str):
                return False
            iso_date(record["publication_date"])
        except ValueError:
            return False
    if year is not None and (type(year) is not int or not 1 <= year <= 9999):
        return False
    if year is not None and record.get("publication_date") and iso_date(record["publication_date"]).year != year:
        return False
    if count is not None and (type(count) is not int or not 0 <= count <= 2_147_483_647):
        return False
    if record.get("abstract_inverted_index") is not None and not isinstance(record["abstract_inverted_index"], dict):
        return False
    primary = record.get("primary_location")
    if primary is not None and not isinstance(primary, dict):
        return False
    primary = primary or {}
    source = primary.get("source")
    if source is not None and not isinstance(source, dict):
        return False
    if any(
        value is not None and not isinstance(value, str)
        for value in (
            primary.get("landing_page_url"),
            (source or {}).get("display_name"),
        )
    ):
        return False
    authorships = record.get("authorships")
    if authorships is not None and not isinstance(authorships, list):
        return False
    for authorship in authorships or []:
        if not isinstance(authorship, dict):
            return False
        author = authorship.get("author")
        if author is not None and not isinstance(author, dict):
            return False
        author = author or {}
        name, author_id = author.get("display_name"), author.get("id")
        if name is not None and not isinstance(name, str):
            return False
        if author_id is not None and (
            not isinstance(author_id, str)
            or len(author_id) > 100
            or not re.fullmatch(r"https://openalex\.org/A\d+", author_id)
        ):
            return False
    return True


def upsert_values(session: Session, model, values: dict, identity: str) -> None:
    """Let the database arbitrate identity conflicts, including concurrent writers."""
    dialect = session.get_bind().dialect.name
    insert = sqlite_insert if dialect == "sqlite" else postgres_insert
    statement = insert(model).values(**values)
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[identity], set_={key: value for key, value in values.items() if key != identity}
        )
    )


def save_openalex_work(session: Session, record: object, *, ingestion_run_id: int | None = None) -> str | None:
    """Retain raw JSON, then upsert valid metadata by stable OpenAlex work ID."""
    raw_id = record.get("id") if isinstance(record, dict) else None
    # Empty means no usable raw identifier; the original value stays in payload.
    session.add(
        RawPublicationRecord(
            provider="openalex",
            provider_work_id=raw_id if isinstance(raw_id, str) and len(raw_id) <= 120 else "",
            payload=record,
            ingestion_run_id=ingestion_run_id,
        )
    )
    if not _valid_openalex_work(record):
        return None
    work_id = record["id"]

    primary = record.get("primary_location") or {}
    source = primary.get("source") or {}
    values = {
        "openalex_id": work_id,
        "title": record.get("title"),
        "doi": normalize_doi(record.get("doi")),
        "publication_year": record.get("publication_year"),
        "publication_date": iso_date(record.get("publication_date")),
        "publication_type": record.get("type"),
        "abstract": abstract_from_index(record.get("abstract_inverted_index")),
        "cited_by_count": record.get("cited_by_count"),
        "source": source.get("display_name"),
        "landing_page_url": primary.get("landing_page_url") or record.get("doi"),
        "last_fetched_at": utc_now(),
    }
    # ON CONFLICT UPDATE holds the publication row lock until commit on PostgreSQL.
    # The raw flush obtains SQLite's write lock before author replacement starts.
    session.flush()
    upsert_values(session, Publication, values, "openalex_id")
    work = session.scalar(
        select(Publication).where(Publication.openalex_id == work_id).execution_options(populate_existing=True)
    )

    # Replace the work's author links on refresh. The author identities remain reusable.
    work.authors.clear()
    session.flush()

    for position, authorship in enumerate(record.get("authorships") or []):
        raw_author = authorship.get("author") or {}
        name = raw_author.get("display_name")
        if not isinstance(name, str) or not name.strip():
            continue
        openalex_author_id = raw_author.get("id")
        if isinstance(openalex_author_id, str):
            upsert_values(session, Author, {"openalex_id": openalex_author_id, "display_name": name}, "openalex_id")
            author = session.scalar(
                select(Author).where(Author.openalex_id == openalex_author_id).execution_options(populate_existing=True)
            )
        else:
            author = Author(openalex_id=None, display_name=name)
            session.add(author)
            session.flush()
        work.authors.append(PublicationAuthor(author=author, position=position))

    return work_id


def publications_by_ids(session: Session, ids: list[str]) -> list[Publication]:
    if not ids:
        return []
    rows = session.scalars(
        select(Publication)
        .options(selectinload(Publication.authors).selectinload(PublicationAuthor.author))
        .where(Publication.openalex_id.in_(ids))
    ).all()
    lookup = {item.openalex_id: item for item in rows}
    return [lookup[work_id] for work_id in ids if work_id in lookup]


def matches_dates(work: Publication, intent: SearchIntent) -> bool:
    value = work.publication_date
    year = value.year if value else work.publication_year
    if intent.year_from and (year is None or year < intent.year_from):
        return False
    if intent.year_to and (year is None or year > intent.year_to):
        return False
    if not intent.date_from and not intent.date_to:
        return True
    return bool(
        value
        and (not intent.date_from or value >= intent.date_from)
        and (not intent.date_to or value <= intent.date_to)
    )


def local_search(session: Session, intent: SearchIntent) -> list[Publication]:
    """Simple local keyword fallback. Semantic/vector ranking is a later phase."""
    tokens = re.findall(r"[a-zA-Z0-9-]+", intent.query.lower())[:12]
    if not tokens:
        return []
    clauses = [
        or_(Publication.title.ilike(f"%{token}%"), Publication.abstract.ilike(f"%{token}%"))
        for token in tokens
        if len(token) >= 3
    ]
    if not clauses:
        return []
    statement = (
        select(Publication)
        .options(selectinload(Publication.authors).selectinload(PublicationAuthor.author))
        .where(or_(*clauses))
    )
    year = func.coalesce(extract("year", Publication.publication_date), Publication.publication_year)
    if intent.year_from:
        statement = statement.where(year >= intent.year_from)
    if intent.year_to:
        statement = statement.where(year <= intent.year_to)
    if intent.date_from:
        statement = statement.where(Publication.publication_date >= intent.date_from)
    if intent.date_to:
        statement = statement.where(Publication.publication_date <= intent.date_to)
    matches = sum(case((Publication.title.ilike(f"%{token}%"), 1), else_=0) for token in tokens)
    citations = Publication.cited_by_count.desc().nullslast()
    order = [matches.desc(), citations]
    if intent.sort == "newest":
        order = [Publication.publication_date.desc().nullslast(), year.desc().nullslast(), matches.desc()]
    elif intent.sort == "most_cited":
        order = [citations, matches.desc()]
    # Rank the complete matching subset before LIMIT; IDs make ties repeatable.
    return list(session.scalars(statement.order_by(*order, Publication.openalex_id).limit(intent.max_results)).all())


def as_work_out(work: Publication) -> WorkOut:
    return WorkOut(
        id=work.openalex_id,
        title=work.title,
        doi=work.doi,
        publication_year=work.publication_year,
        publication_date=work.publication_date,
        has_abstract=bool(work.abstract and work.abstract.strip()),
        authors=[link.author.display_name for link in work.authors],
        cited_by_count=work.cited_by_count,
        source=work.source,
        landing_page_url=work.landing_page_url,
    )


def stats(session: Session) -> dict:
    return {
        "publications": session.scalar(select(func.count(Publication.id))) or 0,
        "authors": session.scalar(select(func.count(Author.id))) or 0,
        "raw_records": session.scalar(select(func.count(RawPublicationRecord.id))) or 0,
        "ingestion_runs": session.scalar(select(func.count(IngestionRun.id))) or 0,
    }
