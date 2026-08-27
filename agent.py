import os
import json
from dotenv import load_dotenv
from google import genai
from mandates import IntentMandate, check_cart_against_intent
import razorpay

load_dotenv()

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))


def load_catalog():
    with open("catalog.json", "r") as f:
        catalog = json.load(f)
    return catalog


def ask_agent_to_pick_item(user_request, catalog):
    prompt = f"""
    You are a shopping assistant agent for a merchant.
    Here is the product catalog (JSON):
    {json.dumps(catalog)}

    The customer has requested: "{user_request}"

    Pick the one best matching product from the catalog.
    Respond only in this exact JSON format, nothing else:
    {{
        "item_id": "the matching product id",
        "reasoning": "one short sentence explaining why you picked it"
    }}
    """
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=prompt
    )
    return response.text


def get_human_approval(item, cart_total):
    print("\n--- Approval Required ---")
    print(f"Agent wants to buy: {item['name']} for ₹{cart_total}")
    decision = input("Approve this purchase? (yes/no): ").strip().lower()
    return decision == "yes"

def create_and_pay_order(item, cart_total):
    client_razorpay = razorpay.Client(
        auth=(os.getenv("RAZORPAY_KEY_ID"), os.getenv("RAZORPAY_KEY_SECRET"))
    )
    order = client_razorpay.order.create({
        "amount": cart_total * 100,  # Razorpay uses paise
        "currency": "INR",
        "receipt": f"agentic_{item['id']}"
    })
    print("\n--- Razorpay Order Created ---")
    print("Order ID:", order["id"])
    print("Amount:", order["amount"] / 100, "INR")
    return order

if __name__ == "__main__":
    catalog = load_catalog()
    print("Loaded catalog:", catalog)

    intent = IntentMandate(
        max_amount=300,
        allowed_categories=["beverages", "snacks", "grocery"],
        expiry="2026-12-31"
    )

    user_request = "I want something for my morning tea routine, under 300 rupees"
    result = ask_agent_to_pick_item(user_request, catalog)
    print("Agent response:\n", result)

    picked = json.loads(result)
    if picked["item_id"] == "none":
        print("Agent found no match.")
    else:
        item = next(p for p in catalog if p["id"] == picked["item_id"])
        cart_total = item["price"]
        cart_category = item["category"]

        allowed, reason = check_cart_against_intent(cart_total, cart_category, intent)
        print("\n--- Mandate Check ---")
        print("Item:", item["name"], "| Price:", cart_total, "| Category:", cart_category)
        print("Allowed:", allowed, "| Reason:", reason)

        if not allowed:
            print("Blocked before reaching human — reason:", reason)
        else:
            approved = get_human_approval(item, cart_total)
            if approved:
                print("Human approved. Ready to proceed to payment. ✅")
                order = create_and_pay_order(item, cart_total)
            else:
                print("Human rejected. Transaction cancelled. ❌")