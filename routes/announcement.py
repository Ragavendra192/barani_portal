import os
import pyodbc
from datetime import datetime
from flask import Blueprint, render_template, request, redirect, session, jsonify, send_from_directory
from werkzeug.utils import secure_filename
from db import get_db as get_connection

announcement_bp = Blueprint("announcement", __name__)

UPLOAD_FOLDER = "static/uploads/announcements"
ALLOWED_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "png", "jpg", "jpeg", "gif"}

# Helper to check allowed extensions
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# ==================================================
# DYNAMIC DB INITIALIZATION
# ==================================================
def init_announcement_db():
    conn = get_connection()
    cur = conn.cursor()
    
    # Drop old tables to perform clean migration to updated spec
    cur.execute("""
        IF EXISTS (SELECT * FROM sys.tables WHERE name = 'Announcement_ReadLog' AND schema_id = SCHEMA_ID('dbo'))
            DROP TABLE dbo.Announcement_ReadLog;
        IF EXISTS (SELECT * FROM sys.tables WHERE name = 'Announcement_Target' AND schema_id = SCHEMA_ID('dbo'))
            DROP TABLE dbo.Announcement_Target;
        IF EXISTS (SELECT * FROM sys.tables WHERE name = 'Announcement_AuditLog' AND schema_id = SCHEMA_ID('dbo'))
            DROP TABLE dbo.Announcement_AuditLog;
        IF EXISTS (SELECT * FROM sys.tables WHERE name = 'Announcement_Master' AND schema_id = SCHEMA_ID('dbo'))
            DROP TABLE dbo.Announcement_Master;
    """)
    conn.commit()
    
    # 1. Create Announcement_Master Table (Updated Specification)
    cur.execute("""
        CREATE TABLE dbo.Announcement_Master (
            NoticeID INT IDENTITY(1,1) PRIMARY KEY,
            Title NVARCHAR(200) NOT NULL,
            Description NVARCHAR(MAX) NOT NULL,
            DepartmentID NVARCHAR(100), -- NULL for General Notices, String department name for Dept Notices
            CreatedBy INT NOT NULL,
            CreatorRole NVARCHAR(50) NOT NULL,
            Priority NVARCHAR(50) NOT NULL,
            AttachmentPath NVARCHAR(500),
            PublishDate DATETIME NOT NULL,
            ExpiryDate DATETIME,
            IsActive BIT NOT NULL DEFAULT 1
        );
        PRINT 'Created updated Announcement_Master table';
    """)
    
    # 2. Create Announcement_ReadLog Table (Updated Specification)
    cur.execute("""
        CREATE TABLE dbo.Announcement_ReadLog (
            ReadLogID INT IDENTITY(1,1) PRIMARY KEY,
            NoticeID INT NOT NULL,
            EmployeeID INT NOT NULL,
            ReadDateTime DATETIME DEFAULT GETDATE(),
            FOREIGN KEY (NoticeID) REFERENCES dbo.Announcement_Master(NoticeID) ON DELETE CASCADE
        );
        PRINT 'Created updated Announcement_ReadLog table';
    """)
    
    conn.commit()
    conn.close()

# Run database initialization/upgrade
try:
    # Check if database table updates are already performed by searching for the old table
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM sys.tables WHERE name = 'Announcement_Target'")
    has_old_table = cur.fetchone() is not None
    conn.close()
    
    if has_old_table:
        print("MIGRATING NOTICE BOARD TO NEW SEPARATED GENERAL/DEPT SPECIFICATION...")
        init_announcement_db()
    else:
        # Check if tables don't exist at all, in that case create them
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM sys.tables WHERE name = 'Announcement_Master'")
        has_tables = cur.fetchone() is not None
        conn.close()
        if not has_tables:
            init_announcement_db()
except Exception as e:
    print(f"DATABASE UPGRADE ERROR: {e}")


# Helper: Check if user is HOD
def user_is_hod():
    emp_id = session.get("emp_id")
    if not emp_id:
        return False
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM dbo.HOD_Master WHERE EmployeeID = ?", (emp_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None

# Helper: Check if user is Supervisor
def user_is_supervisor():
    emp_id = session.get("emp_id")
    if not emp_id:
        return False
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM dbo.Supervisor_Master WHERE EmployeeID = ?", (emp_id,))
    row = cur.fetchone()
    conn.close()
    return row is not None


# ==================================================
# TRIGGER IN-PORTAL NOTIFICATIONS
# ==================================================
def trigger_notifications(notice_title, department_id=None):
    try:
        conn = get_connection()
        cur = conn.cursor()
        
        message = ""
        if department_id:
            message = f"📢 New department notice published: {notice_title}"
            # Notify employees and supervisors in that department
            cur.execute("""
                SELECT emp_id FROM users 
                WHERE department = ? AND role <> 'admin'
            """, (department_id,))
        else:
            message = f"📢 New company notice published: {notice_title}"
            # Notify all active employees
            cur.execute("SELECT emp_id FROM users WHERE role <> 'admin'")
            
        rows = cur.fetchall()
        for r in rows:
            emp_id_target = r[0]
            if emp_id_target:
                cur.execute("""
                    INSERT INTO dbo.Notifications (EmployeeID, Message, IsRead, CreatedDate)
                    VALUES (?, ?, 0, GETDATE())
                """, (emp_id_target, message))
                
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"NOTIFICATION TRIGGER ERROR: {e}")


# ==================================================
# VIEW NOTICE DETAILS & RECORD READ (AJAX)
# ==================================================
@announcement_bp.route("/announcements/view/<int:notice_id>")
def view_notice(notice_id):
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401
    
    user_id = session["user_id"]
    
    conn = get_connection()
    cur = conn.cursor()
    
    # 1. Fetch Notice Details
    cur.execute("""
        SELECT m.NoticeID, m.Title, m.Description, m.DepartmentID, m.CreatorRole, m.Priority, m.AttachmentPath, m.PublishDate, u.full_name
        FROM Announcement_Master m
        JOIN users u ON m.CreatedBy = u.id
        WHERE m.NoticeID = ? AND m.IsActive = 1
    """, (notice_id,))
    row = cur.fetchone()
    
    if not row:
        conn.close()
        return jsonify({"error": "Notice not found or inactive"}), 404
        
    # 2. Record Read Receipt if not already logged
    cur.execute("""
        SELECT 1 FROM Announcement_ReadLog 
        WHERE NoticeID = ? AND EmployeeID = ?
    """, (notice_id, user_id))
    already_read = cur.fetchone() is not None
    
    if not already_read:
        cur.execute("""
            INSERT INTO Announcement_ReadLog (NoticeID, EmployeeID, ReadDateTime)
            VALUES (?, ?, GETDATE())
        """, (notice_id, user_id))
        conn.commit()
        
    conn.close()
    
    return jsonify({
        "id": row[0],
        "title": row[1],
        "description": row[2],
        "department_id": row[3] if row[3] else "General (All)",
        "creator_role": row[4],
        "priority": row[5],
        "attachment": row[6],
        "publish_date": row[7].strftime("%d-%m-%Y %H:%M"),
        "creator_name": row[8]
    })


# ==================================================
# MODULE 1: GENERAL NOTICE BOARD (FOR ALL USERS)
# ==================================================
@announcement_bp.route("/announcements/general", methods=["GET", "POST"])
@announcement_bp.route("/policy", methods=["GET", "POST"])
def general_notices():
    if "user_id" not in session:
        return redirect("/")
        
    user_id = session["user_id"]
    role = session.get("role")
    dept = session.get("department")
    
    conn = get_connection()
    cur = conn.cursor()
    
    # Handle publishing notices if MD, GM, HR, or Admin submits the composer form
    is_general_publisher = role in ["admin", "HR", "GM", "MD"] or dept == "HR & Admin"
    
    if request.method == "POST" and is_general_publisher:
        title = request.form["title"].strip()
        priority = request.form["priority"]
        description = request.form["description"].strip()
        publish_date_str = request.form["publish_date"]
        expiry_date_str = request.form["expiry_date"]
        
        # Parse Dates
        publish_date = datetime.strptime(publish_date_str, "%Y-%m-%dT%H:%M") if publish_date_str else datetime.now()
        expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%dT%H:%M") if expiry_date_str else None
        
        # File Attachment Handle
        attachment_path = None
        if "attachment" in request.files:
            file = request.files["attachment"]
            if file and file.filename != "" and allowed_file(file.filename):
                filename = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                file.save(os.path.join(UPLOAD_FOLDER, filename))
                attachment_path = filename
                
        # Insert general notice (DepartmentID = NULL)
        cur.execute("""
            SET NOCOUNT ON;
            INSERT INTO Announcement_Master 
            (Title, Description, DepartmentID, CreatedBy, CreatorRole, Priority, AttachmentPath, PublishDate, ExpiryDate, IsActive)
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, 1);
            SELECT SCOPE_IDENTITY();
        """, (title, description, user_id, role, priority, attachment_path, publish_date, expiry_date))
        
        notice_id = int(cur.fetchone()[0])
        conn.commit()
        
        # Trigger global notifications
        trigger_notifications(title, department_id=None)
        
        return redirect("/announcements/general")
        
    # GET: Query visible general notices
    cur.execute("""
        SELECT m.NoticeID, m.Title, m.Description, m.Priority, m.PublishDate, m.AttachmentPath, u.full_name, m.CreatorRole, m.IsActive,
               (SELECT COUNT(*) FROM Announcement_ReadLog r WHERE r.NoticeID = m.NoticeID AND r.EmployeeID = ?) AS IsRead
        FROM Announcement_Master m
        JOIN users u ON m.CreatedBy = u.id
        WHERE m.DepartmentID IS NULL
          AND m.IsActive = 1
          AND m.PublishDate <= GETDATE()
          AND (m.ExpiryDate IS NULL OR m.ExpiryDate >= GETDATE())
        ORDER BY 
          CASE WHEN m.Priority = 'Critical' THEN 1
               WHEN m.Priority = 'High' THEN 2
               WHEN m.Priority = 'Medium' THEN 3
               ELSE 4 END ASC,
          m.PublishDate DESC
    """, (user_id,))
    
    rows = cur.fetchall()
    
    notices_list = []
    for r in rows:
        notices_list.append({
            "id": r[0],
            "title": r[1],
            "description": r[2],
            "priority": r[3],
            "publish_date": r[4],
            "attachment": r[5],
            "creator": r[6],
            "creator_role": r[7],
            "is_active": r[8],
            "is_read": r[9] > 0
        })
        
    # If publisher, query all their general notices for the management list
    manage_notices = []
    if is_general_publisher:
        if role == "admin":
            cur.execute("""
                SELECT m.NoticeID, m.Title, m.Priority, m.PublishDate, m.ExpiryDate, m.IsActive, u.full_name,
                       (SELECT COUNT(*) FROM Announcement_ReadLog r WHERE r.NoticeID = m.NoticeID) AS ReadCount
                FROM Announcement_Master m
                JOIN users u ON m.CreatedBy = u.id
                WHERE m.DepartmentID IS NULL
                ORDER BY m.PublishDate DESC
            """)
        else:
            cur.execute("""
                SELECT m.NoticeID, m.Title, m.Priority, m.PublishDate, m.ExpiryDate, m.IsActive, u.full_name,
                       (SELECT COUNT(*) FROM Announcement_ReadLog r WHERE r.NoticeID = m.NoticeID) AS ReadCount
                FROM Announcement_Master m
                JOIN users u ON m.CreatedBy = u.id
                WHERE m.DepartmentID IS NULL AND m.CreatedBy = ?
                ORDER BY m.PublishDate DESC
            """, (user_id,))
            
        rows_manage = cur.fetchall()
        for r in rows_manage:
            manage_notices.append({
                "id": r[0],
                "title": r[1],
                "priority": r[2],
                "publish_date": r[3],
                "expiry_date": r[4],
                "is_active": r[5],
                "creator": r[6],
                "read_count": r[7]
            })
            
    conn.close()
    
    return render_template("general_notices.html", notices=notices_list, manage_notices=manage_notices, is_publisher=is_general_publisher)


# ==================================================
# MODULE 2: DEPARTMENT NOTICE BOARD (FOR HOD/DEPT)
# ==================================================
@announcement_bp.route("/announcements/department", methods=["GET", "POST"])
def department_notices():
    if "user_id" not in session:
        return redirect("/")
        
    user_id = session["user_id"]
    role = session.get("role")
    dept = session.get("department")
    is_hod = user_is_hod()
    is_supervisor = user_is_supervisor()
    
    conn = get_connection()
    cur = conn.cursor()
    
    # Handle publishing notices if HOD or Supervisor submits the composer
    is_dept_publisher = is_hod or is_supervisor
    
    if request.method == "POST" and is_dept_publisher:
        title = request.form["title"].strip()
        priority = request.form["priority"]
        description = request.form["description"].strip()
        publish_date_str = request.form["publish_date"]
        expiry_date_str = request.form["expiry_date"]
        
        # Parse Dates
        publish_date = datetime.strptime(publish_date_str, "%Y-%m-%dT%H:%M") if publish_date_str else datetime.now()
        expiry_date = datetime.strptime(expiry_date_str, "%Y-%m-%dT%H:%M") if expiry_date_str else None
        
        # File Attachment Handle
        attachment_path = None
        if "attachment" in request.files:
            file = request.files["attachment"]
            if file and file.filename != "" and allowed_file(file.filename):
                filename = secure_filename(f"{int(datetime.now().timestamp())}_{file.filename}")
                os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                file.save(os.path.join(UPLOAD_FOLDER, filename))
                attachment_path = filename
                
        # Insert department notice (DepartmentID = HOD's department)
        creator_role = "HOD" if is_hod else "Supervisor"
        cur.execute("""
            SET NOCOUNT ON;
            INSERT INTO Announcement_Master 
            (Title, Description, DepartmentID, CreatedBy, CreatorRole, Priority, AttachmentPath, PublishDate, ExpiryDate, IsActive)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1);
            SELECT SCOPE_IDENTITY();
        """, (title, description, dept, user_id, creator_role, priority, attachment_path, publish_date, expiry_date))
        
        notice_id = int(cur.fetchone()[0])
        conn.commit()
        
        # Trigger department notification
        trigger_notifications(title, department_id=dept)
        
        return redirect("/announcements/department")
        
    # GET: Query active department notices for user's department
    notices_list = []
    if dept:
        cur.execute("""
            SELECT m.NoticeID, m.Title, m.Description, m.Priority, m.PublishDate, m.AttachmentPath, u.full_name, m.CreatorRole, m.IsActive,
                   (SELECT COUNT(*) FROM Announcement_ReadLog r WHERE r.NoticeID = m.NoticeID AND r.EmployeeID = ?) AS IsRead
            FROM Announcement_Master m
            JOIN users u ON m.CreatedBy = u.id
            WHERE m.DepartmentID = ?
              AND m.IsActive = 1
              AND m.PublishDate <= GETDATE()
              AND (m.ExpiryDate IS NULL OR m.ExpiryDate >= GETDATE())
            ORDER BY 
              CASE WHEN m.Priority = 'Critical' THEN 1
                   WHEN m.Priority = 'High' THEN 2
                   WHEN m.Priority = 'Medium' THEN 3
                   ELSE 4 END ASC,
              m.PublishDate DESC
        """, (user_id, dept))
        
        rows = cur.fetchall()
        for r in rows:
            notices_list.append({
                "id": r[0],
                "title": r[1],
                "description": r[2],
                "priority": r[3],
                "publish_date": r[4],
                "attachment": r[5],
                "creator": r[6],
                "creator_role": r[7],
                "is_active": r[8],
                "is_read": r[9] > 0
            })
            
    # HOD View: Fetch all department notices published by this HOD for tracking and analytics
    manage_notices = []
    department_employees = []
    total_dept_employees = 0
    
    if is_hod and dept:
        # Fetch notices published by this HOD
        cur.execute("""
            SELECT m.NoticeID, m.Title, m.Priority, m.PublishDate, m.ExpiryDate, m.IsActive, u.full_name
            FROM Announcement_Master m
            JOIN users u ON m.CreatedBy = u.id
            WHERE m.DepartmentID = ? AND m.CreatedBy = ?
            ORDER BY m.PublishDate DESC
        """, (dept, user_id))
        rows_manage = cur.fetchall()
        
        # Get count of total employees in the department (excluding HOD themselves and Admins)
        cur.execute("""
            SELECT COUNT(*) FROM users 
            WHERE department = ? AND role <> 'admin' AND id <> ?
        """, (dept, user_id))
        total_dept_employees = cur.fetchone()[0]
        
        # Compile read stats for each notice
        for r in rows_manage:
            n_id = r[0]
            
            # Count viewed employees
            cur.execute("""
                SELECT COUNT(DISTINCT r.EmployeeID) 
                FROM Announcement_ReadLog r
                JOIN users u ON r.EmployeeID = u.id
                WHERE r.NoticeID = ? AND u.department = ? AND u.role <> 'admin' AND u.id <> ?
            """, (n_id, dept, user_id))
            viewed_count = cur.fetchone()[0]
            
            pending_count = total_dept_employees - viewed_count
            read_rate = round((viewed_count / total_dept_employees * 100), 1) if total_dept_employees > 0 else 100.0
            
            # Fetch all department employees and their read details for this notice
            cur.execute("""
                SELECT u.id, u.full_name, u.emp_id, r.ReadDateTime
                FROM users u
                LEFT JOIN Announcement_ReadLog r ON u.id = r.EmployeeID AND r.NoticeID = ?
                WHERE u.department = ? AND u.role <> 'admin' AND u.id <> ?
                ORDER BY u.full_name
            """, (n_id, dept, user_id))
            emp_rows = cur.fetchall()
            
            employee_read_list = []
            for emp in emp_rows:
                employee_read_list.append({
                    "id": emp[0],
                    "name": emp[1],
                    "emp_id": emp[2],
                    "viewed": emp[3] is not None,
                    "date": emp[3].strftime("%d-%m-%Y") if emp[3] else None,
                    "time": emp[3].strftime("%I:%M %p") if emp[3] else None
                })
                
            manage_notices.append({
                "id": n_id,
                "title": r[1],
                "priority": r[2],
                "publish_date": r[3],
                "expiry_date": r[4],
                "is_active": r[5],
                "creator": r[6],
                "total_targets": total_dept_employees,
                "viewed": viewed_count,
                "pending": pending_count,
                "read_rate": read_rate,
                "employees": employee_read_list
            })
            
    conn.close()
    
    return render_template("dept_notices.html", notices=notices_list, manage_notices=manage_notices, is_publisher=is_dept_publisher, is_hod=is_hod, total_dept_employees=total_dept_employees, department_name=dept)


# ==================================================
# ARCHIVE NOTICE ACTION (ISACTIVE = 0)
# ==================================================
@announcement_bp.route("/announcements/archive/<int:notice_id>", methods=["POST"])
def archive_notice(notice_id):
    if "user_id" not in session:
        return redirect("/")
        
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT CreatedBy, DepartmentID, Title FROM Announcement_Master WHERE NoticeID = ?", (notice_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return "Notice not found", 404
        
    # Security: Creator or Admin only
    if session.get("role") != "admin" and row[0] != session["user_id"]:
        conn.close()
        return redirect("/access_denied")
        
    # Toggle active/archive status
    cur.execute("UPDATE Announcement_Master SET IsActive = 0 WHERE NoticeID = ?", (notice_id,))
    conn.commit()
    conn.close()
    
    # Redirect back to respective manager
    if row[1]: # has department, so it's a department notice
        return redirect("/announcements/department")
    return redirect("/announcements/general")


# ==================================================
# DELETE NOTICE ACTION
# ==================================================
@announcement_bp.route("/announcements/delete/<int:notice_id>", methods=["POST"])
def delete_notice(notice_id):
    if "user_id" not in session:
        return redirect("/")
        
    conn = get_connection()
    cur = conn.cursor()
    
    cur.execute("SELECT CreatedBy, DepartmentID, Title, AttachmentPath FROM Announcement_Master WHERE NoticeID = ?", (notice_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return "Notice not found", 404
        
    if session.get("role") != "admin" and row[0] != session["user_id"]:
        conn.close()
        return redirect("/access_denied")
        
    # Delete attachment if it exists
    if row[3]:
        try:
            os.remove(os.path.join(UPLOAD_FOLDER, row[3]))
        except Exception:
            pass
            
    # Delete from DB
    cur.execute("DELETE FROM Announcement_Master WHERE NoticeID = ?", (notice_id,))
    conn.commit()
    conn.close()
    
    if row[1]:
        return redirect("/announcements/department")
    return redirect("/announcements/general")


# ==================================================
# ATTACHMENT DOWNLOAD
# ==================================================
@announcement_bp.route("/announcements/file/<filename>")
def download_attachment(filename):
    if "user_id" not in session:
        return redirect("/")
    return send_from_directory(UPLOAD_FOLDER, filename)
