"""
Run this script locally to generate Fyers access token.
Usage: python get_fyers_token.py
"""
import os
from dotenv import load_dotenv
load_dotenv()

APP_ID = os.getenv("FYERS_APP_ID", "LXOJJWRT2P-200")
SECRET_KEY = os.getenv("FYERS_SECRET_KEY", "loMzhqGwL73Uw1ma")
REDIRECT_URI = "https://www.aismartscan.in/"

print("=" * 60)
print("FYERS TOKEN GENERATOR")
print("=" * 60)
print()

from fyers_apiv3 import fyersModel

session = fyersModel.SessionModel(
    client_id=APP_ID,
    secret_key=SECRET_KEY,
    redirect_uri=REDIRECT_URI,
    response_type="code",
    grant_type="authorization_code",
)
auth_url = session.generate_authcode()
print("Step 1: Pehle ye URL browser mein kholo:\n")
print(auth_url)
print()
print("Step 2: Login karo -> redirect hoga -> URL se auth_code copy karo")
print()

auth_code = input("auth_code yahan paste karo: ").strip()

session.set_token(auth_code)
response = session.generate_token()

if response and response.get("access_token"):
    token = response["access_token"]
    print()
    print("=" * 60)
    print("TOKEN MILA!")
    print("=" * 60)
    print()
    print("Railway Variables mein ye set karo:")
    print(f"  FYERS_ACCESS_TOKEN = {token}")
else:
    print("Token generate nahi hua:", response)
