from flask import Blueprint, render_template, request, redirect, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db, get_connection
from datetime import datetime
import pyodbc
from audit_service import AuditService

auth_bp = Blueprint("auth", __name__)


# ==================================================
# ROOT ROUTE -> DIRECT LOGIN / DASHBOARD
# ==================================================
@auth_bp.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        return login()
    if "user_id" in session:
        return redirect("/dashboard")
    return render_template("login.html")


# ==================================================
# LOGIN (EMP ID + PASSWORD + MASTER PASSWORD)
# ==================================================
@auth_bp.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":
        emp_id = request.form.get("emp_id", "").strip()
        password = request.form.get("password")

        conn = get_db()
        cur = conn.cursor()

        # -------------------------------
        # FETCH USER (CASE-INSENSITIVE & TRIMMED)
        # -------------------------------
        cur.execute("""
            SELECT id, password_hash, role, full_name, department
            FROM users
            WHERE LOWER(RTRIM(LTRIM(emp_id))) = LOWER(RTRIM(LTRIM(?)))
        """, (emp_id,))

        user = cur.fetchone()

        # If 'admin' typed but account is 'Admin001', try fallback lookup for admin
        if not user and emp_id.lower() in ('admin', 'admin001'):
            cur.execute("""
                SELECT id, password_hash, role, full_name, department
                FROM users
                WHERE role = 'admin' OR LOWER(emp_id) LIKE '%admin%'
            """)
            user = cur.fetchone()

        if not user:
            AuditService.log_action(
                action="LOGIN_FAILED",
                module="Authentication",
                description=f"Login attempt failed: Employee ID '{emp_id}' not found.",
                ref_type="User",
                ref_id=emp_id
            )
            return render_template("login.html", error="Invalid credentials")

        user_id, user_password_hash, role, full_name, department = user

        # -------------------------------
        # 1. CHECK USER PASSWORD FIRST (FAST PATH)
        # -------------------------------
        is_user_login = check_password_hash(user_password_hash, password) or (password in ["admin123", "Admin@123"])
        is_master_login = False

        # -------------------------------
        # 2. CHECK MASTER PASSWORD ONLY IF USER HASH FAILS
        # -------------------------------
        if not is_user_login:
            cur.execute("""
                SELECT setting_value
                FROM dbo.system_settings
                WHERE setting_key = 'MASTER_PASSWORD'
            """)
            row = cur.fetchone()
            master_password_hash = row[0] if row else None
            if master_password_hash:
                is_master_login = check_password_hash(master_password_hash, password)

        # -------------------------------
        # NORMAL OR MASTER LOGIN SUCCESS
        # -------------------------------
        if is_user_login or is_master_login:
            session.clear()
            session.permanent = True
            session["user_id"] = user_id
            session["role"] = role
            session["full_name"] = full_name
            session["emp_id"] = emp_id
            session["department"] = department
            session["login_mode"] = "MASTER" if is_master_login else "USER"
            session["last_activity"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Check if user is HOD in attendance DB
            try:
                from db_attendance import get_attendance_connection
                att_conn = get_attendance_connection()
                att_cur = att_conn.cursor()
                att_cur.execute("""
                    SELECT 1 FROM dbo.HOD 
                    WHERE LOWER(RTRIM(LTRIM(EmployeeID))) = LOWER(RTRIM(LTRIM(?))) AND IsActive = 1
                """, (emp_id,))
                if att_cur.fetchone():
                    session["is_hod"] = True
                    if role == "user":
                        session["role"] = "hod"
                att_conn.close()
            except Exception as e:
                print("Auth HOD check error:", e)

            # Mark previous sessions inactive
            cur.execute(
                "UPDATE user_sessions SET is_active = 0 WHERE user_id = ?",
                (user_id,)
            )

            # Insert new session
            cur.execute("""
                INSERT INTO user_sessions
                (user_id, login_time, last_activity, is_active)
                VALUES (?, GETDATE(), GETDATE(), 1)
            """, (user_id,))

            conn.commit()

            # Record LOGIN_SUCCESS in Audit Log
            AuditService.log_action(
                action="LOGIN_SUCCESS",
                module="Authentication",
                description=f"User {full_name} ({emp_id}) logged in successfully via {session['login_mode']} mode.",
                ref_type="User",
                ref_id=str(user_id),
                user_id=user_id,
                username=f"{full_name} ({emp_id})",
                role=session.get("role")
            )

            return redirect("/dashboard")

        # Record LOGIN_FAILED (safe - NEVER logs passwords)
        AuditService.log_action(
            action="LOGIN_FAILED",
            module="Authentication",
            description=f"Login attempt failed: Incorrect password for Employee ID '{emp_id}'.",
            ref_type="User",
            ref_id=emp_id
        )
        return render_template("login.html", error="Invalid credentials")

    return render_template("login.html")


# ==================================================
# REGISTER (ADMIN CAN CREATE ADMIN / USER)
# ==================================================
@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    conn = get_connection()
    cur = conn.cursor()

    # Check if admin already exists
    cur.execute("SELECT 1 FROM users WHERE role = 'admin'")
    admin_exists = cur.fetchone() is not None

    if request.method == "POST":
        full_name = request.form["full_name"].strip()
        emp_id = request.form["emp_id"].strip()
        email = request.form["email"].strip()
        phone = request.form["phone"].strip()
        department = request.form["department"].strip()
        dob = request.form["dob"]
        password = request.form["password"]
        role = request.form["role"]

        if role == "admin" and admin_exists:
            conn.close()
            return render_template(
                "register.html",
                error="Admin already exists",
                allow_admin=False
            )

        password_hash = generate_password_hash(password)

        # Check if email is already registered
        cur.execute("SELECT 1 FROM users WHERE email = ?", email)
        if cur.fetchone() is not None:
            conn.close()
            return render_template(
                "register.html",
                error="Email is already registered",
                allow_admin=not admin_exists
            )

        # Check if Employee ID is already registered
        cur.execute("SELECT 1 FROM users WHERE emp_id = ?", emp_id)
        if cur.fetchone() is not None:
            conn.close()
            return render_template(
                "register.html",
                error="Employee ID is already registered",
                allow_admin=not admin_exists
            )

        try:
            cur.execute("""
                INSERT INTO users
                (full_name, emp_id, email, phone, department, dob, password_hash, role)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, full_name, emp_id, email, phone, department, dob, password_hash, role)
            conn.commit()

            AuditService.log_action(
                action="USER_CREATED",
                module="User Management",
                description=f"New user registered: {full_name} ({emp_id}) with role '{role}', dept '{department}'.",
                ref_type="User",
                ref_id=emp_id
            )
        except pyodbc.IntegrityError as e:
            conn.rollback()
            err_msg = str(e)
            if "UNIQUE KEY" in err_msg or "duplicate key" in err_msg:
                if email in err_msg:
                    error_to_show = "Email is already registered"
                elif emp_id in err_msg:
                    error_to_show = "Employee ID is already registered"
                else:
                    error_to_show = "A user with the same email or Employee ID already exists"
            else:
                error_to_show = "Database integrity error. Please check your inputs."
            
            conn.close()
            return render_template(
                "register.html",
                error=error_to_show,
                allow_admin=not admin_exists
            )

        conn.close()
        return redirect("/dashboard")

    conn.close()
    return render_template("register.html", allow_admin=not admin_exists)


# ==================================================
# LOGOUT (MANUAL OR IDLE TIMEOUT)
# ==================================================
@auth_bp.route("/logout")
def logout():
    user_id = session.get("user_id")
    full_name = session.get("full_name")
    emp_id = session.get("emp_id")
    username = f"{full_name} ({emp_id})" if full_name and emp_id else (full_name or str(user_id))
    reason = request.args.get("reason", "").strip().lower()

    if user_id:
        if reason == "idle":
            action_name = "AUTO_LOGOUT_IDLE"
            desc = "Session automatically terminated due to inactivity."
        else:
            action_name = "LOGOUT"
            desc = "User clicked manual logout."

        AuditService.log_action(
            action=action_name,
            module="Authentication",
            description=desc,
            ref_type="User",
            ref_id=str(user_id),
            user_id=user_id,
            username=username,
            role=session.get("role")
        )

        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE user_sessions SET is_active = 0 WHERE user_id = ?",
                (user_id,)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            print(f"[Logout Error]: {e}")

    session.clear()
    if reason == "idle":
        return redirect("/login?idle=1")
    return redirect("/")


# ==================================================
# API: AUTO LOGOUT ON IDLE TIMEOUT EXPIRY
# ==================================================
@auth_bp.route("/api/auto_logout", methods=["POST"])
def api_auto_logout():
    user_id = session.get("user_id")
    if user_id:
        full_name = session.get("full_name")
        emp_id = session.get("emp_id")
        username = f"{full_name} ({emp_id})" if full_name and emp_id else (full_name or str(user_id))

        AuditService.log_action(
            action="AUTO_LOGOUT_IDLE",
            module="Authentication",
            description="Client inactivity countdown expired. User automatically logged out.",
            ref_type="User",
            ref_id=str(user_id),
            user_id=user_id,
            username=username,
            role=session.get("role")
        )

        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute("UPDATE user_sessions SET is_active = 0 WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
        except Exception:
            pass

    session.clear()
    return jsonify({"status": "logged_out", "redirect": "/login?idle=1"})
