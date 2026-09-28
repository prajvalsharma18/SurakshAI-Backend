# Welfare AI Pipeline — Phase 1

This is the downstream pipeline owned by the welfare-analysis team.

## Current milestone

ML JSON -> schema validation -> anomaly detection

The ML model itself is not implemented here. `data/ml_output.json` is the agreed mock representation of the ML teammate's output.

## Setup (Windows PowerShell)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run

```powershell
python main.py
```

## Test

```powershell
python -m pytest
```

`historical_data.csv` is synthetic demo data for development only. Replace it later with the project's real historical data source.

## Next phases

1. Trend analysis
2. Combined analysis schema
3. LangGraph workflow
4. LLM welfare recommendations
5. PDF report generation
6. FastAPI integration
