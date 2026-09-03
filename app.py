from flask import Flask, session, redirect, request, render_template, jsonify
from datetime import datetime, timedelta

from routes.auth import auth_bp
from routes.admin import admin_bp
from routes.dashboard import dashboard_bp
from routes.leave import leave_bp
from routes.announcement import announcement_bp
from routes.expenses import expenses_bp
from db import get_db, close_db
from config import Config
from audit_service import AuditService

app = Flask(__name__)
app.secret_key = Config.SECRET_KEY
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=getattr(Config, "PERMANENT_SESSION_LIFETIME_DAYS", 7))
app.config["SESSION_COOKIE_HTTPONLY"] = getattr(Config, "SESSION_COOKIE_HTTPONLY", True)
app.config["SESSION_COOKIE_SAMESITE"] = getattr(Config, "SESSION_COOKIE_SAMESITE", "Lax")
app.teardown_appcontext(close_db)

# ===============================
# REGISTER BLUEPRINTS
# ===============================
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(dashboard_bp)
app.register_blueprint(leave_bp)
app.register_blueprint(announcement_bp)
app.register_blueprint(expenses_bp)

# ==================================================
# GLOBAL CONTEXT PROCESSOR (ROLES, NOTIFS & CONFIG)
# ==================================================
@app.context_processor
def inject_leave_roles_and_notifs():
    return {
        "is_hod": session.get("is_hod", False),
        "unread_count": 0,
        "unread_general_count": 0,
        "unread_dept_count": 0,
        "is_hr": session.get("department") == "HR & Admin" or session.get("role") == "HR",
        "idle_timeout_minutes": getattr(Config, "IDLE_TIMEOUT_MINUTES", 10),
        "idle_warning_seconds": getattr(Config, "IDLE_WARNING_SECONDS", 60)
    }


# ==================================================
# IDLE TIMEOUT + FORCE LOGOUT CHECK
# ==================================================
@app.before_request
def check_idle_and_force_logout():
    # Allow public auth endpoints, static assets, and health checks
    if request.endpoint in ("auth.index", "auth.login", "auth.register", "static") or request.path.startswith("/static/"):
        return

    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or \
              request.is_json or \
              "application/json" in request.headers.get("Accept", "")

    # If unauthenticated
    if "user_id" not in session:
        if is_ajax:
            return jsonify({"status": "unauthorized", "redirect": "/login"}), 401
        return redirect("/login")

    last_act_str = session.get("last_activity")
    now = datetime.now()
    timeout_minutes = getattr(Config, "IDLE_TIMEOUT_MINUTES", 10)

    if last_act_str:
        try:
            if isinstance(last_act_str, datetime):
                last_act = last_act_str
            else:
                last_act = datetime.strptime(str(last_act_str), "%Y-%m-%d %H:%M:%S")

            idle_time = now - last_act
            if idle_time > timedelta(minutes=timeout_minutes):
                # Record AUTO_LOGOUT_IDLE in SQL Server Audit Log
                user_id = session.get("user_id")
                full_name = session.get("full_name")
                emp_id = session.get("emp_id")
                username = f"{full_name} ({emp_id})" if full_name and emp_id else (full_name or str(user_id))
                
                AuditService.log_action(
                    action="AUTO_LOGOUT_IDLE",
                    module="Authentication",
                    description=f"Session terminated due to inactivity ({timeout_minutes} minutes without interaction).",
                    user_id=user_id,
                    username=username,
                    role=session.get("role")
                )

                # Deactivate user session in DB
                try:
                    conn = get_db()
                    cur = conn.cursor()
                    cur.execute("UPDATE user_sessions SET is_active = 0 WHERE user_id = ?", (user_id,))
                    conn.commit()
                except Exception:
                    pass

                session.clear()
                if is_ajax:
                    return jsonify({"status": "idle_timeout", "redirect": "/login?idle=1"}), 401
                return redirect("/login?idle=1")
        except Exception as e:
            print(f"[check_idle_and_force_logout Warning]: {e}")

    # Passive session checks should NOT reset the last_activity timestamp.
    # Only active interactions and explicit keepalive pings reset it.
    if request.path not in ("/check_session", "/api/session-status"):
        session["last_activity"] = now.strftime("%Y-%m-%d %H:%M:%S")


# ==================================================
# GLOBAL ERROR HANDLER (RECORDS TO APPLICATION ERROR LOG)
# ==================================================
@app.errorhandler(500)
def handle_internal_server_error(e):
    AuditService.log_error(
        message="Internal Server Error (500)",
        error=e,
        module="Application",
        action="HTTP_500",
        level="ERROR"
    )
    is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json
    if is_ajax:
        return jsonify({"status": "error", "message": "A server error occurred. Please try again or contact support."}), 500
    return render_template("login.html", error="A server error occurred. Please try again or contact support."), 500


# ==================================================
# ACCESS DENIED
# ==================================================
@app.route("/access_denied")
def access_denied():
    return "Access Denied", 403


# ==================================================
# RUN SERVER (NETWORK ENABLED)
# ==================================================
if __name__ == "__main__":
    app.run(host=Config.HOST, port=Config.PORT, debug=True)
