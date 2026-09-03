import pyodbc
from db import get_connection

def fix_db():
    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            IF NOT EXISTS (
                SELECT * FROM sys.columns 
                WHERE object_id = OBJECT_ID('Visitor_Master') AND name = 'PhotoPath'
            )
            BEGIN
                ALTER TABLE Visitor_Master ADD PhotoPath NVARCHAR(255)
            END
        """)
        conn.commit()
        print("Fixed Visitor_Master table.")
    except Exception as e:
        print(e)
    finally:
        conn.close()

if __name__ == "__main__":
    fix_db()
