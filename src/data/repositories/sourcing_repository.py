"""Database access for sourcing: which public profiles and resumes we already have."""

from sqlalchemy import select

from src.data.clients.postgres import AsyncSessionLocal
from src.data.models.postgres.document import Document


async def find_filenames_by_sites(sites: tuple[str, ...]) -> list[str | None]:
    """File names of the resumes already stored from these sites (it opens its own session)."""
    async with AsyncSessionLocal() as db:
        return list((await db.execute(
            select(Document.original_filename).where(Document.source_site.in_(sites))
        )).scalars().all())
