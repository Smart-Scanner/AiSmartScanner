import json
from app import app

def extract_routes():
    routes = []
    for rule in app.url_map.iter_rules():
        # skip static routes and API routes for UI coverage
        if rule.endpoint.startswith('static') or rule.rule.startswith('/api/'):
            continue
        # skip wildcard or variables for simple coverage calculation
        if '<' in rule.rule:
            continue
        routes.append(rule.rule)

    # Dedup and sort
    routes = sorted(list(set(routes)))
    
    with open('tests/registered_routes.json', 'w') as f:
        json.dump(routes, f, indent=2)

if __name__ == '__main__':
    extract_routes()
    print("Routes extracted to tests/registered_routes.json")
