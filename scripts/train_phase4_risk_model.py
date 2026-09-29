"""Train the canonical SURAKSHAI Phase 4 risk model from the project CSV."""

import json
from pathlib import Path

import pandas as pd
import xgboost as xgb
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, precision_score, recall_score


BASE_DIR = Path(__file__).resolve().parents[1]
CSV_PATH = BASE_DIR / 'data' / 'surakshai_phase4_synthetic_risk_dataset.csv'
MODEL_DIR = BASE_DIR / 'models' / 'risk'

MODEL_FEATURES = [
    'duty_hours_7d_avg',
    'duty_hours_30d_avg',
    'night_duty_7d_count',
    'night_duty_30d_count',
    'night_duty_frequency_7d',
    'night_duty_frequency_30d',
    'workload_7d_avg',
    'workload_30d_avg',
    'workload_trend',
    'current_consecutive_duty_days',
    'max_consecutive_duty_days_30d',
    'days_since_most_recent_leave',
    'leave_days_30d',
    'leave_episodes_30d',
    'current_deployment_days',
    'deployment_days_30d',
    'deployment_count_30d',
    'training_hours_30d',
    'training_session_count_30d',
    'transfer_count_30d',
    'duty_hours_trend',
    'night_duty_trend',
    'average_tasks_30d',
    'high_workload_days_30d',
    'workload_duty_hours_mean_30d',
    'deployment_intensity_current',
    'sleep_quality',
    'fatigue_level',
    'perceived_stress',
    'mood_wellbeing',
    'wellness_available',
]

LABEL_MAP = {'LOW': 0, 'ELEVATED': 1, 'HIGH': 2}
LABELS = ['LOW', 'ELEVATED', 'HIGH']


def train_to_output(output_dir, training_config=None, model_version='surakshai-risk-v0.1'):
    """Run the canonical training/evaluation pipeline into an isolated directory."""
    training_config = training_config or {}
    df = pd.read_csv(CSV_PATH)
    if list(df.columns[:3]) != ['personnel_id', 'reference_date', 'recommended_split']:
        raise ValueError('Canonical CSV metadata columns do not match the required contract')
    feature_columns = [column for column in df.columns if column not in {'personnel_id', 'reference_date', 'recommended_split', 'risk_category'}]
    if feature_columns != MODEL_FEATURES:
        raise ValueError('Canonical CSV feature columns do not match the 31-feature model contract')

    working = df.copy()
    working['risk_category_code'] = working['risk_category'].map(LABEL_MAP)
    X = working[MODEL_FEATURES].copy()
    y = working['risk_category_code']

    train_mask = working['recommended_split'] == 'TRAIN'
    validation_mask = working['recommended_split'] == 'VALIDATION'
    test_mask = working['recommended_split'] == 'TEST'

    median = X[train_mask].median(numeric_only=True)
    X_train = X[train_mask].fillna(median)
    X_valid = X[validation_mask].fillna(median)
    X_test = X[test_mask].fillna(median)
    y_train = y[train_mask]
    y_valid = y[validation_mask]
    y_test = y[test_mask]

    model = xgb.XGBClassifier(
        objective='multi:softprob',
        num_class=3,
        eval_metric='mlogloss',
        random_state=42,
        n_estimators=training_config.get('n_estimators', 300),
        learning_rate=training_config.get('learning_rate', 0.05),
        max_depth=training_config.get('max_depth', 6),
        subsample=training_config.get('subsample', 0.9),
        colsample_bytree=training_config.get('colsample_bytree', 0.9),
    )
    model.fit(
        X_train,
        y_train,
        eval_set=[(X_valid, y_valid)],
        verbose=False,
    )

    preds = model.predict(X_test)
    accuracy = accuracy_score(y_test, preds)
    precision = precision_score(y_test, preds, average='macro', zero_division=0)
    recall = recall_score(y_test, preds, average='macro', zero_division=0)
    macro_f1 = f1_score(y_test, preds, average='macro', zero_division=0)
    per_class = f1_score(y_test, preds, labels=[0, 1, 2], average=None, zero_division=0)
    matrix = confusion_matrix(y_test, preds, labels=[0, 1, 2]).tolist()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / 'surakshai_risk_model.json'
    model.save_model(str(model_path))

    metadata = {
        'model_version': model_version,
        'algorithm': 'XGBoost',
        'dataset': 'surakshai_phase4_synthetic_risk_dataset.csv',
        'dataset_rows': int(len(df)),
        'feature_count': len(MODEL_FEATURES),
        'feature_version': 'surakshai-phase4-feature-v1',
        'classes': {'0': 'LOW', '1': 'ELEVATED', '2': 'HIGH'},
        'random_seed': 42,
        'feature_list': MODEL_FEATURES,
        'training_config': {
            'n_estimators': training_config.get('n_estimators', 300),
            'learning_rate': training_config.get('learning_rate', 0.05),
            'max_depth': training_config.get('max_depth', 6),
            'subsample': training_config.get('subsample', 0.9),
            'colsample_bytree': training_config.get('colsample_bytree', 0.9),
        },
        'training_metrics': {
            'accuracy': float(accuracy),
            'precision': float(precision),
            'recall': float(recall),
            'macro_f1': float(macro_f1),
            'per_class_f1': {LABELS[i]: float(value) for i, value in enumerate(per_class)},
            'confusion_matrix': matrix,
        },
    }
    metadata_path = output_dir / 'metadata.json'
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return {
        'metadata': metadata,
        'model_path': str(model_path),
        'metadata_path': str(metadata_path),
    }


def main():
    result = train_to_output(MODEL_DIR)
    metadata = result['metadata']

    print('dataset_rows=', metadata['dataset_rows'])
    print('feature_count=', len(MODEL_FEATURES))
    print('metrics=', json.dumps(metadata['training_metrics'], sort_keys=True))
    print('model_path=', result['model_path'])
    print('metadata_path=', result['metadata_path'])


if __name__ == '__main__':
    main()
