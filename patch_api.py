import functools, traceback

with open('routes/api.py', 'r', encoding='utf-8') as f:
    code = f.read()

decorator = """
import functools, traceback
def catch_err(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            with open('logs/dash_err.txt', 'w') as errf:
                errf.write(traceback.format_exc())
            return {'error': str(e)}, 500
    return wrapper

@api_bp.route("/api/dashboard")
@catch_err
def get_dashboard():
"""

code = code.replace('@api_bp.route("/api/dashboard")\ndef get_dashboard():', decorator)

with open('routes/api.py', 'w', encoding='utf-8') as f:
    f.write(code)
print("api patched")
