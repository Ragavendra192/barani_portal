class Config:
    SECRET_KEY = "supersecretkey"   # Keep this consistent with what app.py has ("supersecretkey" or from config)
    HOST = "0.0.0.0"
    PORT = 5010

    # ==================================================
    # CENTRAL IDLE TIMEOUT CONFIGURATION
    # Change IDLE_TIMEOUT_MINUTES here to adjust company-wide policy (e.g. 1, 5, 10, 15)
    # ==================================================
    IDLE_TIMEOUT_MINUTES = 10
    IDLE_WARNING_SECONDS = 60
    PERMANENT_SESSION_LIFETIME_DAYS = 7
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
