import re

def patch_gdelt():
    file_path = r"intelligence\news_gdelt_finbert.py"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # 1. We need to replace the rate limiting comment and sleep
    old_sleep = """            if query != GDELT_QUERIES[0]:
                time.sleep(2)  # Controlled queue approach: 2s between sequential queries

            params = {"""
    
    new_sleep = """            if query != GDELT_QUERIES[0]:
                time.sleep(6)  # GDELT requires 1 request per 5 seconds

            params = {"""
    
    content = content.replace(old_sleep, new_sleep)
    
    # 2. Add 429 retry logic
    old_req = """            resp = requests.get(GDELT_BASE, params=params, headers=headers, timeout=10)
            if resp.status_code != 200:
                log.debug("GDELT query failed with status %d: %s", resp.status_code, resp.text[:100])
                query_failures += 1
                continue"""
                
    new_req = """            resp = requests.get(GDELT_BASE, params=params, headers=headers, timeout=10)
            
            if resp.status_code == 429:
                log.debug("GDELT rate limited (429). Waiting 6 seconds and retrying...")
                time.sleep(6)
                resp = requests.get(GDELT_BASE, params=params, headers=headers, timeout=10)
                
            if resp.status_code != 200:
                log.debug("GDELT query failed with status %d: %s", resp.status_code, resp.text[:100])
                query_failures += 1
                continue"""
                
    content = content.replace(old_req, new_req)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    print("Patched GDELT rate limits")

if __name__ == "__main__":
    patch_gdelt()
