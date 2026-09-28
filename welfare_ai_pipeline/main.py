import json
from pathlib import Path

from schemas.ml_schema import MLModelOutput
from anomaly.detector import detect_current_anomaly, load_history

BASE_DIR = Path(__file__).resolve().parent
ML_OUTPUT_PATH = BASE_DIR / "data" / "ml_output.json"
HISTORY_PATH = BASE_DIR / "data" / "historical_data.csv"


def main() -> None:
    # 1. Read and validate the ML team's output.
    ml_json = json.loads(ML_OUTPUT_PATH.read_text(encoding="utf-8"))
    ml_result = MLModelOutput.model_validate(ml_json)

    print("=" * 60)
    print("ML OUTPUT VALIDATED")
    print("=" * 60)
    print(f"Personnel ID : {ml_result.personnel_id}")
    print(f"Risk         : {ml_result.risk_prediction.risk_category}")
    print(
        "ELEVATED prob: "
        f"{ml_result.risk_prediction.risk_probabilities.ELEVATED:.0%}"
    )

    # 2. Load historical observations for anomaly detection.
    history_df = load_history(HISTORY_PATH)
    anomaly_result = detect_current_anomaly(history_df, ml_result.personnel_id)

    print("\n" + "=" * 60)
    print("ANOMALY ANALYSIS")
    print("=" * 60)
    print(f"Anomaly       : {anomaly_result['anomaly_label']}")
    print(f"Anomaly score : {anomaly_result['anomaly_score']}")
    print(f"Reference date: {anomaly_result['reference_date']}")


if __name__ == "__main__":
    main()
