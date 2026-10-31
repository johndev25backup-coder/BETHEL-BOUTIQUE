# Boutique Retail Management System - Backend

Flask + PostgreSQL backend implementing the build spec, with payment API
calls left as clearly marked stubs (see "What's stubbed out" below).

## Quick Start - Demo Mode (no Postgres, no payment credentials needed)

This gets the full app running today, with a local SQLite database and
mock (instant fake-success) mobile money payments, so you can test the
whole flow - browse, cart, checkout, pay, earn points, track an order -
before you've set up PostgreSQL or registered on any payment provider's
portal.

**Windows (CMD):**
```cmd
python -m venv venv
venv\Scripts\activate.bat
pip install -r requirements.txt

copy .env.example .env

python seed_data.py
python run.py
```

**Mac/Linux:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env

python3 seed_data.py
python3 run.py
```

That's it - no editing `.env` required for demo mode. It works because:
- `DATABASE_URL` defaults to a local SQLite file (`boutique_demo.db`) if not set.
- `SECRET_KEY` / `ADMIN_API_KEY` / `PUBLIC_API_KEY` auto-generate a temporary
  value if not set (you'll see a warning - that's expected in demo mode).
- `PAYMENT_MOCK_MODE=true` is the default, so `/api/payments/<id>/initiate`
  returns instant fake success instead of calling MTN/Airtel/M-Pesa.

`seed_data.py` creates:
- Owner login: `owner@example.com` / `ChangeMe123!`
- Staff login: `staff@example.com` / `ChangeMe123!`
- One sample product ("Ankara Print Dress") with 3 variants, stocked.

Try it: open `http://localhost:5000/api/products` - you should see the
sample dress with its variants and stock counts.

**A full manual walkthrough** (as raw HTTP calls, e.g. via curl or Postman):
1. `POST /api/cart/add` with `{"variant_id": 1, "quantity": 1}`
2. `POST /api/auth/otp/request` with `{"phone": "0700000000"}` → copy the `debug_otp` from the response
3. `POST /api/auth/otp/verify` with `{"phone": "0700000000", "code": "<the otp>", "name": "Test Client"}`
4. `POST /api/orders/checkout` with `{}` → note the `order_id`
5. `POST /api/payments/<order_id>/initiate` with `{"method": "mtn_momo", "phone": "0700000000"}` → instantly succeeds in mock mode
6. `GET /api/orders/mine` → see the order marked `paid`
7. `GET /api/loyalty/balance` → see points awarded if the order was ≥20,000 UGX

## Moving to production

When you're ready to go live, three things change - nothing else in the
app needs rewriting:

1. Set a real `DATABASE_URL` pointing to a managed PostgreSQL instance in `.env`.
2. Set real, fixed values for `SECRET_KEY`, `ADMIN_API_KEY`, `PUBLIC_API_KEY` in `.env` (don't rely on the auto-generated demo ones - those change every restart).
3. Fill in real credentials for whichever payment providers you've completed KYC for, and set `PAYMENT_MOCK_MODE=false`.

Then run the real migration flow instead of `seed_data.py`'s `db.create_all()`:
```bash
flask --app run.py db init
flask --app run.py db migrate -m "initial schema"
flask --app run.py db upgrade
```

The API will be at `http://localhost:5000` either way.

## Desktop, Android, and iPhone support

This is a browser-based application, so the same Flask web app runs on desktop computers, Android phones/tablets, and iPhones/iPads through the browser. The front end includes mobile-safe viewport settings and installable web-app metadata so users can add it to their home screen from Chrome on Android or Safari on iPhone.

- Desktop: open the app in any modern browser.
- Android: use Chrome and install from the browser menu, or open the PWA directly.
- iPhone/iPad: open in Safari and use "Add to Home Screen".

The app is designed for responsive browsing; no separate native codebase is required for cross-device use.

## What's stubbed out (on purpose, per your request)

- `app/payments/providers.py` — MTN MoMo, Airtel Money, and M-Pesa each have
  a real class with the correct method names, config already wired to read
  your `.env` credentials, and step-by-step TODO comments showing exactly
  which endpoint to call and what to send, based on the API reference
  from our earlier conversation. Nothing makes a real network call yet —
  fill in the `requests.post(...)` calls once you have sandbox credentials.
- Once a provider's `initiate_payment` and `verify_callback` are implemented,
  the rest of the app (order finalization, stock deduction, points award)
  already works end-to-end and needs no changes.

## Security patterns baked into this project (reuse these on every client build)

1. **Ownership checked on every request, not just at login.**
   See `app/decorators.py` — `owns_order_or_staff` and
   `owns_user_record_or_staff` re-check `resource.owner_id == current_user.id`
   inside the request itself. A valid session is necessary but never
   sufficient on its own to read someone else's data.
2. **No raw SQL anywhere.** Every query goes through SQLAlchemy's ORM
   (`app/models.py` + query calls throughout), which parameterizes all
   user input automatically. Grep the codebase for `execute(` or `text(`
   before adding to it — if you ever need raw SQL, use
   `db.session.execute(text("..."), {"param": value})` with bound
   parameters, never an f-string.
3. **Admin key stays server-side.** `ADMIN_API_KEY` in `.env` is read only
   by `app/decorators.py::admin_key_required`, used for
   server-to-server/internal calls. The frontend only ever receives
   `PUBLIC_API_KEY` for its own limited use — the two are never the same
   value and the admin key is never rendered into any HTML/JS response.
4. **Every sensitive action is audited.** `log_audit_event()` is called
   on logins, failed access attempts, stock adjustments, discount changes,
   return decisions, and withdrawal reviews. `AuditLog` rows are never
   updated or deleted by any route in this codebase.

## Project layout

```
app/
  auth/        phone+OTP login, Google sign-in stub, staff login
  products/    public catalog, search, filters
  cart/        session-based cart before checkout
  orders/      checkout (stock locking), tracking, returns
  payments/    provider stubs + webhook handlers
  admin/       staff creation, stock adjustments, discounts, withdrawals
  loyalty/     client-facing points balance/withdrawal requests
  jobs/        background job that releases expired stock locks
  models.py    all database tables
  decorators.py access-control decorators (read this file first)
```

## Not yet built (flagged, not forgotten)

- Google ID token verification (stub in `app/auth/routes.py::google_login`)
- SMS/WhatsApp notification sending (marked TODO in `app/auth/routes.py`)
- Frontend (HTML/JS) — this is the API backend only
- Automated seasonal/aging-stock discount application job (rules are
  stored and `/api/products/on-sale` reads them; a scheduled job to apply
  `percent_off` to prices still needs to be added to `app/jobs/scheduler.py`)
