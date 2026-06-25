# Security Audit

Date: 2026-06-25

Scope: local repo review of the FastAPI backend, Next.js frontend, dependency manifests, model artifacts, secret-bearing files, upload/LLM paths, and trading-account control paths. This is not a penetration test, but it does include live dependency advisory checks.

## Executive Summary

This repo should currently be treated as a powerful local trading console that is being hardened, not a network-safe multi-user application. The biggest remaining risk is not one isolated CVE; it is the combination of:

- unauthenticated HTTP endpoints that can trade, approve trades, import broker data, change gates, and mutate account state;
- live brokerage/API secrets loaded into the same process;
- unsafe local model deserialization via pickle/PyTorch load;
- personal financial data and OAuth tokens stored locally in plaintext files.

If a hostile local process, browser-origin bypass, or accidentally exposed port can reach the backend, they can plausibly manipulate orders, approve pending trades, cancel orders, import/alter portfolio data, or trigger account sync flows. The first hardening milestone should be: keep services bound locally by default, add authentication/authorization to every mutating route, keep live trading gated, and remove unsafe model deserialization.

## Current Hardening Status

First-pass fixes applied on 2026-06-25:

- Backend install now requires Python 3.14+ and uses `backend/venv-py314` by default.
- Security tooling now installs into `backend/.security-venv-py314` and is tied to the backend Python version plus the frozen `backend/requirements-security.txt` hash.
- Backend and frontend service defaults now bind to `127.0.0.1`; LAN exposure requires overriding `BACKEND_HOST` and/or `FRONTEND_HOST`.
- Frontend dependencies were upgraded and frozen; `npm audit --audit-level=high` now reports `found 0 vulnerabilities`.
- `npm run build` now passes on Next 16 after adding an explicit App Router `not-found` route.
- The vulnerable `alpaca-trade-api` dependency was removed from runtime requirements and replaced with a small local REST compatibility wrapper using `requests`.
- Python runtime dependencies were pinned and upgraded for Python 3.14 compatibility, including FastAPI/Starlette, `requests`, `urllib3` transitive resolution, `torch`, `python-multipart`, `pypdf`, and Google client libraries.
- Bandit high-severity findings were removed: SHA-1 article IDs were changed to SHA-256, XML parsing now uses `defusedxml`, the missing `requests` timeout was fixed, and dynamic SQL identifiers in cleanup/backup helpers were replaced with explicit whitelists/statements.
- Local permissions were tightened with `chmod 600 backend/.env backend/data/*.db backend/data/gdrive_token.json`.

Current `make sec-audit` status:

- `npm audit`: passing.
- `pip-audit`: one remaining finding, `diskcache==5.6.3` / `GHSA-w8v5-vhqr-4h9v`, pulled in transitively by `lib-pybroker`; no fixed version is reported by `pip-audit`.
- `bandit`: no high findings; 13 medium findings remain, all unsafe local pickle/PyTorch model deserialization.
- `detect-secrets`: still flags real local secrets in `backend/.env` and `backend/data/gdrive_token.json`, plus likely false positives in generated state/cache JSON, tests, and docs.

## Tools Run

Commands run during this audit:

- `npm audit --json` in `frontend/`.
- `pip-audit -r backend/requirements.txt`, using the pinned security venv under `backend/.security-venv-py314`.
- `bandit -r` over first-party backend source directories.
- `detect-secrets scan --all-files`, excluding `.git`, `frontend/node_modules`, `frontend/.next`, backend venv/security venv directories, and generated site output.
- `rg` scans for auth, secrets, upload handlers, subprocess, pickle, PyTorch load, XML parsing, and network calls.
- file mode checks for `backend/.env`, database files, and `backend/data/gdrive_token.json`.

Limitations:

- `gitleaks` was not installed; `detect-secrets` is the repo-standard scanner for now.
- Dependency findings are current as of the audit date and will change over time.
- `pip-audit` reports transitive packages resolved from `backend/requirements.txt`; it does not prove every vulnerable code path is reachable.

## Critical Findings

### 1. No API Authentication On High-Impact Routes

The FastAPI app exposes all routes without authentication. The docs also describe the API as local-only and unauthenticated. This is not enough for a bot with live brokerage credentials.

High-impact unauthenticated routes include:

- trade approval/rejection: `POST /api/pending-trades/{pending_id}/approve`, `POST /api/pending-trades/{pending_id}/reject`;
- broker order cancellation: `POST /api/execution/open-orders/{order_id}/cancel`;
- approval-gate changes: `POST /api/execution/accounts/{account_key}/gate`;
- global auto-trading kill switch: `POST /api/execution/auto-trading`;
- manual liquidation: `POST /api/positions/liquidate`;
- crash-radar rebalance execution: `POST /api/crash/apply`;
- virtual broker order APIs under `/api/virtual_alpaca/v2/*`;
- external broker imports/syncs, including `POST /api/external/sync/robinhood`;
- tax lots, external accounts, strategy buckets, universe, and classification override mutation routes.

Relevant code:

- `backend/app/main.py:79` creates the app without auth middleware.
- `backend/app/main.py:83` configures CORS.
- `backend/app/main.py:4053` toggles approval gates.
- `backend/app/main.py:4101` cancels broker orders.
- `backend/app/main.py:4154` approves pending trades.
- `backend/app/main.py:5402` applies crash rebalancing.

Risk: critical if port `8008` is reachable by anything untrusted. The approval gate is not a security boundary if unauthenticated callers can disable the gate or approve pending trades.

Recommended remediation:

- Add mandatory auth middleware for all non-health routes.
- Use a local admin token at minimum: `Authorization: Bearer <random 32+ byte token>`.
- Split scopes: read-only, trade-approval, settings/admin, import/PII.
- Require a stronger second confirmation for live-trading routes, e.g. typed phrase plus short-lived nonce.
- Add server-side authorization checks so live account gate cannot be disabled unless explicitly allowed by config.
- Add rate limiting and request logging for all mutating endpoints.

### 2. Backend And Frontend Binding

Status: first-pass fixed for default launch paths.

`backend/run.py` and the Makefile now default the backend and frontend to `127.0.0.1`. CORS still allows private LAN origins via regex and `allow_credentials=True`.

Relevant code:

- `backend/run.py:102`
- `Makefile:36`
- `Makefile:511`
- `backend/app/main.py:91`

Risk: medium after local binding; high if either service is explicitly launched with `0.0.0.0` before API auth is added. LAN exposure turns "local-only unauthenticated" into "any device on the network can attempt to hit it." CORS does not stop non-browser clients, compromised local machines, or same-LAN origins.

Recommended remediation:

- Add explicit `LAN_EXPOSE=1` or `HOST=0.0.0.0` opt-in.
- When LAN exposure is enabled, require auth and show a loud startup warning.
- Consider firewall rules allowing only the trusted workstation/phone IPs.

### 3. Vulnerable Frontend Dependency: Next.js

Status: fixed in first pass.

`next` was upgraded from `14.2.4` to `16.2.9`, dev dependencies were frozen, and `postcss` is overridden to `8.5.10`. `npm audit --audit-level=high` now reports `found 0 vulnerabilities`.

Relevant files:

- `frontend/package.json:11`
- `frontend/package-lock.json:12`

Recommended remediation:

- Run `npm run build` before shipping the Next 16 upgrade.
- Add `npm audit --audit-level=high` to CI or a Makefile security target.

### 4. Vulnerable Backend Dependencies

Status: mostly fixed in first pass.

Initial `pip-audit` found 47 known vulnerabilities across 12 packages:

| Package | Current | Findings | Minimum Fixed Versions Reported |
| --- | ---: | ---: | --- |
| `requests` | 2.32.3 | 2 | 2.32.4 / 2.33.0 |
| `msgpack` | 1.0.3 | 1 | 1.2.1 |
| `aiohttp` | 3.13.5 | 11 | 3.14.1 |
| `starlette` | 0.37.2 | 7 | 0.40.0, 0.47.2, 1.x for some 2026 advisories |
| `urllib3` | 1.26.20 | 5 | 2.7.0 |
| `diskcache` | 5.6.3 | 1 | no fix version reported |
| `torch` | 2.8.0 | 7 | 2.9.0, 2.9.1, 2.10.0 depending advisory |
| `curl-cffi` | 0.13.0 | 1 | 0.15.0 |
| `orjson` | 3.11.5 | 1 | 3.11.6 |
| `python-multipart` | 0.0.20 | 6 | 0.0.31 |
| `ujson` | 5.11.0 | 4 | 5.13.0 |
| `python-dotenv` | 1.2.1 | 1 | 1.2.2 |

Most important for this app:

- `starlette` and `python-multipart` matter because the API accepts file/form uploads.
- `requests`, `urllib3`, `aiohttp`, and `curl-cffi` matter because this app makes many outbound authenticated requests.
- `torch` matters because model loading and optional deep models are in process.

Constraints:

- `alpaca-trade-api==3.2.0` previously pinned old transitive packages including `urllib3<2`, `msgpack==1.0.3`, and `PyYAML==6.0.1`; it has now been removed.
- `lib-pybroker==1.2.12` currently pulls `diskcache==5.6.3`, which still has one advisory with no fixed version reported.
- Runtime direct dependencies are now pinned, but this is still not a hash-locked transitive lockfile.

Recommended remediation:

- Review whether `lib-pybroker` is required in the always-installed runtime environment; remove, isolate, or vendor/replace the `diskcache` usage if `GHSA-w8v5-vhqr-4h9v` is relevant to local inputs.
- Pin every dependency, including transitive dependencies, via `pip-tools` or `uv pip compile`.
- Generate hashes for reproducible installs.
- Add a separate `requirements.in` and committed lock file.
- Run `pip-audit` on the locked environment in CI.

### 5. Unsafe Local Model Deserialization

Bandit flagged unsafe deserialization:

- `backend/app/main.py:317`, `319`, `518`, `521`: `pickle.load` for HMM model/metadata.
- `backend/app/main.py:496`: `pickle.load` for PyTorch metadata.
- `backend/app/main.py:499`: `torch.load`.
- `backend/ml_engine/swing_alpha.py:360`: `pickle.load` for swing metadata.
- `backend/ml_engine/deep_models.py:394`: `pickle.load`.
- `backend/ml_engine/deep_models.py:398`: `torch.load`.

The model directory contains `.pkl` and `.pth` artifacts:

- `backend/ml_engine/saved_models/hmm_model.pkl`
- `backend/ml_engine/saved_models/hmm_metadata.pkl`
- `backend/ml_engine/saved_models/*metadata.pkl`
- `backend/ml_engine/saved_models/*.pth`

Risk: high if an attacker can write to `backend/ml_engine/saved_models/`, restore a poisoned backup, or tamper with model artifacts. Pickle and PyTorch model loading can execute code during deserialization.

Recommended remediation:

- Stop using pickle for metadata; store metadata as JSON with schema validation.
- For scikit/HMM models, prefer a safer serialization path where possible, or require artifact signatures before load.
- For PyTorch, load `state_dict` with `weights_only=True` where supported and keep model architecture in code.
- Maintain a manifest file with SHA-256 hashes for all model artifacts.
- Verify artifact hashes at API startup and before every load.
- Store model artifacts with mode `0600` or `0640`; avoid writable shared directories.
- Do not restore model artifacts from backups unless signatures/hashes match.

### 6. Secrets And Financial Data Are Plain Local Files

Secret-bearing files found:

- `backend/.env`
- `backend/data/gdrive_token.json`
- local SQLite DBs: `backend/data/trading_system.db`, `backend/data/trader.db`, `backend/data/trading_system_backup.db`

File modes:

- `backend/.env`: `-rw-r--r--`
- SQLite DBs: `-rw-r--r--`
- `backend/data/gdrive_token.json`: `-rw-------`

The `.gitignore` correctly ignores these files, and `git ls-files` did not show the checked files as tracked. However, world-readable `.env` and DB files are too permissive on a machine that may run untrusted local processes.

Risk: high local-compromise impact. The `.env` can contain Alpaca live credentials, OpenAI key, IMAP app password, Google OAuth client secrets, data vendor tokens, and other account material. The DB contains holdings, tax lots, performance, order state, and possibly imported broker data.

Recommended remediation:

- Set `chmod 600 backend/.env backend/data/*.db`.
- Keep `backend/data/gdrive_token.json` at `0600`.
- Store secrets in OS keychain or a dedicated secret manager when possible.
- Keep live Alpaca credentials disabled unless actively testing live flow.
- Rotate any secret that may have been exposed to broader filesystem users.
- Add a `make permissions-check` target that fails if secret/DB files are group/world readable.
- Add pre-commit secret scanning with a baseline.

### 7. Robinhood Sync Collects Full Credentials And Optional TOTP Secret

The UI accepts Robinhood username, password, MFA code, and optional authenticator seed:

- `frontend/src/app/page.tsx:6068`
- `frontend/src/app/page.tsx:6080`
- `frontend/src/app/page.tsx:6142`

The backend receives those values at:

- `backend/app/main.py:3465`

The code appears to pass `store_session=False`, which is good, but transporting full credentials through an unauthenticated local HTTP API is still high risk if the service is exposed to LAN or malware can intercept browser/API traffic.

Recommended remediation:

- Disable this endpoint by default behind `ENABLE_ROBINHOOD_SYNC=false`.
- Never collect/store authenticator seed if a one-time code flow is feasible.
- Require auth and local-only binding before enabling.
- Prefer manual CSV/PDF import over direct credential sync.
- If direct sync remains, isolate it in a short-lived subprocess with no access to Alpaca live credentials.

### 8. Uploaded Financial Documents May Be Sent To External LLMs

Broker PDFs/CSVs are accepted and parsed:

- `backend/app/main.py:2605`
- `backend/app/main.py:2913`

The parsing fallback can send extracted PDF text to OpenAI:

- `backend/data_ingestion/equity_lot_importer.py:175`
- `backend/data_ingestion/external_importer.py:531`

News/article scoring and batch jobs also send prompts to OpenAI when configured:

- `backend/data_ingestion/news_llm.py:109`
- `backend/data_ingestion/news_llm.py:410`
- `backend/data_ingestion/premium_llm.py:63`

Risk: privacy/data handling. Uploaded statements can contain account numbers, names, addresses, holdings, cost basis, and transaction history. Sending them to an external LLM may be acceptable only with explicit user consent.

Recommended remediation:

- Add explicit UI/server-side confirmation before external LLM parsing of financial documents.
- Default PDF parsing to deterministic/local only.
- Add `ALLOW_EXTERNAL_LLM_FOR_PII=false` default.
- Redact account numbers, addresses, names, and document IDs before LLM calls.
- Log only metadata, never raw document text.

### 9. Uploaded Broker Source Files Are Stashed Locally

`_stash_import_source()` stores uploaded broker files under `backend/data/import_sources/` for backup:

- `backend/app/main.py:2989`

This is useful for recovery but increases blast radius. These files are gitignored, but they are intentionally included in Drive backups according to docs.

Recommended remediation:

- Encrypt broker import archives at rest before backup.
- Add retention policy, e.g. delete raw import source after successful parse unless `KEEP_IMPORT_SOURCES=1`.
- Use randomized filenames and a metadata table rather than preserving user-provided names.

### 10. Other Static Analysis Findings

Bandit first-party summary:

- 1 high, 18 medium, 97 low findings.
- High: `backend/data_ingestion/premium_llm.py:85` uses SHA-1. This is for article IDs, not security; use SHA-256 anyway to remove ambiguity.
- Medium: XML parsing with `xml.etree.ElementTree.fromstring` in `backend/data_ingestion/alternative_fetcher.py:236`; replace with `defusedxml`.
- Medium: `backend/data_ingestion/macro_fetcher.py:31` has a `requests.get` without timeout.
- Medium: SQL string construction in cleanup/backup helper code. The observed table names are hardcoded, so risk is low, but this should be tightened.

Recommended remediation:

- Replace SHA-1 article ID with SHA-256 truncated to 16-24 bytes.
- Use `defusedxml.ElementTree.fromstring`.
- Add timeouts to every network call.
- Where SQL identifiers are dynamic, whitelist identifiers explicitly.

## Dependency Hardening Plan

### Python

Current `backend/requirements.txt` is direct-dependency pinned but not lockfile-grade:

- transitive dependencies are not locked;
- no hashes are enforced.

Plan:

1. Create `backend/requirements.in` with only direct dependencies.
2. Compile `backend/requirements.lock` with exact transitive pins and hashes.
3. Replace `pip install -r requirements.txt` with hash-checked lock installs for production-like runs.
4. Track `pip-audit -r backend/requirements.lock`.
5. Separate optional heavy/experimental ML dependencies from the API runtime where possible.
6. Review whether `torch` and the deep model should be installed in the API environment at all if `SERVED_MODEL=xgboost`.

Remaining dependency investigation:

- `diskcache==5.6.3` advisory with no reported fix, via `lib-pybroker`.
- Whether heavy ML and backtesting packages should be separated from the API runtime.
- Whether to move from direct pins to a hash-locked transitive lockfile.

### Node

Plan:

1. Keep `npm audit --audit-level=high` in `make sec-audit`.
2. Keep `npm run build` in the release/checklist path for frontend dependency upgrades.
3. Consider enabling Dependabot/Renovate for patched Next releases.

### Third-Party / Local Repositories

The requirements include commented guidance for a local `robin_stocks` editable install. If direct Robinhood sync is kept:

- pin the exact vetted commit;
- vendor only if licensing permits;
- scan it separately with `pip-audit`/`bandit`;
- do not allow it to store sessions or secrets on disk;
- keep it optional and disabled by default.

## Application Hardening Plan

Priority 0: immediate exposure reduction

- Bind backend/frontend to `127.0.0.1` by default.
- Add `AUTH_TOKEN` middleware for all mutating routes.
- Require auth for all read routes that expose portfolio, lots, account labels, order IDs, LLM usage, and research history.
- Keep live account approval gate on and disallow API-driven gate-off unless `ALLOW_LIVE_GATE_OFF=true`.
- Set `chmod 600 backend/.env backend/data/*.db`.

Priority 1: trading safety

- Add route-level scopes: `read`, `trade:approve`, `trade:cancel`, `settings:write`, `import:write`.
- Add audit log table for all trade-affecting actions: caller, route, account, payload hash, decision, timestamp.
- Require a short-lived confirmation token for live `approve`, `cancel`, `liquidate`, and `crash/apply`.
- Add max notional and max daily trade count guardrails independent of model suggestions.
- Add a "paper-only mode" startup flag that refuses live account initialization.

Priority 2: data and model safety

- Replace pickle metadata with JSON.
- Add signed artifact manifest.
- Verify model hashes at startup.
- Move raw broker import sources behind encryption or retention controls.
- Add document redaction before external LLM calls.

Priority 3: dependency and CI hygiene

- Run `make sec-audit` in CI before live-trading changes are merged.
- Add pre-commit secret scanning.
- Add Dependabot/Renovate for npm and Python advisories.
- Add SBOM generation for Python and Node dependency sets.

## Security Audit Target

The repo now has a `make sec-audit` target, with `make security-audit` as an alias. The scanner dependencies are pinned in `backend/requirements-security.txt` and installed with `--no-deps` into `backend/.security-venv-py314`, isolated from the trading app runtime venv. `make install` and therefore `make bootstrap` install this security-tool venv automatically.

The target runs:

```bash
cd frontend && npm audit --audit-level=high
backend/.security-venv-py314/bin/python -m pip_audit -r backend/requirements.txt
backend/.security-venv-py314/bin/python -m bandit -q -r backend/app backend/data_ingestion backend/execution backend/ml_engine backend/backtesting backend/scripts backend/run.py
backend/.security-venv-py314/bin/python -m detect_secrets scan --all-files --exclude-files '(^frontend/node_modules/|^frontend/.next/|^frontend/tsconfig.tsbuildinfo|^backend/venv/|^backend/venv-py314/|^backend/.security-venv/|^backend/.security-venv-py314/|^research-wiki/site/|^\.git/)'
```

When a Python lockfile exists, change `pip-audit` to audit that lockfile instead of `backend/requirements.txt`.

## Remediation Checklist

- [x] Change default bind host from `0.0.0.0` to `127.0.0.1`.
- [ ] Add API auth middleware and route scopes.
- [ ] Protect live-trading gate changes and trade approvals with additional confirmation.
- [x] Upgrade `next` to a non-vulnerable pinned version and rerun `npm audit`.
- [ ] Create Python lockfile with hashes.
- [x] Resolve `alpaca-trade-api` dependency conflicts or migrate away from it.
- [x] Upgrade directly vulnerable Python dependencies that have available fixed versions.
- [ ] Resolve or isolate `diskcache` via `lib-pybroker`.
- [ ] Replace pickle metadata with JSON and add model artifact hash verification.
- [x] Use `defusedxml` for SEC XML parsing.
- [x] Add timeout to the known missing `requests` call found by Bandit.
- [x] Set local file permissions for `.env`, DBs, and Google token file.
- [ ] Add an automated permissions check for `.env`, DBs, Google token file, and raw import sources.
- [ ] Add encrypted retention policy for broker source imports.
- [ ] Add explicit consent and redaction for external LLM parsing of financial documents.
- [ ] Wire `make sec-audit` into CI/pre-commit checks and run it before enabling live trading.
