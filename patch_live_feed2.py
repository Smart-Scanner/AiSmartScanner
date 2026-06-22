import re

def fix_live_feed():
    file_path = "live_feed.py"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Fix TOTP secret naming mismatch
    old_totp = 'os.environ.get(f"PROVIDER_{i}_TOTP_SECRET", "")'
    new_totp = 'os.environ.get(f"PROVIDER_{i}_TOTP_SECRET", "") or os.environ.get(f"PROVIDER_{i}_TOTP", "")'
    content = content.replace(old_totp, new_totp)

    old_totp_3 = 'os.environ.get("PROVIDER_3_TOTP_SECRET", "")'
    new_totp_3 = 'os.environ.get("PROVIDER_3_TOTP_SECRET", "") or os.environ.get("PROVIDER_3_TOTP", "")'
    content = content.replace(old_totp_3, new_totp_3)

    # Fix WebSocket initialization
    # old: _sws = SmartWebSocketV2(_auth_token, API_KEY, CLIENT_ID, _feed_token)
    # new:
    # acct = get_active_account()
    # _sws = SmartWebSocketV2(_auth_token, acct["api_key"], acct["client_id"], _feed_token)
    
    ws_init_old = "_sws = SmartWebSocketV2(_auth_token, API_KEY, CLIENT_ID, _feed_token)"
    ws_init_new = """acct = get_active_account()
                _sws = SmartWebSocketV2(_auth_token, acct["api_key"], acct["client_id"], _feed_token)"""
    
    content = content.replace(ws_init_old, ws_init_new)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Fixed live_feed.py TOTP and API_KEY references.")

if __name__ == "__main__":
    fix_live_feed()
