from __future__ import annotations

from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.router import route_entity_fallback, route_theme_query


def test_route_entity_fallback_skips_when_exact_match_found() -> None:
    decision = route_entity_fallback(exact_match_count=1, vector_enabled=True)
    assert decision.channels == ()
    assert decision.reason == "exact_match_found"


def test_route_entity_fallback_tries_lexical_then_vector_when_enabled() -> None:
    decision = route_entity_fallback(exact_match_count=0, vector_enabled=True)
    assert decision.channels == ("lexical", "vector")


def test_route_entity_fallback_skips_vector_when_disabled() -> None:
    decision = route_entity_fallback(exact_match_count=0, vector_enabled=False)
    assert decision.channels == ("lexical",)


def test_route_theme_query_requires_theme_text() -> None:
    assert route_theme_query(has_theme_text=False, vector_enabled=True).channels == ()
    decision = route_theme_query(has_theme_text=True, vector_enabled=True)
    assert decision.channels == ("lexical", "vector")


def test_rrf_prefers_uid_ranked_high_in_multiple_channels() -> None:
    fused = reciprocal_rank_fusion(
        {
            "lexical": ["A", "B", "C"],
            "vector": ["B", "A", "D"],
        }
    )
    # B is rank 2 in lexical + rank 1 in vector; A is rank 1 + rank 2.
    # Scores are symmetric (1/(k+1)+1/(k+2) for both), so the uid tie-break
    # (alphabetical) decides, and both outrank single-channel-only C/D.
    assert fused[0] in {"A", "B"}
    assert set(fused[:2]) == {"A", "B"}
    assert fused.index("A") < fused.index("C")
    assert fused.index("B") < fused.index("D")


def test_rrf_single_channel_preserves_order() -> None:
    fused = reciprocal_rank_fusion({"lexical": ["X", "Y", "Z"]})
    assert fused == ["X", "Y", "Z"]


def test_rrf_empty_input() -> None:
    assert reciprocal_rank_fusion({}) == []
    assert reciprocal_rank_fusion({"lexical": []}) == []


def test_rrf_respects_limit() -> None:
    fused = reciprocal_rank_fusion({"lexical": [str(i) for i in range(10)]}, limit=3)
    assert len(fused) == 3
    assert fused == ["0", "1", "2"]


def test_rrf_deterministic_tie_break_by_uid() -> None:
    fused = reciprocal_rank_fusion({"a": ["Z"], "b": ["A"]})
    # Both rank 1 in their own channel -> identical score -> uid order.
    assert fused == ["A", "Z"]
