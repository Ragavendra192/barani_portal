import pyodbc
from db import get_connection

def migrate_expense_schema():
    conn = get_connection()
    cur = conn.cursor()
    try:
        # 1. Create counter table for globally unique voucher numbers if it doesn't exist
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'voucher_number_tracker')
            BEGIN
                CREATE TABLE dbo.voucher_number_tracker (
                    year_val INT PRIMARY KEY,
                    last_number INT NOT NULL DEFAULT 0
                );
                PRINT 'Created table dbo.voucher_number_tracker';
            END
        """)
        conn.commit()

        # 2. Add UNIQUE constraint to voucher_id column in user_trip_vouchers if not exists
        cur.execute("""
            IF NOT EXISTS (
                SELECT * FROM sys.key_constraints 
                WHERE name = 'UQ_user_trip_vouchers_voucher_id'
            ) AND NOT EXISTS (
                SELECT * FROM sys.indexes 
                WHERE name = 'UQ_user_trip_vouchers_voucher_id' AND object_id = OBJECT_ID('dbo.user_trip_vouchers')
            )
            BEGIN
                ALTER TABLE dbo.user_trip_vouchers
                ADD CONSTRAINT UQ_user_trip_vouchers_voucher_id UNIQUE (voucher_id);
                PRINT 'Added UNIQUE constraint UQ_user_trip_vouchers_voucher_id';
            END
        """)
        conn.commit()

        # 3. Check current highest numeric voucher sequence for 2026 to safely initialize tracker
        cur.execute("""
            SELECT voucher_id FROM dbo.user_trip_vouchers WHERE voucher_id LIKE 'VCH-2026-%'
        """)
        vchs = [r[0] for r in cur.fetchall()]
        max_seq = 0
        for v in vchs:
            parts = v.split('-')
            if len(parts) >= 3 and parts[-1].isdigit():
                val = int(parts[-1])
                if val < 100000: # Only count normal sequential numbers
                    if val > max_seq:
                        max_seq = val

        cur.execute("""
            MERGE dbo.voucher_number_tracker WITH (UPDLOCK, HOLDLOCK) AS target
            USING (SELECT 2026 AS year_val) AS source
            ON target.year_val = source.year_val
            WHEN NOT MATCHED THEN
                INSERT (year_val, last_number) VALUES (2026, ?);
        """, (max_seq,))
        conn.commit()

        print(f"Schema migration & counter table ready. Initial 2026 max_seq: {max_seq}")

    except Exception as e:
        print("Schema setup info:", e)
    finally:
        conn.close()

if __name__ == "__main__":
    migrate_expense_schema()
