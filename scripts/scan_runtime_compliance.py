#!/usr/bin/env python3
"""Fail the build if submitted runtime contains a non-HCX LLM integration."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# app/etl ship in the runtime image; scripts/tests/eval/deploy don't, but a
# stray dependency or credential reference in any of them is still worth
# catching before it has a chance to migrate into shipped code.
SCAN_ROOTS = [
    ROOT / "app",
    ROOT / "etl",
    ROOT / "qa_chat",
    ROOT / "qa_web",
    ROOT / "scripts",
    ROOT / "tests",
    ROOT / "eval",
    ROOT / "deploy",
]
SCAN_FILES = [
    ROOT / "requirements.txt",
    ROOT / "requirements-runtime.txt",
    ROOT / "requirements-runtime.lock",
    ROOT / "requirements-build.lock",
    ROOT / "requirements-dev.txt",
    ROOT / "pyproject.toml",
    ROOT / "Dockerfile",
    ROOT / "Dockerfile.qa",
    ROOT / "compose.qa.yaml",
]

# Split literal strings so this scanner can never flag its own source if its
# scope is expanded later.
FORBIDDEN = {
    "non_hcx_python_sdk": re.compile(
        r"(?:^|\s)(?:from|import)\s+(?:"
        + "open"
        + r"ai|anthropic|cohere|google\.generativeai)(?:\s|\.|$)",
        re.IGNORECASE | re.MULTILINE,
    ),
    "non_hcx_endpoint": re.compile(
        r"api\."
        + "open"
        + r"ai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com",
        re.IGNORECASE,
    ),
    "non_hcx_secret": re.compile(
        "|".join(
            [
                "OPEN" + "AI_API_KEY",
                "ANTHROP" + "IC_API_KEY",
                "GEMIN" + "I_API_KEY",
                "COHER" + "E_API_KEY",
            ]
        ),
        re.IGNORECASE,
    ),
    "non_hcx_dependency": re.compile(
        r"^(?:"
        + "open"
        + r"ai|anthropic|cohere|google-generativeai)(?:\[.*\])?[=<>~!]",
        re.IGNORECASE | re.MULTILINE,
    ),
}


def iter_files() -> list[Path]:
    files = list(SCAN_FILES)
    for root in SCAN_ROOTS:
        files.extend(
            path
            for path in root.rglob("*")
            if path.is_file()
            and "node_modules" not in path.parts
            and "dist" not in path.parts
            and path.suffix
            in {
                ".css",
                ".html",
                ".json",
                ".ps1",
                ".py",
                ".sh",
                ".toml",
                ".ts",
                ".tsx",
                ".txt",
                ".yaml",
                ".yml",
            }
        )
    return sorted(set(files))


def main() -> None:
    findings: list[dict[str, object]] = []
    files = iter_files()
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for rule, pattern in FORBIDDEN.items():
            for match in pattern.finditer(text):
                findings.append(
                    {
                        "rule": rule,
                        "path": str(path.relative_to(ROOT)),
                        "line": text.count("\n", 0, match.start()) + 1,
                    }
                )
    result = {
        "status": "ok" if not findings else "failed",
        "files_scanned": len(files),
        "approved_language_model": "HyperCLOVA X only",
        "findings": findings,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if findings:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
