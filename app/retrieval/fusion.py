"""Fuse ranked candidate lists from multiple retrieval channels.

Reciprocal Rank Fusion (RRF): a channel-agnostic way to combine rankings
without needing comparable scores across channels (BM25 and cosine
similarity are not on the same scale). Fused output is a plain ordered list
of ``product_uid`` values; callers re-join ``product_catalog``/
``product_metrics`` to build ``FieldEvidence`` exactly as the SQL-only path
already does, so no retrieval-specific evidence type is needed.
"""

from __future__ import annotations

from collections.abc import Sequence

RRF_K = 60


def reciprocal_rank_fusion(
    channel_results: dict[str, Sequence[str]], *, k: int = RRF_K, limit: int = 50
) -> list[str]:
    """Merge per-channel ranked ``product_uid`` lists into one ranked list.

    ``score(uid) = sum over channels of 1 / (k + rank)``, rank 1-based within
    each channel. A uid present in more channels, or ranked higher within a
    channel, scores higher. Ties break by uid for determinism.
    """

    scores: dict[str, float] = {}
    for uids in channel_results.values():
        for rank, uid in enumerate(uids, start=1):
            scores[uid] = scores.get(uid, 0.0) + 1.0 / (k + rank)
    ordered = sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
    return [uid for uid, _ in ordered[:limit]]
