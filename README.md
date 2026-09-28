# SURAKSHAI Personnel Welfare Backend

This repository contains the current SURAKSHAI backend and a retained,
deprecated WorkWell compatibility surface. The current architecture is:

```text
Personnel -> Operational data -> Phase 3C features -> Phase 4 XGBoost
          -> Phase 5 SHAP -> Phase 6 grounded welfare support
          -> Phase 7 human-reviewed welfare workflow
```

The legacy WorkWell WESAD/physiological pipeline remains available only for
compatibility and historical demos. It is not the current SURAKSHAI risk path.

A privacy-aware backend for operational personnel welfare decision support.

## 🎯 Overview

The current SURAKSHAI system uses operational records and voluntary wellness data to:

1. Build longitudinal operational features.
2. Predict a prototype `LOW`/`ELEVATED`/`HIGH` risk category with the canonical XGBoost model.
3. Explain the prediction with SHAP.
4. Retrieve grounded welfare guidance and optionally synthesize recommendations.
5. Create persistence-based welfare alerts for authorized human review.

The current model is not clinical, disciplinary, or operational-fitness software.

## 🚀 Installation

### Prerequisites
- Python 3.8+
- pip package manager

### Setup
```bash
# Create virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### Dataset
The current Phase 4 development dataset is
`data/surakshai_phase4_synthetic_risk_dataset.csv`. The old WESAD download is
only required for the deprecated WorkWell demo scripts.

## 🏃 Running the API Server

```bash
python api_server.py
```

The server will start on http://localhost:5000

## 📡 API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | API information |
| `/health` | GET | Health check |
| `/personnel/{id}/risk-prediction` | GET | Get personnel welfare-risk prediction |
| `/personnel/{id}/risk-explanation` | GET | Get SHAP risk explanation |
| `/personnel/{id}/welfare-recommendations` | GET | Get grounded welfare recommendations |
| `/welfare/alerts*` | GET/POST | Welfare alert workflow |
| `/welfare/interventions/{id}` | PATCH | Update a welfare intervention |
| `/operational/*` | GET/POST/PUT/PATCH/DELETE | Operational records |
| `/personnel/me/wellness*` | GET/POST/DELETE | Voluntary wellness assessments |

## 🧠 Machine Learning Models

- **XGBoost**: Current multiclass welfare-risk prototype (`surakshai-risk-v0.1`)

## 🔍 Explainable AI

- **SHAP** (SHapley Additive exPlanations) for global and local explanations
- Natural language explanations of predictions
- Feature importance visualization

## 🤖 Generative AI

- Phase 6 uses the dedicated `surakshai_welfare_knowledge` Chroma collection.
- Recommendations are source-grounded and fail closed when no provider is configured.
- The legacy `stress_management` collection is retained separately for compatibility.

## 📊 Welfare workflow

- Phase 7 uses persistence-based `ATTENTION` and `PRIORITY` workflow signals.
- Authorized welfare personnel acknowledge, review, plan support, schedule follow-up,
  and explicitly resolve or dismiss alerts.
- No automatic disciplinary, medical, fitness, or duty-assignment action is taken.

## ⚙️ Configuration

Edit `config.py` to customize system parameters like stress thresholds and model configurations.

## 🧪 Testing

Run the API server and use tools like Postman or curl to test endpoints:

```bash
# Health check
curl http://localhost:5000/health

# Current risk prediction (requires authentication)
curl http://localhost:5000/personnel/P001/risk-prediction
```

## HRMS personnel synchronization boundary

`POST /admin/integrations/hrms/sync` accepts normalized personnel data for controlled imports. It is restricted to `ADMIN` accounts and does not connect to an external HRMS or create application users.

Request body (maximum 500 records):

```json
{
  "records": [{
    "external_personnel_id": "HRMS-84721",
    "unit_id": "UNIT-A",
    "rank": "Officer",
    "service_years": 5,
    "posting_type": "FIELD",
    "status": "ACTIVE",
    "external_updated_at": "2026-09-27T10:00:00Z"
  }]
}
```

The optional `personnel_id` field maps a known internal record; it is never derived from the external identity. `external_source` defaults to `HRMS`. Repeating an identity updates or leaves unchanged the same personnel record. Mapping conflicts are returned in `conflicts` with HTTP 409; malformed fields, unknown fields, or oversized batches return HTTP 400. Results report `processed`, `created`, `updated`, `unchanged`, `deactivated`, conflicts, and per-record actions. Inactive source records are marked `INACTIVE`; records and their related history are retained.

## Security and data lifecycle operations

Set `APP_ENV` explicitly to `development`, `test`, or `production`. Production startup requires distinct random `JWT_SECRET_KEY` and `PSEUDONYMIZATION_SECRET` values of at least 32 characters, an explicit `CORS_ALLOWED_ORIGINS` allowlist, and a configured MongoDB URI. MongoDB TLS must be enabled for non-SRV production URIs (`MONGODB_TLS=true`); `mongodb+srv` connections use the driver TLS defaults. Access tokens use an explicit HMAC algorithm, issuer and audience, and a configurable lifetime capped at 60 minutes. Production debug mode is always disabled.

Development demo users are created only when `APP_ENV=development` and `SURAKSHAI_ENABLE_DEV_USER_SEED=1`. Synthetic seed scripts are disabled in production. The API applies bounded JSON request size and process-local rate limits; deployments with multiple workers or instances must enforce shared rate limits at the gateway. CORS defaults to local Vite origins only during development. Responses include content-type, frame, referrer, and content-security headers.

### Synthetic staging dataset

The staging dataset uses the existing personnel, operational, consent, wellness,
user-provisioning, risk, and welfare workflow services. It creates no training
data and does not alter model artifacts. Before running it, set `APP_ENV=development`,
`SURAKSHAI_ENABLE_SYNTHETIC_SEED=1`, and a `MONGODB_DATABASE` name containing a
separate `dev`, `development`, `stage`, or `staging` token. Set
`SURAKSHAI_STAGING_PERSONNEL_PASSWORD` locally to a development-only password
of at least 12 characters; the service stores only its password hash.

```powershell
$env:APP_ENV = 'development'
$env:SURAKSHAI_ENABLE_SYNTHETIC_SEED = '1'
$env:MONGODB_DATABASE = 'surakshai_staging'
$env:SURAKSHAI_STAGING_PERSONNEL_PASSWORD = '<local-development-password>'
python scripts/seed_staging_dataset.py
```

The default batch creates 30 synthetic personnel (`P900` onward), with count
configurable from 20 to 50 using `SURAKSHAI_SYNTHETIC_PERSONNEL_COUNT`.
`SYNTHETIC_DATA_RANDOM_SEED` controls generated scenario values;
`SYNTHETIC_DATA_AS_OF` can pin history dates for repeatable clean reseeding.
The batch marker can be set with `SURAKSHAI_SYNTHETIC_SEED_BATCH_ID`. A repeated batch
fails closed unless its existing records are explicitly reset first:

```powershell
$env:SURAKSHAI_RESET_SYNTHETIC_DATA = '1'
python scripts/reset_staging_dataset.py
python scripts/seed_staging_dataset.py
```

Reset deletes only documents tagged with that exact `seed_batch_id` from the
known seed-owned collections. It does not drop the database or remove audit
events, model artifacts, application configuration, demo users, or unrelated
records. The seed persists predictions and evaluates alerts through the normal
model/workflow services. It verifies dashboard aggregates, risk history
consistency, and a local SHAP explanation. Recommendation generation and PDF
report generation are not invoked by the seed because a configured
LLM-compatible recommendation provider may transmit synthetic case context to
an external service; those endpoints remain available for separately
authorized integration testing.

Retention is a governance decision and is not automated by this application. `WELLNESS_RETENTION_DAYS`, `RISK_RETENTION_DAYS`, and `AUDIT_RETENTION_DAYS` are optional policy placeholders; unset values mean no approved deletion period.

| Data domain | Current lifecycle boundary |
| --- | --- |
| Wellness assessments | Voluntary, consent-gated; user-owned deletion exists; no age-based cleanup. |
| Risk predictions | Kept for risk history and welfare workflows; no automatic cleanup. |
| Welfare alerts and interventions | Workflow state is retained; no cascading deletion or automatic cleanup. |
| Consent transitions | Append-only `GRANTED`/`REVOKED` history; current revocation does not erase prior transitions. |
| User accounts | `DISABLED` blocks login; disabled accounts remain stored and auditable. |
| Personnel and operations | Personnel deactivation preserves relationships/history; operational record deletion remains explicit and authorized. |
| Audit events | Timestamped, in-memory development store; no production durable sink or cleanup worker exists. |

The current audit service is suitable only for development; production requires an approved durable, access-controlled audit sink and retention process before deployment. The application does not provide database backups: operators own MongoDB backup scheduling, encryption, restore testing, and recovery objectives. Pseudonymization secret rotation is not automated and requires a governed migration because changing it changes derived pseudonyms.

The inactive `DEV-PATCH5-SMOKE-20260927` personnel record is controlled development verification data. It is not part of any production seed process and must be reviewed under the deployment's data governance process before any production database migration.

## 🛠️ Troubleshooting

Common issues and solutions:
- "Module not found" errors: Install all dependencies with `pip install -r requirements.txt`
- TensorFlow/Keras errors: Ensure compatible versions

## 📚 References

- WESAD Dataset: Schmidt et al., "Introducing WESAD, a Multimodal Dataset for Wearable Stress and Affect Detection"
- SHAP: Lundberg & Lee, "A Unified Approach to Interpreting Model Predictions"

## ⚠️ Disclaimer

This system is for research and educational purposes. It is not a substitute for professional medical advice, diagnosis, or treatment.

## 📄 License

This project is for educational and research purposes. Please cite the WESAD dataset if used in publications.
