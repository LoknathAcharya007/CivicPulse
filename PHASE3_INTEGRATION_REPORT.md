# CivicPulse Phase 3 — Frontend and backend integration test report

**Date:** 13 November 2026

**Scope:** a complete frontend built on the verified Phase 2 backend (`CivicPulse_Phase2_backend_verified.zip`). The Phase 1 models, datasets, cost references and cost calculations are **unchanged**: `ml_artifacts/` is byte-for-byte identical to the Phase 1 artifacts, and the golden samples are 10/10.

All results below come from commands and browser runs that were actually executed.

## 1. Results summary

| Check | Result |
|---|---|
| Backend and frontend pytest suite | **194 passed, 0 failed** (174 Phase 2 tests + 20 new frontend tests; deprecation warnings treated as errors) |
| Real-browser user journeys (Playwright / Chromium, fresh database) | **42 / 42 passed**, 0 server tracebacks |
| Page audit at 1440×900, 820×1180 and 390×844 | 0 pages with horizontal scrolling (one overflow found and fixed); 98 internal links checked, 0 broken; every page has a unique title and a meta description; every form control is labelled |
| Browser console | No errors or warnings, except the two **intentional** 400 responses in the journeys (a forged downgrade and an out-of-range inspection value) |
| Ruff / Bandit / pip-audit | All checks passed / no issues / no known vulnerabilities |
| `flask --app wsgi verify-models` | 15/15 files verified, golden samples reproduced, cost references consistent |

**Changes to Phase 2 tests:** 13 tests looked for the old plain-HTML markup (a `<select>` became radio cards, `<main>` and tables gained attributes) or the old route list. They were updated to check the **same behaviour** against the new markup. No assertion about security, permissions or calculations was weakened.

## 2. User journeys tested in a real browser (42 checks)

| # | Journey | Checks |
|---|---|---|
| 1 | Sign in | Skip link is the first focusable element; a wrong password shows a clear error; empty fields show field errors |
| 2 | Official's view | Dashboard lists only the official's three towns (KPI = 3); no Admin link; `/admin` returns 403; the town explorer shows 3 towns |
| 3 | Asset search and filters | Category filter applies on change and returns only Roads; adding a grade filter keeps the category; text search works; other towns' assets never appear |
| 4 | Asset → upgrade → estimate | Detail page shows grade, priority and group; the ladder offers only better grades; a ladder link preselects the target; changing the option shows that option's backend breakdown; the save label follows the choice; the CPWD 2023 notice is shown; **the saved amount equals the backend figure shown before saving** (₹20,29,963); three rapid submissions create **one** estimate |
| 5 | Downgrade attempts | A tampered radio value (E) is blocked in the browser; a forged POST with E is rejected by the server (400) |
| 6 | History and CSV | Filtering by target grade works; CSV export downloads the filtered rows |
| 7 | Condition updates | An out-of-range inspection value is rejected with a message; the improvement form offers only C, B and A for a D asset; recording an improvement updates the grade to B |
| 8 | Navigation and session | Refreshing a deep link works; logout returns to sign-in; protected pages then redirect to sign-in |
| 9 | Administration | Users table shown; a weak password is rejected with a message; an official account is created; the audit log filter shows the creation; Analytics renders the scatter, confusion matrix and Elbow chart |
| 10 | Mobile | Navigation collapses behind "Menu"; the button sets `aria-expanded`; Escape closes it |

## 3. New automated tests (`tests/test_frontend.py`, 20 tests)

- **Analytics uses only Phase 1 figures:**
  - R² 0.915 and 0.949 and accuracy 76.4% match `feature_spec.json`;
  - per-class precision and recall match `evaluation_report.md`;
  - the scatter has exactly 318 test + 194 held-out points from the saved predictions;
  - the live charts count only the user's towns.
- **Estimate history:** search and filters (town, category, target grade, text), with invalid values rejected (400) and other towns' filters refused (404); CSV export uses the same filters.
- **Asset picker:** lists only permitted assets; grade A assets cannot be selected.
- **Target preselection:** `?target=` is validated. A downgrade or invalid value falls back to the nearest upgrade, and the POST is still checked by the server.
- **Saved estimate pages** show exactly the stored amounts, the author and the time in IST.
- **Every page** has a unique title and a meta description, and contains **no inline scripts, inline styles or inline event handlers**, so the strict CSP stays intact.
- **`app.js`** contains no `innerHTML`/`outerHTML`/`insertAdjacentHTML`, `eval`, `fetch` or cost arithmetic.
- **Navigation, assets and search:** officials' navigation has no admin link; the favicon, fonts, CSS and JS are served with the right types; town search is parameterised and escaped.
- **Chart geometry:** percentages, stacked bars, log-scale scatter bounds, confusion-matrix shading and diverging bars.

## 4. Issues found during Phase 3 testing and fixed

| Issue | Fix |
|---|---|
| The System page overflowed by 92 px on mobile (64-character model fingerprint) | Long codes now wrap |
| Font preload produced a browser warning | Preload removed (fonts still load locally) |
| The estimate total panel stretched to the full height of the breakdown | Columns align to the top; the total panel stays in view while scrolling |
| Wage reference rows were cramped inside the breakdown | Moved into a collapsible "Reference wage rates — not added" table |
| Long feature names were cut off on the Analytics charts; the metric tiles were cramped; the legend dots were the same colour | Labels wrap; tiles flow responsively; legend colours fixed |
| Option card text overflowed on phones; "HIGH/CRITICAL" overflowed the town cards | Text now wraps |
| The condition ladder read A→E | Reordered E→A, so an upgrade moves to the right |

## 5. Features completed
- Sign-in page with validation, loading state, rate limiting and no demo credentials.
- Dashboard with real KPIs and charts:
  - condition grades, maintenance priority and category distribution;
  - critical assets;
  - recent estimates and recent assessments.
- Town explorer with search and filters, and real statistics for every town (all 11 for an administrator).
- Asset explorer: search, plus town, category and condition filters, unit-aware quantities, grade badges and priorities.
- Asset detail: condition ladder (upgrades only), assessment and maintenance history, Decision Tree and K-Means outputs, saved estimates, inspection and improvement forms.
- Cost estimation in four steps, with the backend's transparent breakdown:
  - base cost, price-level adjustment, included material/labour/transport, contingency and final figure;
  - rate sources, verification status (Official / Verified / **ASSUMPTION** / Unavailable) and dates;
  - the "not 2026 market prices" notice.
- Saved estimate page (print-friendly), plus estimate history with filters and CSV export.
- Analytics for all three models, clearly marked as synthetic data.
- Reports with per-town totals and CSV export.
- Administration: users, roles and towns, password reset, audit log, cost references, system and model status.
- Responsive design (desktop, tablet, mobile); keyboard access (skip link, focus styles, Escape closes the menu); favicon; page titles and descriptions; styled error pages; empty states; loading states; double-submit protection.

## 6. Known limitations
1. **Data and prices.** The data is synthetic; metrics do not show real-world accuracy. Costs are at the CPWD DSR 2023 Delhi price level (no regional index; the July 2026 wage rate is unavailable; the 3% contingency is an assumption).
2. **ML figure vs reference check.** The ML cost is usually above the reference-rate check, because the model learned condition, site and mobilisation factors that the plain package rate excludes. Be ready to explain this in the viva.
3. **Fractional streetlights.** Streetlight quantities are fractional (for example 43.1 units), as generated in Phase 1; the data was not changed.
4. **Not built.**
   - Users cannot change their own password (an administrator resets it).
   - There is no two-factor login.
   - There is no map view, because asset coordinates are illustrative.
5. **Browser compatibility.**
   - Without JavaScript, the estimate page shows only the preselected scenario's breakdown. The ladder links switch between scenarios.
   - The "Apply on change" filters also have an Apply button.
6. **Testing coverage.**
   - Browser testing used Chromium only; Firefox and Safari were not tested here.
   - Everything ran on Linux; the Windows commands are documented but not executed.
   - There has been no external penetration test.
7. **Deployment** limits are unchanged from Phase 2: rate-limit counts live in memory (one server process), SQLite is used, and HTTPS is not configured.

## 7. Commands to run locally
See README section 1. In short, inside the `civicpulse` folder:

1. `python -m venv .venv`
2. Activate the environment (`.venv\Scripts\activate` on Windows).
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and paste a generated key into `SECRET_KEY`.
5. `flask --app wsgi init-db --demo-users`
6. `flask --app wsgi verify-models`
7. `flask --app wsgi run`, then open http://127.0.0.1:5000.
