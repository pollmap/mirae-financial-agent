# Project visual kit

This directory contains the reusable 1920×1080 project overview posters. Each poster has an editable SVG source and a PNG export.

| Poster | What it explains |
|---|---|
| `mirae-tech-stack-1920x1080` | Only the runtime/product stack actually used in this project. Developer collaboration tools, voice input, and unrelated local-network tooling are excluded. |
| `mirae-system-architecture-1920x1080` | Request surface, HCX-only planning, grounding, federated retrieval, SQL authority, and evidence output. |
| `mirae-question-flow-1920x1080` | How a question becomes a grounded answer or one precise clarification, without silently losing conditions. |
| `mirae-repository-map-1920x1080` | Code and document ownership map, including the fixed five-field `/answer` contract. |
| `mirae-assurance-release-gates-1920x1080` | The boundary between official inputs, locally verified evidence, and external gates still pending. |

## Rebuild

The source of truth for layout and labels is `scripts/generate_project_visuals.mjs`. The script embeds the locally saved brand marks and poster background, then exports SVG and PNG together.

```powershell
$env:NODE_PATH = 'C:\Users\lch68\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
C:\Users\lch68\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe scripts\generate_project_visuals.mjs
```

The project backdrop is generated specifically for this visual kit. Brand marks in `logos/` are downloaded from Simple Icons' public CDN where a product mark exists; remaining items (for example BM25, Graph, ConditionLedger, and security controls) are intentionally neutral project symbols rather than invented vendor logos.

Do not use these posters to claim live HCX, live Embedding, or public NCP deployment has completed: those remain `PENDING_EXTERNAL` until their separate gates pass.
