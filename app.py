from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/")
def home():
    return "Server Shopify-Poste attivo", 200

@app.route("/shipping-rates", methods=["POST"])
def shipping_rates():
    data = request.get_json(silent=True)
    print("SHOPIFY REQUEST:", data)

    return jsonify({
        "rates": [
            {
                "service_name": "Poste Italiane Standard",
                "service_code": "standard",
                "total_price": "500",
                "currency": "EUR"
            }
        ]
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
