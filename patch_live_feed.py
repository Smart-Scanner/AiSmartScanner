import re

def patch_live_feed():
    file_path = "live_feed.py"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Update to support PROVIDER_ variables
    old_str = """
    for i in range(1, 10):
        ak = os.environ.get(f"ANGEL_API_KEY_{i}")
        if ak:
            _angel_accounts.append({
                "id": i,
                "api_key": ak,
                "client_id": os.environ.get(f"ANGEL_CLIENT_ID_{i}", ""),
                "mpin": os.environ.get(f"ANGEL_MPIN_{i}", ""),
                "totp_secret": os.environ.get(f"ANGEL_TOTP_SECRET_{i}", ""),
"""
    new_str = """
    for i in range(1, 10):
        ak = os.environ.get(f"PROVIDER_{i}_API_KEY") or os.environ.get(f"ANGEL_API_KEY_{i}")
        if ak:
            _angel_accounts.append({
                "id": i,
                "api_key": ak,
                "client_id": os.environ.get(f"PROVIDER_{i}_CLIENT_ID", "") or os.environ.get(f"ANGEL_CLIENT_ID_{i}", ""),
                "mpin": os.environ.get(f"PROVIDER_{i}_MPIN", "") or os.environ.get(f"ANGEL_MPIN_{i}", ""),
                "totp_secret": os.environ.get(f"PROVIDER_{i}_TOTP_SECRET", "") or os.environ.get(f"ANGEL_TOTP_SECRET_{i}", ""),
"""
    
    if old_str.strip() in content:
        content = content.replace(old_str.strip(), new_str.strip())
    else:
        # manual patch just in case
        content = content.replace('ak = os.environ.get(f"ANGEL_API_KEY_{i}")', 'ak = os.environ.get(f"PROVIDER_{i}_API_KEY") or os.environ.get(f"ANGEL_API_KEY_{i}")')
        content = content.replace('os.environ.get(f"ANGEL_CLIENT_ID_{i}", "")', 'os.environ.get(f"PROVIDER_{i}_CLIENT_ID", "") or os.environ.get(f"ANGEL_CLIENT_ID_{i}", "")')
        content = content.replace('os.environ.get(f"ANGEL_MPIN_{i}", "")', 'os.environ.get(f"PROVIDER_{i}_MPIN", "") or os.environ.get(f"ANGEL_MPIN_{i}", "")')
        content = content.replace('os.environ.get(f"ANGEL_TOTP_SECRET_{i}", "")', 'os.environ.get(f"PROVIDER_{i}_TOTP_SECRET", "") or os.environ.get(f"ANGEL_TOTP_SECRET_{i}", "")')

    # Also update the single fallback
    content = content.replace('ak = os.environ.get("ANGEL_API_KEY")', 'ak = os.environ.get("PROVIDER_3_API_KEY") or os.environ.get("ANGEL_API_KEY")')
    content = content.replace('os.environ.get("ANGEL_CLIENT_ID", "")', 'os.environ.get("PROVIDER_3_CLIENT_ID", "") or os.environ.get("ANGEL_CLIENT_ID", "")')
    content = content.replace('os.environ.get("ANGEL_MPIN", "")', 'os.environ.get("PROVIDER_3_MPIN", "") or os.environ.get("ANGEL_MPIN", "")')
    content = content.replace('os.environ.get("ANGEL_TOTP_SECRET", "")', 'os.environ.get("PROVIDER_3_TOTP_SECRET", "") or os.environ.get("ANGEL_TOTP_SECRET", "")')
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    print("live_feed.py successfully patched.")

if __name__ == "__main__":
    patch_live_feed()
