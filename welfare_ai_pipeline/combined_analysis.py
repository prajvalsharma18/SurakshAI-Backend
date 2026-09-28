from __future__ import annotations

import json
from pathlib import Path

from schemas.ml_schema import MLModelOutput
from anomaly.detector import detect_current_anomaly, load_history
from trends.analyzer import analyze_trends


# ============================================================
# PROJECT PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

ML_OUTPUT_PATH = BASE_DIR / "data" / "ml_output.json"
HISTORY_PATH = BASE_DIR / "data" / "historical_data.csv"
COMBINED_OUTPUT_PATH = BASE_DIR / "data" / "combined_analysis.json"


# ============================================================
# BUILD COMBINED ANALYSIS
# ============================================================

def build_combined_analysis() -> dict:
    """
    Combine:
    1. ML risk prediction
    2. ML contributing factors
    3. Anomaly detection
    4. Trend analysis
    """

    # --------------------------------------------------------
    # 1. Read ML output
    # --------------------------------------------------------

    if not ML_OUTPUT_PATH.exists():
        raise FileNotFoundError(
            f"ML output file not found: {ML_OUTPUT_PATH}"
        )

    with ML_OUTPUT_PATH.open("r", encoding="utf-8") as file:
        ml_json = json.load(file)

    # --------------------------------------------------------
    # 2. Validate ML output
    # --------------------------------------------------------

    ml_result = MLModelOutput.model_validate(ml_json)

    personnel_id = ml_result.personnel_id

    # --------------------------------------------------------
    # 3. Load historical data
    # --------------------------------------------------------

    history_df = load_history(HISTORY_PATH)

    # --------------------------------------------------------
    # 4. Run anomaly detection
    # --------------------------------------------------------

    anomaly_result = detect_current_anomaly(
        history_df=history_df,
        personnel_id=personnel_id
    )

    # --------------------------------------------------------
    # 5. Run trend analysis
    # --------------------------------------------------------

    trend_result = analyze_trends(
        history_df=history_df,
        personnel_id=personnel_id
    )

    # --------------------------------------------------------
    # 6. Get contributing factors
    # --------------------------------------------------------

    contributing_factors = [
        factor.model_dump()
        for factor in ml_result.explanation.top_contributing_factors
    ]

    # --------------------------------------------------------
    # 7. Build combined object
    # --------------------------------------------------------

    combined_analysis = {
        "personnel_id": personnel_id,

        "reference_date": ml_result.reference_date.isoformat(),

        "risk_assessment": {
            "risk_category": (
                ml_result.risk_prediction.risk_category
            ),

            "risk_probabilities": (
                ml_result
                .risk_prediction
                .risk_probabilities
                .model_dump()
            ),

            "model_version": (
                ml_result.risk_prediction.model_version
            ),

            "feature_version": (
                ml_result.risk_prediction.feature_version
            ),

            "data_mode": (
                ml_result.risk_prediction.data_mode
            ),

            "wellness_available": (
                ml_result.risk_prediction.wellness_available
            ),
        },

        "contributing_factors": contributing_factors,

        "anomaly_analysis": anomaly_result,

        "trend_analysis": trend_result,
    }

    # --------------------------------------------------------
    # 8. Save combined JSON
    # --------------------------------------------------------

    COMBINED_OUTPUT_PATH.write_text(
        json.dumps(
            combined_analysis,
            indent=2
        ),
        encoding="utf-8"
    )

    return combined_analysis


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    result = build_combined_analysis()

    print()
    print("=" * 70)
    print("COMBINED PERSONNEL ANALYSIS")
    print("=" * 70)

    # --------------------------------------------------------
    # BASIC INFORMATION
    # --------------------------------------------------------

    print(
        f"Personnel ID  : {result['personnel_id']}"
    )

    print(
        f"Reference Date: {result['reference_date']}"
    )

    # --------------------------------------------------------
    # RISK ASSESSMENT
    # --------------------------------------------------------

    print()
    print("RISK ASSESSMENT")
    print("-" * 70)

    print(
        f"Risk Category : "
        f"{result['risk_assessment']['risk_category']}"
    )

    probabilities = result["risk_assessment"]["risk_probabilities"]

    print(
        f"LOW           : {probabilities['LOW']:.2%}"
    )

    print(
        f"ELEVATED      : {probabilities['ELEVATED']:.2%}"
    )

    print(
        f"HIGH          : {probabilities['HIGH']:.2%}"
    )

    # --------------------------------------------------------
    # CONTRIBUTING FACTORS
    # --------------------------------------------------------

    print()
    print("TOP CONTRIBUTING FACTORS")
    print("-" * 70)

    for factor in result["contributing_factors"]:

        print(
            f"{factor['feature']:35}"
            f"{factor['contribution']:>8.2f}   "
            f"{factor['direction']}"
        )

    # --------------------------------------------------------
    # ANOMALY ANALYSIS
    # --------------------------------------------------------

    print()
    print("ANOMALY ANALYSIS")
    print("-" * 70)

    anomaly = result["anomaly_analysis"]

    print(
        f"Status        : {anomaly['anomaly_label']}"
    )

    print(
        f"Detected      : {anomaly['anomaly_detected']}"
    )

    print(
        f"Score         : {anomaly['anomaly_score']}"
    )

    # --------------------------------------------------------
    # TREND ANALYSIS
    # --------------------------------------------------------

    print()
    print("TREND ANALYSIS")
    print("-" * 70)

    trends = result["trend_analysis"]["trends"]

    for feature, data in trends.items():

        print(
            f"{feature:35}"
            f"{data['direction']:12}"
            f"{data['change_percent']:>8.2f}%"
        )

    # --------------------------------------------------------
    # OUTPUT
    # --------------------------------------------------------

    print()
    print("=" * 70)

    print(
        "Combined analysis saved at:"
    )

    print(
        COMBINED_OUTPUT_PATH
    )

    print("=" * 70)