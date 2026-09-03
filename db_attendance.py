import os
import pyodbc


def get_attendance_connection():
    """Return a pyodbc connection for the attendance database.

    Connection can be overridden by setting the `ATT_DB_CONN` environment
    variable to a full ODBC connection string. Otherwise the following
    environment variables are used (with these defaults):

    - ATT_DB_SERVER: localhost
    - ATT_DB_INSTANCE: SQLEXPRESS
    - ATT_DB_DATABASE: etime
    - ATT_DB_TRUSTED: yes
    """

    # Allow a full connection string to be provided (recommended for production)
    conn_str = os.getenv("ATT_DB_CONN")

    if not conn_str:
        # Dynamically default to the same server and instance configured in db.py
        default_server = "localhost"
        default_instance = "SQLEXPRESS"
        try:
            import db
            if hasattr(db, 'SERVER') and db.SERVER:
                parts = db.SERVER.split('\\')
                default_server = parts[0]
                if len(parts) > 1:
                    default_instance = parts[1]
                else:
                    default_instance = ""
        except Exception:
            pass

        server = os.getenv("ATT_DB_SERVER", default_server)
        instance = os.getenv("ATT_DB_INSTANCE", default_instance)
        database = os.getenv("ATT_DB_DATABASE", "etime")
        trusted = os.getenv("ATT_DB_TRUSTED", "yes")

        # Build server spec. If instance is empty use server only.
        server_spec = f"{server}\\{instance}" if instance else server

        conn_str = (
            f"DRIVER={{ODBC Driver 17 for SQL Server}};"
            f"SERVER={server_spec};"
            f"DATABASE={database};"
            f"Trusted_Connection={'yes' if trusted.lower() in ('1','true','yes') else 'no'};"
        )

    try:
        return pyodbc.connect(conn_str, timeout=3)
    except pyodbc.Error as e:
        # Raise a clearer OperationalError that includes the attempted string
        raise pyodbc.OperationalError(
            "Attendance DB connection failed. Tried connection string: %s. Error: %s"
            % (conn_str, e)
        ) from e
