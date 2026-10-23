"""Layering: SQL lives in repositories; services hold business logic and pair one-to-one with a repository."""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
QUERY_CALLS = re.compile(r"\bselect\(|\.execute\(|\bdb\.(add|get|delete|refresh|flush)\(")
# Not query code: the health probe ("SELECT 1") and the repositories themselves.
ALLOWED = {SRC / "api/rest/routes/health.py"}
LAYERS = ("core", "api", "control", "handlers", "utils", "evaluation")


def test_only_repositories_query_the_database():
    offenders = []
    for layer in LAYERS:
        for path in (SRC / layer).rglob("*.py"):
            if path in ALLOWED:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                if QUERY_CALLS.search(line):
                    offenders.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    assert not offenders, "database calls outside data/repositories:\n" + "\n".join(offenders)


def test_one_service_one_repository():
    services = {p.stem.removesuffix("_service") for p in (SRC / "core/services").glob("*_service.py")}
    repositories = {p.stem.removesuffix("_repository") for p in (SRC / "data/repositories").glob("*_repository.py")}
    # "evaluation" is the offline evaluation script's repository; it has no service.
    assert services == repositories - {"evaluation"}
