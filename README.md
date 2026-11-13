# CivicPulse — Infrastructure Cost Prediction for Paschim Bardhaman

**FSP Project, Team 13:** Loknath Acharya, Binayak Mandal, Anurag Tiwari, Badal Kumar

CivicPulse estimates the cost of improving municipal infrastructure assets to a better condition grade. It covers five categories, each with its own unit:

| Category | Unit |
|---|---|
| Roads | m² |
| Drainage | metres |
| Streetlights | units |
| Water supply pipelines | metres |
| Public parks | m² |

It also predicts maintenance priority and groups similar assets. The assets are spread across 11 towns of Paschim Bardhaman, West Bengal.

It uses three machine-learning models, built in a Google Colab notebook and used unchanged by a Flask web application:

| Model | Task |
|---|---|
| Linear Regression | Improvement cost (on log(cost), converted back to rupees) |
| Decision Tree | Maintenance priority: Very Low, Low, Moderate, High, Critical |
| K-Means (K = 4, Elbow Method) | Groups of similar assets |

> **Honest data notice.**
> - All assessment records and the 120 assets are **synthetic and illustrative**, not real municipal data.
> - Model scores describe performance on that synthetic data and do **not** show real-world accuracy.
> - Costs are at the **CPWD DSR 2023 (Delhi) price level**. They are not 2026 market prices, and no regional index is applied. Assumed rates are labelled **ASSUMPTION**.

## Submission files

| File | What it is |
|---|---|
| `CivicPulse_Final.zip` | The complete web application, with the Phase 1 models, tests and documentation |
| `CivicPulse_ML_Notebook_Final.ipynb` | The machine-learning notebook (separate; run it in Google Colab) |
| `CivicPulse_Final_README.md` | This file |
| `CivicPulse_Final_Acceptance_Report.md` | The final acceptance audit |

The PowerPoint presentation is prepared separately.

---

## A. Run the ML notebook in Google Colab

1. Open https://colab.research.google.com and choose **File → Upload notebook**. Select `CivicPulse_ML_Notebook_Final.ipynb`.
2. Choose **Runtime → Run all**. No files need to be uploaded and nothing needs to be installed: the dataset is generated inside the notebook, and NumPy, Pandas, Matplotlib, Seaborn and scikit-learn come pre-installed in Colab.
3. Wait for it to finish (about half a minute on a normal CPU runtime; no GPU needed).
4. Check the result. Near the end, the **independent check** cell should print `19/19 checks passed`. The last cell downloads `artifacts.zip`, which is optional.
5. To re-run from scratch, choose **Runtime → Restart session and run all**.

| What you should see | Expected value |
|---|---|
| Cost model, test set | R² 0.915, MAPE 20.9% (baseline: R² 0.550, MAPE 93.1%) |
| Cost model, held-out towns | R² 0.949 |
| Priority model, test set | accuracy 76.4% (condition-only baseline 67.9%) |
| Priority model, held-out towns | accuracy 72.5% |
| Asset groups | K = 4 |

These numbers are reproducible: random seed 42 is fixed.

**You do not need to copy anything from Colab into the web app.** The app already contains the models exported by this notebook. A Colab run reproduces the same data, predictions and metrics, even though Colab's library versions differ.

The notebook covers all nine syllabus topics: NumPy; Pandas Series and DataFrame; Matplotlib; Seaborn; encoding; data cleaning and filtering; Linear Regression; K-Means with the Elbow Method; and Decision Tree.

## B. Run the web application on your computer

You need **Python 3.13** (`python --version` should print 3.13.x). Extract `CivicPulse_Final.zip`, then run these commands inside the `civicpulse` folder.

**Windows (PowerShell or Command Prompt)**
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```
**Linux / macOS**
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```
Open `.env` in a text editor. Replace `replace-with-64-hex-characters` after `SECRET_KEY=` with the 64 characters the last command printed, then save.

Then:
```
flask --app wsgi init-db --demo-users
flask --app wsgi verify-models
flask --app wsgi run
```

- `init-db --demo-users` prints three accounts with random passwords. They are shown **once**, so copy them.
- `verify-models` should print `Files verified : 15`, three `True` values for the golden samples, and `Cost references : consistent with Phase 1`.
- `run` starts the app. Open **http://127.0.0.1:5000** in a browser.

| Account | Role | Can see |
|---|---|---|
| `admin` | Administrator | All 11 towns and the admin area |
| `official.andal` | Municipal Official | Andal, Ukhra and Dignala |
| `official.asansol` | Municipal Official | Asansol |

Optional:
- `python -m pytest` runs the 194 automated tests (about 5 minutes).
- `flask --app wsgi init-db --reset --demo-users` starts again with an empty database.

### Suggested demonstration path (about 5 minutes)
1. Sign in as `official.andal`. The dashboard shows only that official's towns.
2. Open **Assets**, filter by **Roads** and grade **E**, and open an asset.
3. On the condition ladder, click a better grade. You'll see every upgrade option with the backend's cost, and the full breakdown with rate sources and the ASSUMPTION labels.
4. Click **Save estimate**. The saved estimate shows who made it, when, and the exact inputs used.
5. Open **Estimates**, filter, and use **Export CSV**.
6. Open **Analytics** to show all three models (metrics, actual-vs-predicted chart, confusion matrix, Elbow chart, asset groups).
7. Sign out, sign in as `admin`, and show **Towns** (all 11) and **Admin** (users, audit log, cost references, model integrity).

## C. How the pieces fit
- **Phase 1 (the notebook)** generates the synthetic dataset, cleans and encodes it, trains the three models, and exports them with a shared preprocessing file, golden samples and SHA-256 fingerprints.
- **Phase 2 (the backend)** loads only those verified files. It never retrains. It refuses to start if a file changes or the golden samples are not reproduced. It computes every cost on the server, with upgrade-only rules enforced in the browser, the server and the database.
- **Phase 3 (the frontend)** provides the pages for towns, assets, estimates, analytics, reports and administration.
