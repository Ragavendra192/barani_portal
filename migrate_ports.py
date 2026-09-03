import os
import sys

# Ensure the current directory is in the path to import db
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from db import get_connection

def migrate_urls():
    try:
        conn = get_connection()
        cur = conn.cursor()

        print("Checking for URLs to migrate...")

        # Find applications with :5000 or :6000
        cur.execute("""
            SELECT id, app_name, app_url
            FROM dbo.applications
            WHERE app_url LIKE '%:5000%' OR app_url LIKE '%:6000%'
        """)
        records = cur.fetchall()

        if not records:
            print("No URLs found needing migration.")
            conn.close()
            return

        print(f"Found {len(records)} application(s) needing update.")

        updates_made = 0
        for app in records:
            app_id = app[0]
            app_name = app[1]
            old_url = app[2]
            
            new_url = old_url.replace(":5000", ":5010").replace(":6000", ":5010")
            
            cur.execute("""
                UPDATE dbo.applications
                SET app_url = ?
                WHERE id = ?
            """, (new_url, app_id))
            
            print(f"Updated '{app_name}': {old_url} -> {new_url}")
            updates_made += 1

        conn.commit()
        conn.close()
        print(f"Migration complete. Successfully updated {updates_made} application URL(s).")
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == "__main__":
    migrate_urls()
