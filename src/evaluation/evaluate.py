"""Measure the results the BRD asks for (section 8). Run inside the core container:

    docker compose exec core uv run --no-sync python -m src.evaluation.evaluate <command> ...

Commands
--------
answer-key-template --job <job id>
    Writes evaluation/answer_key_<job>.csv: every resume in the job's category, in a random
    order and WITHOUT our scores, so the person making the key is not steered by the engine.
    Fill "recruiter_rank" 1..10 for the ten people you would call first, then save.

top10 --job <job id> --key <answer key csv>
    Compares our current top 10 for the job with the recruiter's top 10.
    Target: 80% or more overlap.

fields-template [--limit 30]
    Writes evaluation/fields_check.csv: one row per resume with what we extracted. Open each
    resume and correct any wrong cell (leave correct cells as they are), then save.

fields --key <checked fields csv>
    Share of fields read correctly. Target: 95% or more.

timing
    Recent matching runs: resumes scored and seconds taken. Target: 100+ ranked in under 2 minutes.
"""

import argparse
import asyncio
import csv
import json
import random
import uuid
from pathlib import Path

from src.constants.categories import normalize_category
from src.data.clients.postgres import AsyncSessionLocal
from src.data.models.postgres.candidate import Candidate
from src.data.models.postgres.resume_extraction import ResumeExtraction
from src.data.repositories import evaluation_repository

OUT = Path("evaluation")
TOP = 10
FIELDS = ("name", "email", "phone", "current_title", "total_years", "skills")


def _write(path: Path, header: list[str], rows: list[list]) -> None:
    OUT.mkdir(exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # -sig: opens cleanly in Excel
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {path}")


def _read(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _roles(extraction: ResumeExtraction | None) -> str:
    roles = (extraction.roles_and_years if extraction else None) or []
    return "; ".join(f"{r.get('title')} ({r.get('years') or '?'} yrs)" for r in roles if isinstance(r, dict))[:300]


# ---------- answer key and top-10 overlap ----------

async def answer_key_template(job_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        job = await evaluation_repository.get_job(db, job_id)
        rows = await evaluation_repository.find_category_resumes(db, normalize_category(job.category))
    random.shuffle(rows)
    _write(OUT / f"answer_key_{job_id}.csv",
           ["candidate_id", "recruiter_rank", "current_title", "years", "skills", "roles", "education"],
           [[c.id, "", c.current_title or "", c.total_years_experience or "", ", ".join(e.skills or [])[:400],
             _roles(e), "; ".join(d.get("degree", "") for d in e.education or [] if isinstance(d, dict))]
            for c, e in rows])
    print(f"Job: {job.title}. Requirements: {', '.join(job.skills or [])}; {job.experience_years or 0}+ years; "
          f"{job.education or 'no education requirement'}.")


async def top10(job_id: uuid.UUID, key_path: str) -> None:
    key = sorted((r for r in _read(key_path) if r.get("recruiter_rank", "").strip()),
                 key=lambda r: int(r["recruiter_rank"]))[:TOP]
    expected = {r["candidate_id"] for r in key}
    async with AsyncSessionLocal() as db:
        ours = await evaluation_repository.find_top_ranked(db, job_id, TOP)
    if len(expected) < TOP:
        print(f"Warning: the answer key ranks only {len(expected)} people.")
    hits = [(row, c) for row, c in ours if str(c.id) in expected]
    print(f"Top {TOP} overlap: {len(hits)} of {len(expected)} = {round(100 * len(hits) / max(1, len(expected)))}% "
          f"(target 80%)")
    for row, c in ours:
        mark = "match" if str(c.id) in expected else "not in key"
        print(f"  #{row.rank:<3} {float(row.total_score):5.1f}  {c.current_title or '-'}  [{mark}]")
    missed = expected - {str(c.id) for _, c in ours}
    if missed:
        print(f"In the key but not in our top {TOP}: {', '.join(sorted(missed))}")


# ---------- fields read correctly ----------

def _values(c: Candidate, e: ResumeExtraction) -> dict:
    return {"name": c.name or "", "email": c.email or "", "phone": c.contact or "",
            "current_title": c.current_title or "", "total_years": str(c.total_years_experience or ""),
            "skills": "; ".join(e.skills or [])}


async def fields_template(limit: int) -> None:
    async with AsyncSessionLocal() as db:
        rows = await evaluation_repository.find_latest_resumes(db, limit)
    _write(OUT / "fields_check.csv", ["candidate_id", "file", *FIELDS],
           [[c.id, d.original_filename, *_values(c, e).values()] for c, e, d in rows])


def _same(field: str, a: str, b: str) -> bool:
    if field == "skills":
        return {s.strip().lower() for s in a.split(";") if s.strip()} == {s.strip().lower() for s in b.split(";") if s.strip()}
    if field == "total_years":
        try:
            return abs(float(a or 0) - float(b or 0)) <= 0.5
        except ValueError:
            return a.strip() == b.strip()
    return " ".join(a.lower().split()) == " ".join(b.lower().split())


async def fields(key_path: str) -> None:
    key = _read(key_path)
    async with AsyncSessionLocal() as db:
        found = {str(c.id): _values(c, e) for c, e in await evaluation_repository.find_resumes_by_candidates(
            db, [uuid.UUID(r["candidate_id"]) for r in key])}
    right = {f: 0 for f in FIELDS}
    for row in key:
        ours = found.get(row["candidate_id"], {})
        for f in FIELDS:
            if _same(f, ours.get(f, ""), row.get(f, "")):
                right[f] += 1
            else:
                print(f"  {row.get('file')}: {f} read as {ours.get(f, '')!r}, correct is {row.get(f)!r}")
    total = len(key) * len(FIELDS)
    for f in FIELDS:
        print(f"{f:14} {right[f]}/{len(key)}")
    print(f"Fields read correctly: {round(100 * sum(right.values()) / max(1, total), 1)}% (target 95%)")


# ---------- speed ----------

async def timing() -> None:
    async with AsyncSessionLocal() as db:
        logs = await evaluation_repository.find_latest_run_logs(db, 15)
    print(f"{'when':20} {'job':30} {'considered':>10} {'scored':>7} {'from cache':>10} {'seconds':>8}")
    for log, title in logs:
        try:
            out = json.loads(log.output_data or "{}")
        except ValueError:
            continue
        if "seconds" not in out:
            continue
        ok = "OK" if out.get("seconds", 999) < 120 else "slow"
        print(f"{log.created_at:%Y-%m-%d %H:%M}     {title[:30]:30} {out.get('considered', '-'):>10} "
              f"{out.get('scored', '-'):>7} {out.get('from_cache', '-'):>10} {out.get('seconds'):>8}  {ok}")
    print("Target: 100+ resumes ranked in under 120 seconds (scored = ranked by the LLM in that run).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("answer-key-template")
    p.add_argument("--job", type=uuid.UUID, required=True)
    p = sub.add_parser("top10")
    p.add_argument("--job", type=uuid.UUID, required=True)
    p.add_argument("--key", required=True)
    p = sub.add_parser("fields-template")
    p.add_argument("--limit", type=int, default=30)
    p = sub.add_parser("fields")
    p.add_argument("--key", required=True)
    sub.add_parser("timing")
    args = parser.parse_args()

    run = {
        "answer-key-template": lambda: answer_key_template(args.job),
        "top10": lambda: top10(args.job, args.key),
        "fields-template": lambda: fields_template(args.limit),
        "fields": lambda: fields(args.key),
        "timing": timing,
    }[args.command]
    asyncio.run(run())


if __name__ == "__main__":
    main()
