"""
Access-control decorators used across every blueprint.

Standing rule for this app (and every future client build):
  1. Every route that returns or modifies a specific user's data must
     confirm the logged-in user actually owns that data - OR is staff/owner
     acting in an authorized capacity - before responding. Never assume a
     valid session token alone is enough; a logged-in user must not be able
     to fetch someone else's order/address/points just by guessing an ID.
  2. Every account/data route needs an explicit auth check. There is no
     "trusted by default" route once login exists.
  3. Admin/internal-only actions require the server-side ADMIN_API_KEY -
     which the frontend never receives - in addition to normal role checks
     where relevant.
"""
from functools import wraps

from flask import abort, current_app, jsonify, request
from flask_login import current_user

from app.extensions import db
from app.models import AuditLog, Order, UserRole


def login_required_json(fn):
    """Like Flask-Login's login_required, but returns JSON 401 for an API."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({"error": "authentication required"}), 401
        return fn(*args, **kwargs)
    return wrapper


def role_required(*allowed_roles: UserRole):
    """Restrict a route to specific roles (e.g. staff/owner only)."""
    def decorator(fn):
        @wraps(fn)
        @login_required_json
        def wrapper(*args, **kwargs):
            if current_user.role not in allowed_roles:
                log_audit_event(
                    action="access_denied_role",
                    entity_type="route",
                    entity_id=None,
                    details={"route": request.path, "required_roles": [r.value for r in allowed_roles]},
                )
                return jsonify({"error": "forbidden"}), 403
            return fn(*args, **kwargs)
        return wrapper
    return decorator


def owns_order_or_staff(fn):
    """
    Ownership check for any route keyed on order_id.
    A client may only access their own order; staff/owner may access any
    order. This check runs on EVERY request to the decorated route - there
    is no cached/skip path.
    """
    @wraps(fn)
    @login_required_json
    def wrapper(order_id, *args, **kwargs):
        order = db.session.get(Order, order_id)
        if order is None:
            abort(404)

        is_owner_of_record = order.client_id == current_user.id
        if not (is_owner_of_record or current_user.is_staff_or_owner):
            log_audit_event(
                action="access_denied_ownership",
                entity_type="order",
                entity_id=order_id,
                details={"attempted_by": current_user.id},
            )
            # 404, not 403 - don't confirm to an attacker that the order
            # exists at all if it isn't theirs.
            abort(404)

        return fn(order_id, *args, **kwargs)
    return wrapper


def owns_user_record_or_staff(fn):
    """
    Ownership check for routes keyed on a user_id (profile, addresses,
    points balance, withdrawal history, etc). A client may only pull their
    own account; staff/owner may look up any account.
    """
    @wraps(fn)
    @login_required_json
    def wrapper(user_id, *args, **kwargs):
        if user_id != current_user.id and not current_user.is_staff_or_owner:
            log_audit_event(
                action="access_denied_ownership",
                entity_type="user",
                entity_id=user_id,
                details={"attempted_by": current_user.id},
            )
            abort(404)
        return fn(user_id, *args, **kwargs)
    return wrapper


def admin_key_required(fn):
    """
    For server-to-server / internal admin-console endpoints ONLY.
    Checks a secret header against ADMIN_API_KEY, which lives in the server
    environment and is NEVER sent to any frontend template or JS bundle.
    The human-facing admin dashboard uses normal session + role_required
    instead of this - this decorator is for machine-to-machine calls
    (e.g. an internal reporting job, a future mobile admin app's backend
    calls) that need a credential beyond a browser session.
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        supplied_key = request.headers.get("X-Admin-Key", "")
        expected_key = current_app.config.get("ADMIN_API_KEY", "")
        if not expected_key or supplied_key != expected_key:
            abort(403)
        return fn(*args, **kwargs)
    return wrapper


def log_audit_event(action, entity_type=None, entity_id=None, details=None):
    """Central helper so every sensitive action lands in the audit trail."""
    entry = AuditLog(
        user_id=current_user.id if current_user.is_authenticated else None,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details or {},
        ip_address=request.remote_addr,
    )
    db.session.add(entry)
    db.session.commit()
