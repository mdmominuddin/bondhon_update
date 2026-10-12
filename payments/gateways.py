"""
Payment gateways.

 * manual      - member pays by bKash/Nagad/Rocket/bank and submits the TrxID; a
                 treasurer verifies it (works today, no merchant contract needed).
 * sandbox     - a fake hosted payment page for development and demos.
 * sslcommerz  - redirect-based gateway (cards + mobile wallets) with server-side
                 validation.  Needs SSLCOMMERZ_STORE_ID / _STORE_PASSWORD.
"""
import hashlib
import hmac
import json
import urllib.parse
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.urls import reverse


class GatewayError(Exception):
    pass


def _abs(path):
    return settings.SITE_BASE_URL + path


def sign(reference, status):
    """HMAC used by the sandbox so its return URL cannot be forged."""
    return hmac.new(settings.SECRET_KEY.encode(), f"{reference}:{status}".encode(), hashlib.sha256).hexdigest()[:32]


class ManualGateway:
    name = "manual"
    redirects = False

    def initiate(self, payment):
        return None


class SandboxGateway:
    name = "sandbox"
    redirects = True

    def initiate(self, payment):
        if not settings.PAYMENT_SANDBOX_ENABLED:
            raise GatewayError("The sandbox gateway is disabled on this server.")
        return reverse("payment_sandbox", args=[payment.reference])


class SSLCommerzGateway:
    name = "sslcommerz"
    redirects = True

    @property
    def base(self):
        return "https://sandbox.sslcommerz.com" if settings.SSLCOMMERZ_SANDBOX else "https://securepay.sslcommerz.com"

    def _check_config(self):
        if not (settings.SSLCOMMERZ_STORE_ID and settings.SSLCOMMERZ_STORE_PASSWORD):
            raise GatewayError("SSLCommerz credentials are not configured (SSLCOMMERZ_STORE_ID / SSLCOMMERZ_STORE_PASSWORD).")

    def _post(self, url, data):
        req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())

    def initiate(self, payment):
        self._check_config()
        payload = {
            "store_id": settings.SSLCOMMERZ_STORE_ID,
            "store_passwd": settings.SSLCOMMERZ_STORE_PASSWORD,
            "total_amount": str(payment.amount),
            "currency": "BDT",
            "tran_id": payment.reference,
            "success_url": _abs(reverse("payment_gateway_return", args=["sslcommerz", "success"])),
            "fail_url": _abs(reverse("payment_gateway_return", args=["sslcommerz", "fail"])),
            "cancel_url": _abs(reverse("payment_gateway_return", args=["sslcommerz", "cancel"])),
            "ipn_url": _abs(reverse("payment_gateway_return", args=["sslcommerz", "ipn"])),
            "cus_name": payment.member.name[:50],
            "cus_email": (payment.member.user.email if payment.member.user_id and payment.member.user.email else "noreply@example.com"),
            "cus_add1": (payment.member.address or "Dhaka")[:50],
            "cus_city": "Dhaka", "cus_country": "Bangladesh",
            "cus_phone": payment.member.phone_number or "01700000000",
            "shipping_method": "NO", "product_name": "Society contribution",
            "product_category": "Contribution", "product_profile": "non-physical-goods",
        }
        try:
            data = self._post(f"{self.base}/gwprocess/v4/api.php", payload)
        except Exception as exc:
            raise GatewayError(f"Could not reach SSLCommerz: {exc}")
        if data.get("status") != "SUCCESS" or not data.get("GatewayPageURL"):
            raise GatewayError(f"SSLCommerz refused the session: {data.get('failedreason', 'unknown reason')}")
        return data["GatewayPageURL"]

    def validate(self, payment, post):
        """Server-to-server validation. Returns (ok, raw_dict)."""
        self._check_config()
        val_id = post.get("val_id", "")
        if not val_id:
            return False, {"error": "missing val_id"}
        qs = urllib.parse.urlencode({
            "val_id": val_id, "store_id": settings.SSLCOMMERZ_STORE_ID,
            "store_passwd": settings.SSLCOMMERZ_STORE_PASSWORD, "format": "json",
        })
        try:
            with urllib.request.urlopen(f"{self.base}/validator/api/validationserverAPI.php?{qs}", timeout=20) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:
            return False, {"error": f"validation request failed: {exc}"}
        ok = (
            data.get("status") in ("VALID", "VALIDATED")
            and data.get("tran_id") == payment.reference
            and data.get("currency_type", "BDT") == "BDT"
            and Decimal(str(data.get("amount", "0"))) == payment.amount
        )
        return ok, data


GATEWAYS = {"manual": ManualGateway, "sandbox": SandboxGateway, "sslcommerz": SSLCommerzGateway}


def get_gateway(name):
    try:
        return GATEWAYS[name]()
    except KeyError:
        raise GatewayError(f"Unknown gateway '{name}'.")
