from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

# Credenziali Poste Italiane
CLIENT_ID = "842f24cd-7e53-43cb-8a6f-a7930afda146"
SECRET_ID = "2S78Q-Z4JMyQJ1JejV0-SgYsywRUrC2ScvmOcFD"

def get_poste_token():
    response = requests.post(
        "https://api.poste.it/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": SECRET_ID
        }
    )
    return response.json().get("access_token")

@app.route('/shipping-rates', methods=['POST'])
def shipping_rates():
    data = request.json
    token = get_poste_token()
    
    # Per ora restituiamo tariffe di esempio
    return jsonify({
        "rates": [
            {
                "service_name": "Poste Italiane Standard",
                "service_code": "standard",
                "total_price": "500",
                "currency": "EUR",
                "min_delivery_date": "2026-03-20",
                "max_delivery_date": "2026-03-22"
            }
        ]
    })

if __name__ == '__main__':
    app.run(port=5000)
