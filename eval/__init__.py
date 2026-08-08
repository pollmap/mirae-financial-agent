"""Independent evaluation harness for the Mirae financial-product agent.

Modules:
- ``eval.templates``    deterministic question-spec generator (no DB access).
- ``eval.oracle``       expected answers via direct read-only DuckDB SQL only.
- ``eval.run_eval``     async driver that scores the live service vs the oracle.
- ``eval.metamorphic``  paraphrase-invariance checker.

Design rule: the oracle never imports anything from ``app``; the value of the
harness is that expectations are computed through an independent code path.
"""
