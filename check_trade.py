from dotenv import load_dotenv
load_dotenv()
import db, json

rows = db.execute_db("SELECT symbol, score, data FROM scan_results WHERE score >= 65 ORDER BY score DESC", fetch="all")
if not rows:
    print("No rows with score >= 65")
else:
    for row in rows:
        symbol = row["symbol"]
        score = row["score"]
        data = json.loads(row["data"]) if isinstance(row["data"], str) else row["data"]
        trade = data.get("trade", {})
        print(f"\n=== {symbol} | Score: {score} ===")
        print(f"  trade key exists: {'trade' in data}")
        print(f"  trade value: {trade}")
        print(f"  bool(trade): {bool(trade)}")
        print(f"  high_conviction: {data.get('high_conviction')}")
