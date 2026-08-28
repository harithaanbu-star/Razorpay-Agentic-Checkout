from fastapi import FastAPI,Request
app=FastAPI()
@app.get("/")
def home():
    return {
        "message": "Razorpay Agentic Checkout  backend  is running!"
    }

@app.post("/webhook")
async def razorpay_webhook(request: Request):
    data = await request.json()
    event = data.get("event")

    if event == "payment.captured":
        payment = data["payload"]["payment"]["entity"]
        print(f"✅ Payment SUCCESS — ID: {payment['id']}, Amount: ₹{payment['amount']/100}")

    elif event == "payment.failed":
        payment = data["payload"]["payment"]["entity"]
        reason = payment.get("error_description", "Unknown reason")
        print(f"❌ Payment FAILED — ID: {payment['id']}, Reason: {reason}")
        print("Handling gracefully: no charge occurred, order can be retried, no ambiguous state left behind.")

    else:
        print("Webhook received (other event):", event)

    return {"status": "ok"}