import os
import json
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from google import genai
import razorpay
from mandates import IntentMandate, check_cart_against_intent

load_dotenv()

app = FastAPI()
processed_payment_ids = set()

gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
rzp_client = razorpay.Client(auth=(os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET")))
INTENT = IntentMandate(max_amount=300, allowed_categories=["beverages", "snacks", "grocery"], expiry="2026-12-31")

state = {"pending": None, "audit_log": []}


def load_catalog():
    with open("catalog.json", "r") as f:
        return json.load(f)


def log(event):
    state["audit_log"].append(event)
    print("AUDIT:", event)


@app.get("/")
def home():
    return FileResponse("index.html")


@app.get("/audit")
def get_audit():
    return {"log": state["audit_log"]}


@app.post("/request")
async def customer_request(request: Request):
    body = await request.json()
    user_request = body["message"]
    log(f"Customer request received: \"{user_request}\"")

    catalog = load_catalog()
    prompt = f"""
    You are a shopping assistant agent for a merchant.
    Catalog: {json.dumps(catalog)}
    Customer said: "{user_request}"
    Pick the ONE best matching product. If nothing matches, use item_id "none".
    Respond ONLY in JSON: {{"item_id": "...", "reasoning": "..."}}
    """
    response = gemini_client.models.generate_content(model="gemini-3.6-flash", contents=prompt)
    try:
       picked = json.loads(response.text)
    except json.JSONDecodeError:
       log("Agent returned an invalid response — treating as no match")
       return {"status": "no_match", "reasoning": "Agent response could not be parsed. Please try rephrasing your request."}

    if picked["item_id"] == "none":
        log(f"Agent found no matching product. Reason: {picked['reasoning']}")
        return {"status": "no_match", "reasoning": picked["reasoning"]}

    item = next(p for p in catalog if p["id"] == picked["item_id"])
    log(f"Agent selected: {item['name']} (₹{item['price']}) — {picked['reasoning']}")

    allowed, reason = check_cart_against_intent(item["price"], item["category"], INTENT)
    log(f"Mandate check: {'PASSED' if allowed else 'BLOCKED'} — {reason}")

    if not allowed:
        return {"status": "blocked", "item": item, "reason": reason}

    state["pending"] = item
    return {"status": "awaiting_approval", "item": item, "reasoning": picked["reasoning"]}


@app.post("/approve")
def approve():
    item = state["pending"]
    if not item:
        return {"status": "error", "message": "Nothing pending"}

    log(f"Human APPROVED purchase of {item['name']}")

    order = rzp_client.order.create({
        "amount": item["price"] * 100,
        "currency": "INR",
        "receipt": f"agentic_{item['id']}"
    })
    log(f"Razorpay order created: {order['id']}")
    state["pending"] = None

    return {
        "status": "order_created",
        "order_id": order["id"],
        "amount": order["amount"],
        "key_id": os.getenv("RAZORPAY_KEY_ID"),
        "item_name": item["name"]
    }


@app.post("/reject")
def reject():
    item = state["pending"]
    if item:
        log(f"Human REJECTED purchase of {item['name']}")
    state["pending"] = None
    return {"status": "rejected"}


@app.post("/webhook")
async def razorpay_webhook(request: Request):
    data = await request.json()
    event = data.get("event")

    payment_id = data.get("payload", {}).get("payment", {}).get("entity", {}).get("id")
    if payment_id and payment_id in processed_payment_ids:
        log(f"Duplicate webhook ignored for {payment_id}")
        return {"status": "already_processed"}
    if payment_id:
        processed_payment_ids.add(payment_id)

    if event == "payment.captured":
        payment = data["payload"]["payment"]["entity"]
        log(f"✅ Payment SUCCESS — ID: {payment['id']}, Amount: ₹{payment['amount']/100}")
    elif event == "payment.failed":
        payment = data["payload"]["payment"]["entity"]
        reason = payment.get("error_description", "Unknown reason")
        log(f"❌ Payment FAILED — ID: {payment['id']}, Reason: {reason}. Handled gracefully, no ambiguous state.")
    else:
        log(f"Webhook received (other event): {event}")

    return {"status": "ok"}