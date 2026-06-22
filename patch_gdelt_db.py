import re

def patch_gdelt_db_cache():
    file_path = r"intelligence\news_gdelt_finbert.py"
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    # We need to import db and json
    if "import db" not in content:
        content = content.replace("import time\nimport logging\nimport requests", "import time\nimport logging\nimport requests\nimport json\nimport db")

    # In fetch_gdelt_india_bulk, we add the DB cache check at the top
    old_fetch = '''def fetch_gdelt_india_bulk(hours_back: int = 48) -> list:
    """
    Pull up to 1000 Indian business news articles from GDELT.
    Returns list of dicts: {url, title, seendate}
    Zero API key. Zero cost. Single GET per query.
    """
    if not _gdelt_is_available():'''
    
    new_fetch = '''def fetch_gdelt_india_bulk(hours_back: int = 48) -> list:
    """
    Pull up to 1000 Indian business news articles from GDELT.
    Returns list of dicts: {url, title, seendate}
    Zero API key. Zero cost. Single GET per query.
    """
    # [NEW DB CACHE LOGIC] Check DB first to avoid multiple Railway workers hitting 429
    try:
        cached_raw = db.get_meta("gdelt_cache")
        if cached_raw:
            cached_data = json.loads(cached_raw)
            cache_age = time.time() - cached_data.get("timestamp", 0)
            if cache_age < 900:  # 15 minutes valid
                log.info("GDELT fetched %d articles from DB Cache (age: %.1f min)", len(cached_data.get("articles", [])), cache_age / 60)
                return cached_data.get("articles", [])
    except Exception as e:
        log.warning("GDELT DB Cache read failed: %s", e)

    if not _gdelt_is_available():'''
    
    content = content.replace(old_fetch, new_fetch)

    # In fetch_gdelt_india_bulk, we add the DB cache write at the bottom
    old_return = '''    log.info("GDELT fetched %d unique articles", len(articles))
    return articles'''
    
    new_return = '''    log.info("GDELT fetched %d unique articles from API", len(articles))
    
    # Save to DB cache if successful
    if articles:
        try:
            db.set_meta("gdelt_cache", json.dumps({"timestamp": time.time(), "articles": articles}))
        except Exception as e:
            log.warning("GDELT DB Cache write failed: %s", e)

    return articles'''
    
    content = content.replace(old_return, new_return)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    print("Patched GDELT with DB caching logic")

if __name__ == "__main__":
    patch_gdelt_db_cache()
