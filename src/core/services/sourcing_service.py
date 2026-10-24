"""Resume sourcing runs: collect resumes from the chosen sources, then hand them to the
normal ingestion pipeline under the same batch id, so one timeline shows everything:

    Created -> Collecting (per-source scraping events) -> Reading -> ...

Each source is logged as scraping_completed / scraping_partially_completed /
scraping_failed with what it found.
"""

import asyncio
import logging
import re
import uuid

from src.api.rest.dependencies import CurrentUser
from src.api.rest.schemas.sourcing_schema import SourceInfo, SourcingRunRequest
from src.config.settings import settings
from src.constants.categories import SUGGESTED_CATEGORIES
from src.constants.enums import Action, ActionCategory, PipelineStage
from src.core.services import resume_service
from src.core.services.audit_service import log_event
from src.core.services.friendly_errors import friendly_error
from src.data.clients.postgres import AsyncSessionLocal
from src.data.repositories import sourcing_repository
from src.handlers.http_clients.sourcing.keywords import Resolved, resolve
from src.handlers.http_clients.sourcing.sources import (
    github,
    google,
    huggingface,
    kaggle,
    stackexchange,
    url,
)
from src.handlers.http_clients.sourcing.sources.base import SourceResult

logger = logging.getLogger("talentiq.sourcing")
_background: set[asyncio.Task] = set()


def catalog() -> list[SourceInfo]:
    """The sources a recruiter can pick from in the UI (plus any links they paste)."""
    return [
        SourceInfo(
            type="huggingface", name="Hugging Face resume datasets",
            description="Real resumes from free public collections (names removed), picked by field.",
            parameters={"dataset": list(huggingface.DATASETS), "category": SUGGESTED_CATEGORIES,
                        "max_results": "1-200"},
            configured=True, limits="Free, no sign-up. The first time you pick a field it takes about 30 seconds to prepare.",
            terms="Public collections on huggingface.co, fine for testing and demos.",
            example={"type": "huggingface", "dataset": "opensporks/resumes", "category": "IT", "max_results": 20},
        ),
        SourceInfo(
            type="kaggle", name="Kaggle Resume Dataset",
            description="About 2,400 real PDF resumes across 24 fields.",
            parameters={"category": SUGGESTED_CATEGORIES, "max_results": "1-200"},
            configured=kaggle.configured(),
            needs=None if kaggle.configured() else "Ask your admin to add a free Kaggle key (KAGGLE_USERNAME and KAGGLE_KEY).",
            limits="Downloaded once, then unlimited.",
            terms="Meant for learning and research projects.",
            example={"type": "kaggle", "category": "Medical", "max_results": 20},
        ),
        SourceInfo(
            type="github", name="GitHub profiles",
            description="Developers on GitHub. If their profile links a resume we use it; otherwise we make a short "
                        "profile from their public projects.",
            parameters={"query": 'e.g. "language:python location:chennai"', "max_results": "1-200"},
            configured=True, needs=None if settings.github_token else "A free GitHub token (GITHUB_TOKEN) lets you fetch more profiles per hour.",
            limits="About 25 profiles an hour without a token.",
            terms="Only public profile details are used. Don't sell this data or send people unwanted messages.",
            example={"type": "github", "query": "language:python location:chennai", "max_results": 10},
        ),
        SourceInfo(
            type="stackexchange", name="Stack Overflow top answerers",
            description="People who answer the most questions on a topic (for example python), with their public profile.",
            parameters={"tag": 'e.g. "python"', "site": "stackoverflow", "max_results": "1-200"},
            configured=True, needs=None if settings.stackexchange_key else "A free key (STACKEXCHANGE_KEY) allows many more searches.",
            limits="About 100 searches a day without a key.",
            terms="Profiles are shared under CC BY-SA, so we keep a link back to each one.",
            example={"type": "stackexchange", "tag": "python", "max_results": 10},
        ),
        SourceInfo(
            type="google", name="Google search (public resumes)",
            description="Finds public resume files (PDF or Word) and portfolio pages with Google's official search, "
                        "then reads them like uploads. LinkedIn, Naukri and Indeed are left out.",
            parameters={"query": 'e.g. "java developer chennai"', "max_results": "1-30"},
            configured=google.configured(),
            needs=None if google.configured() else "A free Google key (GOOGLE_CSE_API_KEY) and search engine id (GOOGLE_CSE_CX).",
            limits="About 100 searches a day for free; one run uses up to 3.",
            terms="Only public pages and files; each site is visited slowly and its robots.txt rules are respected.",
            example={"type": "google", "query": "java developer chennai", "max_results": 10},
        ),
        SourceInfo(
            type="url", name="Your own links",
            description="Paste links to resume files (PDF or Word) or to personal websites; we look for a resume "
                        "on the page, otherwise we use the page text.",
            parameters={"urls": "up to 200 links", "follow_links": True},
            configured=True, limits="Up to 200 links, files up to 5 MB. We visit each site slowly and respect its rules.",
            terms="Only use pages you're allowed to. Most job sites don't allow automatic collection.",
            example={"type": "url", "urls": ["https://example.com/my-resume.pdf"]},
        ),
    ]


_LIVECAREER = re.compile(r"^(?:kaggle_[a-z0-9-]+|hf_resumes)_(\d{6,})\.pdf$")


async def _skip_known_livecareer(files: list) -> list:
    """Kaggle and the Hugging Face "opensporks/resumes" set hold the same resumes (same IDs).
    They have no emails, so drop any ID we already have, from either source or this run."""
    names = await sourcing_repository.find_filenames_by_sites(("kaggle", "huggingface"))
    seen = {m.group(1) for n in names if n and (m := _LIVECAREER.match(n))}
    kept = []
    for f in files:
        m = _LIVECAREER.match(f.filename)
        if m and m.group(1) in seen:
            continue
        if m:
            seen.add(m.group(1))
        kept.append(f)
    return kept


_PROFILE_NAMES = {
    # github_<login>.pdf (made from the profile) or github_<login>_resume.pdf (their own file)
    "github": re.compile(r"^github_(.+?)(?:_resume)?\.pdf$"),
    # stackexchange_<site>_<user id>.pdf
    "stackexchange": re.compile(r"^stackexchange_[a-z.]+_(\d+)\.pdf$"),
}


async def _known_ids(source: str) -> set[str]:
    """GitHub logins / Stack Exchange user ids we already have, so a new run skips them.
    (Their profile PDFs are rebuilt each time, so the file itself never looks like a duplicate.)"""
    pattern = _PROFILE_NAMES[source]
    names = await sourcing_repository.find_filenames_by_sites((source,))
    return {m.group(1) for n in names if n and (m := pattern.match(n))}


async def _collect(spec, keyword: Resolved | None = None) -> SourceResult:
    """Run one source. With a keyword, it replaces the source's own search fields."""
    note = keyword.notes.get(spec.type) if keyword else None
    if spec.type == "github":
        query = keyword.github_query if keyword else spec.query
        if not query:
            return SourceResult(errors=[note or "Enter a GitHub search or a keyword."])
        result = await github.collect(query, spec.max_results, skip=await _known_ids("github"))
    elif spec.type == "stackexchange":
        tag = keyword.stack_tag if keyword else spec.tag
        if not tag:
            return SourceResult(errors=[note or "Enter a tag or a keyword."])
        known = {int(i) for i in await _known_ids("stackexchange") if i.isdigit()}
        result = await stackexchange.collect(tag, spec.site, spec.max_results, skip=known)
    elif spec.type == "huggingface":
        result = await huggingface.collect(spec.dataset, keyword.category if keyword else spec.category,
                                           spec.max_results)
    elif spec.type == "kaggle":
        result = await kaggle.collect(keyword.category if keyword else spec.category, spec.max_results)
    elif spec.type == "google":
        text = keyword.text if keyword else spec.query
        if not text:
            return SourceResult(errors=[note or "Enter a search or a keyword."])
        result = await google.collect(text, spec.max_results)
    else:
        return await url.collect([str(u) for u in spec.urls], spec.follow_links)
    result.note = " ".join(filter(None, [note, result.note])) or None
    return result


async def start(user: CurrentUser, request: SourcingRunRequest) -> str:
    batch_id = str(uuid.uuid4())
    await log_event(
        user_id=user.id, category=ActionCategory.SCRAPING, stage=PipelineStage.CREATED,
        action=Action.SCRAPING_STARTED,
        input_data={"batch_id": batch_id, "keyword": request.keyword,
                    "sources": [s.model_dump(mode="json") for s in request.sources]},
    )
    task = asyncio.create_task(_run(batch_id, user, request))
    _background.add(task)
    task.add_done_callback(_background.discard)
    return batch_id


async def _run(batch_id: str, user: CurrentUser, request: SourcingRunRequest) -> None:
    def log(**kwargs):
        return log_event(user_id=user.id, category=ActionCategory.SCRAPING,
                         input_data={"batch_id": batch_id, **kwargs.pop("input", {})}, **kwargs)

    try:
        await log(stage=PipelineStage.COLLECTING, output_data={"sources": len(request.sources)})
        files = []
        keyword = resolve(request.keyword) if request.keyword else None
        for spec in request.sources:
            try:
                found = await _collect(spec, keyword)
            except Exception as exc:
                logger.exception("source %s crashed", spec.type)
                found = SourceResult(errors=[friendly_error(exc, f"collecting from {spec.type}")])
            files += found.files
            action = (Action.SCRAPING_FAILED if not found.files
                      else Action.SCRAPING_PARTIALLY_COMPLETED if found.partial or found.errors
                      else Action.SCRAPING_COMPLETED)
            await log(action=action, input={"source": spec.type},
                      output_data={"source": spec.type, "files": len(found.files), "errors": found.errors[:5],
                                   **({"note": found.note} if found.note else {})})

        if not files:
            await log(stage=PipelineStage.FAILED, action=Action.SCRAPING_FAILED,
                      output_data={"finished": True, "total": 0, "saved": 0, "failed": 0,
                                   "note": "No resumes were found by the selected sources."})
            return

        files = await _skip_known_livecareer(files)
        uploads = [resume_service.UploadedFile(f.filename, f.data, f.source_site) for f in files]
        async with AsyncSessionLocal() as db:
            result = await resume_service.accept_uploads(db, user, uploads, source_type="sourced",
                                                         batch_id=batch_id)
        if result.rejected:
            await log(input={"step": "dedupe"}, output_data={
                "skipped": len(result.rejected),
                "examples": [f"{r['filename']}: {r['reason']}" for r in result.rejected[:5]]})
        if result.batch_id is None:  # everything was already in the system
            await log(stage=PipelineStage.READING, action=Action.EXTRACTION_COMPLETED,
                      output_data={"finished": True, "total": 0, "saved": 0, "failed": 0,
                                   "note": "All sourced resumes were already in the system."})
    except Exception as exc:
        logger.exception("sourcing run %s crashed", batch_id)
        await log(stage=PipelineStage.FAILED, action=Action.SCRAPING_FAILED,
                  output_data={"finished": True, "error": friendly_error(exc, "collecting resumes")})
