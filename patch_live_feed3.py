import re

def fix_live_feed_auth():
    file_path = "live_feed.py"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # In _login_account:
    # acct["smart_api"] = obj
    # acct["feed_token"] = obj.getfeedToken()
    # we need to add acct["auth_token"] = data["data"]["jwtToken"]
    
    old_feed = 'acct["feed_token"] = obj.getfeedToken()'
    new_feed = 'acct["feed_token"] = obj.getfeedToken()\n        acct["auth_token"] = data["data"]["jwtToken"]'
    content = content.replace(old_feed, new_feed)

    # In _ws_thread_func:
    # _sws = SmartWebSocketV2(_auth_token, acct["api_key"], acct["client_id"], _feed_token)
    # needs to use acct["auth_token"] and acct["feed_token"] instead of globals!
    # Because _feed_token and _auth_token might be None since they are globals.
    
    old_ws = '_sws = SmartWebSocketV2(_auth_token, acct["api_key"], acct["client_id"], _feed_token)'
    new_ws = '_sws = SmartWebSocketV2(acct.get("auth_token", ""), acct["api_key"], acct["client_id"], acct.get("feed_token", ""))'
    content = content.replace(old_ws, new_ws)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("Fixed live_feed.py auth_token and feed_token references.")

if __name__ == "__main__":
    fix_live_feed_auth()
