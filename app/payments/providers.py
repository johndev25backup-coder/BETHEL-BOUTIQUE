"""
One class per payment provider, all implementing the same interface, so
the rest of the app (app/payments/routes.py) doesn't care which provider
is being used.

Every real-provider method below is a STUB. Fill in the marked TODOs using
the Section 6 API reference (base URLs, auth flow, endpoint paths) once
you've registered on each provider's developer portal and have sandbox
credentials in your .env file. Nothing in the stub classes makes a real
network call yet.

When PAYMENT_MOCK_MODE=true (the default until you set it to false),
get_provider() returns MockPaymentProvider instead of a real provider,
regardless of which method was requested - this is what lets you test the
full checkout -> payment -> points -> tracking flow today, with no
credentials at all. Switch PAYMENT_MOCK_MODE=false in .env once you're
ready to test against a real sandbox.
"""
import uuid

from flask import current_app


class PaymentProviderError(Exception):
    pass


class MockPaymentProvider:
    """
    Simulates an instant successful mobile money payment. Used only when
    PAYMENT_MOCK_MODE=true. Never used in production - see get_provider().
    """

    def initiate_payment(self, phone, amount, currency, order_reference):
        return {
            "status": "success",
            "provider_reference": f"MOCK-{uuid.uuid4().hex[:12]}",
        }

    def verify_callback(self, payload):
        # Not used in mock mode - initiate_payment already returns success
        # synchronously instead of waiting for a webhook.
        return {"status": "success", "provider_reference": payload.get("provider_reference")}


class BasePaymentProvider:
    def initiate_payment(self, phone: str, amount: float, currency: str, order_reference: str) -> dict:
        """
        Should return a dict like:
            {"status": "pending", "provider_reference": "<external txn id>"}
        Raise PaymentProviderError on failure.
        """
        raise NotImplementedError

    def verify_callback(self, payload: dict) -> dict:
        """
        Should validate/parse an incoming webhook payload and return:
            {"provider_reference": ..., "status": "success"|"failed", "order_reference": ...}

        SECURITY - do not skip this: the callback routes in
        app/payments/routes.py are necessarily public (the provider calls
        them, not a logged-in user), which means anyone who knows the URL
        shape can POST a fake "success" payload unless this method
        actually verifies it. Don't just trust payload["status"] as-is.
        Depending on the provider, do ONE of:
          - Re-query the transaction status yourself from the provider's
            API using YOUR OWN credentials (e.g. MTN's
            GET .../requesttopay/{referenceId}) rather than trusting the
            POST body at all - this is the safest option and works even
            if a provider's callback has no signature.
          - Verify a signature/HMAC on the payload if the provider
            provides one.
          - At minimum, restrict the callback route to the provider's
            published IP ranges at the network/proxy level.
        """
        raise NotImplementedError


class MTNMoMoProvider(BasePaymentProvider):
    def initiate_payment(self, phone, amount, currency, order_reference):
        base_url = current_app.config["MTN_MOMO_BASE_URL"]
        subscription_key = current_app.config["MTN_MOMO_SUBSCRIPTION_KEY"]
        # TODO:
        #   1. POST {base_url}/collection/token/  (Basic auth: api_user:api_key)
        #      headers: {"Ocp-Apim-Subscription-Key": subscription_key}
        #      -> access_token
        #   2. POST {base_url}/collection/v1_0/requesttopay
        #      headers: Authorization: Bearer <access_token>,
        #               "X-Reference-Id": <a new UUID you generate>,
        #               "X-Target-Environment": config MTN_MOMO_TARGET_ENVIRONMENT,
        #               "Ocp-Apim-Subscription-Key": subscription_key
        #      body: {"amount": str(amount), "currency": currency,
        #             "externalId": order_reference,
        #             "payer": {"partyIdType": "MSISDN", "partyId": phone},
        #             "payerMessage": "Payment for order", "payeeNote": order_reference}
        #   3. Store the X-Reference-Id you generated as provider_reference -
        #      that's what you poll GET .../requesttopay/{referenceId} with later.
        raise NotImplementedError("Wire up MTN MoMo credentials, then implement this call")

    def verify_callback(self, payload):
        # TODO: MTN's callback payload includes the reference id and status.
        # If callbacks prove unreliable in sandbox, poll requesttopay/{id} instead.
        raise NotImplementedError


class AirtelMoneyProvider(BasePaymentProvider):
    def initiate_payment(self, phone, amount, currency, order_reference):
        base_url = current_app.config["AIRTEL_BASE_URL"]
        # TODO:
        #   1. POST {base_url}/auth/oauth2/token
        #      body: {"client_id": ..., "client_secret": ..., "grant_type": "client_credentials"}
        #      -> access_token
        #   2. POST {base_url}/merchant/v1/payments/
        #      headers: Authorization: Bearer <access_token>,
        #               "X-Country": "UG", "X-Currency": currency
        #      body: {"reference": order_reference,
        #             "subscriber": {"country": "UG", "currency": currency, "msisdn": phone},
        #             "transaction": {"amount": amount, "country": "UG", "currency": currency, "id": order_reference}}
        raise NotImplementedError("Wire up Airtel Money credentials, then implement this call")

    def verify_callback(self, payload):
        raise NotImplementedError


class MpesaProvider(BasePaymentProvider):
    def initiate_payment(self, phone, amount, currency, order_reference):
        base_url = current_app.config["MPESA_BASE_URL"]
        shortcode = current_app.config["MPESA_SHORTCODE"]
        # TODO:
        #   1. GET {base_url}/oauth/v1/generate?grant_type=client_credentials
        #      (Basic auth: consumer_key:consumer_secret) -> access_token
        #   2. Build Password = base64(shortcode + passkey + timestamp)
        #   3. POST {base_url}/mpesa/stkpush/v1/processrequest
        #      headers: Authorization: Bearer <access_token>
        #      body: {"BusinessShortCode": shortcode, "Password": ..., "Timestamp": ...,
        #             "TransactionType": "CustomerPayBillOnline", "Amount": amount,
        #             "PartyA": phone, "PartyB": shortcode, "PhoneNumber": phone,
        #             "CallBackURL": config MPESA_CALLBACK_URL,
        #             "AccountReference": order_reference, "TransactionDesc": "Order payment"}
        raise NotImplementedError("Wire up M-Pesa Daraja credentials, then implement this call")

    def verify_callback(self, payload):
        raise NotImplementedError


def get_provider(method: str):
    if current_app.config.get("PAYMENT_MOCK_MODE"):
        return MockPaymentProvider()

    providers = {
        "mtn_momo": MTNMoMoProvider,
        "airtel_money": AirtelMoneyProvider,
        "mpesa": MpesaProvider,
    }
    provider_cls = providers.get(method)
    if not provider_cls:
        raise PaymentProviderError(f"unknown payment method: {method}")
    return provider_cls()
