"""LangGraph workflows for the long-running pipelines.

Each graph is a thin layer that decides the order of steps and what happens on
failure. The steps themselves are the service functions (parsing, LLM calls,
scoring rules, database writes), so they can be tested without the graph.
"""
