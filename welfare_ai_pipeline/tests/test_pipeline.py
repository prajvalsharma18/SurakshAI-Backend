import json
from pathlib import Path

from anomaly.detector import detect_current_anomaly, load_history
from schemas.ml_schema import MLModelOutput

BASE_DIR = Path(__file__).resolve().parents[1]


def test_ml_output_validation():
    data = json.loads(
        (BASE_DIR / "data" / "ml_output.json").read_text(encoding="utf-8")
    )
    result = MLModelOutput.model_validate(data)
    assert result.personnel_id == "P001"
    assert result.risk_prediction.risk_category == "ELEVATED"


def test_current_anomaly_pipeline():
    df = load_history(BASE_DIR / "data" / "historical_data.csv")
    result = detect_current_anomaly(df, "P001")
    assert result["personnel_id"] == "P001"
    assert "anomaly_detected" in result
    assert "anomaly_score" in result
