import os, psycopg2
from collections import Counter
from dotenv import load_dotenv

load_dotenv()
url = os.getenv('DATABASE_URL').replace('postgres://', 'postgresql://')
if '?' in url:
    url += '&sslmode=require'
else:
    url += '?sslmode=require'

conn = psycopg2.connect(url)
cur = conn.cursor()

TARGET_SCAN = 'scan_manual_1781858049_314672'
SUCCESS_SCAN = 'scan_manual_1781856444_055982'

for scan_id in [TARGET_SCAN, SUCCESS_SCAN]:
    print(f"\n========================================================")
    print(f"ANALYZING: {scan_id}")
    print(f"========================================================")
    
    # Get all SYMBOL_FAILED events
    cur.execute("""
        SELECT details FROM scan_event_audit 
        WHERE scan_id=%s AND event_type='SYMBOL_FAILED'
    """, (scan_id,))
    failed_events = cur.fetchall()
    
    # Get all SYMBOL_COMPLETED events
    cur.execute("""
        SELECT COUNT(*) FROM scan_event_audit 
        WHERE scan_id=%s AND event_type='SYMBOL_COMPLETED'
    """, (scan_id,))
    completed_count = cur.fetchone()[0]
    
    print(f"Total Completed: {completed_count}")
    print(f"Total Failed: {len(failed_events)}")
    
    # Categorize failed events
    categories = Counter()
    for row in failed_events:
        details = row[0]
        if not details:
            categories['Unknown / No details'] += 1
            continue
            
        details_lower = details.lower()
        if 'empty df' in details_lower or 'empty dataframe' in details_lower:
            categories['Empty df'] += 1
        elif 'rate limit' in details_lower or '429' in details_lower:
            categories['Rate limit'] += 1
        elif 'token' in details_lower or 'lookup' in details_lower:
            categories['Token lookup failure'] += 1
        elif 'api' in details_lower or 'angel' in details_lower or 'getcandledata' in details_lower:
            categories['API error'] += 1
        elif 'historical' in details_lower:
            categories['Historical fetch failure'] += 1
        elif 'indicator' in details_lower or 'calculation' in details_lower:
            categories['Indicator calculation failure'] += 1
        else:
            # Just extract the beginning of the error to categorize
            short_detail = details.split(':')[0] if ':' in details else details[:30]
            categories[f"Other: {short_detail}"] += 1
            
    print("\nFailure Categories:")
    for cat, count in categories.items():
        print(f"  - {cat}: {count}")

conn.close()
