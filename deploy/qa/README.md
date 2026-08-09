# Human QA chatbot deployment

This deployment is separate from the contest/public service. It exposes only the QA
chatbot edge. The original engine remains reachable solely as `http://engine:8080`
inside Docker, and `/answer`, `/demo`, `/docs`, `/redoc`, and `/openapi.json` are denied
at both QA edges.

## Prerequisites

1. Commit the release candidate and require a clean worktree. Build the engine with its
   exact Git SHA embedded as an OCI revision label, then build the gateway. Do this before
   generating the release artifact; never rebuild either image afterward.

   ```powershell
   $head = (git rev-parse HEAD).Trim()
   $engineImage = 'mirae-financial-agent:qa-local'
   $gatewayImage = 'mirae-human-qa-chatbot:local'
   docker build --no-cache --build-arg ENGINE_GIT_SHA=$head -t $engineImage .
   docker build --no-cache --target qa-runtime -f Dockerfile.qa -t $gatewayImage .
   $identity = (.\deploy\qa\Get-QaEngineImageIdentity.ps1 -EngineImage $engineImage |
     ConvertFrom-Json)
   $identity
   ```

   Copy the returned full image ID, embedded Git SHA, and in-image DuckDB SHA-256 into
   `ENGINE_IMAGE_DIGEST`, `ENGINE_GIT_SHA`, and `DATA_HASH` in the external environment
   file. `QA_ENGINE_IMAGE` and `QA_GATEWAY_IMAGE` must name the images just built.
2. Generate non-provider secrets in a new, empty directory outside this repository:

   ```powershell
   .\deploy\qa\New-QaSecrets.ps1 -OutputDirectory C:\secure\mirae-qa
   ```

   The helper removes inherited ACLs and permits only the current user, Local System, and
   local Administrators. It fails closed if the destination is inside the repository,
   non-empty, or cannot be protected.
3. Put the real HCX API key in a separate file in that protected directory. Never place
   it in an environment file or command line. Protect and verify that file too:

   ```powershell
   . .\deploy\qa\QaSecurity.ps1
   Set-QaRestrictedAcl -LiteralPath C:\secure\mirae-qa\hcx_api.key
   Assert-QaRestrictedAcl -LiteralPath C:\secure\mirae-qa\hcx_api.key
   ```
4. Copy the appropriate environment example outside the repository and replace every
   placeholder. `QA_ALLOWED_ORIGINS` must exactly match the browser origin. The model,
   Git SHA, data hash, and image digest in the environment file must exactly match the
   release artifact.
5. Complete the real HCX 20-question smoke and 100-question canary with that approved
   model, endpoint, and key. Keep `PILOT_CHAT_ENABLED=false` until both pass and the bound
   release artifact below validates.

## Mandatory release evidence

`QA_LIVE_GATE_STATUS` is not a release authority. The gateway receives one read-only
artifact at `/run/qa-release/qa_release_gate.json` and can become ready only after its
strict v1 schema, canonical self-hash, metadata binding, and both live gates validate.

After the sanitized live scripts have produced `live_hcx_gate_report.json` and
`live_hcx_e2e_gate_report.json`, generate the bound artifact outside the repository:

```powershell
.venv\Scripts\python.exe deploy\qa\release_evidence.py generate `
  --smoke-report artifacts\live_hcx_gate_report.json `
  --canary-report artifacts\live_hcx_e2e_gate_report.json `
  --engine-git-sha <final-40-character-sha> `
  --engine-image-digest sha256:<final-64-hex-image-digest> `
  --data-hash sha256:<serving-data-64-hex-hash> `
  --hcx-model-id <human-approved-hcx-model> `
  --hcx-base-url https://clovastudio.stream.ntruss.com `
  --output C:\secure\mirae-qa\qa_release_gate.json
```

Validate it again while requiring the exact deployment values:

```powershell
.venv\Scripts\python.exe deploy\qa\release_evidence.py validate `
  C:\secure\mirae-qa\qa_release_gate.json `
  --expect-engine-git-sha <final-40-character-sha> `
  --expect-engine-image-digest sha256:<final-64-hex-image-digest> `
  --expect-data-hash sha256:<serving-data-64-hex-hash> `
  --expect-hcx-model-id <human-approved-hcx-model> `
  --expect-hcx-base-url https://clovastudio.stream.ntruss.com
```

The artifact contains only counts, timestamps, hashes, endpoint/model identifiers, and
five explicit `false` sanitization flags. Its schema rejects extra fields, and it never
copies questions, prompts, plans, answers, product identifiers, tokens, or credentials.
`artifact_sha256` is SHA-256 over canonical UTF-8 JSON with that field omitted. This
detects accidental or unreviewed artifact edits; it is not a third-party signature.

## Mandatory immutable host preflight

The release artifact is necessary but cannot identify a running Docker container by
itself. Before `up`, run the host preflight against the clean repository, inspected local
image, in-image database, artifact, and four distinct restricted secret files:

```powershell
.\deploy\qa\Test-QaReleaseDeployment.ps1 `
  -EngineImage mirae-financial-agent:qa-local `
  -ExpectedEngineGitSha <final-40-character-sha> `
  -ExpectedEngineImageDigest sha256:<local-image-id> `
  -ExpectedDataHash sha256:<in-image-duckdb-hash> `
  -ExpectedHcxModelId <human-approved-hcx-model> `
  -ExpectedHcxBaseUrl https://clovastudio.stream.ntruss.com `
  -ReleaseGatePath C:\secure\mirae-qa\qa_release_gate.json `
  -HcxApiKeyFile C:\secure\mirae-qa\hcx_api.key `
  -EngineClarificationSigningKeyFile C:\secure\mirae-qa\engine_clarification.key `
  -QaTranscriptKeyFile C:\secure\mirae-qa\qa_transcript.key `
  -QaAuthSecretFile C:\secure\mirae-qa\qa_auth.key
```

It fails on a dirty or different HEAD, mutable-tag/image mismatch, wrong OCI revision,
wrong database hash, mismatched release evidence, duplicate secret content/path, or a
broad/inherited secret ACL. The Compose services use `pull_policy: never`; start them with
`--no-build` so the inspected local images cannot be silently replaced.

## Local profile

```powershell
docker compose --env-file C:\secure\mirae-qa\local.env -f compose.qa.yaml --profile local config --quiet
docker compose --env-file C:\secure\mirae-qa\local.env -f compose.qa.yaml --profile local up -d --no-build
```

Open `http://127.0.0.1:8090`. No service is bound to another host interface, and the
engine has no published port.

## LAN profile

Set `QA_LAN_BIND_IP` to an IPv4 address currently assigned to the host in an RFC1918
private subnet. Set `QA_ALLOWED_ORIGINS=https://<same-ip>:8443` and
`QA_COOKIE_SECURE=true`. Do not configure a router port forward or public DNS record.

```powershell
.\deploy\qa\Test-QaLanConfig.ps1 -BindAddress 192.168.1.20 -AllowedOrigin https://192.168.1.20:8443 -CookieSecure $true
docker compose --env-file C:\secure\mirae-qa\lan.env -f compose.qa.yaml --profile lan config --quiet
docker compose --env-file C:\secure\mirae-qa\lan.env -f compose.qa.yaml --profile lan up -d --no-build
.\deploy\qa\Export-LanCa.ps1 -OutputPath C:\secure\mirae-qa\caddy-local-ca.crt
.\deploy\qa\Set-QaLanFirewall.ps1 -Action Install -BindAddress 192.168.1.20 -RemotePrivateSubnet 192.168.1.0/24
```

Verify the printed fingerprint out-of-band, then install the CA only on approved team
devices. Caddy's CA storage is tmpfs so that the SQLite `qa_state` volume is the only
persistent writable volume. Recreating the LAN edge container creates a new CA; export,
verify, and trust the replacement before testing again.

The firewall helper creates only an inbound TCP 8443 allow rule for the exact local IP,
Private profile, and supplied RFC1918 subnet. It rejects non-canonical CIDRs and proves the
entire address range—not merely its first address—stays inside one RFC1918 block. It
requires explicit `-Action Install` and PowerShell confirmation. Audit it with
`-Action Audit` and remove the exact rule with `-Action Remove`. The Compose file does not
make firewall or router changes automatically. Also audit pre-existing broad allow rules;
an added allow rule cannot narrow a separate rule created by another application.

## Verification and shutdown

```powershell
docker compose --env-file C:\secure\mirae-qa\local.env -f compose.qa.yaml --profile local ps
curl.exe -i http://127.0.0.1:8090/qa/api/v1/status
curl.exe -i http://127.0.0.1:8090/answer
docker inspect mirae-human-qa-chatbot-engine-1 --format '{{json .HostConfig.PortBindings}}'
docker compose --env-file C:\secure\mirae-qa\local.env -f compose.qa.yaml --profile local restart
```

The status request must be controlled JSON, `/answer` must be `404`, and the engine port
bindings must be `null`. After restart, verify an existing QA session and pending
clarification before admitting testers.

After `up` and after every restart, rerun the immutable preflight above with
`-RunningEngineContainer mirae-human-qa-chatbot-engine-1`. This additionally compares the
actual running container image ID to the bound release digest. A failed engine must not
prevent the gateway from starting: the gateway remains available for history, export,
delete, and logout while new turns are blocked.

## Host-only administration

There is no admin web route. Run the CLI inside the gateway so it uses the mounted secrets
and SQLite volume. Issue or revoke one-time invite codes, purge expired sessions, and make
a consistent encrypted SQLite backup as follows:

```powershell
$compose = @('--env-file','C:\secure\mirae-qa\local.env','-f','compose.qa.yaml','--profile','local')
docker compose @compose exec -T qa-gateway python -m qa_chat.admin issue-invite --hours 24
$rawInviteCode | docker compose @compose exec -T qa-gateway python -m qa_chat.admin revoke-invite
docker compose @compose exec -T qa-gateway python -m qa_chat.admin purge
docker compose @compose exec -T qa-gateway python -m qa_chat.admin backup /var/lib/mirae-qa/manual-backup.sqlite3
docker cp mirae-human-qa-chatbot-qa-gateway-1:/var/lib/mirae-qa/manual-backup.sqlite3 C:\secure\mirae-qa\manual-backup.sqlite3
```

The raw invite is printed only at issuance and only its keyed hash is stored. Avoid placing
invite codes in shared transcripts or logs. The copied backup still contains sensitive,
encrypted test records; protect its ACL and keep the transcript/auth keys needed to restore
it. The CLI refuses the live database path and any existing destination instead of
overwriting them. Verify a backup restoration separately before relying on it.

Stop without deleting retained QA sessions:

```powershell
docker compose --env-file C:\secure\mirae-qa\local.env -f compose.qa.yaml --profile local down
```

Deleting `qa_state` permanently erases all stored test sessions. Do not use `down -v`
unless that destructive deletion is explicitly intended and separately backed up.

## Historical local deployment verification evidence (2026-08-09)

The deployment files were checked from a dirty, uncommitted QA working tree before the
release-artifact hardening above. These results remain useful engineering history but no
longer identify the current source tree.

- `docker build --no-cache --target qa-runtime -f Dockerfile.qa ...`: PASS for the
  pre-hardening tree. Its local digest is intentionally not reused as release evidence.
- `docker compose -f compose.qa.yaml --profile local config --quiet`: PASS.
- `docker compose -f compose.qa.yaml --profile lan config --quiet`: PASS with an explicit
  private bind address. Expanded config had no engine or gateway host port, local edge
  `127.0.0.1:8090`, LAN edge `<private-ip>:8443`, an internal backplane, and only the
  `qa_state` persistent volume.
- Caddy local/LAN `validate`: PASS. Hardened LAN runtime passed with read-only root,
  tmpfs state, no-new-privileges, `cap_drop: ALL`, and only Caddy's required
  `NET_BIND_SERVICE` file capability restored.
- Isolated local-edge HTTP check: `/` and `/qa/api/v1/status` returned 200;
  `/answer`, `/demo`, and `/docs` returned 404; CSP, no-store, no-referrer, noindex, and
  access-log suppression checks passed.
- The non-root gateway created and wrote its SQLite database through an empty Docker
  `qa_state` volume while its root filesystem remained read-only; the exact test volume
  and container were removed afterward.
- PowerShell deployment helpers parsed successfully. Secret generation and overwrite
  refusal, LAN private-address/origin/cookie preflight, and read-only firewall audit ran
  successfully.

Current release status is `PENDING_FINAL_BUILD`. Re-run every command after the final
commit, record that committed SHA and the newly inspected immutable image digest, run the
real HCX 20/100 gates, then generate the bound artifact. LAN device trust/accessibility
checks and the human pilot also remain external work.
