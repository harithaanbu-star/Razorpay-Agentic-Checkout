# This file defines the shape of our AP2-inspired mandates.
# We are NOT implementing full AP2/W3C Verifiable Credentials —
# this is a simplified version for our buildathon project.

class IntentMandate:
    def __init__(self, max_amount, allowed_categories, expiry):
        self.max_amount = max_amount
        self.allowed_categories = allowed_categories
        self.expiry = expiry

class CartMandate:
    def __init__(self, items, total, reasoning):
        self.items = items
        self.total = total
        self.reasoning = reasoning

class PaymentMandate:
    def __init__(self, cart, signature, timestamp):
        self.cart = cart
        self.signature = signature
        self.timestamp = timestamp

def check_cart_against_intent(cart_total, cart_category, intent: IntentMandate):
    if cart_total > intent.max_amount:
        return False, f"Cart total ₹{cart_total} exceeds allowed max of ₹{intent.max_amount}"
    if cart_category not in intent.allowed_categories:
        return False, f"Category '{cart_category}' is not in allowed categories {intent.allowed_categories}"
    return True, "Within bounds"