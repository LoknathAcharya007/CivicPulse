# CivicPulse Phase 2 — Backend verification report

**Date:** 13 November 2026

**Method:** the delivered Phase 2 ZIP was extracted into a new folder and installed in a clean virtual environment by following the README. Every route, the ML integration, the costing engine and the security controls were tested there. Genuine problems were fixed. The corrected project was then packaged again and the whole procedure repeated from the new ZIP in another clean folder. All results below come from commands that were actually run.

> The data is synthetic. Model metrics describe the synthetic Phase 1 data only; **no real-world municipal prediction accuracy is claimed**. Costs are at the CPWD DSR 2023 (Delhi) price level and are **not** 2026 market prices.

## 1. Test summary (final run, fresh install from the corrected ZIP)

| Check | Result |
|---|---|
| pytest | **174 passed, 0 failed** (7 files; deprecation warnings treated as errors) |
| Ruff (rules E, F, W, B, I, UP, S) | All checks passed |
| Bandit (`app`, `config.py`, `wsgi.py`) | No issues. One intentional `exec` is annotated: it runs the SHA-256-verified Phase 1 `preprocessing.py` |
| pip-audit (all 39 pinned packages) | No known vulnerabilities found |
| `flask --app wsgi verify-models` | 15/15 files verified, golden samples 10/10, cost references consistent with Phase 1 |
| Route sweep: every route × anonymous/official/admin × own/other-town/0/missing/huge ID × GET/POST, with and without a CSRF token (237 requests) | No 5xx errors, no anonymous access, no cross-town access, every POST without a token rejected, officials blocked from `/admin` |
| Live load test (Waitress, production mode, real SQLite file, 16 parallel workers) | 102/102 page requests OK; 23/23 parallel estimate saves stored; 0 server errors |
| Real browser (Playwright, production mode on `http://127.0.0.1`) | Login and all pages work; downgrade blocked in the browser; valid upgrade saved; 0 CSP or console errors |

**Tests:** 133 tests came with Phase 2; 41 were added in this verification, giving 174.

**Failures seen during verification, all fixed and rerun:**
- The baseline run of the delivered ZIP passed 133/133.
- During the fixes, the new tests caught one regression of my own: a misplaced decorator that broke `/login`. It was fixed before packaging.
- Two new tests had over-strict assertions and one test expectation needed updating. These were test problems, not app faults, and they were corrected.

## 2. Bugs found and fixed

| # | Severity | Problem found | Fix | Regression test |
|---|---|---|---|---|
| 1 | **Critical** | With `CIVICPULSE_ENV=production` and `FLASK_DEBUG=1` (or `--debug`), the Werkzeug interactive debugger was switched on. It can run arbitrary code. The production check read `app.debug` after the app had already forced it to False, so it never triggered | Production now refuses to start when debug is requested | `test_production_refuses_flask_debug_env` (3 cases) |
| 2 | High | Development mode set `app.debug = True` internally, so debug was not off by default | Debug is off in every mode; development can opt in only explicitly | `test_debug_is_off_by_default_in_every_environment` |
| 3 | High (demo risk) | Successful logins counted toward the 5-per-minute login limit. A presenter switching demo accounts 5 times in a minute would have been locked out | Only failed attempts count; password guessing is still limited | `test_successful_logins_do_not_count_towards_the_limit`, `test_failed_logins_still_limited_after_successes` |
| 4 | Medium | There was no town/region listing | Added `GET /towns/`: permitted towns with district, class, coalfield flag, Census 2011 population and area, and asset counts by grade | `test_towns_page_*` |
| 5 | Medium | Corrupted, unloadable or swapped model files, invalid `MANIFEST.json` or a missing folder gave raw Python errors | Each now gives a clear `ArtifactIntegrityError` naming the file, with a fix hint. Model types and encoders are checked, and unsafe file names in the manifest are rejected | `test_unloadable_model_*`, `test_swapped_model_files_*`, `test_invalid_manifest_*` |
| 6 | Medium | If the reference rates were edited, estimates failed with a generic "Service temporarily unavailable" | New estimates now show an explicit "paused" message (HTML and JSON). Saved estimates are unaffected | `test_historical_estimate_unchanged_after_rate_update` |
| 7 | Medium | Saving an estimate did not check that the asset's grade matched its latest assessment | Inconsistent records are refused | `test_estimate_with_inconsistent_condition_record_is_refused` |
| 8 | Low | A fixed `SECRET_KEY` string sat in `config.py` (testing configuration only) | Replaced by a random key on each run; no secret remains in the code | `test_no_hardcoded_secret_key_in_source` |
| 9 | Low | Tests read files with the system default encoding, which can fail on Windows | UTF-8 is now explicit | (whole suite) |
| 10 | Low | Indirect dependencies (Werkzeug, Jinja2, limits, SciPy and others) were unpinned | All 39 packages are now pinned to the tested versions | pip-audit clean |
| 11 | Low | Display issues: Western digit grouping for quantities and population; UTC times; town name shown twice; no explicit "not 2026 prices" note | Indian grouping, IST times, duplicate removed, price-basis notice and an **ASSUMPTION** marker on assumed rates; source links limited to http(s) | `test_currency_on_pages_uses_indian_grouping`, `test_display_helpers`, per-category test |
| 12 | Low | Lint: an unused import and unsorted imports | Fixed; `ruff.toml` added (Phase 1 artifacts excluded from linting, never edited) | Ruff clean |

## 3. ML integration results

- **Correct files:** models load only from `ml_artifacts/`, which is byte-for-byte identical to the Phase 1 artifacts (verified with `diff`). Every file in `MANIFEST.json` (15) is SHA-256 checked before use. The manifest itself is pinned through `ARTIFACT_MANIFEST_SHA256`.
- **Same preprocessing as Colab:** the app runs the verified bytes of the Phase 1 `preprocessing.py` itself. Its constants (grade order, categories, units, priority order) match the app. The model feature names match `feature_spec.json` in the same order, and the 4 encoders are preserved.
- **Golden samples:** 10/10 reproduced for cost, priority and group.
- **All 839 Phase 1 test and held-out predictions** are reproduced exactly by the app's model service.
- **No retraining:** there is no training call anywhere in `app/`. Model coefficients and cluster centres are unchanged after startup and predictions, and the files still match their checksums.
- **Integrity failures** detected with clear messages: modified model, modified `preprocessing.py`, missing file, rewritten manifest (caught by the pin), wrong golden answers, wrong scikit-learn version, unloadable file, swapped models, invalid manifest JSON, unsafe file names, missing folder. In every case the app refuses to start.
- **Models used:** Linear Regression gives the base cost, the Decision Tree gives the maintenance priority, and K-Means gives the asset group and its name.

## 4. Cost calculation verification

- **Units:** all 5 categories use their correct unit (Roads m², Drainage m, Streetlights units, Water supply pipelines m, Public parks m²). This is enforced by a database CHECK constraint and tested per category.
- **Grades:** A–E are interpreted in the same order (E worst, A best) everywhere: constants, preprocessing, database constraints and forms.
- **Upgrade paths:** all 10 paths (E→D … B→A) work, and a better target always costs more.
- **Downgrades** are rejected at four levels:
  1. the browser (only better grades are offered, plus a JS check confirmed in a real browser);
  2. server-side form validation;
  3. the service layer;
  4. database CHECK constraints.

  Same-grade and invalid targets are also rejected.
- **No double counting:** material, labour and transport are shown as "Included (₹0 added)", because CPWD composite rates already contain them. The WB wage rows are reference-only, and the database guard refuses estimates if a labour row is ever marked as used.
- **Assumptions are visible:** assumed rates (A01–A08) are marked "Assumed composite rate / Assumption" and shown as **ASSUMPTION**. CPWD rows are marked "Official (third-party copy)" with a 2023 price basis. The July 2026 wage is "Unavailable", not estimated.
- **Transparent arithmetic:** the price-level adjustment (factor 1.00) and the 3% contingency are shown as separate lines. The test checks that final = base + adjustment + contingency.
- **Traceable:** the stored input snapshot reproduces the ML base cost exactly. The reference-check components add up to the package rate. Every rate in an estimate matches its source document and date, and the model-set fingerprint is stored with the estimate.
- **History:** after a reference rate is doubled, a saved estimate's amounts, breakdown and page stay identical. New estimates are paused with a clear message.
- **Manipulation:** extra fields (`final_cost`, `quantity`, `contingency_percent`, `current_grade`, `price_index_factor`, `unit`) are ignored, and the saved amounts equal the server's calculation.
- **INR format:** all ₹ amounts on the pages are checked against the Indian grouping pattern (for example ₹94,38,540).

## 5. Security checks (implemented and tested)

| Area | What was verified |
|---|---|
| Passwords | Argon2id hashes (`$argon2id$`); the password itself is never stored or logged |
| Accounts | Only administrators create accounts; the password policy is enforced |
| Roles | Officials get 403 on every `/admin` route, for GET and POST |
| Town access (IDOR) | Another town's assets, estimates, filters, inspections, improvements, API and CSV give 404 or are excluded, and the attempt is logged |
| CSRF | Every POST route (checked automatically from the route map) rejects a missing, forged or other-session token |
| Rate limiting | 6th failed login in a minute gets 429; successful logins are not counted; 20 failed attempts per hour per username |
| Sessions | HttpOnly and SameSite=Lax cookies (Secure in production); 30-minute idle expiry, tested by letting a session lapse; a new cookie at login; sessions ended on password, role or town change and on deactivation |
| Debug | Off by default; refused in production |
| Secrets | `SECRET_KEY` comes only from the environment; the placeholder key is refused |
| SQL injection | Search, filters, login and URL IDs are harmless or rejected; the table is intact afterwards |
| XSS | Notes, names and search text are escaped; the CSP blocks inline scripts |
| Input validation | Out-of-range values, NaN and Infinity, future and pre-commissioning dates, missing fields and invalid IDs are all rejected with 400 or 404, never 500 |
| Headers | CSP, X-Frame-Options, nosniff, Referrer-Policy, no-store caching; HSTS in production; no CORS |
| Private files | `.env`, the database, `config.py` and `ml_artifacts` all return 404, including `../` path tricks |
| Errors | Generic error pages; details go only to the server log |
| Audit | Logins, failures, logouts, denied access, CSRF and rate-limit events, estimates, inspections, improvements and user changes; no passwords |
| Models | Integrity-verified before loading (section 3) |

## 6. Remaining limitations (none blocks Phase 3)

1. **Data and prices.**
   - Synthetic data: no real-world accuracy is demonstrated.
   - Prices are at the CPWD DSR 2023 Delhi level, with no regional index; the July 2026 wage rate is unavailable; the 3% contingency is an assumption.
2. **ML figure vs reference check.** The ML cost is usually higher than the reference-rate check (for example ₹91.6 lakh vs ₹41.0 lakh for one E→A job). The model learned the condition, site and mobilisation factors in the Phase 1 data, while the reference check is the bare package rate. Be ready to explain this in the viva.
3. **Fractional streetlights.** Phase 1 generated non-whole streetlight quantities (for example 43.7 units). They are kept, because the Phase 1 data must not change.
4. **Inspections can lower a grade.** An inspection records the observed condition, and assets can deteriorate. Improvements and estimates are strictly upgrade-only.
5. **Deployment.**
   - Rate-limit counts live in memory: correct for one server process, but several processes would need Redis. Behind a reverse proxy, real client addresses would need ProxyFix.
   - The per-username limit could temporarily lock an account after 20 wrong passwords in an hour.
   - Rate-limited requests are written to the audit log, bounded by the global limit of 300 per minute per address.
   - SQLite has no migration tool: schema changes need `init-db --reset`.
   - HTTPS is not configured.
6. **Features not built.** Users cannot change their own password (an administrator resets it), there is no two-factor login, and the saved-estimates list is not paginated (the CSV has everything).
7. **Testing coverage.** Tested on Linux only (Windows commands are provided but not executed here). No external penetration test has been done; passing automated checks does not prove the app is completely secure.

## 7. Commands to run locally
See README section 1. In short, inside the `civicpulse` folder:

1. `python -m venv .venv`
2. Activate the environment (`.venv\Scripts\activate` on Windows).
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and paste a generated key into `SECRET_KEY`.
5. `flask --app wsgi init-db --demo-users`
6. `flask --app wsgi verify-models`
7. `flask --app wsgi run`, then open http://127.0.0.1:5000.

## Decision
READY FOR PHASE 3 — backend verification passed.
