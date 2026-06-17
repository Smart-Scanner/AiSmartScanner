import auth_db
import sqlite3
from datetime import datetime, timezone, timedelta

def create_test_users():
    conn = auth_db._get_conn()
    c = conn.cursor()
    
    # 1. Create Test Admin
    print("Creating admin...")
    try:
        admin_id = auth_db.register_user("admin", "admin123", "admin")
        print(f"Registered admin with id {admin_id}")
    except ValueError as e:
        print(f"Admin registration error: {e}")
        admin_row = c.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
        admin_id = admin_row[0] if admin_row else None

    if admin_id:
        c.execute("""
            UPDATE users
            SET status = 'approved', is_admin = 1
            WHERE id = ?
        """, (admin_id,))
        print("Admin verified and approved.")

    # 2. Create Test User
    print("Creating testuser...")
    try:
        user_id = auth_db.register_user("testuser", "admin123", "testuser")
        print(f"Registered testuser with id {user_id}")
    except ValueError as e:
        print(f"User registration error: {e}")
        user_row = c.execute("SELECT id FROM users WHERE username = 'testuser'").fetchone()
        user_id = user_row[0] if user_row else None
        
    if user_id:
        future_date = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
        c.execute("""
            UPDATE users
            SET status = 'approved', is_admin = 0, sub_expires_at = ?
            WHERE id = ?
        """, (future_date, user_id))
        print("Testuser verified, approved, and subscribed.")

    conn.commit()
    conn.close()

if __name__ == "__main__":
    create_test_users()
    print("Test users created successfully.")
