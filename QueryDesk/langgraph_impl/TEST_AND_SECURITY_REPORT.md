# End-to-End Test and Security Review

**Assessment date:** 15 November 2026  
**Scope:** Current local project files, automated tests, browser flow, and dependency advisories. This is not a penetration test or a certification of a production deployment.

## Executive summary

The local regression and browser suites pass after remediation. The vulnerable frontend test dependencies were upgraded, Python packaging tools were updated, first-administrator setup now fails closed without a secret token, and row-level security is enabled in the active local configuration. The bundled launcher binds to loopback; no remotely exploitable vulnerability was confirmed for that supported local launch path.

## End-to-end and regression results

| Check | Result | Notes |
|---|---|---|
| Python/API suite | **PASS** — 238 tests | `python -m unittest discover -s tests -q`; completed in 129.4 seconds. |
| Frontend unit tests | **PASS** — 40 tests | `npm test`; 6 test files passed. |
| Browser E2E | **PASS** — 1 test | `npm run test:e2e`; Playwright exercised token-protected administrator setup, table confirmation, SQL approval, and results using the local UI/API, sample SQLite data, and deterministic model behavior. |
| Frontend production build | **PASS** | `npm run build` (TypeScript check and Vite production build). |
| Python dependency consistency | **PASS** | `pip check`; no broken requirements. |
| Python dependency vulnerability scans | **PASS** | `pip-audit` found no known vulnerabilities in the installed environment or locked application dependencies after updating `pip` and `setuptools`. The local editable project itself is skipped because it is not published on PyPI. |
| npm production dependency scan | **PASS** | `npm audit --omit=dev`; zero vulnerabilities. |
| Full npm dependency scan | **PASS** | `npm audit`; zero vulnerabilities after upgrading Vitest to 5.1.0. |

The Python TLS-policy tests emitted deprecation warnings for intentionally configured TLS 1.0/1.1 test cases; they did not fail. No live LLM-provider or remote warehouse calls were made. These tests do not establish live connector compatibility, model accuracy, or multi-process safety.

## Security review findings

| # | Severity | File | Lines | Vulnerability | Confidence |
|---|----------|------|-------|---------------|------------|
| 1 | 🔴 CRITICAL | `web/package-lock.json` (via Vitest) | dependency tree | At assessment time, `tinypool` had critical worker-options prototype-pollution/RCE advisories (GHSA-5gmw-xhrv-c9v3 and GHSA-85c8-ppgw-ccpr). **Resolved:** Vitest was upgraded to 5.1.0 and the full npm audit is now clean. | 9/10 |
| 2 | 🟠 HIGH | `api.py`, `auth.py` | `api.py:775-781`; `auth.py:70,83-90` | At assessment time, first-admin setup did not require a separate secret. **Resolved:** setup now requires a configured token of 32–256 characters, compared in constant time; missing configuration fails closed. | 9/10 |
| 3 | 🟠 HIGH | `.env`, `api.py` | `.env:40`; `api.py:756` | `AUTH_COOKIE_SECURE=false` omits the `Secure` cookie attribute. **Accepted for local use only:** the provided launcher binds to loopback and serves HTTP. Network deployments must use HTTPS and set `AUTH_COOKIE_SECURE=true`; do not expose this local configuration to untrusted networks. | 9/10 |
| 4 | 🟡 MEDIUM | `web/package-lock.json` (via Vitest) | dependency tree | At assessment time, `@vitest/mocker` had path-traversal/arbitrary-file-read and code-injection advisories (GHSA-82fw-gwwq-j7x9 and GHSA-qxg6-89mg-rqv8). **Resolved:** dependency upgrade; full npm audit is now clean. | 9/10 |
| 5 | 🟡 MEDIUM | `.env`, `row_security.py` | `.env:48`; `row_security.py:81-90,121` | Row-level security had been disabled in the active configuration. **Resolved for this installation:** it is now enabled. Members need appropriate row-policy group/user attributes; otherwise they see no rows. | 8/10 |

The cookie setting remains intentionally HTTP-compatible for the loopback preview, not for network deployment. The API and UI must remain loopback-only in this mode. The local `.env` already has an administrator, so adding a setup token is needed only if administrator setup is required again; configure a new random value before using an empty instance. Restart the backend for environment changes to take effect.

## Recommended actions

1. **Protect the setup token.** Generate a random secret of 32–256 characters, store it only in the ignored `.env`, enter it once during first-admin setup, and remove it afterward.
2. **Use HTTPS for network deployment.** Set `AUTH_COOKIE_SECURE=true` and ensure plaintext HTTP is not reachable by users. The bundled launch scripts intentionally bind to loopback.
3. **Assign row-policy attributes to members.** Row security is enabled in the active configuration; assign the appropriate region/group attributes before members query protected data.
4. **Keep deployment limits explicit.** The app's job queue and approval state remain process-local; multi-instance deployment is not validated or supported by these tests.

## Reproduction commands

Run from this project directory:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pip_audit
.\.venv\Scripts\python.exe -m pip_audit -r requirements-lock.txt
Push-Location web
npm test
npm run build
npm run test:e2e
npm audit --omit=dev
npm audit
Pop-Location
```

`pip-audit` was installed into the existing local virtual environment to perform the scan; no project dependency manifest was changed. The virtual environment's `pip` and `setuptools` were updated as part of remediation.
