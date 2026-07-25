"""
Business (Tara) shop-assistant API — prefix /api/business. Fully isolated from
the school app (biz_* tables only).

Pipeline per message:
  input (voice / text / photo) -> transcribe or OCR -> LLM extracts intent +
  structured transactions ("the key thing") -> BACKEND does the money maths
  deterministically -> short reply. Records are scoped per shopkeeper (phone).
"""
import re
import json
import uuid
import functools
from datetime import datetime, timezone, timedelta
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Depends, Form, File, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.db.supabase import get_supabase_client
from app.business.auth import make_token, get_current_business_user
from app.business.transcribe import transcribe_audio

router = APIRouter(prefix="/api/business", tags=["Business (Tara)"])


# ==========================================================================
# helpers
# ==========================================================================
def _db():
    return get_supabase_client().client


def surface_errors(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except HTTPException:
            raise
        except Exception as e:  # noqa: BLE001 — surface the real cause
            raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return wrapper


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _fmt(n) -> str:
    """Format a money amount with comma thousands separators (46,500)."""
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        v = 0.0
    return f"{int(round(v)):,}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_start() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _month_start() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-01")


CURRENCY = "KSh"


# ==========================================================================
# request models
# ==========================================================================
class RegisterRequest(BaseModel):
    phone: str = Field(..., example="+254700000000")
    name: Optional[str] = None
    shop_name: Optional[str] = None


class LoginRequest(BaseModel):
    phone: str


class EditRequest(BaseModel):
    text: str


# ==========================================================================
# entity resolution
# ==========================================================================
def _get_user_by_phone(phone: str) -> Optional[dict]:
    res = _db().table("biz_users").select("*").eq("phone_number", phone).limit(1).execute()
    return res.data[0] if res.data else None


def _auth_response(user: dict) -> dict:
    return {
        "token": make_token(user["id"]),
        "user": {
            "id": user["id"], "phone": user.get("phone_number"),
            "name": user.get("name"), "shop_name": user.get("shop_name"),
        },
    }


def _get_or_create_customer(user_id: str, name: Optional[str]) -> Optional[dict]:
    name = (name or "").strip()
    if not name:
        return None
    res = _db().table("biz_customers").select("*").eq("user_id", user_id).ilike("name", name).limit(1).execute()
    if res.data:
        return res.data[0]
    return _db().table("biz_customers").insert({"user_id": user_id, "name": name}).execute().data[0]


def _get_or_create_item(user_id: str, name: Optional[str], unit: Optional[str]) -> Optional[dict]:
    name = (name or "").strip()
    if not name:
        return None
    res = _db().table("biz_items").select("*").eq("user_id", user_id).ilike("name", name).limit(1).execute()
    if res.data:
        return res.data[0]
    payload = {"user_id": user_id, "name": name}
    if unit:
        payload["unit"] = unit
    return _db().table("biz_items").insert(payload).execute().data[0]


def _save_message(user_id: str, role: str, content: str) -> Optional[str]:
    if not content:
        return None
    res = _db().table("biz_messages").insert({"user_id": user_id, "role": role, "content": content}).execute()
    return res.data[0]["id"] if res.data else None


# ==========================================================================
# LLM extraction — the "key thing"
# ==========================================================================
_EXTRACT_SYSTEM = """You are the parser for a shop assistant used by African duka / provision-shop keepers
(Kenya; money is in Kenyan Shillings, KSh). Read the shopkeeper's message (which may be English, Swahili
or Sheng, possibly from a photo of a notebook) and return STRICT JSON describing what to do.

Classify the intent and extract every transaction mentioned. JSON shape:
{
  "intent": "record" | "query" | "correction" | "advice" | "chitchat",
  "transactions": [
    {
      "type": "sale" | "restock" | "credit" | "payment" | "expense",
      "item": "sugar" | null,
      "quantity": 4 | null,
      "unit": "kg" | null,
      "amount": 10400,               // total value in KSh (number, no separators)
      "unit_price": 2600 | null,
      "cost_price": null,            // per-unit BUY price, only for restock or if stated
      "credit_amount": 0,            // portion of a SALE given on credit (0 if fully paid)
      "customer": "Marie" | null     // required for credit and payment
    }
  ],
  "query": "cash" | "debts" | "profit" | "restock" | "report" | "customer_balance" | "business" | null,
  "query_customer": "Marie" | null   // for customer_balance
}

Rules:
- "sold / j'ai vendu / I sell" -> sale. "bought / restock / j'ai acheté" -> restock.
- "X took/owes / à crédit / na credit" -> credit (goods on credit; amount is what is owed).
- "X paid / a payé / don pay" -> payment.
- If a quantity and unit price are given, amount = quantity * unit_price when the total isn't stated.
- "my cash / combien dans caisse / how much money" -> intent query, query "cash".
- "who owes / les dettes / who get my money" -> query "debts".
- "my profit / bénéfice" -> "profit". "what to restock / restocking list" -> "restock".
- "report / résumé du jour" -> "report". "how much does Marie owe" -> "customer_balance" + query_customer.
- "how is my business doing / how's business / how am I doing / business overview / comment va mon commerce" -> "business".
- A pricing question ("I bought the carton at 14500, what do I sell at?") -> intent "advice".
- Greetings / small talk -> "chitchat".
Return ONLY the JSON object."""


async def _extract(text: str, image_url: Optional[str], recent: list) -> dict:
    from app.gateway.routes.llm_clients import async_openrouter_client, GEMINI_MODEL

    context = ""
    if recent:
        context = "Recent messages (oldest first):\n" + "\n".join(
            f"{m.get('role')}: {m.get('content')}" for m in recent[-6:]
        ) + "\n\n"
    user_text = f"{context}Message: {text or '(see attached image)'}"

    if image_url:
        user_content = [
            {"type": "text", "text": user_text},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
    else:
        user_content = user_text

    resp = await async_openrouter_client.chat.completions.create(
        model=GEMINI_MODEL,
        messages=[
            {"role": "system", "content": _EXTRACT_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    raw = (resp.choices[0].message.content or "{}") if resp.choices else "{}"
    try:
        data = json.loads(raw)
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("intent", "chitchat")
    data.setdefault("transactions", [])
    data.setdefault("query", None)
    data.setdefault("query_customer", None)
    return data


async def _advice_reply(text: str, recent: list) -> str:
    """Free-form reply for pricing advice / small talk."""
    from app.gateway.routes.llm_clients import async_openrouter_client, GEMINI_MODEL
    msgs = [{"role": "system", "content": (
        "You are Tara, a friendly, concise shop assistant for Kenyan duka keepers (KSh). "
        "Reply in the language the shopkeeper used. Plain text, short. For pricing questions, suggest a "
        "sensible selling price and margin. Be practical and warm."
    )}]
    for m in recent[-6:]:
        if m.get("role") in ("user", "assistant") and m.get("content"):
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": text})
    resp = await async_openrouter_client.chat.completions.create(model=GEMINI_MODEL, messages=msgs, temperature=0.4)
    return (resp.choices[0].message.content or "").strip() if resp.choices else ""


# ==========================================================================
# recording (deterministic side effects)
# ==========================================================================
def _record_transaction(user_id: str, tx: dict, source: str, raw_text: str, message_id: Optional[str] = None) -> dict:
    ttype = (tx.get("type") or "sale").strip().lower()
    if ttype not in ("sale", "restock", "credit", "payment", "expense"):
        ttype = "other"

    amount = _num(tx.get("amount")) or 0.0
    qty = _num(tx.get("quantity"))
    unit_price = _num(tx.get("unit_price"))
    cost_price = _num(tx.get("cost_price"))
    credit_amount = _num(tx.get("credit_amount")) or 0.0
    if amount == 0 and qty and unit_price:
        amount = qty * unit_price

    item = _get_or_create_item(user_id, tx.get("item"), tx.get("unit"))
    customer = None
    if ttype in ("credit", "payment") or credit_amount > 0:
        customer = _get_or_create_customer(user_id, tx.get("customer"))

    direction = "in" if ttype in ("sale", "payment") else "out"
    # 'credit' = goods out on full credit; treat the whole amount as owed.
    if ttype == "credit":
        credit_amount = amount
        direction = "out"

    # Pull cost from the item if we know it and the message didn't state one.
    if cost_price is None and item and item.get("cost_price") is not None:
        cost_price = _num(item.get("cost_price"))

    row = _db().table("biz_transactions").insert({
        "user_id": user_id,
        "type": ttype,
        "item_name": (tx.get("item") or None),
        "item_id": item["id"] if item else None,
        "quantity": qty,
        "unit": tx.get("unit"),
        "amount": amount,
        "credit_amount": credit_amount,
        "unit_price": unit_price,
        "cost_price": cost_price,
        "customer_id": customer["id"] if customer else None,
        "customer_name": customer["name"] if customer else (tx.get("customer") or None),
        "direction": direction,
        "source": source,
        "raw_text": raw_text,
        "message_id": message_id,
        "occurred_at": _now_iso(),
    }).execute().data[0]

    # Item side effects: stock + prices.
    if item:
        updates = {}
        stock = _num(item.get("stock_qty")) or 0.0
        if qty:
            if ttype in ("sale", "credit"):
                updates["stock_qty"] = stock - qty
            elif ttype == "restock":
                updates["stock_qty"] = stock + qty
        if ttype == "restock" and cost_price is not None:
            updates["cost_price"] = cost_price
        if ttype in ("sale", "credit") and unit_price is not None and item.get("sell_price") is None:
            updates["sell_price"] = unit_price
        if updates:
            _db().table("biz_items").update(updates).eq("id", item["id"]).execute()

    return row


def _line_for(tx: dict) -> str:
    parts = []
    if tx.get("item_name"):
        parts.append(str(tx["item_name"]))
    if tx.get("quantity"):
        q = tx["quantity"]
        q = int(q) if float(q).is_integer() else q
        parts.append(f"{q}{(' ' + tx['unit']) if tx.get('unit') else ''}")
    if tx.get("amount"):
        parts.append(f"{_fmt(tx['amount'])} {CURRENCY}")
    label = {"sale": "Sold", "restock": "Restocked", "credit": "On credit", "payment": "Payment", "expense": "Expense"}.get(tx.get("type"), "Noted")
    who = f" · {tx['customer_name']}" if tx.get("customer_name") else ""
    return f"{label}: " + " · ".join(parts) + who


# ==========================================================================
# computations (the money maths — done in code, never by the LLM)
# ==========================================================================
def _today_rows(user_id: str) -> list:
    return (_db().table("biz_transactions").select("*")
            .eq("user_id", user_id).gte("occurred_at", _today_start()).execute().data or [])


def _cash_summary(rows: list) -> dict:
    sales = sum(_num(r.get("amount")) or 0 for r in rows if r.get("type") == "sale")
    credit_given = sum(_num(r.get("credit_amount")) or 0 for r in rows if r.get("type") in ("sale", "credit"))
    payments = sum(_num(r.get("amount")) or 0 for r in rows if r.get("type") == "payment")
    cash_out = sum(_num(r.get("amount")) or 0 for r in rows if r.get("type") in ("restock", "expense"))
    drawer = sales - credit_given + payments - cash_out
    return {"sales": sales, "credit_given": credit_given, "payments": payments,
            "cash_out": cash_out, "drawer": drawer}


def _debts(user_id: str) -> list:
    rows = (_db().table("biz_transactions").select("customer_id,customer_name,type,amount,credit_amount")
            .eq("user_id", user_id).not_.is_("customer_id", "null").execute().data or [])
    bal: dict = {}
    for r in rows:
        cid = r.get("customer_id")
        if not cid:
            continue
        b = bal.setdefault(cid, {"customer_id": cid, "name": r.get("customer_name"), "balance": 0.0})
        if r.get("type") in ("sale", "credit"):
            b["balance"] += _num(r.get("credit_amount")) or 0
        elif r.get("type") == "payment":
            b["balance"] -= _num(r.get("amount")) or 0
    out = [b for b in bal.values() if round(b["balance"]) > 0]
    out.sort(key=lambda x: x["balance"], reverse=True)
    return out


def _profit(user_id: str, since: str) -> dict:
    rows = (_db().table("biz_transactions").select("type,amount,quantity,cost_price")
            .eq("user_id", user_id).eq("type", "sale").gte("occurred_at", since).execute().data or [])
    known = 0.0
    unknown = 0
    for r in rows:
        amount = _num(r.get("amount")) or 0
        cost_pu = _num(r.get("cost_price"))
        qty = _num(r.get("quantity")) or 0
        if cost_pu is not None and qty:
            known += amount - cost_pu * qty
        elif cost_pu is not None:
            known += amount - cost_pu
        else:
            unknown += 1
    return {"profit": known, "sales_without_cost": unknown}


def _restock_list(user_id: str) -> list:
    items = (_db().table("biz_items").select("name,unit,stock_qty,low_stock_threshold")
             .eq("user_id", user_id).execute().data or [])
    low = []
    for it in items:
        thr = _num(it.get("low_stock_threshold")) or 0
        stock = _num(it.get("stock_qty")) or 0
        if thr > 0 and stock <= thr:
            low.append({"name": it["name"], "stock": stock, "unit": it.get("unit")})
    return low


# ==========================================================================
# reply builders
# ==========================================================================
def _reply_cash(user_id: str) -> str:
    s = _cash_summary(_today_rows(user_id))
    return (f"Today — Sales: {_fmt(s['sales'])} · Credit given: {_fmt(s['credit_given'])}"
            f"{(' · Payments: ' + _fmt(s['payments'])) if s['payments'] else ''}"
            f"{(' · Cash out: ' + _fmt(s['cash_out'])) if s['cash_out'] else ''}\n"
            f"The drawer should hold: {_fmt(s['drawer'])} {CURRENCY}")


def _reply_debts(user_id: str) -> str:
    debts = _debts(user_id)
    if not debts:
        return "Nobody owes you right now. 👍"
    total = sum(d["balance"] for d in debts)
    lines = [f"• {d['name']}: {_fmt(d['balance'])} {CURRENCY}" for d in debts[:15]]
    return f"Owed to you: {_fmt(total)} {CURRENCY}\n" + "\n".join(lines)


def _reply_customer_balance(user_id: str, name: Optional[str]) -> str:
    if not name:
        return _reply_debts(user_id)
    for d in _debts(user_id):
        if (d["name"] or "").lower() == name.lower():
            return f"{d['name']} owes you {_fmt(d['balance'])} {CURRENCY}."
    return f"{name} owes you nothing right now."


def _reply_profit(user_id: str) -> str:
    p = _profit(user_id, _month_start())
    note = f" (excludes {p['sales_without_cost']} sales with no cost recorded)" if p["sales_without_cost"] else ""
    return f"Profit this month: about {_fmt(p['profit'])} {CURRENCY}{note}."


def _reply_restock(user_id: str) -> str:
    low = _restock_list(user_id)
    if not low:
        return "Nothing is low on stock right now."
    lines = [f"• {i['name']} ({_fmt(i['stock'])}{(' ' + i['unit']) if i.get('unit') else ''} left)" for i in low]
    return "To restock:\n" + "\n".join(lines)


def _reply_report(user_id: str) -> str:
    s = _cash_summary(_today_rows(user_id))
    debts = _debts(user_id)
    p = _profit(user_id, _today_start())
    out = [
        "Today's report",
        f"Sales: {_fmt(s['sales'])} {CURRENCY}",
        f"Credit given: {_fmt(s['credit_given'])} {CURRENCY}",
        f"Drawer should hold: {_fmt(s['drawer'])} {CURRENCY}",
        f"Profit today: about {_fmt(p['profit'])} {CURRENCY}",
    ]
    if debts:
        out.append(f"Outstanding debts: {_fmt(sum(d['balance'] for d in debts))} {CURRENCY} from {len(debts)} people")
    return "\n".join(out)


def _business_stats(user_id: str) -> dict:
    """Aggregate the whole ledger into headline numbers over several periods."""
    rows = (_db().table("biz_transactions")
            .select("type,amount,credit_amount,quantity,cost_price,item_name,occurred_at")
            .eq("user_id", user_id).order("occurred_at", desc=True).limit(3000).execute().data or [])
    day = _today_start()
    month = _month_start()
    week = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()

    def agg(since: Optional[str]) -> dict:
        def keep(r):
            return since is None or (r.get("occurred_at") or "") >= since
        sales = [r for r in rows if r.get("type") == "sale" and keep(r)]
        total = sum(_num(r.get("amount")) or 0 for r in sales)
        profit = 0.0
        for r in sales:
            cp = _num(r.get("cost_price"))
            if cp is not None:
                q = _num(r.get("quantity")) or 1
                profit += (_num(r.get("amount")) or 0) - cp * q
        credit = sum(_num(r.get("credit_amount")) or 0
                     for r in rows if r.get("type") in ("sale", "credit") and keep(r))
        return {"sales": round(total), "count": len(sales), "profit": round(profit), "credit_given": round(credit)}

    items: dict = {}
    for r in rows:
        if r.get("type") == "sale" and r.get("item_name"):
            items[r["item_name"]] = items.get(r["item_name"], 0) + (_num(r.get("amount")) or 0)
    top = sorted(items.items(), key=lambda x: x[1], reverse=True)[:5]
    debts = _debts(user_id)
    return {
        "today": agg(day), "this_week": agg(week), "this_month": agg(month), "all_time": agg(None),
        "top_items": [{"item": k, "sold": round(v)} for k, v in top],
        "owed_to_you": round(sum(d["balance"] for d in debts)),
        "people_who_owe": len(debts),
        "currency": CURRENCY,
    }


async def _describe_business(stats: dict) -> str:
    from app.gateway.routes.llm_clients import async_openrouter_client, GEMINI_MODEL
    prompt = (
        "You are Tara, a warm shop assistant. Using ONLY the figures below (all in KSh), write a short "
        "plain-text summary (3 to 5 sentences) of how the shop is doing: how much has been sold "
        "(today / this week / this month), the profit, the best-selling items, how much money is owed to "
        "the shopkeeper, and ONE practical suggestion. Be encouraging and concrete. No markdown, no "
        "asterisks, no bullet points.\n\nFigures:\n" + json.dumps(stats, ensure_ascii=False)
    )
    try:
        resp = await async_openrouter_client.chat.completions.create(
            model=GEMINI_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0.4,
        )
        return (resp.choices[0].message.content or "").strip() if resp.choices else ""
    except Exception:
        return ""


async def _reply_business(user_id: str) -> str:
    """How's my business doing — deterministic headline + an LLM narrative."""
    st = _business_stats(user_id)
    m, a = st["this_month"], st["all_time"]
    headline = (
        f"This month you've sold {_fmt(m['sales'])} {CURRENCY} across {m['count']} "
        f"{'sale' if m['count'] == 1 else 'sales'}"
        f"{', profit about ' + _fmt(m['profit']) + ' ' + CURRENCY if m['profit'] else ''}. "
        f"All-time: {_fmt(a['sales'])} {CURRENCY}."
    )
    desc = await _describe_business(st)
    return headline + ("\n\n" + desc if desc else "")


def _query_reply(user_id: str, query: Optional[str], query_customer: Optional[str]) -> str:
    return {
        "cash": lambda: _reply_cash(user_id),
        "debts": lambda: _reply_debts(user_id),
        "profit": lambda: _reply_profit(user_id),
        "restock": lambda: _reply_restock(user_id),
        "report": lambda: _reply_report(user_id),
        "customer_balance": lambda: _reply_customer_balance(user_id, query_customer),
    }.get(query, lambda: "Ask me about your cash, debts, profit, restocking, or a customer's balance.")()


# ==========================================================================
# AUTH endpoints
# ==========================================================================
@router.post("/register", status_code=201)
@surface_errors
async def register(payload: RegisterRequest):
    phone = payload.phone.strip()
    if not phone:
        raise HTTPException(status_code=400, detail="phone is required")
    existing = _get_user_by_phone(phone)
    if existing:
        return _auth_response(existing)  # phone is the identity — just log them in
    user = _db().table("biz_users").insert({
        "phone_number": phone,
        "name": (payload.name or "").strip() or None,
        "shop_name": (payload.shop_name or "").strip() or None,
    }).execute().data[0]
    return _auth_response(user)


@router.post("/login")
@surface_errors
async def login(payload: LoginRequest):
    user = _get_user_by_phone(payload.phone.strip())
    if not user:
        raise HTTPException(status_code=404, detail="No shop found for this phone. Register first.")
    return _auth_response(user)


@router.get("/me")
@surface_errors
async def me(user: dict = Depends(get_current_business_user)):
    return {"user": {"id": user["id"], "phone": user.get("phone_number"),
                     "name": user.get("name"), "shop_name": user.get("shop_name")}}


# ==========================================================================
# THE MESSAGE PIPELINE
# ==========================================================================
_AUDIO_EXTS = (".wav", ".mp3", ".m4a", ".ogg", ".oga", ".webm", ".amr", ".aac")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")


async def _process_message(user: dict, text: str = "", data: Optional[bytes] = None,
                           filename: str = "", mime: str = "") -> dict:
    """The core Tara pipeline, shared by the web API and the WhatsApp webhook.

    Takes raw text and/or an attachment (voice note or photo), records or
    answers, and returns {reply, intent, message_id, recorded}. Never raises for
    ordinary bad input — it returns a friendly reply instead, so a WhatsApp
    sender always gets an answer.
    """
    user_id = user["id"]
    source = "text"
    said = (text or "").strip()
    image_url: Optional[str] = None

    if data:
        m = (mime or "").lower()
        fn = (filename or "").lower()
        if m.startswith("audio") or fn.endswith(_AUDIO_EXTS):
            source = "voice"
            transcript = await transcribe_audio(data, filename or "audio.ogg", mime or "audio/ogg")
            if not transcript.strip():
                msg = "I couldn't catch that — please record again, a little closer to the phone."
                _save_message(user_id, "assistant", msg)
                return {"reply": msg, "intent": "error", "recorded": 0, "message_id": None}
            said = (said + " " if said else "") + transcript
        elif m.startswith("image") or fn.endswith(_IMAGE_EXTS):
            source = "photo"
            from app.gateway.routes.llm_clients import image_data_url
            image_url = image_data_url(data, mime or "image/jpeg")

    if not said and image_url is None:
        return {"reply": "Send a message, a voice note, or a photo.",
                "intent": "error", "recorded": 0, "message_id": None}

    recent = (_db().table("biz_messages").select("role,content")
              .eq("user_id", user_id).order("created_at", desc=True).limit(6).execute().data or [])
    recent = list(reversed(recent))

    user_msg_id = _save_message(user_id, "user", said or "(photo)")
    parsed = await _extract(said, image_url, recent)
    intent = parsed.get("intent")
    txs = parsed.get("transactions") or []

    if intent in ("record", "correction"):
        if intent == "correction":
            # simplest correction: undo the most recent transaction, then record the fix
            last = (_db().table("biz_transactions").select("id")
                    .eq("user_id", user_id).order("created_at", desc=True).limit(1).execute().data)
            if last:
                _db().table("biz_transactions").delete().eq("id", last[0]["id"]).execute()
        recorded = [_record_transaction(user_id, t, source, said, message_id=user_msg_id) for t in txs] if txs else []
        if recorded:
            lines = "\n".join(_line_for(r) for r in recorded)
            s = _cash_summary(_today_rows(user_id))
            reply = f"{'Fixed' if intent == 'correction' else 'Noted'}:\n{lines}\nToday's sales: {_fmt(s['sales'])} {CURRENCY}"
        else:
            reply = "I couldn't find anything to record — tell me what you sold, bought, or who took credit."
    elif intent == "query":
        q = parsed.get("query")
        if q == "business":
            reply = await _reply_business(user_id)
        else:
            reply = _query_reply(user_id, q, parsed.get("query_customer"))
    else:  # advice / chitchat
        reply = await _advice_reply(said, recent) or "How can I help with your shop today?"

    _save_message(user_id, "assistant", reply)
    return {"reply": reply, "intent": intent, "message_id": user_msg_id,
            "recorded": len(txs) if intent in ("record", "correction") else 0}


@router.post("/message")
@surface_errors
async def message(
    text: str = Form(""),
    file: UploadFile = File(None),
    user: dict = Depends(get_current_business_user),
):
    """Record or answer. Accepts text and/or an attachment (voice note or a
    photo of the notebook)."""
    data = filename = mime = None
    if file is not None and file.filename:
        data = await file.read()
        filename = file.filename
        mime = (file.content_type or "").lower()
    return await _process_message(user, text=text, data=data, filename=filename, mime=mime)


@router.post("/messages/{message_id}/edit")
@surface_errors
async def edit_message(message_id: str, payload: EditRequest, user: dict = Depends(get_current_business_user)):
    """WhatsApp-style edit: rewrite a message and update its record. Deletes the
    transactions the original message created, then re-records from the new text."""
    user_id = user["id"]
    new_text = (payload.text or "").strip()
    if not new_text:
        raise HTTPException(status_code=400, detail="New text is required.")

    msg = (_db().table("biz_messages").select("*")
           .eq("id", message_id).eq("user_id", user_id).limit(1).execute().data)
    if not msg or msg[0].get("role") != "user":
        raise HTTPException(status_code=404, detail="Message not found.")

    _db().table("biz_transactions").delete().eq("message_id", message_id).execute()
    _db().table("biz_messages").update({"content": new_text}).eq("id", message_id).execute()

    parsed = await _extract(new_text, None, [])
    intent = parsed.get("intent")
    txs = parsed.get("transactions") or []
    if intent in ("record", "correction"):
        recorded = [_record_transaction(user_id, t, "edit", new_text, message_id=message_id) for t in txs]
        if recorded:
            lines = "\n".join(_line_for(r) for r in recorded)
            s = _cash_summary(_today_rows(user_id))
            reply = f"Updated:\n{lines}\nToday's sales: {_fmt(s['sales'])} {CURRENCY}"
        else:
            reply = "Updated — nothing to record from that message."
    elif intent == "query":
        q = parsed.get("query")
        reply = await _reply_business(user_id) if q == "business" else _query_reply(user_id, q, parsed.get("query_customer"))
    else:
        reply = "Updated."
    return {"reply": reply, "message_id": message_id, "recorded": len(txs)}


# ==========================================================================
# READ endpoints (for the web UI / dashboards)
# ==========================================================================
@router.get("/summary")
@surface_errors
async def summary(user: dict = Depends(get_current_business_user)):
    uid = user["id"]
    s = _cash_summary(_today_rows(uid))
    p = _profit(uid, _month_start())
    debts = _debts(uid)
    return {
        "today": s,
        "profit_month": p["profit"],
        "debt_total": sum(d["balance"] for d in debts),
        "debtor_count": len(debts),
        "currency": CURRENCY,
    }


@router.get("/transactions")
@surface_errors
async def transactions(limit: int = 50, user: dict = Depends(get_current_business_user)):
    rows = (_db().table("biz_transactions").select("*")
            .eq("user_id", user["id"]).order("occurred_at", desc=True).limit(min(limit, 200)).execute().data or [])
    return {"transactions": rows}


@router.get("/debts")
@surface_errors
async def debts(user: dict = Depends(get_current_business_user)):
    d = _debts(user["id"])
    return {"debts": d, "total": sum(x["balance"] for x in d), "currency": CURRENCY}


@router.get("/customers")
@surface_errors
async def customers(user: dict = Depends(get_current_business_user)):
    rows = (_db().table("biz_customers").select("*")
            .eq("user_id", user["id"]).order("name").execute().data or [])
    return {"customers": rows}


@router.get("/items")
@surface_errors
async def items(user: dict = Depends(get_current_business_user)):
    rows = (_db().table("biz_items").select("*")
            .eq("user_id", user["id"]).order("name").execute().data or [])
    return {"items": rows, "restock": _restock_list(user["id"])}


@router.get("/report")
@surface_errors
async def report(user: dict = Depends(get_current_business_user)):
    return {"report": _reply_report(user["id"])}


@router.get("/overview")
@surface_errors
async def overview(user: dict = Depends(get_current_business_user)):
    """How's my business doing — aggregated stats + a written description."""
    st = _business_stats(user["id"])
    desc = await _describe_business(st)
    return {"stats": st, "description": desc}


# ==========================================================================
# PDF REPORTS — day / week / month / quarter / year
# ==========================================================================
def _period_range(period: str):
    """Return (start_datetime, title, human range) for a reporting period."""
    n = datetime.now(timezone.utc)
    p = (period or "day").strip().lower()
    if p in ("day", "daily", "today"):
        start = n.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, "Daily report", start.strftime("%d %B %Y")
    if p in ("week", "weekly"):
        start = (n - timedelta(days=n.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        return start, "Weekly report", f"{start.strftime('%d %b')} – {n.strftime('%d %b %Y')}"
    if p in ("month", "monthly"):
        start = n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, "Monthly report", start.strftime("%B %Y")
    if p in ("quarter", "quarterly"):
        q = (n.month - 1) // 3
        start = n.replace(month=q * 3 + 1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, "Quarterly report", f"Q{q + 1} {n.year} · {start.strftime('%b')}–{n.strftime('%b %Y')}"
    if p in ("year", "yearly", "annual", "annually"):
        start = n.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        return start, "Annual report", str(n.year)
    raise HTTPException(status_code=400, detail="period must be one of: day, week, month, quarter, year")


def _qty_str(v) -> str:
    q = _num(v)
    if not q:
        return "—"
    return str(int(q)) if float(q).is_integer() else str(q)


def _report_data(user: dict, period: str) -> dict:
    uid = user["id"]
    start, title, range_label = _period_range(period)
    rows = (_db().table("biz_transactions").select("*")
            .eq("user_id", uid).gte("occurred_at", start.isoformat())
            .order("occurred_at", desc=True).limit(2000).execute().data or [])

    sales = [r for r in rows if r.get("type") == "sale"]
    total_sales = sum(_num(r.get("amount")) or 0 for r in sales)
    credit_given = sum(_num(r.get("credit_amount")) or 0 for r in rows if r.get("type") in ("sale", "credit"))
    payments = sum(_num(r.get("amount")) or 0 for r in rows if r.get("type") == "payment")
    cash_out = sum(_num(r.get("amount")) or 0 for r in rows if r.get("type") in ("restock", "expense"))
    profit = 0.0
    for r in sales:
        cp = _num(r.get("cost_price"))
        if cp is not None:
            profit += (_num(r.get("amount")) or 0) - cp * (_num(r.get("quantity")) or 1)
    net_cash = total_sales - credit_given + payments - cash_out

    # Group sales by day (short periods) or by month (quarter / year).
    by_month = (period or "").strip().lower() in ("quarter", "quarterly", "year", "yearly", "annual", "annually")
    buckets: dict = {}
    for r in sales:
        ts = (r.get("occurred_at") or "")[:10]
        if not ts:
            continue
        key = ts[:7] if by_month else ts
        buckets[key] = buckets.get(key, 0) + (_num(r.get("amount")) or 0)
    breakdown = [{"label": k, "sales_f": _fmt(v)} for k, v in sorted(buckets.items())]
    if len(breakdown) < 2:
        breakdown = []

    items: dict = {}
    for r in sales:
        if r.get("item_name"):
            items[r["item_name"]] = items.get(r["item_name"], 0) + (_num(r.get("amount")) or 0)
    top = sorted(items.items(), key=lambda x: x[1], reverse=True)[:8]

    debts = _debts(uid)
    limit = 60
    txs = [{
        "date": (r.get("occurred_at") or "")[:10],
        "type": (r.get("type") or "").title(),
        "item": r.get("item_name"),
        "qty": _qty_str(r.get("quantity")),
        "amount_f": _fmt(r.get("amount")),
        "customer": r.get("customer_name"),
    } for r in rows[:limit]]

    return {
        "shop": user.get("shop_name") or user.get("name") or "My shop",
        "period_label": title,
        "range_label": range_label,
        "currency": CURRENCY,
        "generated_at": datetime.now(timezone.utc).strftime("%d %b %Y, %H:%M UTC"),
        "kpis": {
            "sales_f": _fmt(total_sales), "credit_given_f": _fmt(credit_given),
            "payments_f": _fmt(payments), "cash_out_f": _fmt(cash_out),
            "net_cash_f": _fmt(net_cash), "profit_f": _fmt(profit), "count": len(sales),
        },
        "breakdown": breakdown,
        "top_items": [{"item": k, "sold_f": _fmt(v)} for k, v in top],
        "debts": [{"name": d["name"], "balance_f": _fmt(d["balance"])} for d in debts],
        "transactions": txs,
        "transactions_total": len(rows),
        "transactions_truncated": len(rows) > limit,
    }


async def _report_summary(d: dict) -> str:
    """Short narrative for the top of the report (best-effort)."""
    from app.gateway.routes.llm_clients import async_openrouter_client, GEMINI_MODEL
    facts = {
        "period": d.get("period_label"), "range": d.get("range_label"),
        "kpis": d.get("kpis"), "top_items": d.get("top_items"),
        "people_who_owe": len(d.get("debts") or []),
    }
    prompt = (
        "You are Tara, a shop assistant. Write 3 to 4 plain-text sentences summarising how this shop "
        "performed for the period, using ONLY the figures given (KSh). Mention total sales, profit, "
        "what sold best, money owed, and end with one practical suggestion. No markdown, no asterisks, "
        "no bullet points.\n\n" + json.dumps(facts, ensure_ascii=False)
    )
    try:
        resp = await async_openrouter_client.chat.completions.create(
            model=GEMINI_MODEL, messages=[{"role": "user", "content": prompt}], temperature=0.4,
        )
        return (resp.choices[0].message.content or "").strip() if resp.choices else ""
    except Exception:
        return ""


@router.get("/report/pdf")
@surface_errors
async def report_pdf(period: str = "day", user: dict = Depends(get_current_business_user)):
    """Download a branded PDF report for day | week | month | quarter | year."""
    data = _report_data(user, period)
    data["summary"] = await _report_summary(data)
    from app.business.report import render_report_pdf
    pdf = render_report_pdf(data)
    p = (period or "day").strip().lower()
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="tara-{p}-report.pdf"'},
    )
