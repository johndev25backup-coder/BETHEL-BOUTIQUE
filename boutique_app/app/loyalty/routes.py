from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from app.decorators import log_audit_event, owns_user_record_or_staff
from app.extensions import db
from app.models import PaymentMethod, PointsTransaction, PointsTxnType, PointsWithdrawal, WithdrawalStatus

loyalty_bp = Blueprint("loyalty", __name__, url_prefix="/api/loyalty")

MIN_WITHDRAWAL_POINTS = 4000


@loyalty_bp.route("/balance", methods=["GET"])
@login_required
def my_balance():
    # No user_id parameter - always the caller's own balance.
    return jsonify({"points_balance": current_user.points_balance}), 200


@loyalty_bp.route("/users/<int:user_id>/history", methods=["GET"])
@owns_user_record_or_staff
def points_history(user_id):
    txns = PointsTransaction.query.filter_by(user_id=user_id).order_by(PointsTransaction.created_at.desc()).all()
    return jsonify([{
        "points": t.points,
        "type": t.txn_type.value,
        "created_at": t.created_at.isoformat(),
    } for t in txns]), 200


@loyalty_bp.route("/withdraw", methods=["POST"])
@login_required
def request_withdrawal():
    data = request.get_json(silent=True) or {}
    points_requested = data.get("points_requested")
    payout_method_raw = data.get("payout_method")
    payout_phone = data.get("payout_phone")

    try:
        points_requested = int(points_requested)
    except (TypeError, ValueError):
        return jsonify({"error": "points_requested must be an integer"}), 400
    if points_requested < MIN_WITHDRAWAL_POINTS:
        return jsonify({"error": f"minimum withdrawal is {MIN_WITHDRAWAL_POINTS} points"}), 400
    if points_requested > current_user.points_balance:
        return jsonify({"error": "insufficient points balance"}), 400

    try:
        payout_method = PaymentMethod(payout_method_raw)
    except ValueError:
        return jsonify({"error": "invalid payout method"}), 400
    if payout_method == PaymentMethod.CASH or not payout_phone:
        return jsonify({"error": "payout requires a mobile money method and phone number"}), 400

    # Deduct immediately to prevent double-spending the same points across
    # two concurrent withdrawal requests; refunded automatically if rejected
    # (see app/admin/routes.py: review_withdrawal).
    current_user.points_balance -= points_requested
    db.session.add(PointsTransaction(
        user_id=current_user.id, points=-points_requested,
        txn_type=PointsTxnType.WITHDRAWAL_REQUESTED,
    ))
    withdrawal = PointsWithdrawal(
        user_id=current_user.id, points_requested=points_requested,
        cash_equivalent=points_requested,  # 1 point = 1 UGX, per spec
        payout_method=payout_method, payout_phone=payout_phone,
        status=WithdrawalStatus.PENDING,
    )
    db.session.add(withdrawal)
    db.session.commit()

    log_audit_event(action="points_withdrawal_requested", entity_type="points_withdrawal",
                     entity_id=withdrawal.id, details={"points_requested": points_requested})
    return jsonify({"message": "withdrawal requested, pending owner approval", "withdrawal_id": withdrawal.id}), 201
