import pyodbc
from db import get_connection

def fix_schema():
    conn = get_connection()
    cur = conn.cursor()
    try:
        queries = [
            "ALTER TABLE Leave_Request ALTER COLUMN EmployeeID NVARCHAR(50) NOT NULL",
            "ALTER TABLE Leave_Request ALTER COLUMN HODID NVARCHAR(50)",
            "ALTER TABLE Permission_Request ALTER COLUMN EmployeeID NVARCHAR(50) NOT NULL",
            "ALTER TABLE Permission_Request ALTER COLUMN HODID NVARCHAR(50)",
            "ALTER TABLE Outpass_Request ALTER COLUMN EmployeeID NVARCHAR(50) NOT NULL",
            "ALTER TABLE Outpass_Request ALTER COLUMN HODID NVARCHAR(50)",
            "ALTER TABLE Request_AuditLog ALTER COLUMN EmployeeID NVARCHAR(50)",
            "ALTER TABLE Request_AuditLog ALTER COLUMN UserID NVARCHAR(50)"
        ]
        
        for q in queries:
            try:
                cur.execute(q)
                print(f"Executed: {q}")
            except Exception as e:
                print(f"Error on {q}: {e}")
        
        conn.commit()
        print("Schema altered successfully.")
    except Exception as e:
        print(e)
    finally:
        conn.close()

if __name__ == "__main__":
    fix_schema()
