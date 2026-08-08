"""Federated retrieval router: decide which channels a query needs.

Pure decision logic, no I/O. Precedence mirrors the briefing's retrieval
ladder: exact identifier and structured filters are SQL's job and never
reach this module; what remains is relation lookups (graph), name/text
matching (lexical), and thematic/free-text matching (vector, when enabled).
The router returns *which* channels to try, in order; callers execute them
and hand the per-channel results to :mod:`app.retrieval.fusion`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RouteDecision:
    channels: tuple[str, ...]  # ordered, e.g. ("graph", "lexical", "vector")
    reason: str


def route_entity_fallback(*, exact_match_count: int, vector_enabled: bool) -> RouteDecision:
    """Decide fallback channels once an exact code/alias match found nothing.

    Exact identifier and exact KG-alias matching happen before this is ever
    consulted (``DuckDBEngine.resolve_entity_matches``); this only fires on a
    genuine zero-match name, where silently returning "no results" would
    discard a plausible near-match the user could confirm.
    """

    if exact_match_count > 0:
        return RouteDecision(channels=(), reason="exact_match_found")
    channels = ["lexical"]
    if vector_enabled:
        channels.append("vector")
    return RouteDecision(channels=tuple(channels), reason="exact_match_empty")


def route_theme_query(*, has_theme_text: bool, vector_enabled: bool) -> RouteDecision:
    """Decide channels for a free-text thematic query (e.g. strategy text)."""

    if not has_theme_text:
        return RouteDecision(channels=(), reason="no_theme_text")
    channels = ["lexical"]
    if vector_enabled:
        channels.append("vector")
    return RouteDecision(channels=tuple(channels), reason="theme_text_present")
