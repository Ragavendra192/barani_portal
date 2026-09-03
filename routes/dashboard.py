from flask import Blueprint, render_template, session, redirect, request
from datetime import datetime
from db import get_db as get_portal_connection
from db_attendance import get_attendance_connection

dashboard_bp = Blueprint("dashboard", __name__)


def get_logged_in_emp_id():
    emp_id = session.get("emp_id")
    if ("emp_id" not in session or "department" not in session) and "user_id" in session:
        conn = get_portal_connection()
        cur = conn.cursor()
        cur.execute("SELECT emp_id, department FROM dbo.users WHERE id = ?", (session["user_id"],))
        row = cur.fetchone()
        conn.close()
        if row:
            session["emp_id"] = row[0]
            session["department"] = row[1]
            emp_id = row[0]
    return emp_id


# ==================================================
# MAIN DASHBOARD
# ==================================================
@dashboard_bp.route("/dashboard")
def dashboard():
    if "user_id" not in session:
        return redirect("/")

    # Fetch user metadata to check supervisor/HOD status
    emp_id = get_logged_in_emp_id()
    is_supervisor = False
    is_hod = False
    if emp_id:
        try:
            conn = get_portal_connection()
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM dbo.Supervisor_Master WHERE EmployeeID = ?", (emp_id,))
            is_supervisor = cur.fetchone() is not None
            cur.execute("SELECT 1 FROM dbo.HOD_Master WHERE EmployeeID = ?", (emp_id,))
            is_hod = cur.fetchone() is not None
            conn.close()
        except Exception:
            pass

    # Query active notices visible to this user for the bottom announcement ticker
    notices = []
    try:
        conn = get_portal_connection()
        cur = conn.cursor()
        dept = session.get("department")
        cur.execute("""
            SELECT TOP 5 NoticeID, Title, Description, Priority, PublishDate, CreatorRole, AttachmentPath
            FROM dbo.Announcement_Master
            WHERE IsActive = 1
              AND PublishDate <= GETDATE()
              AND (ExpiryDate IS NULL OR ExpiryDate >= GETDATE())
              AND (DepartmentID IS NULL OR DepartmentID = ?)
            ORDER BY PublishDate DESC
        """, (dept,))
        rows = cur.fetchall()
        conn.close()

        for r in rows:
            notices.append({
                "id": r[0],
                "title": r[1],
                "description": r[2],
                "priority": r[3],
                "publish_date": r[4].strftime("%d-%b-%Y") if r[4] else "",
                "creator_role": r[5],
                "attachment": r[6]
            })
    except Exception as e:
        print(f"Notice fetch error: {e}")

    # ============================
    # ADMIN DASHBOARD
    # ============================
    if session.get("role") == "admin":
        # ---- Applications ----
        portal_conn = get_portal_connection()
        portal_cur = portal_conn.cursor()
        portal_cur.execute("""
            SELECT id, app_name, app_url, app_image
            FROM dbo.applications
            WHERE is_active = 1
        """)
        apps = portal_cur.fetchall()
        portal_conn.close()

        # ---- Recent Attendance (Preview) ----
        attendance = []

        return render_template(
            "admin_dashboard.html",
            apps=apps,
            attendance=attendance,
            notices=notices
        )

    # ============================
    # USER DASHBOARD (APPS ONLY)
    # ============================
    portal_conn = get_portal_connection()
    portal_cur = portal_conn.cursor()
    portal_cur.execute("""
        SELECT a.id, a.app_name, a.app_url, a.app_image
        FROM dbo.applications a
        JOIN dbo.user_app_permissions p
            ON a.id = p.app_id
        WHERE p.user_id = ?
          AND a.is_active = 1
    """, (session["user_id"],))
    apps = portal_cur.fetchall()
    portal_conn.close()

    return render_template(
        "user_dashboard.html",
        apps=apps,
        notices=notices
    )


# ==================================================
# HR PANEL (ATTENDANCE REPORT FOR ALL EMPLOYEES)
# ==================================================
@dashboard_bp.route("/hr/panel")
def hr_panel():
    if "user_id" not in session:
        return redirect("/")

    # Verify session fields are initialized (caches department in session)
    emp_id = get_logged_in_emp_id()

    if session.get("department") != "HR & Admin" and session.get("role") != "HR":
        return redirect("/access_denied")

    date = request.args.get("date")
    month_filter = request.args.get("month_filter")
    if month_filter:
        try:
            year, month = map(int, month_filter.split("-"))
        except ValueError:
            year = datetime.today().year
            month = datetime.today().month
            month_filter = f"{year}-{month:02d}"
    else:
        year = datetime.today().year
        month = datetime.today().month
        month_filter = f"{year}-{month:02d}"

    # Get portal users to map EmployeeId -> Name & Department
    portal_conn = get_portal_connection()
    portal_cur = portal_conn.cursor()
    portal_cur.execute("SELECT emp_id, full_name, department FROM dbo.users")
    user_map = {row[0]: {"name": row[1], "dept": row[2]} for row in portal_cur.fetchall()}
    portal_conn.close()

    # ---- Fetch Attendance Logs of All Employees ----
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

    # Helper to parse time string
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

    converted_records = []
    total_late_today = 0
    total_leave_today = 0

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

        is_on_leave = bool(row[7])
        if is_on_leave:
            total_leave_today += 1
        if late_by and late_by > 0:
            total_late_today += 1

        emp_info = user_map.get(row[1], {"name": "Unknown", "dept": "Unknown"})

        row_dict = {
            'AttendanceDate': datetime.strptime(row[0], '%Y-%m-%d').date() if isinstance(row[0], str) else row[0],
            'EmployeeId': row[1],
            'EmployeeName': emp_info["name"],
            'Department': emp_info["dept"],
            'InTime': in_time,
            'OutTime': out_time,
            'Duration': duration,
            'LateBy': late_by,
            'EarlyBy': early_by,
            'IsOnLeave': is_on_leave,
            'WeeklyOff': row[8],
            'Holiday': row[9]
        }
        converted_records.append(row_dict)

    # ---- Fetch Overall Attendance Summary per Employee (Filtered by Month & Year) ----
    cur.execute("""
        SELECT
            EmployeeId,
            COUNT(*) as TotalDays,
            SUM(CASE WHEN LateBy > 0 THEN 1 ELSE 0 END) as TotalLateDays,
            SUM(CASE WHEN EarlyBy > 0 THEN 1 ELSE 0 END) as TotalEarlyDays,
            SUM(CASE WHEN IsOnLeave = 1 THEN 1 ELSE 0 END) as TotalLeaveDays,
            ROUND(SUM(Duration), 1) as TotalDuration,
            ROUND(AVG(Duration), 1) as AvgDuration
        FROM dbo.AttendanceLogs
        WHERE MONTH(AttendanceDate) = ? AND YEAR(AttendanceDate) = ?
        GROUP BY EmployeeId
        ORDER BY EmployeeId
    """, (month, year))
    overall_rows = cur.fetchall()

    overall_attendance = []
    for r in overall_rows:
        e_id = r[0]
        emp_info = user_map.get(e_id, {"name": "Unknown", "dept": "Unknown"})
        overall_attendance.append({
            'EmployeeId': e_id,
            'EmployeeName': emp_info["name"],
            'Department': emp_info["dept"],
            'TotalDays': r[1],
            'TotalLateDays': r[2] or 0,
            'TotalEarlyDays': r[3] or 0,
            'TotalLeaveDays': r[4] or 0,
            'TotalDuration': r[5] or 0.0,
            'AvgDuration': r[6] or 0.0
        })

    conn.close()

    return render_template(
        "hr_panel.html",
        records=converted_records,
        overall_records=overall_attendance,
        total_late=total_late_today,
        total_leave=total_leave_today,
        month_filter=month_filter
    )


# ==================================================
# HR EXPORT ATTENDANCE AS EXCEL
# ==================================================
@dashboard_bp.route("/hr/export_attendance")
def hr_export_attendance():
    if "user_id" not in session or session.get("department") != "HR & Admin":
        return redirect("/access_denied")

    month_filter = request.args.get("month_filter")
    if month_filter:
        try:
            year, month = map(int, month_filter.split("-"))
        except ValueError:
            year = datetime.today().year
            month = datetime.today().month
            month_filter = f"{year}-{month:02d}"
    else:
        year = datetime.today().year
        month = datetime.today().month
        month_filter = f"{year}-{month:02d}"

    # Get portal users to map EmployeeId -> Name & Department
    portal_conn = get_portal_connection()
    portal_cur = portal_conn.cursor()
    portal_cur.execute("SELECT emp_id, full_name, department FROM dbo.users")
    user_map = {row[0]: {"name": row[1], "dept": row[2]} for row in portal_cur.fetchall()}
    portal_conn.close()

    # Query attendance database
    conn = get_attendance_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            EmployeeId,
            COUNT(*) as TotalDays,
            SUM(CASE WHEN LateBy > 0 THEN 1 ELSE 0 END) as TotalLateDays,
            SUM(CASE WHEN EarlyBy > 0 THEN 1 ELSE 0 END) as TotalEarlyDays,
            SUM(CASE WHEN IsOnLeave = 1 THEN 1 ELSE 0 END) as TotalLeaveDays,
            ROUND(SUM(Duration), 1) as TotalDuration,
            ROUND(AVG(Duration), 1) as AvgDuration
        FROM dbo.AttendanceLogs
        WHERE MONTH(AttendanceDate) = ? AND YEAR(AttendanceDate) = ?
        GROUP BY EmployeeId
        ORDER BY EmployeeId
    """, (month, year))
    overall_rows = cur.fetchall()
    conn.close()

    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    import io
    from flask import send_file
    import calendar

    wb = Workbook()
    ws = wb.active
    month_name = calendar.month_name[month]
    ws.title = f"Summary - {month_name}"

    # Style definitions
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="4F46E5", end_color="4F46E5", fill_type="solid") # Deep Indigo
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")

    headers = [
        "Employee ID", "Employee Name", "Department", 
        "Total Days Logged", "Late Days", "Early Off Days", 
        "On Leave Days", "Total Hours Worked", "Avg Daily Hours"
    ]
    
    ws.append(headers)
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_center

    # Add records
    for r in overall_rows:
        e_id = r[0]
        emp_info = user_map.get(e_id, {"name": "Unknown", "dept": "Unknown"})
        row_data = [
            e_id,
            emp_info["name"],
            emp_info["dept"],
            r[1],
            r[2] or 0,
            r[3] or 0,
            r[4] or 0,
            r[5] or 0.0,
            r[6] or 0.0
        ]
        ws.append(row_data)

    # Auto-adjust column widths
    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = max(max_len + 3, 12)

    # Save to BytesIO buffer
    file_stream = io.BytesIO()
    wb.save(file_stream)
    file_stream.seek(0)

    return send_file(
        file_stream,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"Attendance_Summary_{month_name}_{year}.xlsx"
    )


# ==================================================
# PERSONAL INFO → DEFAULT
# ==================================================
@dashboard_bp.route("/user/personal")
def personal_info():
    return redirect("/user/personal/attendance")


# ==================================================
# PERSONAL INFO → ATTENDANCE
# ==================================================
@dashboard_bp.route("/user/personal/attendance")
def personal_attendance():
    if "user_id" not in session:
        return redirect("/")

    emp_id = get_logged_in_emp_id()
    selected_date = request.args.get("date")

    raw_records = []
    if emp_id:
        try:
            conn = get_attendance_connection()
            cur = conn.cursor()

            if selected_date:
                cur.execute("""
                    SELECT AttendanceDate, InTime, OutTime, Duration,
                           LateBy, EarlyBy, IsOnLeave,
                           LeaveType, LeaveDuration,
                           WeeklyOff, Holiday, LeaveRemarks
                    FROM dbo.AttendanceLogs
                    WHERE EmployeeId = ?
                      AND AttendanceDate = ?
                    ORDER BY AttendanceDate DESC
                """, (emp_id, selected_date))
            else:
                cur.execute("""
                    SELECT TOP 15 AttendanceDate, InTime, OutTime, Duration,
                                  LateBy, EarlyBy, IsOnLeave,
                                  LeaveType, LeaveDuration,
                                  WeeklyOff, Holiday, LeaveRemarks
                    FROM dbo.AttendanceLogs
                    WHERE EmployeeId = ?
                    ORDER BY AttendanceDate DESC
                """, (emp_id,))

            raw_records = cur.fetchall()
            conn.close()
        except Exception as e:
            print(f"Attendance query error for emp_id '{emp_id}': {e}")
            raw_records = []

    attendance = []
    for row in raw_records:
        att_date = row[0]
        if isinstance(att_date, str):
            try:
                att_date = datetime.strptime(att_date, '%Y-%m-%d').date()
            except ValueError:
                pass

        in_time_dt = None
        if isinstance(row[1], str) and row[1]:
            try:
                in_time_dt = datetime.strptime(row[1], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    in_time_dt = datetime.strptime(row[1], '%H:%M:%S')
                except ValueError:
                    pass

        out_time_dt = None
        if isinstance(row[2], str) and row[2]:
            try:
                out_time_dt = datetime.strptime(row[2], '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    out_time_dt = datetime.strptime(row[2], '%H:%M:%S')
                except ValueError:
                    pass

        duration = row[3]
        is_in_valid = in_time_dt and not (in_time_dt.year == 1900 and in_time_dt.hour == 0 and in_time_dt.minute == 0)
        is_out_valid = out_time_dt and not (out_time_dt.year == 1900 and out_time_dt.hour == 0 and out_time_dt.minute == 0)

        if (not duration or duration == 0.0) and in_time_dt and out_time_dt:
            if is_in_valid and is_out_valid:
                diff = out_time_dt - in_time_dt
                minutes = diff.total_seconds() / 60.0
                if minutes > 0:
                    duration = round(minutes, 1)

        # Calculate Delay (Late Check-in after 08:30 or Early Check-out before 19:00)
        delay_parts = []
        late_by = row[4]
        early_by = row[5]

        if is_in_valid:
            in_time_comp = in_time_dt.time()
            shift_start_time = datetime.strptime("08:30:00", "%H:%M:%S").time()
            if in_time_comp > shift_start_time:
                dt_in = datetime.combine(datetime.today(), in_time_comp)
                dt_start = datetime.combine(datetime.today(), shift_start_time)
                late_mins = int((dt_in - dt_start).total_seconds() / 60)
                if late_mins > 0:
                    late_by = late_mins
                    delay_parts.append(f"Late: {late_mins}m")

        if is_out_valid:
            out_time_comp = out_time_dt.time()
            shift_end_time = datetime.strptime("19:00:00", "%H:%M:%S").time()
            if out_time_comp < shift_end_time:
                dt_out = datetime.combine(datetime.today(), out_time_comp)
                dt_end = datetime.combine(datetime.today(), shift_end_time)
                early_mins = int((dt_end - dt_out).total_seconds() / 60)
                if early_mins > 0:
                    early_by = early_mins
                    delay_parts.append(f"Early: {early_mins}m")

        delay_str = ", ".join(delay_parts) if delay_parts else "-"

        in_time_str = "-"
        if in_time_dt and not (in_time_dt.year == 1900 and in_time_dt.hour == 0 and in_time_dt.minute == 0):
            in_time_str = in_time_dt.strftime("%H:%M")

        out_time_str = "-"
        if out_time_dt and not (out_time_dt.year == 1900 and out_time_dt.hour == 0 and out_time_dt.minute == 0):
            out_time_str = out_time_dt.strftime("%H:%M")

        attendance.append({
            'AttendanceDate': att_date,
            'InTime': in_time_str,
            'OutTime': out_time_str,
            'Duration': duration,
            'Delay': delay_str,
            'LateBy': late_by,
            'EarlyBy': early_by,
            'IsOnLeave': row[6],
            'LeaveType': row[7],
            'LeaveDuration': row[8],
            'WeeklyOff': row[9],
            'Holiday': row[10],
            'LeaveRemarks': row[11]
        })

    return render_template(
        "attendance.html",
        attendance=attendance,
        active_tab="attendance"
    )





# ==================================================
# PERSONAL INFO → SALARY (REDIRECTED)
# ==================================================
@dashboard_bp.route("/user/personal/salary")
def personal_salary():
    return redirect("/user/personal/attendance")


# ==================================================
# PERSONAL INFO → PROFILE & 15-FIELD DATA ENTRY
# ==================================================

@dashboard_bp.route("/user/personal/profile")
def personal_profile():
    if "user_id" not in session:
        return redirect("/")

    user_id = session.get("user_id")
    conn = get_portal_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            full_name,
            emp_id,
            email,
            phone,
            department,
            role,
            ISNULL(data_entry_enabled, 1) as data_entry_enabled
        FROM dbo.users
        WHERE id = ?
    """, (user_id,))
    user = cur.fetchone()

    # Query global data entry setting (defaults to False unless Admin explicitly enables it)
    cur.execute("SELECT setting_value FROM dbo.system_settings WHERE setting_key = 'enable_personal_data_entry'")
    setting_row = cur.fetchone()
    global_enabled = (setting_row[0] == 'true') if setting_row else False

    # Query extended 15-field personal details
    cur.execute("""
        SELECT 
            phone, alt_phone, personal_email, dob, blood_group, gender, marital_status,
            present_address, permanent_address, emergency_name, emergency_relation, emergency_phone,
            aadhaar_no, pan_no, qualification, bank_name, account_no, ifsc_code, updated_at
        FROM dbo.user_personal_details
        WHERE user_id = ?
    """, (user_id,))
    details_row = cur.fetchone()
    conn.close()

    details = None
    if details_row:
        details = {
            'phone': details_row[0],
            'alt_phone': details_row[1],
            'personal_email': details_row[2],
            'dob': details_row[3],
            'blood_group': details_row[4],
            'gender': details_row[5],
            'marital_status': details_row[6],
            'present_address': details_row[7],
            'permanent_address': details_row[8],
            'emergency_name': details_row[9],
            'emergency_relation': details_row[10],
            'emergency_phone': details_row[11],
            'aadhaar_no': details_row[12],
            'pan_no': details_row[13],
            'qualification': details_row[14],
            'bank_name': details_row[15],
            'account_no': details_row[16],
            'ifsc_code': details_row[17],
            'updated_at': details_row[18]
        }

    can_data_entry = global_enabled and bool(user[6] if user and user[6] is not None else True)

    return render_template(
        "personal_profile.html",
        user=user,
        details=details,
        can_data_entry=can_data_entry,
        global_enabled=global_enabled,
        active_tab="profile"
    )


@dashboard_bp.route("/safety")
def company_safety():
    if "user_id" not in session:
        return redirect("/")

    return render_template("safety.html")


@dashboard_bp.route("/user/personal/save_profile", methods=["POST"])
def save_personal_profile():
    if "user_id" not in session:
        return redirect("/")
    alt_phone = request.form.get("alt_phone", "").strip()
    personal_email = request.form.get("personal_email", "").strip()
    dob = request.form.get("dob", "").strip()
    blood_group = request.form.get("blood_group", "").strip()
    gender = request.form.get("gender", "").strip()
    marital_status = request.form.get("marital_status", "").strip()
    present_address = request.form.get("present_address", "").strip()
    permanent_address = request.form.get("permanent_address", "").strip()
    emergency_name = request.form.get("emergency_name", "").strip()
    emergency_relation = request.form.get("emergency_relation", "").strip()
    emergency_phone = request.form.get("emergency_phone", "").strip()
    aadhaar_no = request.form.get("aadhaar_no", "").strip()
    pan_no = request.form.get("pan_no", "").strip()
    qualification = request.form.get("qualification", "").strip()
    bank_name = request.form.get("bank_name", "").strip()
    account_no = request.form.get("account_no", "").strip()
    ifsc_code = request.form.get("ifsc_code", "").strip()

    conn = get_portal_connection()
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM dbo.user_personal_details WHERE user_id = ?", (user_id,))
    exists = cur.fetchone() is not None

    if exists:
        cur.execute("""
            UPDATE dbo.user_personal_details SET
                phone = ?, alt_phone = ?, personal_email = ?, dob = ?, blood_group = ?,
                gender = ?, marital_status = ?, present_address = ?, permanent_address = ?,
                emergency_name = ?, emergency_relation = ?, emergency_phone = ?,
                aadhaar_no = ?, pan_no = ?, qualification = ?, bank_name = ?, account_no = ?, ifsc_code = ?,
                updated_at = GETDATE()
            WHERE user_id = ?
        """, (phone, alt_phone, personal_email, dob, blood_group, gender, marital_status,
              present_address, permanent_address, emergency_name, emergency_relation, emergency_phone,
              aadhaar_no, pan_no, qualification, bank_name, account_no, ifsc_code, user_id))
    else:
        cur.execute("""
            INSERT INTO dbo.user_personal_details (
                user_id, phone, alt_phone, personal_email, dob, blood_group, gender, marital_status,
                present_address, permanent_address, emergency_name, emergency_relation, emergency_phone,
                aadhaar_no, pan_no, qualification, bank_name, account_no, ifsc_code, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE()
            )
        """, (user_id, phone, alt_phone, personal_email, dob, blood_group, gender, marital_status,
              present_address, permanent_address, emergency_name, emergency_relation, emergency_phone,
              aadhaar_no, pan_no, qualification, bank_name, account_no, ifsc_code))

    if phone:
        cur.execute("UPDATE dbo.users SET phone = ? WHERE id = ?", (phone, user_id))

    conn.commit()
    conn.close()

    return redirect("/user/personal/profile?saved=1")


# ==================================================
# LAUNCH APPLICATION
# ==================================================
@dashboard_bp.route("/launch/<int:app_id>")
def launch_application(app_id):
    if "user_id" not in session:
        return redirect("/")

    user_id = session.get("user_id")
    role = session.get("role")

    portal_conn = get_portal_connection()
    portal_cur = portal_conn.cursor()

    if role == "admin":
        portal_cur.execute("""
            SELECT app_name, app_url 
            FROM dbo.applications 
            WHERE id = ? AND is_active = 1
        """, (app_id,))
    else:
        portal_cur.execute("""
            SELECT a.app_name, a.app_url 
            FROM dbo.applications a
            JOIN dbo.user_app_permissions p ON a.id = p.app_id
            WHERE a.id = ? AND p.user_id = ? AND a.is_active = 1
        """, (app_id, user_id))

    app = portal_cur.fetchone()
    portal_conn.close()

    if not app:
        return redirect("/access_denied")

    app_name, app_url = app

    # Check if internal or external URL
    if app_url.startswith("http://") or app_url.startswith("https://"):
        return render_template("launch_app.html", app_name=app_name, app_url=app_url, app_id=app_id)
    else:
        # Internal module, redirect to route
        return redirect(app_url)


# ==================================================
# SESSION MANAGEMENT (POLLING AND KEEPALIVE)
# ==================================================
from flask import jsonify
from config import Config
from datetime import datetime

@dashboard_bp.route("/check_session")
def check_session():
    if "user_id" not in session:
        return jsonify({"status": "unauthorized", "redirect": "/login"}), 401
    return jsonify({
        "status": "active",
        "idle_timeout_minutes": getattr(Config, "IDLE_TIMEOUT_MINUTES", 10),
        "idle_warning_seconds": getattr(Config, "IDLE_WARNING_SECONDS", 60)
    })

@dashboard_bp.route("/ping_activity", methods=["POST"])
def ping_activity():
    if "user_id" in session:
        session["last_activity"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return jsonify({"status": "ok", "timestamp": session["last_activity"]})
    return jsonify({"status": "unauthorized", "redirect": "/login?idle=1"}), 401

