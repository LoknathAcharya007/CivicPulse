# CivicPulse — Final acceptance audit (Phase 4)

**Date:** 13 November 2026

**Audited inputs:**
- `CivicPulse_Phase3_app.zip` and its documentation;
- the Phase 1 notebook `CivicPulse_Infrastructure_Cost_Prediction.ipynb`;
- the Phase 1 artifacts.

**Approach:** inspect, test, and fix only genuine issues. No features were added and nothing was restructured. Every result below comes from commands, test runs and browser sessions that were actually executed.

## 1. Outcome

| Area | Result |
|---|---|
| Application (clean install of the final ZIP) | Installs, initialises and starts; debug off by default; Phase 1 model files verified (15/15, golden samples 10/10) |
| Automated test suite | **194 passed, 0 failed**: on the Phase 3 ZIP before the fixes, and again on `CivicPulse_Final.zip` |
| Browser user journeys (Chromium) | **42 / 42 passed** on the final package, 0 server tracebacks |
| New acceptance checks (HTTP, fresh database) | **55 / 55 passed**, plus a strict cross-town estimate check (1 allowed, 4 refused) |
| Ruff / Bandit / pip-audit | All checks passed / no issues / no known vulnerabilities (39 pinned packages) |
| ML consistency (app vs Phase 1) | Model files byte-identical to Phase 1; all metrics, coefficients, cluster profiles and the confusion matrix match the Phase 1 report |
| Notebook | Runs top to bottom with **no errors or warnings** in a Colab-like environment and with the newest libraries; 19/19 self-checks; data, predictions and metrics **identical** to the files inside the app. **Not executed in Google Colab itself** (see section 5) |

## 2. Issues found and fixed

All fixes are wording or documentation. **No code, model, data or calculation was changed.**

| # | Where | Issue | Fix |
|---|---|---|---|
| 1 | Dashboard, Towns pages | "live counts" could be misread as live municipal data | Now "current counts from the CivicPulse database … not live municipal data" |
| 2 | Analytics intro | "Every figure here is read from the verified model artifacts" was not exact: the two "Your assets" panels classify the current records | Wording corrected to say exactly which figures come from Phase 1 |
| 3 | Analytics, Linear Regression | The inputs were described loosely ("town") | Now lists the actual features: log(quantity), current/target grade, damaged share, asset age, town type (coalfield area, municipal corporation), category |
| 4 | Analytics, Decision Tree | The inputs were not stated | Now lists the actual features |
| 5 | Notebook intro (markdown) | Said the web app loads "exactly these files" and must use "the same versions". A Colab run uses Colab's library versions, and the app ships its own copy of the exported models | Clarified: the app already contains the models exported by this notebook; a Colab run reproduces the same results; nothing needs copying into the app |
| 6 | Notebook, Section 2 (markdown) | Dependencies were listed but not stated as pre-installed in Colab, and there was no local install line | Added the dependency note |
| 7 | Acceptance test | One of my own new checks accepted either 200 or 404 for a cross-town estimate | Made strict (404 only) and re-run: passes |

The final notebook has **all 88 code cells unchanged** (verified programmatically); only three markdown cells were edited.

## 3. Application verification

| Requirement | Evidence |
|---|---|
| Required files present | 123 entries (files and folders) in the ZIP: application, `ml_artifacts` (identical to Phase 1), tests, requirements, `.env.example`, README and reports. No `.env`, database or caches included |
| README works from a clean environment | Followed literally in a new folder and virtual environment: install, `.env`, `init-db`, `verify-models` and `run` all succeeded |
| All 11 towns | Town explorer shows all 11 for the administrator, each with the correct asset count (Asansol 15, Durgapur 15, the other nine towns 10 each = 120), town class and Census 2011 population |
| All five categories | Each category filter returns its 24 assets, quantities show the right unit (m², metres, units, metres, m²), and a real estimate was saved through the backend for **each** category |
| Authentication and roles | Admin and official logins; wrong and empty logins show errors; officials get 403 on all four admin areas; anonymous users are redirected to sign-in |
| Town-level restrictions | The Asansol official sees only Asansol, gets 404 for an Andal asset and for Andal estimates, and opens their own Asansol estimate normally |
| Search and filters | Category, grade, town and text filters, separately and combined; other towns never appear in results |
| Upgrade-only logic | Only better grades are offered; the browser blocks a tampered downgrade; the server rejects C→C, C→D and C→E (400); the database refuses downgrades (automated test) |
| Backend calculation | Saved amounts equal the backend figure shown before saving; tampered `final_cost` and `quantity` fields are ignored; three rapid submissions create one estimate |
| Saved estimates and CSV | 5 estimates saved; the CSV export has one row per estimate with download headers; the history filter and filtered CSV work |
| Three ML models | Linear Regression (cost), Decision Tree (priority) and K-Means (groups) all appear in estimates, asset pages and Analytics; golden samples reproduced at every startup |
| Synthetic-data disclosures | On every page (footer), on the sign-in page, as a prominent notice on Analytics, and on the Towns page (Census 2011 vs synthetic assets) |
| Cost assumptions identified | Assumed rates show as **ASSUMPTION** / "Assumed composite rate"; CPWD rates show as "Official (third-party copy)" with 2023 dates; the July 2026 wage shows as "Unavailable"; the CPWD 2023 notice is on every estimate page |

## 4. Academic correctness (app vs Phase 1)

- **Model roles are correct:** Linear Regression predicts log(cost) (exp() back to rupees); the Decision Tree classifies the 5 priority levels (depth 7 from 5-fold CV); K-Means has K = 4, chosen with the Elbow Method on standardised condition, damage, age, years since maintenance and complaints.
- **Metrics:** the app shows exactly the Phase 1 values.

  | Metric | Value |
  |---|---|
  | LR R², test / held-out towns | 0.915 / 0.949 |
  | LR MAPE, test / held-out towns | 20.9% / 20.7% |
  | Baseline R², test | 0.550 |
  | Baseline MAPE, test | 93.1% |
  | DT accuracy, test / held-out | 76.4% / 72.5% |
  | Condition-only baseline accuracy, test | 67.9% |
  | Per-class precision and recall | match the report |
  | Confusion-matrix diagonal | 34, 56, 121, 104, 92 |

- **Coefficients:** all ten effects match the report (for example −55.5% per current grade, +115.6% per target grade). The elasticity of 0.911 uses the natural log of quantity, so "doubling quantity ≈ +88%" is correct.
- **Clusters:** the names and profiles come from `feature_spec.json`, and they match the profiles:
  - group 0 is ageing (27.5 years, 67% damaged);
  - group 1 is good and newer (7.6 years, 11% damaged);
  - group 2 is fair and mid-life;
  - group 3 is maintenance overdue (10.5 years since maintenance).
- **No unsupported claims:** a scan of every template, the README and the notebook markdown found no real-world-accuracy or live-data or live-price claims. The texts explicitly say the opposite (for example "do **not** establish real-world accuracy", "not live 2026 market prices").

## 5. Notebook findings

**Structure and readability**
- 163 cells: 88 code, 75 markdown, in 17 numbered sections.
- Every section starts with a plain-language explanation, suitable for a BCA reader.
- The order is logical: K-Means scaling (Section 13), then the Elbow Method and fitting with K = 4 (Section 14).

**Syllabus coverage (all nine topics)**

| Topic | Uses in the code |
|---|---|
| NumPy | 81 |
| Pandas Series / DataFrame | 9 / 16 |
| Matplotlib | 58 |
| Seaborn | 10 |
| Encoding (Ordinal, Label, One-hot) | 7 |
| Data cleaning and filtering | 52 |
| Linear Regression | ✓ |
| K-Means + Elbow Method | ✓ (11 inertia references) |
| Decision Tree | ✓ |

**Colab compatibility**
- No local paths: no `/home`, `/mnt`, drive letters or Drive mounts.
- Nothing to upload: the dataset is generated in the notebook, and the cost references are embedded.
- Nothing to install.
- Relative output folder (`artifacts/`).
- `google.colab` is imported only inside a `try/except` for the final download, so the notebook also runs outside Colab.

**Test runs**

| Environment | Result |
|---|---|
| **Colab-like:** Python 3.12.16, numpy 2.0.2, pandas 2.2.2, scikit-learn 1.6.1, matplotlib 3.10.0, seaborn 0.13.2 (typical Colab versions) | Completed in about 25 s, **0 errors and 0 warnings**, 11 charts rendered correctly (checked visually), self-check **19/19**; all datasets, golden samples and the 839 predictions **byte-identical** to the files inside the app; evaluation report identical |
| **Newest libraries:** Python 3.13.18, numpy 2.5.3, pandas 3.0.5, scikit-learn 1.9.1 | Same results: 0 errors, 19/19, metrics identical |

**Not done:** the notebook was **not executed inside Google Colab** in this audit, because there is no Colab access from this environment. The Colab-like run is strong evidence, but it is not proof. Running it once in Colab is a remaining manual step (section 8).

## 6. Known limitations
1. **Data and prices.**
   - The data is synthetic: the metrics show how well the models learned the generated data, not real-world accuracy.
   - Priority labels are simulated expert decisions.
   - Costs are at the CPWD DSR 2023 Delhi price level, with no regional Building Cost Index and no 2026 price rise. The July 2026 WB wage rate is unavailable. The 3% contingency and the streetlight, landscaping, desilting and leak-repair rates are assumptions.
2. **ML figure vs reference check.** The ML cost is often well above the reference-rate check (for example ₹91.6 lakh vs ₹41.0 lakh for one E→A road job). The model learned condition, site and mobilisation factors that the bare package rate leaves out. Prepare an explanation for the viva.
3. **Fractional streetlights.** Streetlight quantities are fractional (for example 43.1 units), as generated in Phase 1; the data was deliberately left unchanged.
4. **Inspections can lower a grade.** An inspection records the observed condition, which can be worse. Improvements and estimates are strictly upgrade-only.
5. **Not built.** Users cannot change their own password (an administrator resets it); there is no two-factor login; there is no map.
6. **Testing coverage.**
   - Browser testing used Chromium only.
   - All runs were on Linux; the Windows commands are documented but were not executed here.
   - There has been no external penetration test, so automated checks do not prove complete security.
7. **Deployment.** Rate-limit counts live in memory (one server process), the database is SQLite, and HTTPS is not configured. This is fine for a local demonstration.

## 7. Exact instructions

**Web application** (Python 3.13; extract `CivicPulse_Final.zip`, then run inside the `civicpulse` folder):
```
python -m venv .venv
.venv\Scripts\activate                       (Linux/macOS: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env                       (Linux/macOS: cp .env.example .env)
python -c "import secrets; print(secrets.token_hex(32))"
   → paste the printed key after SECRET_KEY= in .env and save
flask --app wsgi init-db --demo-users        (copy the three passwords; they are shown once)
flask --app wsgi verify-models
flask --app wsgi run                         → open http://127.0.0.1:5000
```

**Google Colab:**
1. Open colab.research.google.com and choose **File → Upload notebook**. Select `CivicPulse_ML_Notebook_Final.ipynb`.
2. Choose **Runtime → Run all**. No uploads or installs are needed.
3. Check that the independent check prints `19/19 checks passed`, and that the test results show R² 0.915 and accuracy 76.4%.
4. To re-run from scratch, choose **Runtime → Restart session and run all**.

## 8. Remaining manual steps
1. **Run the notebook once in Google Colab** and confirm 19/19 and the metrics above (not done in this audit).
2. **Install and start the app on the laptop used for the presentation** (Windows commands above). Keep the printed passwords somewhere safe.
3. Before presenting, optionally save two or three estimates so the history, reports and dashboard show data.
4. Prepare the PowerPoint (separate deliverable, not started).
5. Practise an explanation of the ML-vs-reference-rate difference and the synthetic-data limitation for the viva.

## Decision
READY FOR FINAL PRESENTATION PREPARATION.
