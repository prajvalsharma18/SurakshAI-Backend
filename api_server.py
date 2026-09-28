"""
SURAKSHAI Personnel Welfare Intelligence API
"""

from datetime import datetime
from io import BytesIO

from flask import Flask, g, request, jsonify, send_file
from flask_cors import CORS
from werkzeug.exceptions import HTTPException

from config import (
    ADMIN_USER_RATE_LIMIT,
    HRMS_SYNC_RATE_LIMIT,
    LOGIN_RATE_LIMIT,
    RATE_LIMIT_WINDOW_SECONDS,
    get_app_env,
    get_cors_allowed_origins,
    get_debug_mode,
    get_max_request_size_bytes,
    is_development_seed_enabled,
    validate_production_configuration,
)

from src.db.mongodb import close_mongo_clients, get_database
from src.db.repositories.operational_repository import OperationalRepository
from src.db.repositories.personnel_repository import PersonnelRepository
from src.db.repositories.consent_repository import ConsentRepository
from src.db.repositories.user_repository import UserRepository
from src.db.repositories.wellness_repository import (
    DuplicateWellnessAssessment,
    WellnessRepository,
)
from src.schemas.operational import OPERATIONAL_SCHEMAS
from src.schemas.personnel import PERSONNEL_STATUSES
from src.services.operational_service import OperationalService
from src.features.feature_service import FeatureService
from src.services.wellness_service import (
    WellnessAuthorizationError,
    WellnessService,
)

from src.ml.risk_service import RiskService
from src.ml.recommendation_service import (
    RecommendationService as WelfareRecommendationService,
    WelfareRecommendationError,
)

from src.welfare.repositories import (
    RiskPredictionRepository,
    WelfareAlertRepository,
    WelfareInterventionRepository,
)
from src.welfare.workflow_service import (
    WelfareWorkflowService,
    WorkflowError,
)

from src.security.auth import authenticate_user, require_auth
from src.security.audit import RESULT_DENIED, RESULT_FAILURE, RESULT_SUCCESS, get_audit_service
from src.security.consent import ConsentService
from src.security.permissions import (
    PERMISSION_MANAGE_OPERATIONAL_RECORDS,
    PERMISSION_VIEW_OPERATIONAL_RECORDS,
    PERMISSION_VIEW_OPERATIONAL_SUMMARY,
    PERMISSION_SUBMIT_WELLNESS,
    PERMISSION_VIEW_OWN_WELLNESS,
)
from src.security.rbac import (
    require_operational_personnel_access,
    require_permission,
    require_wellness_personnel_access,
)
from src.security.token import TokenManager
from src.reports.welfare_report_builder import WelfareReportBuilder
from src.reports.welfare_report_service import (
    WelfareReportAuthorizationError,
    WelfareReportGenerationError,
    WelfareReportService,
    WelfareReportValidationError,
)
from src.services.risk_history_service import RiskHistoryService
from src.services.welfare_dashboard_service import WelfareDashboardService
from src.services.user_service import UserService
from src.services.hrms_sync_service import HRMSSyncService
from src.integrations.hrms import NormalizedPayloadHRMSAdapter
from src.schemas.hrms import normalize_hrms_sync_request
from src.security.permissions import PERMISSION_MANAGE_USERS
from src.security.demo_users import DEVELOPMENT_USERS
from src.security.rate_limit import rate_limit
import os


app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = get_max_request_size_bytes()
CORS(
    app,
    origins=get_cors_allowed_origins(),
    allow_headers=['Authorization', 'Content-Type'],
    methods=['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
)


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Content-Security-Policy', "default-src 'none'; frame-ancestors 'none'")
    return response


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if isinstance(error, HTTPException):
        return jsonify({'error': error.name}), error.code
    app.logger.error('Unhandled request failure (%s)', type(error).__name__)
    return jsonify({'error': 'Internal server error'}), 500


# -------------------------------------------------------------------
# Shared services
# -------------------------------------------------------------------

audit_service = get_audit_service()
user_service = UserService(
    repository=UserRepository(),
    personnel_repository=PersonnelRepository(),
    audit_service=audit_service,
)
_development_bootstrap_complete = False


def _ensure_development_users():
    global _development_bootstrap_complete
    if _development_bootstrap_complete or not is_development_seed_enabled():
        return
    try:
        user_service.bootstrap_development_users(DEVELOPMENT_USERS)
        _development_bootstrap_complete = True
    except (RuntimeError, ValueError):
        # Development may be started before MongoDB is available. Authentication
        # remains database-backed and fails closed until the store is reachable.
        close_mongo_clients()
consent_service = ConsentService(
    repository=ConsentRepository(),
    audit_service=audit_service,
)

operational_service = None
personnel_service = None
feature_service = None
wellness_service = None
risk_service = None
risk_history_service = None
welfare_dashboard_service = None
welfare_recommendation_service = None
welfare_workflow_service = None
welfare_report_service = None
hrms_sync_service = None


def _get_operational_service():
    global operational_service

    if operational_service is None:
        operational_service = OperationalService(
            OperationalRepository(get_database())
        )

    return operational_service


def _get_personnel_service():
    global personnel_service

    if personnel_service is None:
        from src.services.personnel_service import PersonnelService

        personnel_service = PersonnelService(
            repository=PersonnelRepository(get_database()),
            audit_service=audit_service,
        )

    return personnel_service


def _get_hrms_sync_service():
    global hrms_sync_service
    if hrms_sync_service is None:
        service = _get_personnel_service()
        hrms_sync_service = HRMSSyncService(
            personnel_repository=service.repository,
            personnel_service=service,
            audit_service=audit_service,
        )
    return hrms_sync_service


def _get_feature_service():
    global feature_service

    if feature_service is None:
        feature_service = FeatureService(
            _get_operational_service().repository
        )

    return feature_service


def _get_risk_history_service():
    global risk_history_service

    if risk_history_service is None:
        risk_history_service = RiskHistoryService(
            RiskPredictionRepository(get_database()),
            audit_service=audit_service,
        )

    return risk_history_service


def _get_welfare_dashboard_service():
    global welfare_dashboard_service

    if welfare_dashboard_service is None:
        database = get_database()
        welfare_dashboard_service = WelfareDashboardService(
            personnel_repository=_get_personnel_service().repository,
            risk_repository=RiskPredictionRepository(database),
            alert_repository=WelfareAlertRepository(database),
            intervention_repository=WelfareInterventionRepository(database),
            audit_service=audit_service,
        )

    return welfare_dashboard_service


def _get_wellness_service():
    global wellness_service

    if wellness_service is None:
        wellness_service = WellnessService(
            WellnessRepository(get_database()),
            consent_service,
        )

    return wellness_service


def _get_risk_service():
    global risk_service

    if risk_service is None:
        risk_service = RiskService(
            feature_service=_get_feature_service(),
            wellness_service=_get_wellness_service(),
            consent_service=consent_service,
            audit_service=audit_service,
        )

    return risk_service


def _get_welfare_recommendation_service():
    global welfare_recommendation_service

    if welfare_recommendation_service is None:
        welfare_recommendation_service = WelfareRecommendationService(
            risk_service=_get_risk_service(),
            audit_service=audit_service,
        )

    return welfare_recommendation_service


def _get_welfare_workflow_service():
    global welfare_workflow_service

    if welfare_workflow_service is None:
        database = get_database()

        welfare_workflow_service = WelfareWorkflowService(
            risk_service=_get_risk_service(),
            prediction_repository=RiskPredictionRepository(database),
            alert_repository=WelfareAlertRepository(database),
            intervention_repository=WelfareInterventionRepository(database),
            audit_service=audit_service,
        )

    return welfare_workflow_service


def _get_welfare_report_service():
    global welfare_report_service

    if welfare_report_service is None:
        welfare_report_service = WelfareReportService(
            feature_service=_get_feature_service(),
            risk_service=_get_risk_service(),
            wellness_service=_get_wellness_service(),
            recommendation_service=_get_welfare_recommendation_service(),
            workflow_service=_get_welfare_workflow_service(),
            consent_service=consent_service,
            audit_service=audit_service,
        )

    return welfare_report_service


def _record_current_action(
    action,
    resource_type=None,
    resource_id=None,
    purpose=None,
    consent_type=None,
    granted=None,
):
    identity = g.get("authenticated_identity") or {}

    return audit_service.record_event(
        actor_user_id=identity.get("user_id"),
        actor_role=identity.get("role"),
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=RESULT_SUCCESS,
        source=request.path,
        purpose=purpose,
        consent_type=consent_type,
        granted=granted,
    )


# -------------------------------------------------------------------
# Root / health
# -------------------------------------------------------------------

@app.route("/", methods=["GET"])
def home():
    endpoint_catalog = (
        ("GET", "/", "API information", "home"),
        ("GET", "/health", "Health check", "health_check"),

        ("POST", "/auth/login",
         "Development user authentication", "login"),

        ("GET", "/admin/users",
         "List user accounts for administrators", "list_admin_users"),

        ("POST", "/admin/users",
         "Provision a user account for administrators", "create_admin_user"),

        ("GET", "/admin/users/<user_id>",
         "View a user account for administrators", "get_admin_user"),

        ("PATCH", "/admin/users/<user_id>",
         "Update a user account for administrators", "update_admin_user"),

        ("POST", "/admin/integrations/hrms/sync",
         "Synchronize normalized HRMS personnel records", "sync_hrms_personnel"),

        ("GET", "/consent",
         "Get authenticated user consent state", "get_consent"),

        ("POST", "/consent",
         "Update authenticated user consent state", "update_consent"),

        ("GET", "/personnel/<personnel_id>/operational/<domain>",
         "Read authorized personnel operational records",
         "get_personnel_operational_records"),

        ("GET", "/personnel/<personnel_id>/features",
         "Compute protected longitudinal features",
         "get_personnel_features"),

        ("GET", "/personnel/<personnel_id>/risk-prediction",
         "Get personnel welfare-risk prediction",
         "get_personnel_risk_prediction"),

        ("GET", "/personnel/<personnel_id>/risk-explanation",
         "Get SHAP risk explanation",
         "get_personnel_risk_explanation"),

        ("GET", "/personnel/<personnel_id>/risk-history",
         "Get authorized persisted personnel risk history",
         "get_personnel_risk_history"),

        ("GET", "/personnel/<personnel_id>/welfare-recommendations",
         "Get grounded welfare recommendations",
         "get_personnel_welfare_recommendations"),

        ("GET", "/personnel/<personnel_id>/welfare-report",
         "Download a privacy-preserving welfare report",
         "get_personnel_welfare_report"),

        ("GET", "/personnel/<personnel_id>/alerts",
         "List visible welfare alerts",
         "list_my_welfare_alerts"),

        ("GET", "/welfare/personnel",
         "List authorized privacy-minimized personnel directory records",
         "list_welfare_personnel"),

        ("GET", "/welfare/personnel/<personnel_id>",
         "Get an authorized privacy-minimized personnel directory record",
         "get_welfare_personnel"),

        ("GET", "/welfare/risk-summary",
         "Get authorized aggregate persisted welfare risk summary",
         "get_welfare_risk_summary"),

        ("GET", "/welfare/dashboard/summary",
         "Get aggregate welfare dashboard metrics",
         "get_welfare_dashboard_summary"),

        ("POST", "/personnel/me/wellness",
         "Submit a voluntary wellness assessment",
         "create_my_wellness_assessment"),

        ("GET", "/personnel/me/wellness",
         "List own wellness assessments",
         "list_my_wellness_assessments"),

        ("GET", "/personnel/me/wellness/<assessment_id>",
         "Get own wellness assessment",
         "get_my_wellness_assessment"),

        ("DELETE", "/personnel/me/wellness/<assessment_id>",
         "Delete own wellness assessment",
         "delete_my_wellness_assessment"),

        ("GET", "/personnel/<personnel_id>/wellness",
         "List authorized personnel wellness assessments",
         "list_authorized_personnel_wellness"),

        ("GET", "/operational/<domain>",
         "List operational records",
         "list_operational_records"),

        ("POST", "/operational/<domain>",
         "Create an operational record",
         "create_operational_record"),

        ("GET", "/operational/<domain>/<record_id>",
         "Get an operational record",
         "get_operational_record"),

        ("PUT/PATCH", "/operational/<domain>/<record_id>",
         "Update an operational record",
         "update_operational_record"),

        ("DELETE", "/operational/<domain>/<record_id>",
         "Delete an operational record",
         "delete_operational_record"),

        ("GET", "/operational/summary",
         "Get aggregate operational metrics",
         "get_operational_summary"),

        ("GET", "/welfare/alerts",
         "List welfare alerts for authorized staff",
         "list_welfare_alerts"),

        ("POST", "/welfare/alerts/evaluate/<personnel_id>",
         "Evaluate welfare alerts",
         "evaluate_welfare_alert"),

        ("GET", "/welfare/alerts/<alert_id>",
         "Get a welfare alert",
         "get_welfare_alert"),

        ("POST", "/welfare/alerts/<alert_id>/acknowledge",
         "Acknowledge a welfare alert",
         "acknowledge_welfare_alert"),

        ("POST", "/welfare/alerts/<alert_id>/review",
         "Review a welfare alert",
         "review_welfare_alert"),

        ("POST", "/welfare/alerts/<alert_id>/intervention",
         "Create a welfare intervention",
         "create_welfare_intervention"),

        ("POST", "/welfare/alerts/<alert_id>/follow-up",
         "Schedule welfare alert follow-up",
         "schedule_welfare_follow_up"),

        ("POST", "/welfare/alerts/<alert_id>/resolve",
         "Resolve a welfare alert",
         "resolve_welfare_alert"),

        ("POST", "/welfare/alerts/<alert_id>/dismiss",
         "Dismiss a welfare alert",
         "dismiss_welfare_alert"),

        ("PATCH", "/welfare/interventions/<intervention_id>",
         "Update a welfare intervention",
         "update_welfare_intervention"),
    )

    registered_routes = {
        (rule.rule, rule.endpoint, method)
        for rule in app.url_map.iter_rules()
        for method in rule.methods
    }

    endpoints = {}

    for methods, path, description, endpoint in endpoint_catalog:
        method_names = methods.split("/")

        if all(
            (path, endpoint, method) in registered_routes
            for method in method_names
        ):
            endpoints[f"{methods} {path}"] = description

    return jsonify({
        "api": "SURAKSHAI Personnel Welfare Intelligence API",
        "version": "1.0.0",
        "status": "active",
        "endpoints": endpoints,
        "model_info": {
            "model": "XGBoost",
            "model_version": "surakshai-risk-v0.1",
            "feature_count": 31,
            "classes": ["LOW", "ELEVATED", "HIGH"],
            "training_data": "Synthetic development dataset",
        },
    })


@app.route("/health", methods=["GET"])
def health_check():
    return jsonify({
        "status": "healthy",
        "api": "SURAKSHAI",
        "risk_engine": "surakshai-risk-v0.1",
        "timestamp": datetime.now().isoformat(),
    })


# -------------------------------------------------------------------
# Authentication
# -------------------------------------------------------------------

@app.route("/auth/login", methods=["POST"])
@rate_limit(
    limit=LOGIN_RATE_LIMIT,
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
    bucket='login',
    count_status_codes={401, 503},
)
def login():
    data = request.get_json(silent=True)

    if (
        not isinstance(data, dict)
        or not isinstance(data.get('username'), str)
        or not isinstance(data.get('password'), str)
        or not data['username']
        or not data['password']
        or len(data['username']) > 64
        or len(data['password']) > 1024
    ):
        audit_service.record_event(
            action="LOGIN_FAILURE",
            result=RESULT_FAILURE,
            source=request.path,
            purpose="Credential validation",
            attempted_username=(
                data.get("username")
                if isinstance(data, dict)
                else None
            ),
        )

        return jsonify({'error': 'Invalid username or password'}), 401

    _ensure_development_users()
    try:
        identity = authenticate_user(
            data["username"],
            data["password"],
            user_service,
        )
    except (RuntimeError, ValueError):
        audit_service.record_event(
            action="LOGIN_FAILURE",
            result=RESULT_FAILURE,
            source=request.path,
            purpose="Persistent user store unavailable",
            attempted_username=data.get("username"),
        )
        return jsonify({"error": "Authentication service unavailable"}), 503

    if identity is None:
        audit_service.record_event(
            action="LOGIN_FAILURE",
            result=RESULT_FAILURE,
            source=request.path,
            purpose="Credential validation",
            attempted_username=data.get("username"),
        )

        return jsonify({'error': 'Invalid username or password'}), 401

    try:
        token_manager = TokenManager()

        token, expires_at = token_manager.create_access_token(
            identity
        )

    except ValueError:
        audit_service.record_event(
            actor_user_id=identity.get("user_id"),
            actor_role=identity.get("role"),
            action="LOGIN_FAILURE",
            result=RESULT_FAILURE,
            source=request.path,
            purpose="Token issuance",
        )

        return jsonify({'error': 'Authentication service unavailable'}), 503

    audit_service.record_event(
        actor_user_id=identity.get("user_id"),
        actor_role=identity.get("role"),
        action="LOGIN_SUCCESS",
        result=RESULT_SUCCESS,
        source=request.path,
        purpose="Credential validation",
    )

    return jsonify({
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": token_manager.expires_minutes * 60,
        "expires_at": expires_at.isoformat(),
        "user": identity,
    }), 200


# -------------------------------------------------------------------
# Admin user provisioning
# -------------------------------------------------------------------

@app.route('/admin/users', methods=['GET'])
@require_auth
@require_permission(PERMISSION_MANAGE_USERS)
def list_admin_users():
    try:
        return jsonify({'users': user_service.list_users(
            status=request.args.get('status'),
            role=request.args.get('role'),
        )}), 200
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except RuntimeError:
        return jsonify({'error': 'User service unavailable'}), 503


@app.route('/admin/users', methods=['POST'])
@require_auth
@require_permission(PERMISSION_MANAGE_USERS)
@rate_limit(limit=ADMIN_USER_RATE_LIMIT, window_seconds=RATE_LIMIT_WINDOW_SECONDS, bucket='admin_user_create')
def create_admin_user():
    data = request.get_json(silent=True) or {}
    required = ('username', 'password', 'role')
    if any(field not in data for field in required):
        return jsonify({'error': 'username, password, and role are required'}), 400
    try:
        created = user_service.create_user(
            username=data['username'],
            password=data['password'],
            role=data['role'],
            personnel_id=data.get('personnel_id'),
            status=data.get('status', 'ACTIVE'),
            source='admin_api',
            actor=g.authenticated_identity,
        )
        _record_current_action(
            'USER_CREATED', resource_type='user', resource_id=created['user_id'],
            purpose='Provision user account',
        )
        return jsonify(created), 201
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except RuntimeError:
        return jsonify({'error': 'User service unavailable'}), 503


@app.route('/admin/users/<user_id>', methods=['GET'])
@require_auth
@require_permission(PERMISSION_MANAGE_USERS)
def get_admin_user(user_id):
    try:
        user = user_service.get_user(user_id)
        if user is None:
            return jsonify({'error': 'User not found'}), 404
        return jsonify(user), 200
    except RuntimeError:
        return jsonify({'error': 'User service unavailable'}), 503


@app.route('/admin/users/<user_id>', methods=['PATCH'])
@require_auth
@require_permission(PERMISSION_MANAGE_USERS)
@rate_limit(limit=ADMIN_USER_RATE_LIMIT, window_seconds=RATE_LIMIT_WINDOW_SECONDS, bucket='admin_user_update')
def update_admin_user(user_id):
    data = request.get_json(silent=True) or {}
    allowed = {'password', 'role', 'personnel_id', 'status'}
    if not set(data).issubset(allowed):
        return jsonify({'error': 'Only password, role, personnel_id, and status may be updated'}), 400
    try:
        kwargs = {field: data[field] for field in allowed if field in data}
        updated = user_service.update_user(user_id, actor=g.authenticated_identity, **kwargs)
        _record_current_action(
            'USER_UPDATED', resource_type='user', resource_id=user_id,
            purpose='Update user account',
        )
        return jsonify(updated), 200
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except RuntimeError:
        return jsonify({'error': 'User service unavailable'}), 503


@app.route('/admin/integrations/hrms/sync', methods=['POST'])
@require_auth
@require_permission(PERMISSION_MANAGE_USERS)
@rate_limit(limit=HRMS_SYNC_RATE_LIMIT, window_seconds=RATE_LIMIT_WINDOW_SECONDS, bucket='hrms_sync')
def sync_hrms_personnel():
    data = request.get_json(silent=True) or {}
    try:
        records = normalize_hrms_sync_request(data)
        adapter = NormalizedPayloadHRMSAdapter(records)
        result = _get_hrms_sync_service().sync(adapter, actor=g.authenticated_identity)
        return jsonify(result), 409 if result['conflicts'] else 200
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except RuntimeError:
        return jsonify({'error': 'HRMS synchronization service unavailable'}), 503


# -------------------------------------------------------------------
# Consent
# -------------------------------------------------------------------

@app.route("/consent", methods=["GET"])
@require_auth
def get_consent():
    identity = g.authenticated_identity
    user_id = identity["user_id"]

    consent_service.link_personnel_identity(
        user_id,
        identity.get("personnel_id"),
    )

    _record_current_action(
        "VIEW_CONSENT",
        resource_type="consent",
        resource_id=user_id,
    )

    return jsonify({
        "user_id": user_id,
        "consents": consent_service.get_user_consents(
            user_id,
            personnel_id=identity.get("personnel_id"),
        ),
    }), 200


@app.route("/consent", methods=["POST"])
@require_auth
def update_consent():
    data = request.get_json(silent=True)

    if (
        not data
        or "consent_type" not in data
        or "granted" not in data
    ):
        return jsonify({
            "error": "consent_type and granted are required"
        }), 400

    try:
        identity = g.authenticated_identity
        user_id = identity["user_id"]

        consent_service.link_personnel_identity(
            user_id,
            identity.get("personnel_id"),
        )

        record = consent_service.set_consent(
            user_id,
            data["consent_type"],
            data["granted"],
            personnel_id=identity.get("personnel_id"),
        )

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    _record_current_action(
        "UPDATE_CONSENT",
        resource_type="consent",
        resource_id=user_id,
        consent_type=record["consent_type"],
        granted=record["granted"],
    )

    return jsonify(record), 200


# -------------------------------------------------------------------
# Operational data
# -------------------------------------------------------------------

def _operational_domain_error(domain):
    if domain not in OPERATIONAL_SCHEMAS:
        return jsonify({
            "error": "Operational record domain not found"
        }), 404

    return None


def _operational_error_response(error):
    if isinstance(error, ValueError):
        return jsonify({"error": str(error)}), 400

    return jsonify({
        "error": "Operational data service unavailable"
    }), 503


@app.route(
    "/personnel/<personnel_id>/operational/<domain>",
    methods=["GET"],
)
@require_auth
@require_operational_personnel_access()
def get_personnel_operational_records(personnel_id, domain):
    invalid_domain = _operational_domain_error(domain)

    if invalid_domain:
        return invalid_domain

    try:
        records = _get_operational_service().list(
            domain,
            personnel_id=personnel_id,
            start_date=request.args.get("start_date"),
            end_date=request.args.get("end_date"),
            identity=g.authenticated_identity,
        )

        return jsonify({
            "records": records,
            "count": len(records),
        }), 200

    except Exception as error:
        return _operational_error_response(error)


@app.route("/personnel/<personnel_id>/features", methods=["GET"])
@require_auth
@require_operational_personnel_access()
def get_personnel_features(personnel_id):
    try:
        features = _get_feature_service().compute(
            personnel_id,
            reference_date=request.args.get("reference_date"),
            identity=g.authenticated_identity,
        )

        return jsonify(features), 200

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    except Exception:
        return jsonify({
            "error": "Feature service unavailable"
        }), 503


@app.route("/operational/<domain>", methods=["GET"])
@require_auth
@require_permission(PERMISSION_VIEW_OPERATIONAL_RECORDS)
def list_operational_records(domain):
    invalid_domain = _operational_domain_error(domain)

    if invalid_domain:
        return invalid_domain

    try:
        records = _get_operational_service().list(
            domain,
            personnel_id=request.args.get("personnel_id"),
            start_date=request.args.get("start_date"),
            end_date=request.args.get("end_date"),
            identity=g.authenticated_identity,
        )

        return jsonify({
            "records": records,
            "count": len(records),
        }), 200

    except Exception as error:
        return _operational_error_response(error)


@app.route("/operational/<domain>", methods=["POST"])
@require_auth
@require_permission(PERMISSION_MANAGE_OPERATIONAL_RECORDS)
def create_operational_record(domain):
    invalid_domain = _operational_domain_error(domain)

    if invalid_domain:
        return invalid_domain

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict):
        return jsonify({
            "error": "JSON object required"
        }), 400

    try:
        record = _get_operational_service().create(
            domain,
            payload,
            g.authenticated_identity,
        )

        return jsonify(record), 201

    except Exception as error:
        return _operational_error_response(error)


@app.route(
    "/operational/<domain>/<record_id>",
    methods=["GET"],
)
@require_auth
@require_permission(PERMISSION_VIEW_OPERATIONAL_RECORDS)
def get_operational_record(domain, record_id):
    invalid_domain = _operational_domain_error(domain)

    if invalid_domain:
        return invalid_domain

    try:
        record = _get_operational_service().get(
            domain,
            record_id,
            g.authenticated_identity,
        )

        if record is None:
            return jsonify({
                "error": "Operational record not found"
            }), 404

        return jsonify(record), 200

    except Exception as error:
        return _operational_error_response(error)


@app.route(
    "/operational/<domain>/<record_id>",
    methods=["PUT", "PATCH"],
)
@require_auth
@require_permission(PERMISSION_MANAGE_OPERATIONAL_RECORDS)
def update_operational_record(domain, record_id):
    invalid_domain = _operational_domain_error(domain)

    if invalid_domain:
        return invalid_domain

    payload = request.get_json(silent=True)

    if not isinstance(payload, dict) or not payload:
        return jsonify({
            "error": "Non-empty JSON object required"
        }), 400

    try:
        record = _get_operational_service().update(
            domain,
            record_id,
            payload,
            g.authenticated_identity,
        )

        if record is None:
            return jsonify({
                "error": "Operational record not found"
            }), 404

        return jsonify(record), 200

    except Exception as error:
        return _operational_error_response(error)


@app.route(
    "/operational/<domain>/<record_id>",
    methods=["DELETE"],
)
@require_auth
@require_permission(PERMISSION_MANAGE_OPERATIONAL_RECORDS)
def delete_operational_record(domain, record_id):
    invalid_domain = _operational_domain_error(domain)

    if invalid_domain:
        return invalid_domain

    try:
        deleted = _get_operational_service().delete(
            domain,
            record_id,
            g.authenticated_identity,
        )

        if not deleted:
            return jsonify({
                "error": "Operational record not found"
            }), 404

        return jsonify({"deleted": True}), 200

    except Exception as error:
        return _operational_error_response(error)


@app.route("/operational/summary", methods=["GET"])
@require_auth
@require_permission(PERMISSION_VIEW_OPERATIONAL_SUMMARY)
def get_operational_summary():
    try:
        summary = _get_operational_service().aggregate_summary(
            start_date=request.args.get("start_date"),
            end_date=request.args.get("end_date"),
            identity=g.authenticated_identity,
        )

        return jsonify(summary), 200

    except Exception as error:
        return _operational_error_response(error)


# -------------------------------------------------------------------
# Risk prediction
# -------------------------------------------------------------------

@app.route(
    "/personnel/<personnel_id>/risk-prediction",
    methods=["GET"],
)
@require_auth
def get_personnel_risk_prediction(personnel_id):
    identity = g.authenticated_identity or {}
    role = identity.get("role")

    if (
        role == "PERSONNEL"
        and identity.get("personnel_id") != personnel_id
    ):
        return jsonify({"error": "Forbidden"}), 403

    if role == "COMMANDER":
        return jsonify({"error": "Forbidden"}), 403

    if role not in {
        "PERSONNEL",
        "WELFARE_OFFICER",
        "ADMIN",
    }:
        return jsonify({"error": "Forbidden"}), 403

    try:
        result = _get_risk_service().predict_for_personnel(
            personnel_id,
            reference_date=request.args.get("reference_date"),
            identity=identity,
        )

        return jsonify(result), 200

    except PermissionError as error:
        return jsonify({"error": str(error)}), 403

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    except Exception:
        return jsonify({
            "error": "Risk prediction service unavailable"
        }), 503


# -------------------------------------------------------------------
# SHAP explanation
# -------------------------------------------------------------------

def _risk_history_parameters():
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 25))
    except (TypeError, ValueError) as error:
        raise ValueError('page and page_size must be integers') from error
    return {
        'page': page,
        'page_size': page_size,
        'reference_date_from': request.args.get('reference_date_from'),
        'reference_date_to': request.args.get('reference_date_to'),
    }


@app.route(
    "/personnel/<personnel_id>/risk-history",
    methods=["GET"],
)
@require_auth
def get_personnel_risk_history(personnel_id):
    try:
        response = _get_risk_history_service().get_history(
            g.authenticated_identity,
            personnel_id,
            **_risk_history_parameters(),
        )
        return jsonify(response), 200
    except PermissionError:
        return jsonify({'error': 'Forbidden'}), 403
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except Exception:
        return jsonify({'error': 'Risk history service unavailable'}), 503


@app.route('/welfare/risk-summary', methods=['GET'])
@require_auth
def get_welfare_risk_summary():
    try:
        parameters = _risk_history_parameters()
        response = _get_risk_history_service().get_summary(
            g.authenticated_identity,
            reference_date_from=parameters['reference_date_from'],
            reference_date_to=parameters['reference_date_to'],
        )
        return jsonify(response), 200
    except PermissionError:
        return jsonify({'error': 'Forbidden'}), 403
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except Exception:
        return jsonify({'error': 'Risk summary service unavailable'}), 503


@app.route('/welfare/dashboard/summary', methods=['GET'])
@require_auth
def get_welfare_dashboard_summary():
    try:
        response = _get_welfare_dashboard_service().get_summary(
            g.authenticated_identity,
        )
        return jsonify(response), 200
    except PermissionError:
        return jsonify({'error': 'Forbidden'}), 403
    except Exception:
        return jsonify({'error': 'Welfare dashboard summary unavailable'}), 503


@app.route(
    "/personnel/<personnel_id>/risk-explanation",
    methods=["GET"],
)
@require_auth
def get_personnel_risk_explanation(personnel_id):
    identity = g.authenticated_identity or {}
    role = identity.get("role")

    if (
        role == "PERSONNEL"
        and identity.get("personnel_id") != personnel_id
    ):
        return jsonify({"error": "Forbidden"}), 403

    if role == "COMMANDER":
        return jsonify({"error": "Forbidden"}), 403

    if role not in {
        "PERSONNEL",
        "WELFARE_OFFICER",
        "ADMIN",
    }:
        return jsonify({"error": "Forbidden"}), 403

    try:
        result = _get_risk_service().explain_for_personnel(
            personnel_id,
            reference_date=request.args.get("reference_date"),
            identity=identity,
        )

        return jsonify(result), 200

    except PermissionError as error:
        return jsonify({"error": str(error)}), 403

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    except Exception:
        return jsonify({
            "error": "Risk explanation service unavailable"
        }), 503


# -------------------------------------------------------------------
# Welfare recommendations
# -------------------------------------------------------------------

@app.route(
    "/personnel/<personnel_id>/welfare-recommendations",
    methods=["GET"],
)
@require_auth
def get_personnel_welfare_recommendations(personnel_id):
    identity = g.authenticated_identity or {}
    role = identity.get("role")

    if (
        role == "PERSONNEL"
        and identity.get("personnel_id") != personnel_id
    ):
        return jsonify({"error": "Forbidden"}), 403

    if role == "COMMANDER":
        return jsonify({"error": "Forbidden"}), 403

    if role not in {
        "PERSONNEL",
        "WELFARE_OFFICER",
        "ADMIN",
    }:
        return jsonify({"error": "Forbidden"}), 403

    try:
        result = _get_welfare_recommendation_service().recommend_for_personnel(
            personnel_id,
            reference_date=request.args.get("reference_date"),
            identity=identity,
        )

        return jsonify(result), 200

    except PermissionError as error:
        return jsonify({"error": str(error)}), 403

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    except WelfareRecommendationError:
        return jsonify({
            "error": "Grounded welfare recommendation service unavailable"
        }), 503
    except Exception:
        return jsonify({
            "error": "Grounded welfare recommendation service unavailable"
        }), 503


@app.route(
    "/personnel/<personnel_id>/welfare-report",
    methods=["GET"],
)
@require_auth
def get_personnel_welfare_report(personnel_id):
    try:
        report = _get_welfare_report_service().generate(
            personnel_id,
            reference_date=request.args.get("reference_date"),
            identity=g.authenticated_identity,
        )
        pdf = WelfareReportBuilder().build(report)
        filename = f"surakshai-welfare-report-{report.personnel.pseudonymous_reference}.pdf"
        return send_file(
            BytesIO(pdf),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename,
        )
    except WelfareReportAuthorizationError as error:
        return jsonify({"error": str(error)}), 403
    except WelfareReportValidationError as error:
        return jsonify({"error": str(error)}), 400
    except WelfareReportGenerationError:
        return jsonify({"error": "Welfare report service unavailable"}), 503


# -------------------------------------------------------------------
# Wellness
# -------------------------------------------------------------------

def _wellness_error_response(error):
    if isinstance(error, DuplicateWellnessAssessment):
        return jsonify({"error": str(error)}), 409

    if isinstance(error, WellnessAuthorizationError):
        return jsonify({"error": str(error)}), 403

    if isinstance(error, ValueError):
        return jsonify({"error": str(error)}), 400

    return jsonify({
        "error": "Wellness service unavailable"
    }), 503


@app.route("/personnel/me/wellness", methods=["POST"])
@require_auth
@require_permission(PERMISSION_SUBMIT_WELLNESS)
def create_my_wellness_assessment():
    try:
        assessment = _get_wellness_service().create_for_self(
            request.get_json(silent=True),
            g.authenticated_identity,
        )

        return jsonify(assessment), 201

    except Exception as error:
        return _wellness_error_response(error)


@app.route("/personnel/me/wellness", methods=["GET"])
@require_auth
@require_permission(PERMISSION_VIEW_OWN_WELLNESS)
def list_my_wellness_assessments():
    try:
        assessments = _get_wellness_service().list_for_self(
            g.authenticated_identity,
            start_date=request.args.get("start_date"),
            end_date=request.args.get("end_date"),
        )

        return jsonify({
            "assessments": assessments,
            "count": len(assessments),
        }), 200

    except Exception as error:
        return _wellness_error_response(error)


@app.route(
    "/personnel/me/wellness/<assessment_id>",
    methods=["GET"],
)
@require_auth
@require_permission(PERMISSION_VIEW_OWN_WELLNESS)
def get_my_wellness_assessment(assessment_id):
    try:
        assessment = _get_wellness_service().get_for_self(
            assessment_id,
            g.authenticated_identity,
        )

        if assessment is None:
            return jsonify({
                "error": "Wellness assessment not found"
            }), 404

        return jsonify(assessment), 200

    except Exception as error:
        return _wellness_error_response(error)


@app.route(
    "/personnel/me/wellness/<assessment_id>",
    methods=["DELETE"],
)
@require_auth
@require_permission(PERMISSION_VIEW_OWN_WELLNESS)
def delete_my_wellness_assessment(assessment_id):
    try:
        deleted = _get_wellness_service().delete_for_self(
            assessment_id,
            g.authenticated_identity,
        )

        if not deleted:
            return jsonify({
                "error": "Wellness assessment not found"
            }), 404

        return jsonify({"deleted": True}), 200

    except Exception as error:
        return _wellness_error_response(error)


@app.route(
    "/personnel/<personnel_id>/wellness",
    methods=["GET"],
)
@require_auth
@require_wellness_personnel_access()
def list_authorized_personnel_wellness(personnel_id):
    try:
        assessments = _get_wellness_service().list_for_authorized_personnel(
            personnel_id,
            g.authenticated_identity,
            start_date=request.args.get("start_date"),
            end_date=request.args.get("end_date"),
        )

        return jsonify({
            "assessments": assessments,
            "count": len(assessments),
        }), 200

    except Exception as error:
        return _wellness_error_response(error)


# -------------------------------------------------------------------
# Welfare personnel directory
# -------------------------------------------------------------------

def _directory_access_denied(identity, purpose):
    identity = identity or {}
    audit_service.record_event(
        actor_user_id=identity.get('user_id'),
        actor_role=identity.get('role'),
        action='ACCESS_DENIED',
        resource_type='personnel_directory',
        result=RESULT_DENIED,
        source=request.path,
        purpose=purpose,
    )
    return jsonify({'error': 'Forbidden'}), 403


def _directory_page_parameters():
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 25))
    except (TypeError, ValueError):
        raise ValueError('page and page_size must be integers')
    if page < 1:
        raise ValueError('page must be at least 1')
    if page_size < 1 or page_size > 100:
        raise ValueError('page_size must be between 1 and 100')
    search = request.args.get('search')
    if search is not None:
        search = search.strip()
        if not search or len(search) > 100 or '$' in search:
            raise ValueError('search must be between 1 and 100 safe characters')
    status = request.args.get('status')
    if status is not None and status not in PERSONNEL_STATUSES:
        raise ValueError('status is not supported')
    unit_id = request.args.get('unit')
    if unit_id is not None and (not unit_id.strip() or len(unit_id) > 100 or '$' in unit_id):
        raise ValueError('unit must be between 1 and 100 safe characters')
    return page, page_size, search, status, unit_id


@app.route('/welfare/personnel', methods=['GET'])
@require_auth
def list_welfare_personnel():
    identity = g.authenticated_identity or {}
    if identity.get('role') not in {'WELFARE_OFFICER', 'ADMIN'}:
        return _directory_access_denied(identity, 'List authorized welfare personnel directory')
    try:
        page, page_size, search, status, unit_id = _directory_page_parameters()
        response = _get_personnel_service().list_directory(
            identity,
            page=page,
            page_size=page_size,
            search=search,
            status=status,
            unit_id=unit_id,
        )
        return jsonify(response), 200
    except ValueError as error:
        return jsonify({'error': str(error)}), 400
    except Exception:
        return jsonify({'error': 'Personnel directory unavailable'}), 503


@app.route('/welfare/personnel/<personnel_id>', methods=['GET'])
@require_auth
def get_welfare_personnel(personnel_id):
    identity = g.authenticated_identity or {}
    if not _get_personnel_service().can_view_directory(identity, personnel_id):
        return _directory_access_denied(identity, 'View authorized welfare personnel directory record')
    try:
        record = _get_personnel_service().get_directory(identity, personnel_id)
        if record is None:
            return jsonify({'error': 'Personnel record not found'}), 404
        return jsonify(record), 200
    except Exception:
        return jsonify({'error': 'Personnel directory unavailable'}), 503


# -------------------------------------------------------------------
# Welfare alerts / human-in-the-loop workflow
# -------------------------------------------------------------------

def _welfare_staff_allowed(identity):
    return (identity or {}).get("role") in {
        "WELFARE_OFFICER",
        "ADMIN",
    }


def _alert_visible_to_identity(alert, identity):
    identity = identity or {}
    role = identity.get("role")

    if role in {"WELFARE_OFFICER", "ADMIN"}:
        return True

    return (
        role == "PERSONNEL"
        and identity.get("personnel_id")
        == alert.get("personnel_id")
    )


@app.route(
    "/personnel/<personnel_id>/alerts",
    methods=["GET"],
)
@require_auth
def list_my_welfare_alerts(personnel_id):
    identity = g.authenticated_identity or {}

    if not _alert_visible_to_identity(
        {"personnel_id": personnel_id},
        identity,
    ):
        return jsonify({"error": "Forbidden"}), 403

    try:
        alerts = _get_welfare_workflow_service().list_alerts(
            personnel_id
        )

        return jsonify({"alerts": alerts}), 200

    except Exception:
        return jsonify({
            "error": "Welfare alert service unavailable"
        }), 503


@app.route("/welfare/alerts", methods=["GET"])
@require_auth
def list_welfare_alerts():
    if not _welfare_staff_allowed(
        g.authenticated_identity
    ):
        return jsonify({"error": "Forbidden"}), 403

    try:
        alerts = _get_welfare_workflow_service().list_alerts()

        return jsonify({"alerts": alerts}), 200

    except Exception:
        return jsonify({
            "error": "Welfare alert service unavailable"
        }), 503


@app.route(
    "/welfare/alerts/evaluate/<personnel_id>",
    methods=["POST"],
)
@require_auth
def evaluate_welfare_alert(personnel_id):
    if not _welfare_staff_allowed(
        g.authenticated_identity
    ):
        return jsonify({"error": "Forbidden"}), 403

    try:
        payload = request.get_json(silent=True) or {}

        result = _get_welfare_workflow_service().evaluate(
            personnel_id,
            reference_date=payload.get("reference_date"),
            identity=g.authenticated_identity,
        )

        return jsonify(result), 200

    except PermissionError as error:
        return jsonify({"error": str(error)}), 403

    except ValueError as error:
        return jsonify({"error": str(error)}), 400

    except Exception:
        return jsonify({
            "error": "Welfare alert evaluation unavailable"
        }), 503


@app.route(
    "/welfare/alerts/<alert_id>",
    methods=["GET"],
)
@require_auth
def get_welfare_alert(alert_id):
    try:
        alert = _get_welfare_workflow_service().get_alert(
            alert_id
        )

        if not _alert_visible_to_identity(
            alert,
            g.authenticated_identity,
        ):
            return jsonify({"error": "Forbidden"}), 403

        return jsonify(alert), 200

    except WorkflowError as error:
        return jsonify({"error": str(error)}), 404

    except Exception:
        return jsonify({
            "error": "Welfare alert service unavailable"
        }), 503


def _transition_welfare_alert(
    alert_id,
    target_status,
    action_fields=None,
):
    if not _welfare_staff_allowed(
        g.authenticated_identity
    ):
        return jsonify({"error": "Forbidden"}), 403

    try:
        alert = _get_welfare_workflow_service().transition(
            alert_id,
            target_status,
            g.authenticated_identity,
            **(action_fields or {}),
        )

        return jsonify(alert), 200

    except WorkflowError as error:
        return jsonify({"error": str(error)}), 409

    except Exception:
        return jsonify({
            "error": "Welfare workflow service unavailable"
        }), 503


@app.route(
    "/welfare/alerts/<alert_id>/acknowledge",
    methods=["POST"],
)
@require_auth
def acknowledge_welfare_alert(alert_id):
    return _transition_welfare_alert(
        alert_id,
        "ACKNOWLEDGED",
    )


@app.route(
    "/welfare/alerts/<alert_id>/review",
    methods=["POST"],
)
@require_auth
def review_welfare_alert(alert_id):
    return _transition_welfare_alert(
        alert_id,
        "UNDER_REVIEW",
    )


@app.route(
    "/welfare/alerts/<alert_id>/follow-up",
    methods=["POST"],
)
@require_auth
def schedule_welfare_follow_up(alert_id):
    payload = request.get_json(silent=True) or {}

    return _transition_welfare_alert(
        alert_id,
        "FOLLOW_UP",
        {
            "scheduled_follow_up":
                payload.get("scheduled_follow_up")
        },
    )


@app.route(
    "/welfare/alerts/<alert_id>/resolve",
    methods=["POST"],
)
@require_auth
def resolve_welfare_alert(alert_id):
    payload = request.get_json(silent=True) or {}

    return _transition_welfare_alert(
        alert_id,
        "RESOLVED",
        {
            "resolution_type":
                payload.get("resolution_type")
        },
    )


@app.route(
    "/welfare/alerts/<alert_id>/dismiss",
    methods=["POST"],
)
@require_auth
def dismiss_welfare_alert(alert_id):
    payload = request.get_json(silent=True) or {}

    return _transition_welfare_alert(
        alert_id,
        "DISMISSED",
        {
            "resolution_type":
                payload.get("resolution_type")
        },
    )


@app.route(
    "/welfare/alerts/<alert_id>/intervention",
    methods=["POST"],
)
@require_auth
def create_welfare_intervention(alert_id):
    if not _welfare_staff_allowed(
        g.authenticated_identity
    ):
        return jsonify({"error": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}

    try:
        alert = _get_welfare_workflow_service().get_alert(
            alert_id
        )

        intervention = (
            _get_welfare_workflow_service()
            .create_intervention(
                alert_id,
                alert["personnel_id"],
                payload.get("action_type"),
                g.authenticated_identity,
                scheduled_follow_up=payload.get(
                    "scheduled_follow_up"
                ),
            )
        )

        return jsonify(intervention), 201

    except (WorkflowError, ValueError) as error:
        return jsonify({"error": str(error)}), 409

    except Exception:
        return jsonify({
            "error": "Welfare intervention service unavailable"
        }), 503


@app.route(
    "/welfare/interventions/<intervention_id>",
    methods=["PATCH"],
)
@require_auth
def update_welfare_intervention(intervention_id):
    if not _welfare_staff_allowed(
        g.authenticated_identity
    ):
        return jsonify({"error": "Forbidden"}), 403

    payload = request.get_json(silent=True) or {}

    try:
        intervention = (
            _get_welfare_workflow_service()
            .update_intervention(
                intervention_id,
                payload.get("status"),
                g.authenticated_identity,
                outcome_category=payload.get(
                    "outcome_category"
                ),
            )
        )

        return jsonify(intervention), 200

    except (WorkflowError, ValueError) as error:
        return jsonify({"error": str(error)}), 409

    except Exception:
        return jsonify({
            "error": "Welfare intervention service unavailable"
        }), 503


# -------------------------------------------------------------------
# Application entry point
# -------------------------------------------------------------------

if __name__ == "__main__":
    try:
        validate_production_configuration()
    except ValueError:
        raise SystemExit('Production security configuration is invalid; review environment settings.')
    print("=" * 70)
    print("  SURAKSHAI Personnel Welfare Intelligence API")
    print("=" * 70)
    print("  API:    http://localhost:5000")
    print("  Health: http://localhost:5000/health")
    print("  Risk:   XGBoost / surakshai-risk-v0.1")
    print("=" * 70)

    app.run(
        host="0.0.0.0",
        port=int(os.getenv('PORT', '5000')),
        debug=get_debug_mode(),
    )
