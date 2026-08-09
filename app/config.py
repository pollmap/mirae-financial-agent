"""Environment-backed runtime configuration without secret logging."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Validated application settings.

    `production` requires HCX credentials.  Development/test may use the
    deterministic, non-LLM parser for reproducible local E2E checks.
    """

    environment: str = "development"
    database_path: Path = PROJECT_ROOT / "data" / "serving" / "mirae_agent.duckdb"
    planner_mode: str = "deterministic"
    # "one" = full physical QueryPlan from HCX; "two" = HCX emits concepts
    # only and the server-side grounder maps them to physical fields.
    planner_stage: str = "two"
    # Vector retrieval requires a committed embeddings cache
    # (artifacts/embeddings/embeddings_cache.parquet, built by
    # scripts/build_embeddings.py with a real CLOVA_STUDIO_API_KEY). Off by
    # default: the cache does not exist yet, and every retrieval path
    # degrades to lexical/BM25 without it.
    vector_enabled: bool = False
    hcx_model_id: str = "HCX-007"
    # Human-approved release lock.  HCX-007 remains the development/team
    # default, but production is not ready until an operator explicitly sets
    # and matches this independent value.
    approved_hcx_model_id: str | None = None
    hcx_base_url: str = "https://clovastudio.stream.ntruss.com"
    clova_studio_api_key: str | None = None
    hcx_timeout_seconds: float = 12.0
    hcx_total_deadline_seconds: float = 25.0
    hcx_max_retries: int = 2
    hcx_max_concurrency: int = 3
    hcx_qpm_limit: int = 60
    hcx_tpm_budget: int = 60_000
    db_max_concurrency: int = 8
    question_max_chars: int = 2000
    default_limit: int = 10
    max_limit: int = 50
    enable_clarification_state: bool = True
    clarification_signing_key: str = "development-only-rotate-before-production"

    @classmethod
    def from_env(cls) -> Settings:
        settings = cls(
            environment=os.getenv("APP_ENV", "development").strip().lower(),
            database_path=Path(
                os.getenv(
                    "MIRAE_DATABASE_PATH",
                    str(PROJECT_ROOT / "data" / "serving" / "mirae_agent.duckdb"),
                )
            ),
            planner_mode=os.getenv("PLANNER_MODE", "deterministic").strip().lower(),
            planner_stage=os.getenv("PLANNER_STAGE", "two").strip().lower(),
            vector_enabled=_bool_env("VECTOR_ENABLED", False),
            hcx_model_id=os.getenv("HCX_MODEL_ID", "HCX-007").strip(),
            approved_hcx_model_id=(
                os.getenv("APPROVED_HCX_MODEL_ID", "").strip() or None
            ),
            hcx_base_url=os.getenv("HCX_BASE_URL", "https://clovastudio.stream.ntruss.com").rstrip(
                "/"
            ),
            clova_studio_api_key=os.getenv("CLOVA_STUDIO_API_KEY"),
            hcx_timeout_seconds=float(os.getenv("HCX_TIMEOUT_SECONDS", "12")),
            hcx_total_deadline_seconds=float(os.getenv("HCX_TOTAL_DEADLINE_SECONDS", "25")),
            hcx_max_retries=int(os.getenv("HCX_MAX_RETRIES", "2")),
            hcx_max_concurrency=int(os.getenv("HCX_MAX_CONCURRENCY", "3")),
            hcx_qpm_limit=int(os.getenv("HCX_QPM_LIMIT", "60")),
            hcx_tpm_budget=int(os.getenv("HCX_TPM_BUDGET", "60000")),
            db_max_concurrency=int(os.getenv("DB_MAX_CONCURRENCY", "8")),
            question_max_chars=int(os.getenv("QUESTION_MAX_CHARS", "2000")),
            default_limit=int(os.getenv("DEFAULT_RESULT_LIMIT", "10")),
            max_limit=int(os.getenv("MAX_RESULT_LIMIT", "50")),
            enable_clarification_state=_bool_env("ENABLE_CLARIFICATION_STATE", True),
            clarification_signing_key=os.getenv(
                "CLARIFICATION_SIGNING_KEY", "development-only-rotate-before-production"
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        if self.environment not in {"development", "test", "production"}:
            raise ValueError("APP_ENV must be development, test, or production")
        if self.planner_mode not in {"hcx", "deterministic"}:
            raise ValueError("PLANNER_MODE must be hcx or deterministic")
        if self.planner_stage not in {"one", "two"}:
            raise ValueError("PLANNER_STAGE must be one or two")
        if self.environment == "production" and self.planner_mode != "hcx":
            raise ValueError("production requires PLANNER_MODE=hcx")
        if self.planner_mode == "hcx" and not self.clova_studio_api_key:
            raise ValueError("CLOVA_STUDIO_API_KEY is required for HCX planning")
        if self.environment == "production" and (
            not self.clova_studio_api_key
            or len(self.clova_studio_api_key.strip()) < 20
            or self.clova_studio_api_key.strip().lower()
            in {"mock-key", "test-key", "replace-me", "changeme"}
        ):
            raise ValueError("production requires a non-placeholder CLOVA_STUDIO_API_KEY")
        if self.environment == "production" and self.hcx_base_url != (
            "https://clovastudio.stream.ntruss.com"
        ):
            raise ValueError("production HCX_BASE_URL must be the official CLOVA Studio HTTPS host")
        if not re.fullmatch(r"HCX-[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.hcx_model_id):
            raise ValueError("HCX_MODEL_ID must identify a HyperCLOVA X model")
        if not 1 <= self.default_limit <= self.max_limit <= 50:
            raise ValueError("result limits must satisfy 1 <= default <= max <= 50")
        if not 1 <= self.question_max_chars <= 2000:
            raise ValueError("QUESTION_MAX_CHARS must be between 1 and 2000")
        if not 1 <= self.hcx_max_retries <= 3:
            raise ValueError("HCX_MAX_RETRIES must be between 1 and 3")
        if not 1 <= self.hcx_max_concurrency <= 16:
            raise ValueError("HCX_MAX_CONCURRENCY must be between 1 and 16")
        if not 1 <= self.hcx_qpm_limit <= 180:
            raise ValueError("HCX_QPM_LIMIT must be between 1 and 180")
        if not 1_024 <= self.hcx_tpm_budget <= 10_000_000:
            raise ValueError("HCX_TPM_BUDGET must be between 1024 and 10000000")
        if not 1 <= self.db_max_concurrency <= 32:
            raise ValueError("DB_MAX_CONCURRENCY must be between 1 and 32")
        if not self.hcx_timeout_seconds > 0:
            raise ValueError("HCX_TIMEOUT_SECONDS must be positive")
        if not self.hcx_total_deadline_seconds >= self.hcx_timeout_seconds:
            raise ValueError("HCX_TOTAL_DEADLINE_SECONDS must be at least HCX_TIMEOUT_SECONDS")
        if (
            self.environment == "production"
            and self.enable_clarification_state
            and (
                self.clarification_signing_key
                == "development-only-rotate-before-production"
                or len(self.clarification_signing_key.encode("utf-8")) < 24
            )
        ):
            raise ValueError(
                "production clarification state requires a 24-byte-or-longer "
                "CLARIFICATION_SIGNING_KEY"
            )
        if self.approved_hcx_model_id is not None and not re.fullmatch(
            r"HCX-[A-Za-z0-9][A-Za-z0-9._-]{0,63}", self.approved_hcx_model_id
        ):
            raise ValueError("APPROVED_HCX_MODEL_ID must identify a HyperCLOVA X model")
        if self.environment == "production" and not self.approved_hcx_model_id:
            raise ValueError("production requires a human-approved APPROVED_HCX_MODEL_ID")
        if (
            self.approved_hcx_model_id is not None
            and self.hcx_model_id != self.approved_hcx_model_id
        ):
            raise ValueError("HCX_MODEL_ID must match APPROVED_HCX_MODEL_ID")
