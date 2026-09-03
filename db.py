import pyodbc
import time
from flask import g, has_app_context

SERVER = r"RAGAVENDRA\SQLEXPRESS"
DATABASE = "CompanyPortalDB"

def get_connection():
    conn_str = (
        "DRIVER={ODBC Driver 17 for SQL Server};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        "Trusted_Connection=yes;"
    )
    for attempt in range(4):
        try:
            return pyodbc.connect(conn_str, timeout=5)
        except pyodbc.Error as e:
            if attempt < 3:
                time.sleep(0.1)
                continue
            raise e

class SafeConnectionProxy:
    """
    Wrapper around pyodbc connection that ignores explicit close() calls during a request,
    deferring closure to Flask teardown_appcontext.
    """
    def __init__(self, conn):
        self._conn = conn

    def close(self):
        # Ignore explicit close() in routes to allow connection reuse during request
        pass

    def __getattr__(self, item):
        return getattr(self._conn, item)

def get_db():
    """
    Returns a request-scoped database connection stored in Flask 'g'.
    Reuses 1 open connection per HTTP request lifecycle.
    Falls back to a new connection if called outside Flask request context.
    """
    if has_app_context():
        real_conn = g.get('db_real_conn', None)
        if real_conn is not None:
            try:
                real_conn.cursor()
                return SafeConnectionProxy(real_conn)
            except Exception:
                g.db_real_conn = None

        new_conn = get_connection()
        g.db_real_conn = new_conn
        return SafeConnectionProxy(new_conn)

    return get_connection()

def close_db(e=None):
    """
    Closes the request-scoped database connection upon request teardown.
    """
    if has_app_context():
        real_conn = g.pop('db_real_conn', None)
        if real_conn is not None:
            try:
                real_conn.close()
            except Exception:
                pass
