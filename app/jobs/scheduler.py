from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from app.extensions import db
from app.models import Order, OrderStatus, ReservationStatus, StockReservation


def release_expired_reservations(app):
    """
    Runs periodically. Any stock reservation past its expiry with no
    successful payment is released, and its order is cancelled - this is
    what stops an abandoned checkout from permanently locking stock.
    """
    with app.app_context():
        now = datetime.utcnow()
        expired = StockReservation.query.filter(
            StockReservation.status == ReservationStatus.ACTIVE,
            StockReservation.expires_at < now,
        ).all()

        touched_order_ids = set()
        for reservation in expired:
            reservation.status = ReservationStatus.RELEASED
            touched_order_ids.add(reservation.order_id)

        for order_id in touched_order_ids:
            order = db.session.get(Order, order_id)
            if order and order.status == OrderStatus.PENDING_PAYMENT:
                order.status = OrderStatus.CANCELLED

        if expired:
            db.session.commit()


def init_scheduler(app):
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        func=lambda: release_expired_reservations(app),
        trigger="interval",
        minutes=1,
        id="release_expired_reservations",
        replace_existing=True,
    )
    scheduler.start()
    return scheduler
