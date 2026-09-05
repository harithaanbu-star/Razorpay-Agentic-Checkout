import os
import json
import re
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from google import genai
import razorpay
from mandates import IntentMandate, check_cart_against_intent

load_dotenv()

app = FastAPI()

processed_payment_ids = set()

gemini_client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

rzp_client = razorpay.Client(
    auth=(
        os.getenv("RAZORPAY_KEY_ID"),
        os.getenv("RAZORPAY_KEY_SECRET")
    )
)

INTENT = IntentMandate(
    max_amount=300,
    allowed_categories=["beverages", "snacks", "grocery"],
    expiry="2026-12-31"
)

state = {
    "pending": None,
    "audit_log": []
}


def load_catalog():
    with open("catalog.json", "r") as f:
        return json.load(f)


def log(event):
    state["audit_log"].append(event)
    print("AUDIT:", event)


def parse_gemini_json(text):
    raw = text.strip()

    print("RAW GEMINI RESPONSE:", repr(raw))

    raw = re.sub(
        r"^```(?:json)?\s*",
        "",
        raw,
        flags=re.IGNORECASE
    )

    raw = re.sub(
        r"\s*```$",
        "",
        raw
    )

    raw = raw.strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, re.DOTALL)

        if match:
            return json.loads(match.group(0))

        raise


@app.get("/")
def home():
    return FileResponse("index.html")


@app.get("/audit")
def get_audit():
    return {
        "log": state["audit_log"]
    }


@app.post("/request")
async def customer_request(request: Request):
    body = await request.json()
    user_request = body["message"]

    log(f'Customer request received: "{user_request}"')

    catalog = load_catalog()

    prompt = f"""
You are a shopping assistant agent for a merchant.

Catalog:
{json.dumps(catalog)}

Customer said:
"{user_request}"

Pick UP TO 4 matching products, best match first.

Rules:
- Only select products that actually exist in the catalog.
- Do not invent product IDs.
- Return an empty list if nothing matches.
- Return at most 4 product IDs.
- Keep reasoning to one short sentence.
- Respond ONLY with valid JSON.
- Do NOT use markdown code fences.
- Do NOT include any text before or after the JSON.

Required format:
{{"item_ids": ["...", "..."], "reasoning": "one short sentence"}}
"""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-3.6-flash",
            contents=prompt
        )
    except Exception as e:
        log(f"Gemini API error: {str(e)}")
        return {
            "status": "error",
            "message": "Unable to contact the shopping agent."
        }

    try:
        picked = parse_gemini_json(response.text)
    except (json.JSONDecodeError, TypeError, AttributeError):
        log(
            "Agent returned an invalid response — "
            "treating as no match"
        )
        return {
            "status": "no_match",
            "reasoning": "Could not parse agent response. Try rephrasing."
        }

    item_ids = picked.get("item_ids", [])

    if not isinstance(item_ids, list):
        item_ids = []

    reasoning = picked.get(
        "reasoning",
        "Matching products found."
    )

    valid_catalog_ids = {
        product["id"]
        for product in catalog
    }

    item_ids = [
        item_id
        for item_id in item_ids
        if item_id in valid_catalog_ids
    ]

    item_ids = item_ids[:4]

    items = [
        product
        for product in catalog
        if product["id"] in item_ids
    ]

    if not items:
        log(
            f"Agent found no matching product. "
            f"Reason: {reasoning}"
        )

        return {
            "status": "no_match",
            "reasoning": reasoning
        }

    ordered_items = []

    for item_id in item_ids:
        for item in items:
            if item["id"] == item_id:
                ordered_items.append(item)
                break

    items = ordered_items[:4]

    log(
        f"Agent found {len(items)} option(s): "
        f"{[i['name'] for i in items]} — {reasoning}"
    )

    return {
        "status": "options",
        "items": items,
        "reasoning": reasoning
    }


@app.post("/select")
async def select_item(request: Request):
    body = await request.json()

    item_id = body["item_id"]

    try:
        quantity = int(body.get("quantity", 1))
    except (ValueError, TypeError):
        quantity = 1

    quantity = max(1, quantity)

    catalog = load_catalog()

    item = next(
        (p for p in catalog if p["id"] == item_id),
        None
    )

    if not item:
        log(
            f"Selection failed — unknown item ID: {item_id}"
        )

        return {
            "status": "error",
            "message": "Selected product was not found."
        }

    cart_total = item["price"] * quantity

    allowed, reason = check_cart_against_intent(
        cart_total,
        item["category"],
        INTENT
    )

    log(
        f"Selected: {item['name']} x{quantity} "
        f"(₹{cart_total}) — Mandate check: "
        f"{'PASSED' if allowed else 'BLOCKED'} — {reason}"
    )

    if not allowed:
        return {
            "status": "blocked",
            "item": item,
            "quantity": quantity,
            "total": cart_total,
            "reason": reason
        }

    state["pending"] = {
        "item": item,
        "quantity": quantity,
        "total": cart_total
    }

    return {
        "status": "awaiting_approval",
        "item": item,
        "quantity": quantity,
        "total": cart_total
    }


@app.post("/approve")
def approve():
    pending = state["pending"]

    if not pending:
        return {
            "status": "error",
            "message": "Nothing pending"
        }

    item = pending["item"]
    quantity = pending["quantity"]
    total = pending["total"]

    log(
        f"Human APPROVED purchase of "
        f"{item['name']} x{quantity} (₹{total})"
    )

    try:
        order = rzp_client.order.create({
            "amount": total * 100,
            "currency": "INR",
            "receipt": f"agentic_{item['id']}_{quantity}"
        })
    except Exception as e:
        log(
            f"Razorpay order creation failed: {str(e)}"
        )

        return {
            "status": "error",
            "message": "Could not create Razorpay order."
        }

    log(
        f"Razorpay order created: {order['id']}"
    )

    state["pending"] = None

    return {
        "status": "order_created",
        "order_id": order["id"],
        "amount": order["amount"],
        "key_id": os.getenv("RAZORPAY_KEY_ID"),
        "item_name": f"{item['name']} x{quantity}"
    }


@app.post("/reject")
def reject():
    pending = state["pending"]

    if pending:
        item = pending["item"]
        quantity = pending["quantity"]

        log(
            f"Human REJECTED purchase of "
            f"{item['name']} x{quantity}"
        )

    state["pending"] = None

    return {
        "status": "rejected"
    }


@app.post("/webhook")
async def razorpay_webhook(request: Request):
    data = await request.json()

    event = data.get("event")

    payment_id = (
        data
        .get("payload", {})
        .get("payment", {})
        .get("entity", {})
        .get("id")
    )

    if payment_id and payment_id in processed_payment_ids:
        log(
            f"Duplicate webhook ignored for {payment_id}"
        )

        return {
            "status": "already_processed"
        }

    if payment_id:
        processed_payment_ids.add(payment_id)

    if event == "payment.captured":
        payment = data["payload"]["payment"]["entity"]

        log(
            f"✅ Payment SUCCESS — "
            f"ID: {payment['id']}, "
            f"Amount: ₹{payment['amount'] / 100}"
        )

    elif event == "payment.failed":
        payment = data["payload"]["payment"]["entity"]

        reason = payment.get(
            "error_description",
            "Unknown reason"
        )

        log(
            f"❌ Payment FAILED — "
            f"ID: {payment['id']}, "
            f"Reason: {reason}. "
            f"Handled gracefully, no ambiguous state."
        )

    else:
        log(
            f"Webhook received (other event): {event}"
        )

    return {
        "status": "ok"
    }