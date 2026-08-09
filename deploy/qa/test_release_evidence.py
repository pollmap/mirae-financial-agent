from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import release_evidence

MODEL = "HCX-007"
GIT_SHA = "a" * 40
IMAGE_DIGEST = "sha256:" + "b" * 64
DATA_HASH = "sha256:" + "c" * 64
SUITE_HASH = "d" * 64
WHEN = "2026-08-09T00:00:00+00:00"


def _smoke() -> dict[str, object]:
    return {
        "status": "PASS",
        "gate": "HCX_20_QUESTION_ONE_VS_TWO_STAGE",
        "model_id": MODEL,
        "approved_planner_stage": "two",
        "completed_at_utc": WHEN,
        "question_suite_sha256": SUITE_HASH,
        "case_count": 20,
        "provider_call_count": 40,
        "both_stage_valid_count": 20,
        "both_stage_match_count": 20,
        "cases": [
            {
                "case_id": f"LIVE-{index:02d}",
                "stage_one_valid": True,
                "stage_two_valid": True,
                "canonical_match": True,
            }
            for index in range(1, 21)
        ],
        "usage": {
            "stage_one": {"prompt_tokens": 10},
            "stage_two": {"prompt_tokens": 5},
        },
        "secret_values_recorded": False,
        "questions_recorded": False,
        "plans_recorded": False,
    }


def _canary(*, passed: int = 100) -> dict[str, object]:
    by_kind = {
        "rank_single": {"total": 35, "passed": 35},
        "filter_search": {"total": 25, "passed": 25},
        "count_aggregate": {"total": 20, "passed": 20},
        "cross_scope": {"total": 20, "passed": 20},
    }
    if passed < 100:
        by_kind["rank_single"]["passed"] -= 100 - passed
    return {
        "status": "PASS",
        "gate": "HCX_100_QUESTION_TWO_STAGE_E2E",
        "model_id": MODEL,
        "approved_planner_stage": "two",
        "completed_at_utc": WHEN,
        "question_suite_sha256": SUITE_HASH,
        "case_count": 100,
        "minimum_accuracy": 0.98,
        "passed_count": passed,
        "accuracy": passed / 100,
        "hcx_planned_case_count": 100,
        "evidence_linked_case_count": 100,
        "cross_scope_refusal_count": 0,
        "by_kind": by_kind,
        "secret_values_recorded": False,
        "questions_recorded": False,
        "prompts_recorded": False,
        "plans_recorded": False,
        "answers_recorded": False,
        "product_identifiers_recorded": False,
    }


class ReleaseEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        self.smoke_path = root / "smoke.json"
        self.canary_path = root / "canary.json"
        self.smoke_path.write_text(json.dumps(_smoke()), encoding="utf-8")
        self.canary_path.write_text(json.dumps(_canary()), encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def build(self) -> dict[str, object]:
        return release_evidence.build_artifact(
            smoke_report_path=self.smoke_path,
            canary_report_path=self.canary_path,
            engine_git_sha=GIT_SHA,
            engine_image_digest=IMAGE_DIGEST,
            data_hash=DATA_HASH,
            hcx_model_id=MODEL,
            hcx_base_url="https://clovastudio.stream.ntruss.com",
            generated_at_utc=WHEN,
        )

    def test_valid_reports_create_self_hashed_sanitized_artifact(self) -> None:
        artifact = self.build()
        release_evidence.validate_artifact(
            artifact,
            expected_engine_git_sha=GIT_SHA,
            expected_engine_image_digest=IMAGE_DIGEST,
            expected_data_hash=DATA_HASH,
            expected_hcx_model_id=MODEL,
            expected_hcx_base_url="https://clovastudio.stream.ntruss.com",
        )
        self.assertEqual(artifact["gates"]["smoke_20"]["passed"], 20)
        self.assertEqual(artifact["gates"]["canary_100"]["passed"], 100)
        self.assertTrue(all(value is False for value in artifact["sanitization"].values()))

    def test_artifact_tamper_is_rejected(self) -> None:
        artifact = self.build()
        artifact["data_hash"] = "sha256:" + "e" * 64
        with self.assertRaises(release_evidence.EvidenceError):
            release_evidence.validate_artifact(artifact)

    def test_source_report_with_extra_question_field_is_rejected(self) -> None:
        smoke = _smoke()
        smoke["question"] = "must never be retained"
        self.smoke_path.write_text(json.dumps(smoke), encoding="utf-8")
        with self.assertRaises(release_evidence.EvidenceError):
            self.build()

    def test_canary_below_98_percent_is_rejected(self) -> None:
        self.canary_path.write_text(json.dumps(_canary(passed=97)), encoding="utf-8")
        with self.assertRaises(release_evidence.EvidenceError):
            self.build()

    def test_expected_deployment_metadata_must_match(self) -> None:
        artifact = self.build()
        with self.assertRaises(release_evidence.EvidenceError):
            release_evidence.validate_artifact(
                artifact,
                expected_engine_image_digest="sha256:" + "f" * 64,
            )

    def test_all_zero_placeholder_digests_are_rejected(self) -> None:
        with self.assertRaises(release_evidence.EvidenceError):
            release_evidence.build_artifact(
                smoke_report_path=self.smoke_path,
                canary_report_path=self.canary_path,
                engine_git_sha="0" * 40,
                engine_image_digest=IMAGE_DIGEST,
                data_hash=DATA_HASH,
                hcx_model_id=MODEL,
                hcx_base_url="https://clovastudio.stream.ntruss.com",
                generated_at_utc=WHEN,
            )

        artifact = self.build()
        artifact["gates"]["smoke_20"]["suite_sha256"] = "0" * 64
        artifact["artifact_sha256"] = release_evidence.canonical_artifact_sha256(
            artifact
        )
        with self.assertRaises(release_evidence.EvidenceError):
            release_evidence.validate_artifact(artifact)

    def test_cli_generate_validate_and_overwrite_guard(self) -> None:
        output = Path(self.temporary.name) / "qa_release_gate.json"
        generate_args = [
            "generate",
            "--smoke-report",
            str(self.smoke_path),
            "--canary-report",
            str(self.canary_path),
            "--engine-git-sha",
            GIT_SHA,
            "--engine-image-digest",
            IMAGE_DIGEST,
            "--data-hash",
            DATA_HASH,
            "--hcx-model-id",
            MODEL,
            "--hcx-base-url",
            "https://clovastudio.stream.ntruss.com",
            "--output",
            str(output),
        ]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(release_evidence.main(generate_args), 0)
            self.assertEqual(
                release_evidence.main(
                    [
                        "validate",
                        str(output),
                        "--expect-engine-git-sha",
                        GIT_SHA,
                        "--expect-engine-image-digest",
                        IMAGE_DIGEST,
                        "--expect-data-hash",
                        DATA_HASH,
                        "--expect-hcx-model-id",
                        MODEL,
                    ]
                ),
                0,
            )
            self.assertEqual(release_evidence.main(generate_args), 2)


if __name__ == "__main__":
    unittest.main()
