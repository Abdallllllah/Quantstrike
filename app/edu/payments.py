"""
Payments (FR-S5, FR-P1, FR-M1). Provider-agnostic: a purchase creates a
pending reg_payments row and asks the provider to push a USSD prompt to the
PARENT's phone; when the provider confirms, reg_grant_payment() adds the
credits / season pass / track access exactly once.

Provider "manual" (the default) does nothing outbound: an admin confirms the
payment from the admin API. To go live, implement a provider class for the
chosen aggregator (Campay, CinetPay, ...) and set CARATI_PAYMENT_PROVIDER.
All amounts are FCFA.
"""
import os
from typing import Optional

from app.edu import allowance, config
from app.edu.db import db, one, rpc
from app.edu.phone import normalize_phone


class ManualProvider:
    name = "manual"

    def request_payment(self, payment: dict) -> dict:
        """Ask the aggregator for a USSD push. Manual: nothing to send."""
        return {"provider_ref": None, "instructions": (
            "Payment requested. A Carati admin will confirm it once the mobile-money "
            "transfer is received.")}


_PROVIDERS = {"manual": ManualProvider}


def provider():
    return _PROVIDERS.get(os.getenv("CARATI_PAYMENT_PROVIDER", "manual"), ManualProvider)()


def catalogue() -> list:
    today = allowance.wat_now().date()
    out = []
    for code, p in config.PRODUCTS.items():
        item = {"code": code, **p, "currency": "FCFA"}
        if p["type"] == "season_pass":
            item["valid_until"] = config.season_end(today).isoformat()
        out.append(item)
    return out


def _create(user: dict, payer_phone: str, ptype: str, code: str, quantity: int, amount: int,
            track_id: Optional[str] = None, metadata: Optional[dict] = None) -> dict:
    payer = normalize_phone(payer_phone)
    if not payer:
        raise ValueError("A valid parent phone number is required to pay")
    row = db().table("reg_payments").insert({
        "user_id": user["id"], "payer_phone": payer, "type": ptype, "product_code": code,
        "quantity": quantity, "amount_fcfa": amount, "track_id": track_id,
        "provider": provider().name, "metadata": metadata or {},
    }).execute().data[0]
    push = provider().request_payment(row)
    if push.get("provider_ref"):
        db().table("reg_payments").update({"provider_ref": push["provider_ref"]}).eq("id", row["id"]).execute()
    return {"payment": _public(row), "instructions": push.get("instructions")}


def buy_topup(user: dict, product_code: str, parent_phone: str) -> dict:
    """Question pack, paper pack or season pass. The parent's phone receives the
    payment push and the receipt (FR-S5)."""
    p = config.PRODUCTS.get(product_code)
    if not p:
        raise ValueError("Unknown product")
    meta = {}
    if p["type"] == "season_pass":
        meta["until"] = config.season_end(allowance.wat_now().date()).isoformat()
    return _create(user, parent_phone, p["type"], product_code, p["quantity"], p["price_fcfa"], metadata=meta)


def buy_track(user: dict, track: dict, enrolment_id: str, parent_phone: str) -> dict:
    return _create(user, parent_phone, "track", f"track:{track['id']}", 0, track["price_fcfa"],
                   track_id=track["id"], metadata={"enrolment_id": enrolment_id})


def confirm(payment_id: str, provider_ref: Optional[str] = None) -> str:
    """Mark paid and grant what was bought. Idempotent (safe for webhook retries)."""
    if provider_ref:
        db().table("reg_payments").update({"provider_ref": provider_ref}).eq("id", payment_id) \
            .is_("provider_ref", "null").execute()
    return rpc("reg_grant_payment", {"p_payment": payment_id})


def mark_failed(payment_id: str) -> None:
    db().table("reg_payments").update({"status": "failed"}).eq("id", payment_id).eq("status", "pending").execute()


def history(user_id: str) -> list:
    rows = (db().table("reg_payments").select("*").eq("user_id", user_id)
            .order("created_at", desc=True).limit(100).execute().data) or []
    return [_public(r) for r in rows]


def get(payment_id: str) -> Optional[dict]:
    return one(db().table("reg_payments").select("*").eq("id", payment_id))


def _public(r: dict) -> dict:
    return {k: r.get(k) for k in ("id", "type", "product_code", "quantity", "amount_fcfa", "status",
                                  "payer_phone", "track_id", "created_at", "paid_at")}
