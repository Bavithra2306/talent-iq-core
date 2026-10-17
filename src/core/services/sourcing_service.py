"""Resume sourcing runs: collect resumes from the chosen sources, then hand them to the
normal ingestion pipeline under the same batch id, so one timeline shows everything:

    Created -> Collecting (per-source scraping events) -> Reading -> ...

Each source is logged as scraping_completed / scraping_partially_completed /
scraping_failed with what it found.
"""

import asyncio
import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth_dependency import CurrentUser
from src.config.settings import settings
from src.constants.categories import SUGGESTED_CATEGORIES
from src.constants.enums import Action, ActionCategory, PipelineStage
from src.core.services import resume_service
from src.core.services.audit_service import log_event
from src.core.sourcing.sources import github, huggingface, kaggle, stackexchange, url
from src.core.sourcing.sources.base import SourceResult
from src.data.clients.postgres import AsyncSessionLocal
from src.schema.sourcing_schema import SourceInfo, SourcingRunRequest

logger = logging.getLogger("talentiq.sourcing")
_background: set[asyncio.Task] = set()


def catalog() -> list[SourceInfo]:
    """The sources a recruiter can pick from in the UI (plus any links they paste)."""
    return [
        SourceInfo(
            type="huggingface", name="Hugging Face resume datasets",
            description="Real resumes from free public collections (names removed), picked by field.",
            parameters={"dataset": list(huggingface.DATASETS), "category": SUGGESTED_CATEGORIES,
                        "max_results": "1-50"},
            configured=True, limits="Free, no sign-up. The first time you pick a field it takes about 30 seconds to prepare.",
            terms="Public collections on huggingface.co, fine for testing and demos.",
            example={"type": "huggingface", "dataset": "opensporks/resumes", "category": "IT", "max_results": 20},
        ),
        SourceInfo(
            type="kaggle", name="Kaggle Resume Dataset",
            description="About 2,400 real PDF resumes across 24 fields.",
            parameters={"category": SUGGESTED_CATEGORIES, "max_results": "1-50"},
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
            parameters={"query": 'e.g. "language:python location:chennai"', "max_results": "1-30"},
            configured=True, needs=None if settings.github_token else "A free GitHub token (GITHUB_TOKEN) lets you fetch more profiles per hour.",
            limits="About 25 profiles an hour without a token.",
            terms="Only public profile details are used. Don't sell this data or send people unwanted messages.",
            example={"type": "github", "query": "language:python location:chennai", "max_results": 10},
        ),
        SourceInfo(
            type="stackexchange", name="Stack Overflow top answerers",
            description="People who answer the most questions on a topic (for example python), with their public profile.",
            parameters={"tag": 'e.g. "python"', "site": "stackoverflow", "max_results": "1-30"},
            configured=True, needs=None if settings.stackexchange_key else "A free key (STACKEXCHANGE_KEY) allows many more searches.",
            limits="About 100 searches a day without a key.",
            terms="Profiles are shared under CC BY-SA, so we keep a link back to each one.",
            example={"type": "stackexchange", "tag": "python", "max_results": 10},
        ),
        SourceInfo(
            type="url", name="Links you paste",
            description="Paste links to resume files (PDF or Word) or to personal websites; we look for a resume "
                        "on the page, otherwise we use the page text.",
            parameters={"urls": "up to 20 links", "follow_links": True},
            configured=True, limits="Up to 20 links, files up to 10 MB. We visit each site slowly and respect its rules.",
            terms="Only use pages you're allowed to. Most job sites don't allow automatic collection.",
            example={"type": "url", "urls": ["https://example.com/my-resume.pdf"]},
        ),
    ]


async def _collect(spec) -> SourceResult:
    if spec.type == "github":
        return await github.collect(spec.query, spec.max_results)
    if spec.type == "stackexchange":
        return await stackexchange.collect(spec.tag, spec.site, spec.max_results)
    if spec.type == "huggingface":
        return await huggingface.collect(spec.dataset, spec.category, spec.max_results)
    if spec.type == "kaggle":
        return await kaggle.collect(spec.category, spec.max_results)
    return await url.collect([str(u) for u in spec.urls], spec.follow_links)


async def start(user: CurrentUser, request: SourcingRunRequest) -> str:
    batch_id = str(uuid.uuid4())
    await log_event(
        user_id=user.id, category=ActionCategory.SCRAPING, stage=PipelineStage.CREATED,
        action=Action.SCRAPING_STARTED,
        input_data={"batch_id": batch_id, "sources": [s.model_dump(mode="json") for s in request.sources]},
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
        for spec in request.sources:
            try:
                found = await _collect(spec)
            except Exception as exc:
                logger.exception("source %s crashed", spec.type)
                found = SourceResult(errors=[f"unexpected error: {str(exc)[:200]}"])
            files += found.files
            action = (Action.SCRAPING_FAILED if not found.files
                      else Action.SCRAPING_PARTIALLY_COMPLETED if found.partial or found.errors
                      else Action.SCRAPING_COMPLETED)
            await log(action=action, input={"source": spec.type},
                      output_data={"source": spec.type, "files": len(found.files), "errors": found.errors[:5]})

        if not files:
            await log(stage=PipelineStage.FAILED, action=Action.SCRAPING_FAILED,
                      output_data={"finished": True, "total": 0, "saved": 0, "failed": 0,
                                   "note": "No resumes were found by the selected sources."})
            return

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
                  output_data={"finished": True, "error": str(exc)[:300]})
