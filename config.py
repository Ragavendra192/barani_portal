class Config:
    SECRET_KEY = "supersecretkey"   # Keep this consistent with what app.py has ("supersecretkey" or from config)
    HOST = "0.0.0.0"
    PORT = 5010

    # ==================================================
    # CENTRAL IDLE TIMEOUT CONFIGURATION
    # Change IDLE_TIMEOUT_MINUTES here to adjust company-wide policy (e.g. 480 = 8 hours full shift)
    # ==================================================
    IDLE_TIMEOUT_MINUTES = 480
    IDLE_WARNING_SECONDS = 120
    PERMANENT_SESSION_LIFETIME_DAYS = 7
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
