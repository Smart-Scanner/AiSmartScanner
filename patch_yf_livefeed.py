"""Patch live_feed.py and master_sync.py to use get_yf_ticker wrapper."""

def patch_file(path, replacements):
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
        else:
            print(f"  WARNING: pattern not found in {path}: {old[:60]}...")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  Patched {path}")

# master_sync.py
print("master_sync.py")
patch_file("master_sync.py", [
    (
        "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_session",
        "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_session, get_yf_ticker"
    ),
    (
        'ticker = yf.Ticker(f"{symbol}.NS", session=get_yf_session())',
        'ticker = get_yf_ticker(f"{symbol}.NS", source="master_sync")'
    ),
    (
        "yf_record_failure()",
        'yf_record_failure(source="master_sync")'
    ),
])

# live_feed.py
print("live_feed.py")
patch_file("live_feed.py", [
    (
        "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_session",
        "from intelligence.yf_guard import yf_is_available, yf_record_failure, yf_record_success, get_yf_session, get_yf_ticker"
    ),
    (
        'ticker = yf.Ticker(f"{clean}.NS", session=get_yf_session())',
        'ticker = get_yf_ticker(f"{clean}.NS", source="live_feed")'
    ),
    (
        'df = yf.Ticker(f"{clean}.NS", session=get_yf_session()).history(period="1y")',
        'df = get_yf_ticker(f"{clean}.NS", source="live_feed_historical").history(period="1y")'
    ),
    (
        "yf_record_failure()",
        'yf_record_failure(source="live_feed")'
    ),
])

print("Done")
