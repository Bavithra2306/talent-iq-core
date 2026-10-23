"""Repositories: the only place that talks to the database (SQLAlchemy queries and writes).

One repository per service: ``core/services/<name>_service.py`` uses ``data/repositories/<name>_repository.py``.
A service holds the business rules (checks, decisions, scoring, what to log) and asks its repository for data.
The service also owns the transaction: it decides when to ``commit``; repositories only add, query and flush.
"""
