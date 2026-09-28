import pandas as pd

from trends.analyzer import analyze_trends


# Load historical data
df = pd.read_csv(
    "data/historical_data.csv"
)


# Run trend analysis
result = analyze_trends(
    history_df=df,
    personnel_id="P001"
)


print()
print("=" * 60)
print("TREND ANALYSIS")
print("=" * 60)

print(
    f"Personnel ID : "
    f"{result['personnel_id']}"
)

print(
    f"Period       : "
    f"{result['start_date']} → "
    f"{result['end_date']}"
)

print(
    f"Observations : "
    f"{result['observations']}"
)

print()
print("-" * 60)

for feature, data in result["trends"].items():

    print(
        f"{feature:35}"
        f"{data['direction']:12}"
        f"{data['change_percent']:>8.2f}%"
    )