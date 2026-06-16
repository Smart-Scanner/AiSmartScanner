import urllib.request
import json

base_url = 'http://localhost:5051/api/portfolios/1/positions'
headers = {'Content-Type': 'application/json'}

positions = [
    {'symbol': 'RELIANCE', 'buy_price': 2500, 'quantity': 10, 'side': 'LONG', 'strategy': 'CORE'},
    {'symbol': 'TCS', 'buy_price': 3500, 'quantity': 15, 'side': 'LONG', 'strategy': 'CORE'},
    {'symbol': 'HDFCBANK', 'buy_price': 1500, 'quantity': 20, 'side': 'LONG', 'strategy': 'CORE'},
    {'symbol': 'INFY', 'buy_price': 1400, 'quantity': 25, 'side': 'LONG', 'strategy': 'CORE'},
    {'symbol': 'WIPRO', 'buy_price': 400, 'quantity': 30, 'side': 'LONG', 'strategy': 'CORE'}
]

for p in positions:
    req = urllib.request.Request(base_url, data=json.dumps(p).encode(), headers=headers, method='POST')
    try:
        urllib.request.urlopen(req)
        print("Added " + p["symbol"])
    except Exception as e:
        print("Failed " + p["symbol"] + ": " + str(e))
