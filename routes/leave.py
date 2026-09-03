from flask import Blueprint, render_template, request, redirect, session, jsonify, flash, send_file
from datetime import datetime
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from werkzeug.utils import secure_filename
from db import get_db as get_connection

# ReportLab imports for PDF generation
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.units import inch
from io import BytesIO

leave_bp = Blueprint("leave", __name__)

UPLOAD_FOLDER = os.path.join("static", "uploads", "leave_attachments")

def create_notification(conn, employee_id, message, email_recipient=None, email_subject=None):
    """
    Creates an in-app notification and attempts to send an email notification if SMTP is configured.
    """
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO dbo.Notifications (EmployeeID, Message, IsRead, CreatedDate)
        VALUES (?, ?, 0, GETDATE())
    """, (employee_id, message))
    
    if email_recipient:
        try:
            print(f"[EMAIL] Notification Email queued to {email_recipient}: {email_subject}\nBody: {message}")
            
            smtp_server = os.getenv("SMTP_SERVER", "")
            smtp_port = int(os.getenv("SMTP_PORT", "587"))
            smtp_user = os.getenv("SMTP_EMAIL", "")
            smtp_password = os.getenv("SMTP_PASSWORD", "")
            
            if smtp_server and smtp_user:
                msg = MIMEMultipart()
                msg['From'] = smtp_user
                msg['To'] = email_recipient
                msg['Subject'] = email_subject
                msg.attach(MIMEText(message, 'plain'))
                
                server = smtplib.SMTP(smtp_server, smtp_port)
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.send_message(msg)
                server.quit()
                print("[EMAIL] Email sent successfully!")
        except Exception as e:
            print(f"[WARNING] Email sending failed: {e}")

def get_logged_in_emp_id():
    emp_id = session.get("emp_id")
    if not emp_id and "user_id" in session:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT emp_id FROM dbo.users WHERE id = ?", (session["user_id"],))
        row = cur.fetchone()
        conn.close()
        if row:
            emp_id = row[0]
            session["emp_id"] = emp_id
    return emp_id

# ==================================================
# EMPLOYEE DASHBOARD & APPLICATION FORM
# ==================================================
@leave_bp.route("/user/personal/leave")
def personal_leave():
    if "user_id" not in session:
        return redirect("/")

    emp_id = get_logged_in_emp_id()
    if not emp_id:
        return "Employee ID not found for your account.", 400

    conn = get_connection()
    cur = conn.cursor()

    # Get employee details
    cur.execute("SELECT emp_id, full_name, department, email FROM dbo.users WHERE id = ?", (session["user_id"],))
    emp_details = cur.fetchone()

    # Get supervisor for department
    supervisor = None
    if emp_details and emp_details[2]:
        cur.execute("""
            SELECT EmployeeID, SupervisorName, EmailID
            FROM dbo.Supervisor_Master
            WHERE Department = ?
        """, (emp_details[2],))
        row = cur.fetchone()
        if row:
            supervisor = {
                'emp_id': row[0],
                'name': row[1] if row[0] != emp_id else "Not Required (Self is Supervisor)",
                'email': row[2]
            }

    # Get HOD for department
    hod = None
    if emp_details and emp_details[2]:
        cur.execute("""
            SELECT EmployeeID, EmployeeName, EmailID
            FROM dbo.HOD_Master
            WHERE Department = ?
        """, (emp_details[2],))
        row = cur.fetchone()
        if row:
            hod = {
                'emp_id': row[0],
                'name': row[1],
                'email': row[2]
            }

    # Fetch Leave History
    cur.execute("""
        SELECT 
            LeaveID, EmployeeID, EmployeeName, Department, SupervisorID, HODID,
            LeaveType, FromDate, ToDate, TotalDays, Reason, AttachmentPath, Status,
            SupervisorStatus, SupervisorRemarks, SupervisorApprovedDate,
            HODStatus, HODRemarks, HODApprovedDate, CreatedDate
        FROM dbo.LeaveRequests
        WHERE EmployeeID = ?
        ORDER BY CreatedDate DESC
    """, (emp_id,))
    history_rows = cur.fetchall()

    leave_history = []
    for r in history_rows:
        leave_history.append({
            'id': r[0],
            'emp_id': r[1],
            'name': r[2],
            'department': r[3],
            'supervisor_id': r[4],
            'hod_id': r[5],
            'leave_type': r[6],
            'from_date': r[7].strftime('%d-%m-%Y') if isinstance(r[7], datetime) or hasattr(r[7], 'strftime') else r[7],
            'to_date': r[8].strftime('%d-%m-%Y') if isinstance(r[8], datetime) or hasattr(r[8], 'strftime') else r[8],
            'total_days': r[9],
            'reason': r[10],
            'attachment': r[11],
            'status': r[12],
            'sup_status': r[13],
            'sup_remarks': r[14] if r[14] else '',
            'sup_date': r[15].strftime('%d-%m-%Y %H:%M') if r[15] else '',
            'hod_status': r[16],
            'hod_remarks': r[17] if r[17] else '',
            'hod_date': r[18].strftime('%d-%m-%Y %H:%M') if r[18] else ''
        })

    # Fetch Notifications
    cur.execute("""
        SELECT NotificationID, Message, IsRead, CreatedDate
        FROM dbo.Notifications
        WHERE EmployeeID = ?
        ORDER BY CreatedDate DESC
    """, (emp_id,))
    notif_rows = cur.fetchall()
    
    notifications = []
    for n in notif_rows:
        notifications.append({
            'id': n[0],
            'message': n[1],
            'is_read': n[2],
            'date': n[3].strftime('%d-%m-%Y %H:%M')
        })

    pending_count = sum(1 for r in leave_history if r['status'] in ('Pending Supervisor Approval', 'Pending HOD Approval'))
    approved_count = sum(1 for r in leave_history if r['status'] == 'Approved')
    rejected_count = sum(1 for r in leave_history if 'Rejected' in r['status'])
    total_count = len(leave_history)

    conn.close()

    return render_template(
        "leave.html",
        active_tab="leave",
        emp=emp_details,
        supervisor=supervisor,
        hod=hod,
        leave_history=leave_history,
        notifications=notifications,
        pending_count=pending_count,
        approved_count=approved_count,
        rejected_count=rejected_count,
        total_count=total_count
    )

# ==================================================
# SUBMIT LEAVE REQUEST
# ==================================================
@leave_bp.route("/leave/apply", methods=["POST"])
def apply_leave():
    if "user_id" not in session:
        return redirect("/")

    emp_id = get_logged_in_emp_id()
    if not emp_id:
        return "Employee ID not found for your account.", 400

    leave_type = request.form.get("leave_type")
    from_date_str = request.form.get("from_date")
    to_date_str = request.form.get("to_date")
    reason = request.form.get("reason").strip()
    attachment = request.files.get("attachment")

    if not leave_type or not from_date_str or not to_date_str or not reason:
        flash("All fields are required.", "danger")
        return redirect("/user/personal/leave")

    try:
        from_date = datetime.strptime(from_date_str, "%Y-%m-%d").date()
        to_date = datetime.strptime(to_date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("Invalid date format.", "danger")
        return redirect("/user/personal/leave")

    total_days = (to_date - from_date).days + 1
    if total_days <= 0:
        flash("To Date must be equal to or after From Date.", "danger")
        return redirect("/user/personal/leave")

    conn = get_connection()
    cur = conn.cursor()

    # Get employee details
    cur.execute("SELECT emp_id, full_name, department, email FROM dbo.users WHERE id = ?", (session["user_id"],))
    emp_details = cur.fetchone()

    if not emp_details:
        conn.close()
        flash("User details not found.", "danger")
        return redirect("/user/personal/leave")

    emp_name, department, emp_email = emp_details[1], emp_details[2], emp_details[3]

    # Resolve Supervisor and HOD for department
    cur.execute("""
        SELECT EmployeeID, SupervisorName, EmailID
        FROM dbo.Supervisor_Master
        WHERE Department = ?
    """, (department,))
    sup_row = cur.fetchone()

    cur.execute("""
        SELECT EmployeeID, EmployeeName, EmailID
        FROM dbo.HOD_Master
        WHERE Department = ?
    """, (department,))
    hod_row = cur.fetchone()

    if not sup_row:
        conn.close()
        flash("No supervisor is assigned to your department. Please contact Administrator.", "danger")
        return redirect("/user/personal/leave")

    supervisor_id, supervisor_name, supervisor_email = sup_row[0], sup_row[1], sup_row[2]
    hod_id = hod_row[0] if hod_row else None
    hod_email = hod_row[2] if hod_row else None

    is_self_supervisor = (supervisor_id == emp_id)

    # If the applicant is the supervisor, they must have an HOD assigned
    if is_self_supervisor and not hod_id:
        conn.close()
        flash("No Head of Department (HOD) is assigned to your department. Please contact Administrator.", "danger")
        return redirect("/user/personal/leave")

    # Handle file upload
    attachment_path = None
    if attachment and attachment.filename:
        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
        filename = secure_filename(attachment.filename)
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        saved_filename = f"{emp_id}_{timestamp}_{filename}"
        attachment.save(os.path.join(UPLOAD_FOLDER, saved_filename))
        attachment_path = f"uploads/leave_attachments/{saved_filename}"

    # Insert Leave Request
    if is_self_supervisor:
        cur.execute("""
            INSERT INTO dbo.LeaveRequests (
                EmployeeID, EmployeeName, Department, SupervisorID, HODID,
                LeaveType, FromDate, ToDate, TotalDays, Reason, AttachmentPath,
                Status, SupervisorStatus, SupervisorRemarks, SupervisorApprovedDate, HODStatus, CreatedDate
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending HOD Approval', 'Approved', 'Self-applied by Supervisor', GETDATE(), 'Pending', GETDATE())
        """, (emp_id, emp_name, department, supervisor_id, hod_id, leave_type, from_date, to_date, total_days, reason, attachment_path))
        
        # Notify HOD directly
        msg = f"New leave request submitted by Supervisor {emp_name} ({emp_id}) for {total_days} day(s) starting from {from_date.strftime('%d-%m-%Y')}. Pending your approval."
        create_notification(conn, hod_id, msg, email_recipient=hod_email, email_subject=f"Leave Request Submitted (Supervisor) - {emp_name}")
        
        success_flash_message = "Leave request submitted successfully. Since you are the Supervisor, it has been forwarded directly to the HOD."
    else:
        cur.execute("""
            INSERT INTO dbo.LeaveRequests (
                EmployeeID, EmployeeName, Department, SupervisorID, HODID,
                LeaveType, FromDate, ToDate, TotalDays, Reason, AttachmentPath,
                Status, SupervisorStatus, HODStatus, CreatedDate
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending Supervisor Approval', 'Pending', 'Pending', GETDATE())
        """, (emp_id, emp_name, department, supervisor_id, hod_id, leave_type, from_date, to_date, total_days, reason, attachment_path))
        
        # Notify Supervisor
        msg = f"New leave request submitted by {emp_name} ({emp_id}) for {total_days} day(s) starting from {from_date.strftime('%d-%m-%Y')}."
        create_notification(conn, supervisor_id, msg, email_recipient=supervisor_email, email_subject=f"Leave Request Submitted - {emp_name}")
        
        success_flash_message = "Leave request submitted successfully and is pending Supervisor approval."

    conn.commit()
    conn.close()

    flash(success_flash_message, "success")
    return redirect("/user/personal/leave")

# ==================================================
# SUPERVISOR DASHBOARD
# ==================================================
@leave_bp.route("/supervisor/dashboard")
def supervisor_dashboard():
    if "user_id" not in session:
        return redirect("/")

    emp_id = get_logged_in_emp_id()
    
    conn = get_connection()
    cur = conn.cursor()

    # Check if supervisor
    cur.execute("SELECT SupervisorID, SupervisorName, Department FROM dbo.Supervisor_Master WHERE EmployeeID = ?", (emp_id,))
    sup_info = cur.fetchone()

    if not sup_info:
        conn.close()
        return redirect("/access_denied")

    # Fetch Pending Approvals
    cur.execute("""
        SELECT LeaveID, EmployeeID, EmployeeName, Department, LeaveType, FromDate, ToDate, TotalDays, Reason, AttachmentPath, CreatedDate
        FROM dbo.LeaveRequests
        WHERE SupervisorID = ? AND Status = 'Pending Supervisor Approval'
        ORDER BY CreatedDate DESC
    """, (emp_id,))
    pending_rows = cur.fetchall()

    pending = []
    for r in pending_rows:
        pending.append({
            'id': r[0],
            'emp_id': r[1],
            'name': r[2],
            'department': r[3],
            'leave_type': r[4],
            'from_date': r[5].strftime('%d-%m-%Y') if hasattr(r[5], 'strftime') else r[5],
            'to_date': r[6].strftime('%d-%m-%Y') if hasattr(r[6], 'strftime') else r[6],
            'total_days': r[7],
            'reason': r[8],
            'attachment': r[9],
            'created_date': r[10].strftime('%d-%m-%Y %H:%M')
        })

    # Fetch Approved (Previously approved by this supervisor)
    cur.execute("""
        SELECT LeaveID, EmployeeID, EmployeeName, Department, LeaveType, FromDate, ToDate, TotalDays, Reason, AttachmentPath, SupervisorRemarks, SupervisorApprovedDate, Status
        FROM dbo.LeaveRequests
        WHERE SupervisorID = ? AND SupervisorStatus = 'Approved'
        ORDER BY SupervisorApprovedDate DESC
    """, (emp_id,))
    approved_rows = cur.fetchall()

    approved = []
    for r in approved_rows:
        approved.append({
            'id': r[0],
            'emp_id': r[1],
            'name': r[2],
            'department': r[3],
            'leave_type': r[4],
            'from_date': r[5].strftime('%d-%m-%Y') if hasattr(r[5], 'strftime') else r[5],
            'to_date': r[6].strftime('%d-%m-%Y') if hasattr(r[6], 'strftime') else r[6],
            'total_days': r[7],
            'reason': r[8],
            'attachment': r[9],
            'remarks': r[10] if r[10] else '',
            'action_date': r[11].strftime('%d-%m-%Y %H:%M') if r[11] else '',
            'status': r[12]
        })

    # Fetch Rejected (Rejected by this supervisor)
    cur.execute("""
        SELECT LeaveID, EmployeeID, EmployeeName, Department, LeaveType, FromDate, ToDate, TotalDays, Reason, AttachmentPath, SupervisorRemarks, SupervisorApprovedDate, Status
        FROM dbo.LeaveRequests
        WHERE SupervisorID = ? AND SupervisorStatus = 'Rejected'
        ORDER BY SupervisorApprovedDate DESC
    """, (emp_id,))
    rejected_rows = cur.fetchall()

    rejected = []
    for r in rejected_rows:
        rejected.append({
            'id': r[0],
            'emp_id': r[1],
            'name': r[2],
            'department': r[3],
            'leave_type': r[4],
            'from_date': r[5].strftime('%d-%m-%Y') if hasattr(r[5], 'strftime') else r[5],
            'to_date': r[6].strftime('%d-%m-%Y') if hasattr(r[6], 'strftime') else r[6],
            'total_days': r[7],
            'reason': r[8],
            'attachment': r[9],
            'remarks': r[10] if r[10] else '',
            'action_date': r[11].strftime('%d-%m-%Y %H:%M') if r[11] else '',
            'status': r[12]
        })

    conn.close()

    return render_template(
        "supervisor_dashboard.html",
        sup_name=sup_info[1],
        pending=pending,
        approved=approved,
        rejected=rejected
    )

# ==================================================
# SUPERVISOR ACTIONS
# ==================================================
@leave_bp.route("/supervisor/approve/<int:leave_id>", methods=["POST"])
def supervisor_approve(leave_id):
    if "user_id" not in session:
        return redirect("/")

    emp_id = get_logged_in_emp_id()
    remarks = request.form.get("remarks", "").strip()

    conn = get_connection()
    cur = conn.cursor()

    # Ensure supervisor ownership
    cur.execute("SELECT EmployeeID FROM dbo.Supervisor_Master WHERE EmployeeID = ?", (emp_id,))
    if not cur.fetchone():
        conn.close()
        return redirect("/access_denied")

    # Update Leave Status to Pending HOD Approval
    cur.execute("""
        UPDATE dbo.LeaveRequests
        SET Status = 'Pending HOD Approval',
            SupervisorStatus = 'Approved',
            SupervisorRemarks = ?,
            SupervisorApprovedDate = GETDATE()
        WHERE LeaveID = ? AND SupervisorID = ? AND Status = 'Pending Supervisor Approval'
    """, (remarks, leave_id, emp_id))

    if cur.rowcount > 0:
        # Fetch leave details for notification routing
        cur.execute("SELECT EmployeeID, EmployeeName, HODID, TotalDays, Department FROM dbo.LeaveRequests WHERE LeaveID = ?", (leave_id,))
        leave_row = cur.fetchone()
        
        if leave_row:
            emp_uid, emp_name, hod_id, total_days, dept = leave_row
            
            # Fetch HOD email
            hod_email = None
            if hod_id:
                cur.execute("SELECT EmailID FROM dbo.HOD_Master WHERE EmployeeID = ?", (hod_id,))
                hod_row = cur.fetchone()
                if hod_row:
                    hod_email = hod_row[0]
            
            if hod_id:
                # Notify HOD
                msg = f"Leave request from {emp_name} ({emp_uid}) for {total_days} day(s) has been approved by the Supervisor and is pending your approval."
                create_notification(conn, hod_id, msg, email_recipient=hod_email, email_subject=f"Leave Approval Required (HOD) - {emp_name}")
            
            # Notify Employee of Supervisor approval
            cur.execute("SELECT email FROM dbo.users WHERE emp_id = ?", (emp_uid,))
            emp_email_row = cur.fetchone()
            emp_email = emp_email_row[0] if emp_email_row else None
            
            msg_emp = f"Your leave request has been approved by your Supervisor ({session.get('full_name')}) and is now pending final HOD approval."
            create_notification(conn, emp_uid, msg_emp, email_recipient=emp_email, email_subject="Leave Approved by Supervisor")

        conn.commit()
        flash("Leave request approved and forwarded to HOD.", "success")
    else:
        flash("Could not process approval.", "danger")

    conn.close()
    return redirect("/supervisor/dashboard")

@leave_bp.route("/supervisor/reject/<int:leave_id>", methods=["POST"])
def supervisor_reject(leave_id):
    if "user_id" not in session:
        return redirect("/")

    emp_id = get_logged_in_emp_id()
    remarks = request.form.get("remarks", "").strip()

    conn = get_connection()
    cur = conn.cursor()

    # Ensure supervisor ownership
    cur.execute("SELECT EmployeeID FROM dbo.Supervisor_Master WHERE EmployeeID = ?", (emp_id,))
    if not cur.fetchone():
        conn.close()
        return redirect("/access_denied")

    # Update Leave Status to Rejected by Supervisor
    cur.execute("""
        UPDATE dbo.LeaveRequests
        SET Status = 'Rejected by Supervisor',
            SupervisorStatus = 'Rejected',
            SupervisorRemarks = ?,
            SupervisorApprovedDate = GETDATE()
        WHERE LeaveID = ? AND SupervisorID = ? AND Status = 'Pending Supervisor Approval'
    """, (remarks, leave_id, emp_id))

    if cur.rowcount > 0:
        # Fetch leave details for notification routing
        cur.execute("SELECT EmployeeID, EmployeeName FROM dbo.LeaveRequests WHERE LeaveID = ?", (leave_id,))
        leave_row = cur.fetchone()
        
        if leave_row:
            emp_uid, emp_name = leave_row
            cur.execute("SELECT email FROM dbo.users WHERE emp_id = ?", (emp_uid,))
            emp_email_row = cur.fetchone()
            emp_email = emp_email_row[0] if emp_email_row else None
            
            msg = f"Your leave request has been rejected by your supervisor. Remarks: {remarks}"
            create_notification(conn, emp_uid, msg, email_recipient=emp_email, email_subject="Leave Request Rejected")

        conn.commit()
        flash("Leave request rejected.", "warning")
    else:
        flash("Could not process rejection.", "danger")

    conn.close()
    return redirect("/supervisor/dashboard")

# ==================================================
# HOD DASHBOARD
# ==================================================
@leave_bp.route("/hod/dashboard")
def hod_dashboard():
    if "user_id" not in session:
        return redirect("/")

    emp_id = get_logged_in_emp_id()

    conn = get_connection()
    cur = conn.cursor()

    # Check if HOD
    cur.execute("SELECT HODID, EmployeeName, Department FROM dbo.HOD_Master WHERE EmployeeID = ?", (emp_id,))
    hod_info = cur.fetchone()

    if not hod_info:
        conn.close()
        return redirect("/access_denied")

    hod_dept = hod_info[2]

    # Fetch Pending Approvals (Approved by Supervisor, pending HOD approval)
    cur.execute("""
        SELECT LeaveID, EmployeeID, EmployeeName, Department, LeaveType, FromDate, ToDate, TotalDays, Reason, AttachmentPath, SupervisorRemarks, CreatedDate
        FROM dbo.LeaveRequests
        WHERE HODID = ? AND Status = 'Pending HOD Approval'
        ORDER BY CreatedDate DESC
    """, (emp_id,))
    pending_rows = cur.fetchall()

    pending = []
    for r in pending_rows:
        pending.append({
            'id': r[0],
            'emp_id': r[1],
            'name': r[2],
            'department': r[3],
            'leave_type': r[4],
            'from_date': r[5].strftime('%d-%m-%Y') if hasattr(r[5], 'strftime') else r[5],
            'to_date': r[6].strftime('%d-%m-%Y') if hasattr(r[6], 'strftime') else r[6],
            'total_days': r[7],
            'reason': r[8],
            'attachment': r[9],
            'sup_remarks': r[10] if r[10] else '',
            'created_date': r[11].strftime('%d-%m-%Y %H:%M')
        })

    # Fetch All Department Leave Requests (HOD Oversight)
    cur.execute("""
        SELECT LeaveID, EmployeeID, EmployeeName, Department, LeaveType, FromDate, ToDate, TotalDays, Reason, AttachmentPath, Status, SupervisorRemarks, HODRemarks, CreatedDate
        FROM dbo.LeaveRequests
        WHERE Department = ?
        ORDER BY CreatedDate DESC
    """, (hod_dept,))
    dept_rows = cur.fetchall()

    department_leaves = []
    for r in dept_rows:
        department_leaves.append({
            'id': r[0],
            'emp_id': r[1],
            'name': r[2],
            'department': r[3],
            'leave_type': r[4],
            'from_date': r[5].strftime('%d-%m-%Y') if hasattr(r[5], 'strftime') else r[5],
            'to_date': r[6].strftime('%d-%m-%Y') if hasattr(r[6], 'strftime') else r[6],
            'total_days': r[7],
            'reason': r[8],
            'attachment': r[9],
            'status': r[10],
            'sup_remarks': r[11] if r[11] else '',
            'hod_remarks': r[12] if r[12] else '',
            'created_date': r[13].strftime('%d-%m-%Y %H:%M')
        })

    # Calculate Department Leave Statistics
    stats = {'approved': 0, 'pending': 0, 'rejected': 0, 'total': 0}
    for row in department_leaves:
        stats['total'] += 1
        if row['status'] == 'Approved':
            stats['approved'] += 1
        elif row['status'] in ('Pending Supervisor Approval', 'Pending HOD Approval'):
            stats['pending'] += 1
        elif 'Rejected' in row['status']:
            stats['rejected'] += 1

    conn.close()

    return render_template(
        "hod_dashboard.html",
        hod_name=hod_info[1],
        department=hod_dept,
        pending=pending,
        department_leaves=department_leaves,
        stats=stats
    )

# ==================================================
# HOD ACTIONS
# ==================================================
@leave_bp.route("/hod/approve/<int:leave_id>", methods=["POST"])
def hod_approve(leave_id):
    if "user_id" not in session:
        return redirect("/")

    emp_id = get_logged_in_emp_id()
    remarks = request.form.get("remarks", "").strip()

    conn = get_connection()
    cur = conn.cursor()

    # Ensure HOD ownership
    cur.execute("SELECT EmployeeID FROM dbo.HOD_Master WHERE EmployeeID = ?", (emp_id,))
    if not cur.fetchone():
        conn.close()
        return redirect("/access_denied")

    # Update Leave Status to Approved
    cur.execute("""
        UPDATE dbo.LeaveRequests
        SET Status = 'Approved',
            HODStatus = 'Approved',
            HODRemarks = ?,
            HODApprovedDate = GETDATE()
        WHERE LeaveID = ? AND HODID = ? AND Status = 'Pending HOD Approval'
    """, (remarks, leave_id, emp_id))

    if cur.rowcount > 0:
        # Fetch leave details for notification routing
        cur.execute("SELECT EmployeeID, EmployeeName, TotalDays FROM dbo.LeaveRequests WHERE LeaveID = ?", (leave_id,))
        leave_row = cur.fetchone()
        
        if leave_row:
            emp_uid, emp_name, total_days = leave_row
            cur.execute("SELECT email FROM dbo.users WHERE emp_id = ?", (emp_uid,))
            emp_email_row = cur.fetchone()
            emp_email = emp_email_row[0] if emp_email_row else None
# ==================================================
# MARK NOTIFICATION AS READ
# ==================================================
@leave_bp.route("/notifications/read/<int:notif_id>", methods=["POST"])
def read_notification(notif_id):
    if "user_id" not in session:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    emp_id = get_logged_in_emp_id()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        UPDATE dbo.Notifications
        SET IsRead = 1
        WHERE NotificationID = ? AND EmployeeID = ?
    """, (notif_id, emp_id))
    
    conn.commit()
    conn.close()

    return jsonify({"success": True})


# ==================================================
# DOWNLOAD LEAVE FORM AS PDF
# ==================================================
@leave_bp.route("/leave/download/<int:leave_id>")
def download_leave_pdf(leave_id):
    if "user_id" not in session:
        return redirect("/")

    emp_id = get_logged_in_emp_id()
    if not emp_id:
        return "Employee ID not found for your account.", 400

    conn = get_connection()
    cur = conn.cursor()

    # Fetch Leave Request
    cur.execute("""
        SELECT 
            LeaveID, EmployeeID, EmployeeName, Department, SupervisorID, HODID,
            LeaveType, FromDate, ToDate, TotalDays, Reason, AttachmentPath, Status,
            SupervisorStatus, SupervisorRemarks, SupervisorApprovedDate,
            HODStatus, HODRemarks, HODApprovedDate, CreatedDate
        FROM dbo.LeaveRequests
        WHERE LeaveID = ?
    """, (leave_id,))
    row = cur.fetchone()

    if not row:
        conn.close()
        return "Leave request not found.", 404

    (
        l_id, employee_id, employee_name, department, supervisor_id, hod_id,
        leave_type, from_date, to_date, total_days, reason, attachment_path, status,
        sup_status, sup_remarks, sup_approved_date,
        hod_status, hod_remarks, hod_approved_date, created_date
    ) = row

    # Access security check: applicant, direct supervisor, HOD, or Admin
    user_role = session.get("role", "").lower()
    is_authorized = (
        user_role == "admin"
        or emp_id == employee_id
        or emp_id == supervisor_id
        or emp_id == hod_id
    )

    if not is_authorized:
        conn.close()
        return redirect("/access_denied")

    # Fetch applicant email
    cur.execute("SELECT email FROM dbo.users WHERE emp_id = ?", (employee_id,))
    email_row = cur.fetchone()
    email_id = email_row[0] if email_row else "N/A"

    # Fetch Supervisor Name
    supervisor_name = "Not Assigned"
    if supervisor_id:
        cur.execute("SELECT SupervisorName FROM dbo.Supervisor_Master WHERE EmployeeID = ?", (supervisor_id,))
        s_row = cur.fetchone()
        if s_row:
            supervisor_name = s_row[0]

    # Fetch HOD Name
    hod_name = "Not Assigned"
    if hod_id:
        cur.execute("SELECT EmployeeName FROM dbo.HOD_Master WHERE EmployeeID = ?", (hod_id,))
        h_row = cur.fetchone()
        if h_row:
            hod_name = h_row[0]

    conn.close()

    # Format dates
    from_date_str = from_date.strftime('%d-%m-%Y') if hasattr(from_date, 'strftime') else str(from_date)
    to_date_str = to_date.strftime('%d-%m-%Y') if hasattr(to_date, 'strftime') else str(to_date)
    created_date_str = created_date.strftime('%d-%m-%Y %H:%M') if hasattr(created_date, 'strftime') else str(created_date)
    
    sup_date_str = sup_approved_date.strftime('%d-%m-%Y %H:%M') if sup_approved_date else "N/A"
    hod_date_str = hod_approved_date.strftime('%d-%m-%Y %H:%M') if hod_approved_date else "N/A"

    # Seal Date determination
    seal_date = hod_approved_date or sup_approved_date or datetime.now()
    seal_date_str = seal_date.strftime('%d-%m-%Y') if hasattr(seal_date, 'strftime') else str(seal_date)

    # Setup Document
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        leftMargin=0.5*inch,
        rightMargin=0.5*inch,
        topMargin=0.5*inch,
        bottomMargin=0.5*inch
    )

    styles = getSampleStyleSheet()
    
    # Custom Typography and Palette Styles
    title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=20,
        leading=24,
        textColor=colors.HexColor('#0f172a'),
        alignment=1,
        spaceAfter=4
    )
    
    subtitle_style = ParagraphStyle(
        'DocSubtitle',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=10,
        leading=14,
        textColor=colors.HexColor('#475569'),
        alignment=1,
        spaceAfter=15
    )
    
    section_heading = ParagraphStyle(
        'SectionHeading',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=11,
        leading=15,
        textColor=colors.HexColor('#1e293b'),
        spaceBefore=12,
        spaceAfter=6
    )
    
    label_style = ParagraphStyle(
        'Label',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor('#475569')
    )
    
    value_style = ParagraphStyle(
        'Value',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor('#0f172a')
    )
    
    value_bold_style = ParagraphStyle(
        'ValueBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=9.5,
        leading=13,
        textColor=colors.HexColor('#0f172a')
    )

    reason_text_style = ParagraphStyle(
        'ReasonText',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#334155')
    )
    
    remarks_text_style = ParagraphStyle(
        'RemarksText',
        parent=styles['Normal'],
        fontName='Helvetica-Oblique',
        fontSize=9,
        leading=13,
        textColor=colors.HexColor('#475569')
    )

    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8,
        leading=10,
        textColor=colors.HexColor('#94a3b8'),
        alignment=1
    )

    story = []
    
    # --------------------------------------------------
    # DOCUMENT STORY BUILD
    # --------------------------------------------------
    story.append(Paragraph("BARANI HYDRAULICS", title_style))
    story.append(Paragraph("LEAVE APPLICATION FORM", subtitle_style))
    story.append(Spacer(1, 10))
    
    # 1. Employee Details Section
    story.append(Paragraph("EMPLOYEE DETAILS", section_heading))
    emp_data = [
        [
            Paragraph("Employee ID:", label_style), Paragraph(employee_id, value_style),
            Paragraph("Employee Name:", label_style), Paragraph(employee_name, value_style)
        ],
        [
            Paragraph("Department:", label_style), Paragraph(department, value_style),
            Paragraph("Email ID:", label_style), Paragraph(email_id, value_style)
        ]
    ]
    emp_table = Table(emp_data, colWidths=[110, 160, 110, 160])
    emp_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor('#f1f5f9')),
    ]))
    story.append(emp_table)
    story.append(Spacer(1, 10))
    
    # 2. Leave Request Details Section
    story.append(Paragraph("LEAVE REQUEST DETAILS", section_heading))
    leave_data = [
        [
            Paragraph("Leave Type:", label_style), Paragraph(leave_type, value_bold_style),
            Paragraph("Submission Date:", label_style), Paragraph(created_date_str, value_style)
        ],
        [
            Paragraph("From Date:", label_style), Paragraph(from_date_str, value_style),
            Paragraph("To Date:", label_style), Paragraph(to_date_str, value_style)
        ],
        [
            Paragraph("Total Days:", label_style), Paragraph(f"{total_days} Day(s)", value_bold_style),
            Paragraph("Current Status:", label_style), Paragraph(status, value_bold_style)
        ],
        [
            Paragraph("Reason for Leave:", label_style), Paragraph(reason, reason_text_style),
            "", ""
        ]
    ]
    leave_table = Table(leave_data, colWidths=[110, 160, 110, 160])
    leave_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('SPAN', (1, 3), (3, 3)),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor('#f1f5f9')),
    ]))
    story.append(leave_table)
    story.append(Spacer(1, 10))
    
    # 3. Approval Workflow Details Section
    story.append(Paragraph("APPROVAL WORKFLOW DETAILS", section_heading))
    workflow_data = [
        [
            Paragraph("Supervisor Name:", label_style), Paragraph(supervisor_name, value_style),
            Paragraph("HOD Name:", label_style), Paragraph(hod_name, value_style)
        ],
        [
            Paragraph("Supervisor Status:", label_style), Paragraph(sup_status, value_style),
            Paragraph("HOD Status:", label_style), Paragraph(hod_status, value_style)
        ],
        [
            Paragraph("Review Date:", label_style), Paragraph(sup_date_str, value_style),
            Paragraph("Review Date:", label_style), Paragraph(hod_date_str, value_style)
        ],
        [
            Paragraph("Supervisor Remarks:", label_style), Paragraph(sup_remarks if sup_remarks else "No remarks provided.", remarks_text_style),
            Paragraph("HOD Remarks:", label_style), Paragraph(hod_remarks if hod_remarks else "No remarks provided.", remarks_text_style)
        ]
    ]
    workflow_table = Table(workflow_data, colWidths=[110, 160, 110, 160])
    workflow_table.setStyle(TableStyle([
        ('VALIGN', (0,0), (-1,-1), 'TOP'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 5),
        ('TOPPADDING', (0,0), (-1,-1), 5),
        ('LINEBELOW', (0,0), (-1,-1), 0.5, colors.HexColor('#f1f5f9')),
    ]))
    story.append(workflow_table)
    story.append(Spacer(1, 30))
    
    # Footer Notice
    story.append(Paragraph("This is a system generated document. Powered by Barani Hydraulics Leave Management Module.", footer_style))

    # --------------------------------------------------
    # DRAW STAMP ON CANVAS
    # --------------------------------------------------
    def draw_decorations_and_stamp(canvas, doc_obj):
        # Draw elegant borders
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#e2e8f0"))
        canvas.setLineWidth(1.5)
        # Margin is 0.5 in, so width is 612 - 72 = 540, height is 792 - 72 = 720
        # draw border at 0.4 in
        canvas.rect(28, 28, 556, 736)

        # Draw stamp based on Status
        if status == 'Approved':
            bg_color = "#dcfce7"
            border_color = "#16a34a"
            stamp_text = "APPROVED"
        elif 'Rejected' in status:
            bg_color = "#fee2e2"
            border_color = "#dc2626"
            stamp_text = "REJECTED"
        else:
            bg_color = "#f1f5f9"
            border_color = "#64748b"
            stamp_text = "PENDING"

        # Position stamp at top right
        canvas.translate(460, 680)
        canvas.rotate(12)  # Rotated stamp for authenticity

        # Draw stamp boundary with roundRect
        canvas.setFillColor(colors.HexColor(bg_color))
        canvas.setStrokeColor(colors.HexColor(border_color))
        canvas.setLineWidth(3)
        canvas.roundRect(-65, -30, 130, 60, 8, fill=True, stroke=True)

        # Draw stamp text
        canvas.setFillColor(colors.HexColor(border_color))
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawCentredString(0, 10, stamp_text)
        canvas.setFont("Helvetica-Bold", 7.5)
        canvas.drawCentredString(0, -6, "BARANI HYDRAULICS")
        canvas.setFont("Helvetica", 7)
        canvas.drawCentredString(0, -18, seal_date_str)

        canvas.restoreState()

    # Build document
    doc.build(story, onFirstPage=draw_decorations_and_stamp)
    
    buffer.seek(0)
    
    pdf_filename = f"LeaveForm_{employee_id}_{from_date_str}.pdf"
    return send_file(
        buffer,
        as_attachment=True,
        download_name=pdf_filename,
        mimetype="application/pdf"
    )
