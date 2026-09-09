from flask import Blueprint, render_template, request, redirect, session
from werkzeug.utils import secure_filename
from db import get_db as get_connection
from db_attendance import get_attendance_connection
import os
from datetime import datetime
from audit_service import AuditService

admin_bp = Blueprint("admin", __name__)

UPLOAD_FOLDER = "static/images"


# ==================================================
# USER MANAGEMENT (ADMIN ONLY)
# ==================================================
@admin_bp.route("/admin/users")
def manage_users():
    return redirect("/admin/roles")


@admin_bp.route("/admin/roles")
def manage_roles():
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    # Fetch active HODs from etime database
    att_conn = get_attendance_connection()
    att_cur = att_conn.cursor()
    att_cur.execute("""
        SELECT HODID, EmployeeID, EmployeeName, Department, AssignedDate, IsActive, EmailID
        FROM dbo.HOD
        ORDER BY EmployeeName
    """)
    hod_rows = att_cur.fetchall()
    att_conn.close()

    active_hod_ids = {row[1] for row in hod_rows if row[5] == 1}

    hod_list = []
    for row in hod_rows:
        hod_list.append({
            'hod_id': row[0],
            'emp_id': row[1],
            'name': row[2],
            'department': row[3],
            'assigned_date': row[4],
            'is_active': row[5],
            'email': row[6]
        })

    conn = get_connection()
    cur = conn.cursor()

    # Fetch Supervisors from local database
    cur.execute("""
        SELECT SupervisorID, EmployeeID, SupervisorName, EmailID, Department, AssignedDate
        FROM dbo.Supervisor_Master
        ORDER BY SupervisorName
    """)
    supervisor_rows = cur.fetchall()
    active_supervisor_ids = {row[1] for row in supervisor_rows}

    supervisor_list = []
    for row in supervisor_rows:
        supervisor_list.append({
            'supervisor_id': row[0],
            'emp_id': row[1],
            'name': row[2],
            'email': row[3],
            'department': row[4],
            'assigned_date': row[5]
        })

    # Fetch global data entry setting (defaults to False until Admin enables it)
    cur.execute("SELECT setting_value FROM dbo.system_settings WHERE setting_key = 'enable_personal_data_entry'")
    setting_row = cur.fetchone()
    global_data_entry = (setting_row[0] == 'true') if setting_row else False

    cur.execute("""
        SELECT 
            u.id,
            u.full_name,
            u.emp_id,
            u.email,
            u.role,
            u.department,
            CASE 
                WHEN s.is_active = 1 THEN 'Active'
                ELSE 'Idle'
            END AS status,
            ISNULL(u.data_entry_enabled, 1) AS data_entry_enabled
        FROM users u
        LEFT JOIN user_sessions s 
            ON u.id = s.user_id AND s.is_active = 1
        ORDER BY u.full_name
    """)

    raw_users = cur.fetchall()
    conn.close()

    users_list = []
    for row in raw_users:
        users_list.append({
            'id': row[0],
            'full_name': row[1],
            'emp_id': row[2],
            'email': row[3],
            'role': row[4],
            'department': row[5],
            'status': row[6],
            'data_entry_enabled': bool(row[7]),
            'is_hod': row[2] in active_hod_ids if row[2] else False,
            'is_supervisor': row[2] in active_supervisor_ids if row[2] else False
        })

    return render_template(
        "manage_roles.html",
        users=users_list,
        hods=hod_list,
        supervisors=supervisor_list,
        global_data_entry=global_data_entry
    )


@admin_bp.route("/admin/toggle_global_data_entry", methods=["POST"])
def toggle_global_data_entry():
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT setting_value FROM dbo.system_settings WHERE setting_key = 'enable_personal_data_entry'")
    row = cur.fetchone()
    curr_val = row[0] if row else 'true'
    new_val = 'false' if curr_val == 'true' else 'true'

    if row:
        cur.execute("UPDATE dbo.system_settings SET setting_value = ? WHERE setting_key = 'enable_personal_data_entry'", (new_val,))
    else:
        cur.execute("INSERT INTO dbo.system_settings (setting_key, setting_value) VALUES ('enable_personal_data_entry', ?)", (new_val,))

    conn.commit()
    conn.close()
    return redirect("/admin/roles")


@admin_bp.route("/admin/toggle_user_data_entry/<int:user_id>", methods=["POST"])
def toggle_user_data_entry(user_id):
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT data_entry_enabled FROM dbo.users WHERE id = ?", (user_id,))
    row = cur.fetchone()
    if row:
        curr_enabled = 1 if row[0] is None or row[0] else 0
        new_enabled = 0 if curr_enabled == 1 else 1
        cur.execute("UPDATE dbo.users SET data_entry_enabled = ? WHERE id = ?", (new_enabled, user_id))
        conn.commit()
    conn.close()

    return redirect("/admin/roles")



@admin_bp.route("/admin/make_hod/<int:user_id>")
def make_hod(user_id):
    print(f"DEBUG: make_hod called for user_id={user_id}")
    if "user_id" not in session or session.get("role") != "admin":
        print("DEBUG: Access denied (not admin or not logged in)")
        return redirect("/access_denied")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT emp_id, full_name, department, email FROM users WHERE id = ?", user_id)
    user = cur.fetchone()

    if not user or not user[0]:
        print(f"DEBUG: User not found or empty Employee ID for user_id={user_id}")
        conn.close()
        return redirect("/admin/roles")

    emp_id, full_name, department, email = user
    print(f"DEBUG: Appointing HOD - EmployeeID: {emp_id}, Name: {full_name}, Dept: {department}")

    # Write to local HOD_Master in CompanyPortalDB
    cur.execute("SELECT HODID FROM dbo.HOD_Master WHERE EmployeeID = ?", emp_id)
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE dbo.HOD_Master
            SET EmployeeName = ?, EmailID = ?, Department = ?, AssignedDate = GETDATE()
            WHERE EmployeeID = ?
        """, (full_name, email, department or 'IT', emp_id))
        print("DEBUG: Updated existing HOD_Master record")
    else:
        cur.execute("""
            INSERT INTO dbo.HOD_Master (EmployeeID, EmployeeName, EmailID, Department, AssignedDate)
            VALUES (?, ?, ?, ?, GETDATE())
        """, (emp_id, full_name, email, department or 'IT'))
        print("DEBUG: Inserted new HOD_Master record")
    conn.commit()
    conn.close()

    # Synchronize with etime HOD table
    att_conn = get_attendance_connection()
    att_cur = att_conn.cursor()
    att_cur.execute("SELECT HODID FROM dbo.HOD WHERE EmployeeID = ?", emp_id)
    row = att_cur.fetchone()
    if row:
        att_cur.execute("""
            UPDATE dbo.HOD
            SET IsActive = 1, AssignedDate = GETDATE(), EmployeeName = ?, Department = ?, EmailID = ?
            WHERE EmployeeID = ?
        """, (full_name, department or 'IT', email, emp_id))
    else:
        att_cur.execute("""
            INSERT INTO dbo.HOD (EmployeeID, EmployeeName, Department, AssignedDate, IsActive, CreatedDate, EmailID)
            VALUES (?, ?, ?, GETDATE(), 1, GETDATE(), ?)
        """, (emp_id, full_name, department or 'IT', email))

    att_conn.commit()
    att_conn.close()

    return redirect("/admin/roles")


@admin_bp.route("/admin/remove_hod/<int:user_id>")
def remove_hod(user_id):
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT emp_id FROM users WHERE id = ?", user_id)
    row = cur.fetchone()

    if not row or not row[0]:
        conn.close()
        return redirect("/admin/roles")

    emp_id = row[0]

    # Delete from HOD_Master in CompanyPortalDB
    cur.execute("DELETE FROM dbo.HOD_Master WHERE EmployeeID = ?", emp_id)
    conn.commit()
    conn.close()

    # Deactivate in etime HOD table
    att_conn = get_attendance_connection()
    att_cur = att_conn.cursor()
    att_cur.execute("UPDATE dbo.HOD SET IsActive = 0 WHERE EmployeeID = ?", emp_id)
    att_conn.commit()
    att_conn.close()

    return redirect("/admin/roles")


@admin_bp.route("/admin/make_supervisor/<int:user_id>")
def make_supervisor(user_id):
    print(f"DEBUG: make_supervisor called for user_id={user_id}")
    if "user_id" not in session or session.get("role") != "admin":
        print("DEBUG: Access denied (not admin or not logged in)")
        return redirect("/access_denied")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT emp_id, full_name, department, email FROM users WHERE id = ?", user_id)
    user = cur.fetchone()

    if not user or not user[0]:
        print(f"DEBUG: User not found or empty Employee ID for user_id={user_id}")
        conn.close()
        return redirect("/admin/roles")

    emp_id, full_name, department, email = user
    print(f"DEBUG: Appointing Supervisor - EmployeeID: {emp_id}, Name: {full_name}, Dept: {department}")

    # Write to local Supervisor_Master
    cur.execute("SELECT SupervisorID FROM dbo.Supervisor_Master WHERE EmployeeID = ?", emp_id)
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE dbo.Supervisor_Master
            SET SupervisorName = ?, EmailID = ?, Department = ?, AssignedDate = GETDATE()
            WHERE EmployeeID = ?
        """, (full_name, email, department or 'IT', emp_id))
        print("DEBUG: Updated existing Supervisor_Master record")
    else:
        cur.execute("""
            INSERT INTO dbo.Supervisor_Master (EmployeeID, SupervisorName, EmailID, Department, AssignedDate)
            VALUES (?, ?, ?, ?, GETDATE())
        """, (emp_id, full_name, email, department or 'IT'))
        print("DEBUG: Inserted new Supervisor_Master record")

    conn.commit()
    conn.close()

    return redirect("/admin/roles")


@admin_bp.route("/admin/remove_supervisor/<int:user_id>")
def remove_supervisor(user_id):
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT emp_id FROM users WHERE id = ?", user_id)
    row = cur.fetchone()

    if not row or not row[0]:
        conn.close()
        return redirect("/admin/roles")

    emp_id = row[0]

    # Delete from Supervisor_Master
    cur.execute("DELETE FROM dbo.Supervisor_Master WHERE EmployeeID = ?", emp_id)
    conn.commit()
    conn.close()

    return redirect("/admin/roles")


# ==================================================
# MANAGE APP PERMISSIONS
# ==================================================
@admin_bp.route("/admin/permissions")
def manage_permissions_list():
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    emp_id_filter = request.args.get("emp_id", "").strip()

    conn = get_connection()
    cur = conn.cursor()

    # Fetch users (optionally filtered by emp_id)
    if emp_id_filter:
        cur.execute("""
            SELECT id, emp_id, full_name, email, department
            FROM users
            WHERE emp_id LIKE ?
            ORDER BY full_name
        """, f"%{emp_id_filter}%")
    else:
        cur.execute("""
            SELECT id, emp_id, full_name, email, department
            FROM users
            ORDER BY full_name
        """)
    raw_users = cur.fetchall()

    # Fetch all active user app permissions
    cur.execute("""
        SELECT p.user_id, a.app_name
        FROM user_app_permissions p
        JOIN applications a ON p.app_id = a.id
        WHERE a.is_active = 1
    """)
    raw_perms = cur.fetchall()
    conn.close()

    # Group permissions by user_id
    perms_by_user = {}
    for user_id, app_name in raw_perms:
        if user_id not in perms_by_user:
            perms_by_user[user_id] = []
        perms_by_user[user_id].append(app_name)

    # Combine users with their permissions
    users_list = []
    for row in raw_users:
        u_id = row[0]
        users_list.append({
            'id': u_id,
            'emp_id': row[1],
            'full_name': row[2],
            'email': row[3],
            'department': row[4],
            'apps': perms_by_user.get(u_id, [])
        })

    return render_template("manage_permissions_list.html", users=users_list, emp_id_filter=emp_id_filter)


@admin_bp.route("/admin/permissions/<int:user_id>", methods=["GET", "POST"])
def manage_permissions(user_id):
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    conn = get_connection()
    cur = conn.cursor()

    if request.method == "POST":
        selected_apps = request.form.getlist("apps")

        cur.execute(
            "DELETE FROM user_app_permissions WHERE user_id = ?",
            user_id
        )

        for app_id in selected_apps:
            cur.execute(
                "INSERT INTO user_app_permissions (user_id, app_id) VALUES (?, ?)",
                user_id, app_id
            )

        conn.commit()
        conn.close()
        return redirect("/admin/permissions")

    # All apps
    cur.execute("""
        SELECT id, app_name
        FROM applications
        WHERE is_active = 1
    """)
    apps = cur.fetchall()

    # User allowed apps
    cur.execute("""
        SELECT app_id
        FROM user_app_permissions
        WHERE user_id = ?
    """, user_id)
    allowed_apps = [row[0] for row in cur.fetchall()]

    conn.close()

    return render_template(
        "manage_permissions.html",
        apps=apps,
        allowed_apps=allowed_apps,
        user_id=user_id
    )


# ==================================================
# FORCE LOGOUT USER
# ==================================================
@admin_bp.route("/admin/logout_user/<int:user_id>")
def force_logout_user(user_id):
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE user_sessions
        SET is_active = 0
        WHERE user_id = ?
    """, user_id)

    conn.commit()
    conn.close()

    return redirect("/admin/roles")


# ==================================================
# ADD APPLICATION (ADMIN)
# ==================================================
@admin_bp.route("/admin/add_app", methods=["GET", "POST"])
def add_application():
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    if request.method == "POST":
        app_code = request.form["app_code"].strip()
        app_name = request.form["app_name"].strip()
        app_url = request.form["app_url"].strip()
        image = request.files.get("app_image")

        filename = None
        if image and image.filename:
            filename = secure_filename(image.filename)
            os.makedirs(UPLOAD_FOLDER, exist_ok=True)
            image.save(os.path.join(UPLOAD_FOLDER, filename))

        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO applications
            (app_code, app_name, app_url, app_image, is_active)
            VALUES (?, ?, ?, ?, 1)
        """, app_code, app_name, app_url, filename)

        conn.commit()
        conn.close()

        return redirect("/admin/add_app")

    # GET: fetch existing applications to allow admin to remove them
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT id, app_code, app_name, app_url, app_image, is_active
        FROM applications
        ORDER BY app_name
    """)
    apps = cur.fetchall()
    conn.close()

    return render_template("add_application.html", apps=apps)


@admin_bp.route("/admin/delete_app/<int:app_id>", methods=["POST"])
def delete_application(app_id):
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    conn = get_connection()
    cur = conn.cursor()

    # Find image filename to remove file from disk
    cur.execute("SELECT app_image FROM applications WHERE id = ?", app_id)
    row = cur.fetchone()
    if row and row[0]:
        try:
            img_path = os.path.join(UPLOAD_FOLDER, row[0])
            if os.path.exists(img_path):
                os.remove(img_path)
        except Exception:
            pass

    cur.execute("DELETE FROM applications WHERE id = ?", app_id)
    conn.commit()
    conn.close()

    return redirect("/admin/add_app")


# ==================================================
# ADMIN ATTENDANCE (ALL EMPLOYEES)
# ==================================================
@admin_bp.route("/admin/attendance")
def admin_attendance():
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    date = request.args.get("date")

    conn = get_attendance_connection()
    cur = conn.cursor()

    if date:
        query = """
            SELECT
                AttendanceDate,
                EmployeeId,
                InTime,
                OutTime,
                Duration,
                LateBy,
                EarlyBy,
                IsOnLeave,
                WeeklyOff,
                Holiday
            FROM dbo.AttendanceLogs
            WHERE AttendanceDate = ?
            ORDER BY AttendanceDate DESC
        """
        params = [date]
    else:
        query = """
            SELECT TOP 100
                AttendanceDate,
                EmployeeId,
                InTime,
                OutTime,
                Duration,
                LateBy,
                EarlyBy,
                IsOnLeave,
                WeeklyOff,
                Holiday
            FROM dbo.AttendanceLogs
            ORDER BY AttendanceDate DESC
        """
        params = []

    cur.execute(query, params)
    records = cur.fetchall()
    
    # Helper to optimize parsing time string (e.g. "1900-01-01 08:30:00" or "08:30:00")
    def parse_time_str(val):
        if not val or not isinstance(val, str):
            return None
        parts = val.split()
        time_part = parts[-1] if parts else val
        try:
            t = datetime.strptime(time_part, '%H:%M:%S').time()
            if t.hour == 0 and t.minute == 0 and t.second == 0:
                return None
            return t
        except ValueError:
            return None

    # Convert string dates to datetime objects
    converted_records = []
    for row in records:
        in_time = parse_time_str(row[2])
        out_time = parse_time_str(row[3])
        
        duration = row[4]
        if (not duration or duration == 0.0) and in_time and out_time:
            dt_in = datetime.combine(datetime.today(), in_time)
            dt_out = datetime.combine(datetime.today(), out_time)
            diff = dt_out - dt_in
            minutes = diff.total_seconds() / 60.0
            if minutes > 0:
                duration = round(minutes, 1)

        late_by = row[5]
        if (not late_by or late_by == 0) and in_time:
            shift_start = datetime.strptime("08:30:00", "%H:%M:%S").time()
            if in_time > shift_start:
                dt_in = datetime.combine(datetime.today(), in_time)
                dt_start = datetime.combine(datetime.today(), shift_start)
                late_mins = int((dt_in - dt_start).total_seconds() / 60)
                if late_mins > 0:
                    late_by = late_mins

        early_by = row[6]
        if (not early_by or early_by == 0) and out_time:
            shift_end = datetime.strptime("19:00:00", "%H:%M:%S").time()
            if out_time < shift_end:
                dt_out = datetime.combine(datetime.today(), out_time)
                dt_end = datetime.combine(datetime.today(), shift_end)
                early_mins = int((dt_end - dt_out).total_seconds() / 60)
                if early_mins > 0:
                    early_by = early_mins

        row_dict = {
            'AttendanceDate': datetime.strptime(row[0], '%Y-%m-%d').date() if isinstance(row[0], str) else row[0],
            'EmployeeId': row[1],
            'InTime': in_time,
            'OutTime': out_time,
            'Duration': duration,
            'LateBy': late_by,
            'EarlyBy': early_by,
            'IsOnLeave': row[7],
            'WeeklyOff': row[8],
            'Holiday': row[9]
        }
        converted_records.append(row_dict)
    
    conn.close()

    return render_template(
        "admin_attendance.html",
        records=converted_records
    )


# ==================================================
# UPDATE USER ROLE (ADMIN ONLY)
# ==================================================
@admin_bp.route("/admin/update_role/<int:user_id>", methods=["POST"])
def update_role(user_id):
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    new_role = request.form.get("role")
    if new_role not in ["admin", "user", "MD", "GM", "HR"]:
        return redirect("/admin/roles")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT full_name, emp_id, role FROM users WHERE id = ?", (user_id,))
    u_row = cur.fetchone()
    old_role = u_row[2] if u_row else "unknown"
    u_name = u_row[0] if u_row else f"User {user_id}"
    emp_code = u_row[1] if u_row else ""

    cur.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
    conn.commit()
    conn.close()

    AuditService.log_action(
        action="ROLE_CHANGED",
        module="User Management",
        description=f"Role for {u_name} ({emp_code}) changed from '{old_role}' to '{new_role}'.",
        ref_type="User",
        ref_id=str(user_id)
    )

    return redirect("/admin/roles")


# ==================================================
# SYSTEM AUDIT LOGS VIEW (ADMIN & ACCOUNTS)
# ==================================================
@admin_bp.route("/admin/audit_logs")
def view_audit_logs():
    user_role = (session.get("role") or "").lower().strip()
    if "user_id" not in session or (user_role not in ["admin", "accounts", "md", "gm"] and not session.get("is_hod")):
        return redirect("/access_denied")

    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    selected_module = request.args.get("module", "").strip()
    selected_action = request.args.get("action", "").strip()
    search = request.args.get("search", "").strip()

    conn = get_connection()
    cur = conn.cursor()

    # Dynamic filter query
    where_clauses = ["1=1"]
    params = []

    if date_from:
        where_clauses.append("CAST(CreatedAt AS DATE) >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("CAST(CreatedAt AS DATE) <= ?")
        params.append(date_to)
    if selected_module:
        where_clauses.append("Module = ?")
        params.append(selected_module)
    if selected_action:
        where_clauses.append("Action = ?")
        params.append(selected_action)
    if search:
        where_clauses.append("(Username LIKE ? OR Description LIKE ? OR ReferenceId LIKE ? OR IPAddress LIKE ?)")
        s_term = f"%{search}%"
        params.extend([s_term, s_term, s_term, s_term])

    where_sql = " AND ".join(where_clauses)

    query = f"""
        SELECT TOP 400 Id, UserId, Username, Role, Action, Module, Description,
               ReferenceType, ReferenceId, IPAddress, UserAgent, CreatedAt
        FROM UserActionLog
        WHERE {where_sql}
        ORDER BY CreatedAt DESC
    """
    cur.execute(query, tuple(params))
    rows = cur.fetchall()

    logs = []
    for r in rows:
        created_dt = r[11]
        dt_str = created_dt.strftime("%d-%m-%Y %I:%M:%S %p") if isinstance(created_dt, datetime) else str(created_dt)
        logs.append({
            "id": r[0],
            "user_id": r[1],
            "username": r[2] or "Anonymous",
            "role": r[3] or "—",
            "action": r[4],
            "module": r[5],
            "description": r[6] or "",
            "ref_type": r[7] or "",
            "ref_id": r[8] or "—",
            "ip": r[9] or "—",
            "user_agent": r[10] or "",
            "created_at_str": dt_str
        })

    # Fetch filter options
    cur.execute("SELECT DISTINCT Module FROM UserActionLog WHERE Module IS NOT NULL ORDER BY Module")
    available_modules = [m[0] for m in cur.fetchall()]

    cur.execute("SELECT DISTINCT Action FROM UserActionLog WHERE Action IS NOT NULL ORDER BY Action")
    available_actions = [a[0] for a in cur.fetchall()]

    # Fetch quick summary stats
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT UserId) FROM UserActionLog")
    stat_row = cur.fetchone()
    total_actions = stat_row[0] if stat_row else 0
    total_users = stat_row[1] if stat_row else 0

    cur.execute("SELECT COUNT(*) FROM UserActionLog WHERE CAST(CreatedAt AS DATE) = CAST(GETDATE() AS DATE)")
    today_actions = cur.fetchone()[0] or 0

    conn.close()

    return render_template(
        "admin_audit_logs.html",
        logs=logs,
        modules=available_modules,
        actions=available_actions,
        date_from=date_from,
        date_to=date_to,
        selected_module=selected_module,
        selected_action=selected_action,
        search=search,
        total_actions=total_actions,
        total_users=total_users,
        today_actions=today_actions
    )


# ==================================================
# APPLICATION ERROR LOGS VIEW (ADMIN ONLY)
# ==================================================
@admin_bp.route("/admin/error_logs")
def view_error_logs():
    if "user_id" not in session or session.get("role") != "admin":
        return redirect("/access_denied")

    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    selected_level = request.args.get("level", "").strip()
    selected_module = request.args.get("module", "").strip()
    search = request.args.get("search", "").strip()

    conn = get_connection()
    cur = conn.cursor()

    where_clauses = ["1=1"]
    params = []

    if date_from:
        where_clauses.append("CAST(Timestamp AS DATE) >= ?")
        params.append(date_from)
    if date_to:
        where_clauses.append("CAST(Timestamp AS DATE) <= ?")
        params.append(date_to)
    if selected_level:
        where_clauses.append("Level = ?")
        params.append(selected_level)
    if selected_module:
        where_clauses.append("Module = ?")
        params.append(selected_module)
    if search:
        where_clauses.append("(Message LIKE ? OR ExceptionType LIKE ? OR RequestPath LIKE ? OR Action LIKE ?)")
        s_term = f"%{search}%"
        params.extend([s_term, s_term, s_term, s_term])

    where_sql = " AND ".join(where_clauses)

    query = f"""
        SELECT TOP 250 Id, Timestamp, Level, Message, ExceptionType, StackTrace,
               UserId, Username, Role, Module, Action, RequestPath, HTTPMethod, IP, ReferenceId, CreatedAt
        FROM ApplicationErrorLog
        WHERE {where_sql}
        ORDER BY Timestamp DESC
    """
    cur.execute(query, tuple(params))
    rows = cur.fetchall()

    error_logs = []
    for r in rows:
        ts = r[1]
        ts_str = ts.strftime("%d-%m-%Y %I:%M:%S %p") if isinstance(ts, datetime) else str(ts)
        error_logs.append({
            "id": r[0],
            "timestamp_str": ts_str,
            "level": r[2],
            "message": r[3],
            "exception_type": r[4] or "Exception",
            "stack_trace": r[5] or "",
            "user_id": r[6],
            "username": r[7] or "System",
            "role": r[8] or "—",
            "module": r[9] or "General",
            "action": r[10] or "—",
            "path": r[11] or "—",
            "method": r[12] or "—",
            "ip": r[13] or "—",
            "ref_id": r[14] or "—"
        })

    # Summary counts
    cur.execute("SELECT COUNT(*) FROM ApplicationErrorLog")
    total_errors = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(*) FROM ApplicationErrorLog WHERE CAST(Timestamp AS DATE) = CAST(GETDATE() AS DATE)")
    today_errors = cur.fetchone()[0] or 0

    cur.execute("SELECT DISTINCT Level FROM ApplicationErrorLog WHERE Level IS NOT NULL")
    levels = [l[0] for l in cur.fetchall()]

    cur.execute("SELECT DISTINCT Module FROM ApplicationErrorLog WHERE Module IS NOT NULL")
    modules = [m[0] for m in cur.fetchall()]

    conn.close()

    return render_template(
        "admin_error_logs.html",
        error_logs=error_logs,
        levels=levels,
        modules=modules,
        date_from=date_from,
        date_to=date_to,
        selected_level=selected_level,
        selected_module=selected_module,
        search=search,
        total_errors=total_errors,
        today_errors=today_errors
    )
