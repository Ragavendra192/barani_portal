import pyodbc
from db import get_connection

def force_drop_tables():
    conn = get_connection()
    cur = conn.cursor()
    try:
        print("Finding and dropping foreign keys referencing visitor tables...")
        # Drop foreign keys referencing these tables
        tables = ['Visitor_Master', 'Visitor_Request', 'Visitor_EntryExit', 'Visitor_AuditLog']
        for table in tables:
            cur.execute(f"""
                SELECT 
                    fk.name AS ForeignKey,
                    tp.name AS ParentTable
                FROM sys.foreign_keys fk
                INNER JOIN sys.tables tp ON fk.parent_object_id = tp.object_id
                INNER JOIN sys.tables tr ON fk.referenced_object_id = tr.object_id
                WHERE tr.name = '{table}'
            """)
            fks = cur.fetchall()
            for fk in fks:
                cur.execute(f"ALTER TABLE {fk.ParentTable} DROP CONSTRAINT {fk.ForeignKey}")
                print(f"Dropped FK {fk.ForeignKey} from {fk.ParentTable}")
        
        conn.commit()
        
        print("Dropping visitor tables...")
        cur.execute("IF OBJECT_ID('Visitor_AuditLog', 'U') IS NOT NULL DROP TABLE Visitor_AuditLog")
        cur.execute("IF OBJECT_ID('Visitor_EntryExit', 'U') IS NOT NULL DROP TABLE Visitor_EntryExit")
        cur.execute("IF OBJECT_ID('Visitor_Request', 'U') IS NOT NULL DROP TABLE Visitor_Request")
        cur.execute("IF OBJECT_ID('Visitor_Master', 'U') IS NOT NULL DROP TABLE Visitor_Master")
        conn.commit()
        print("Tables dropped successfully.")
    except Exception as e:
        print(f"Error dropping tables: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    force_drop_tables()
