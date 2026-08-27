from fastapi import FastAPI,Request
app=FastAPI()
@app.get("/")
def home():
    return {
        "message": "Razorpay Agentic Checkout  backend  is running!"
    }

@app.post("/webhook")
async def razorpay_webhook(request: Request):
    data=await request.json()
    print("webhook received:",data)
    return {"status":"success"}