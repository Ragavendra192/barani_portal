import pyodbc
from db import get_connection

def check_db():
    conn = get_connection()
    cur = conn.cursor()
    try:
        print("--- Visitor_Request Columns ---")
        cur.execute("""
            SELECT COLUMN_NAME, DATA_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'Visitor_Request'
        """)
        for row in cur.fetchall():
            print(f"- {row[0]} ({row[1]})")

        print("\n--- Visitor_Request Row Count ---")
        cur.execute("SELECT COUNT(*) FROM Visitor_Request")
        print(cur.fetchone()[0])
        
        print("\n--- Visitor_Master Columns ---")
        cur.execute("""
            SELECT COLUMN_NAME, DATA_TYPE 
            FROM INFORMATION_SCHEMA.COLUMNS 
            WHERE TABLE_NAME = 'Visitor_Master'
        """)
        for row in cur.fetchall():
            print(f"- {row[0]} ({row[1]})")

    except Exception as e:
        print(e)
    finally:
        conn.close()

if __name__ == "__main__":
    check_db()
