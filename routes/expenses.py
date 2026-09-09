from flask import Blueprint, render_template, request, redirect, session, jsonify, send_from_directory, abort
from werkzeug.utils import secure_filename
from db import get_db as get_connection
from db_attendance import get_attendance_connection
from datetime import datetime, date
import os
from audit_service import AuditService

expenses_bp = Blueprint("expenses", __name__)

UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "uploads", "receipts")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Jinja Helper Filter to format INR currency cleanly
def format_inr(amount):
    if amount is None:
        return "₹0.00"
    try:
        val = float(amount)
        if val < 0:
            return f"-₹{abs(val):,.2f}"
        return f"₹{val:,.2f}"
    except (ValueError, TypeError):
        return "₹0.00"

expenses_bp.add_app_template_filter(format_inr, 'inr')


# Helper function to check if user has Accounts / Admin role or department access
def is_accounts_authorized():
    role = (session.get("role") or "").lower().strip()
    dept = (session.get("department") or "").lower().strip()
    full_name = (session.get("full_name") or "").lower().strip()
    emp_id = (session.get("emp_id") or "").lower().strip()

    return (
        role in ['accounts', 'account', 'finance', 'admin', 'hr', 'md', 'gm'] or
        'account' in dept or 'finance' in dept or
        'acc' in emp_id or 'accounts' in full_name
    )


# Helper function to check if user has Department Head / Manager / HOD authorization
def is_hod_authorized():
    if session.get("is_hod"):
        return True

    role = (session.get("role") or "").lower().strip()
    dept = (session.get("department") or "").lower().strip()
    full_name = (session.get("full_name") or "").lower().strip()
    emp_id = (session.get("emp_id") or "").lower().strip()

    if role in ['hod', 'head', 'manager', 'admin', 'accounts', 'account', 'finance', 'hr', 'md', 'gm', 'lead', 'supervisor']:
        return True
    if any(kw in role for kw in ['hod', 'head', 'manager', 'admin', 'accounts', 'account', 'finance', 'hr', 'md', 'gm', 'lead', 'supervisor']):
        return True
    if any(kw in full_name for kw in ['hod', 'head', 'manager', 'lead', 'supervisor']):
        return True
    if any(kw in emp_id for kw in ['hod', 'head', 'mgr', 'sup', 'admin']):
        return True
    if any(kw in dept for kw in ['account', 'finance', 'admin', 'management']):
        return True

    # Database check in dbo.HOD table
    try:
        att_conn = get_attendance_connection()
        att_cur = att_conn.cursor()
        att_cur.execute("""
            SELECT 1 FROM dbo.HOD 
            WHERE (LOWER(RTRIM(LTRIM(EmployeeID))) = LOWER(RTRIM(LTRIM(?)))) AND IsActive = 1
        """, (emp_id,))
        row = att_cur.fetchone()
        att_conn.close()
        if row:
            session["is_hod"] = True
            return True
    except Exception as e:
        print("HOD table check exception:", e)

    return is_accounts_authorized()


# Concurrency-Safe Database-Backed Global Voucher Number Generator
def generate_next_voucher_number(cur, year=None):
    if not year:
        year = datetime.now().year
    try:
        year_int = int(year)
    except (ValueError, TypeError):
        year_int = datetime.now().year

    # Atomic SQL Server MERGE with UPDLOCK and HOLDLOCK to serialize concurrency
    cur.execute("""
        MERGE dbo.voucher_number_tracker WITH (UPDLOCK, HOLDLOCK) AS target
        USING (SELECT ? AS year_val) AS source
        ON target.year_val = source.year_val
        WHEN MATCHED THEN
            UPDATE SET last_number = target.last_number + 1
        WHEN NOT MATCHED THEN
            INSERT (year_val, last_number) VALUES (source.year_val, 1);
    """, (year_int,))

    cur.execute("SELECT last_number FROM dbo.voucher_number_tracker WHERE year_val = ?", (year_int,))
    seq_row = cur.fetchone()
    seq = seq_row[0] if seq_row else 1

    return f"VCH-{year_int}-{seq:04d}"


# Concurrency-Safe Auto-Incrementing Ledger Reference Number Generator (LED-XXXX)
def generate_next_ledger_no(cur):
    """
    Safely generates the next auto-incrementing Ledger Reference Number (LED-XXXX).
    Format: LED-0001, LED-0002, LED-0003...
    Calculated using MAX sequence number in dbo.cashier_transactions to prevent duplicates.
    """
    cur.execute("""
        SELECT ISNULL(MAX(
            CAST(SUBSTRING(ledger_no, 5, 20) AS INT)
        ), 0) + 1
        FROM dbo.cashier_transactions WITH (UPDLOCK, HOLDLOCK)
        WHERE ledger_no LIKE 'LED-%'
    """)
    row = cur.fetchone()
    next_num = row[0] if row and row[0] is not None else 1
    return f"LED-{next_num:04d}"


# Helper function to calculate voucher financials
def get_trip_financials(cur, trip_id):
    cur.execute("""
        SELECT v.voucher_amount, v.new_advance, v.opening_advance, v.voucher_type, v.previous_trip_id
        FROM dbo.user_trip_vouchers v
        WHERE v.trip_id = ? OR v.voucher_id = ?
    """, (trip_id, trip_id))
    v_row = cur.fetchone()
    if not v_row:
        return {
            'initial_advance': 0.0,
            'new_advance': 0.0,
            'opening_advance': 0.0,
            'additional_advance': 0.0,
            'total_available_advance': 0.0,
            'total_expenses': 0.0,
            'closing_balance': 0.0,
            'status_label': 'FULLY SETTLED',
            'sub_label': 'Fully Settled: ₹0.00',
            'status_type': 'SETTLED',
            'voucher_type': 'Expense',
            'previous_trip_id': None
        }

    v_amount = float(v_row[0] if v_row[0] is not None else 0.0)
    new_adv = float(v_row[1] if v_row[1] is not None else v_amount)
    opening_adv = float(v_row[2] or 0.0)
    voucher_type = v_row[3] or 'Expense'
    prev_trip_id = v_row[4]

    # Additional advances
    cur.execute("""
        SELECT ISNULL(SUM(amount), 0.00) 
        FROM dbo.trip_advances 
        WHERE trip_id = ? AND advance_type = 'ADDITIONAL_ADVANCE'
    """, (trip_id,))
    add_row = cur.fetchone()
    additional_adv = float(add_row[0] or 0.0)

    total_available = opening_adv + new_adv + additional_adv

    # Sum line item expenses
    cur.execute("SELECT ISNULL(SUM(amount), 0.00) FROM dbo.trip_daily_expenses WHERE trip_id = ?", (trip_id,))
    e_row = cur.fetchone()
    total_expenses = float(e_row[0] or 0.0)

    closing_balance = total_available - total_expenses

    if closing_balance > 0:
        status_label = "EMPLOYEE → COMPANY"
        sub_label = f"RETURN TO COMPANY: ₹{closing_balance:,.2f}"
        status_type = "RETURN_TO_COMPANY"
    elif closing_balance < 0:
        status_label = "COMPANY → EMPLOYEE"
        sub_label = f"PAYABLE TO EMPLOYEE: ₹{abs(closing_balance):,.2f}"
        status_type = "EMPLOYEE_REIMBURSEMENT"
    else:
        status_label = "FULLY SETTLED"
        sub_label = "Fully Settled: ₹0.00"
        status_type = "SETTLED"

    return {
        'initial_advance': new_adv,
        'new_advance': new_adv,
        'opening_advance': opening_adv,
        'additional_advance': additional_adv,
        'total_available_advance': total_available,
        'total_expenses': total_expenses,
        'closing_balance': closing_balance,
        'status_label': status_label,
        'sub_label': sub_label,
        'status_type': status_type,
        'voucher_type': voucher_type,
        'previous_trip_id': prev_trip_id
    }


def get_user_carry_forward(cur, user_id, emp_id):
    cur.execute("""
        SELECT TOP 1 trip_id, voucher_id, start_date, visited_company
        FROM dbo.user_trip_vouchers
        WHERE (user_id = ? OR emp_id = ?) AND status IN ('APPROVED', 'SETTLED', 'CLOSED')
        ORDER BY created_at DESC
    """, (user_id, emp_id))
    row = cur.fetchone()
    if row:
        prev_trip_id = row[0]
        prev_vch_id = row[1]
        fin = get_trip_financials(cur, prev_trip_id)
        if fin['closing_balance'] > 0:
            return {
                'previous_trip_id': prev_trip_id,
                'previous_voucher_id': prev_vch_id,
                'opening_advance': fin['closing_balance']
            }
    return {
        'previous_trip_id': None,
        'previous_voucher_id': None,
        'opening_advance': 0.0
    }


# ==================================================
# CASHIER HELPER FUNCTIONS & SUMMARY COMPUTATION
# ==================================================
def get_cashier_summary_dict(cur):
    today_str = datetime.now().strftime("%Y-%m-%d")

    # Ensure table exists
    cur.execute("""
        IF OBJECT_ID('dbo.cashier_transactions', 'U') IS NULL
        BEGIN
            CREATE TABLE dbo.cashier_transactions (
                txn_id INT IDENTITY(1,1) PRIMARY KEY,
                txn_date DATE NOT NULL,
                txn_type VARCHAR(10) NOT NULL,
                voucher_id VARCHAR(50) NULL,
                trip_id VARCHAR(50) NULL,
                particulars NVARCHAR(255) NOT NULL,
                party_name NVARCHAR(150) NULL,
                emp_id NVARCHAR(50) NULL,
                amount DECIMAL(18,2) NOT NULL,
                payment_reference NVARCHAR(100) NULL,
                created_by INT NOT NULL,
                created_at DATETIME DEFAULT GETDATE()
            );
        END
    """)

    # 1. Opening Balance prior to Today
    cur.execute("""
        SELECT 
            ISNULL(SUM(CASE WHEN txn_type = 'CREDIT' THEN amount ELSE -amount END), 0.00)
        FROM dbo.cashier_transactions
        WHERE txn_date < ?
    """, (today_str,))
    op_row = cur.fetchone()
    opening_balance = float(op_row[0] or 0.0)

    # 2. Today's Credit
    cur.execute("""
        SELECT ISNULL(SUM(amount), 0.00)
        FROM dbo.cashier_transactions
        WHERE txn_date = ? AND txn_type = 'CREDIT'
    """, (today_str,))
    today_credit = float(cur.fetchone()[0] or 0.0)

    # 3. Today's Debit
    cur.execute("""
        SELECT ISNULL(SUM(amount), 0.00)
        FROM dbo.cashier_transactions
        WHERE txn_date = ? AND txn_type = 'DEBIT'
    """, (today_str,))
    today_debit = float(cur.fetchone()[0] or 0.0)

    # 4. Closing Balance
    closing_balance = opening_balance + today_credit - today_debit

    return {
        'opening_balance': opening_balance,
        'today_credit': today_credit,
        'today_debit': today_debit,
        'closing_balance': closing_balance
    }


# ==================================================
# CLEAR ALL VOUCHER DATA & RESET SEQUENCE ROUTE
# ==================================================
@expenses_bp.route("/expenses/reset_all_data")
def reset_all_expense_data():
    if "user_id" not in session:
        return redirect("/")

    conn = get_connection()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM dbo.trip_settlements")
        cur.execute("DELETE FROM dbo.trip_advances")
        cur.execute("DELETE FROM dbo.trip_daily_expenses")
        cur.execute("DELETE FROM dbo.user_trip_vouchers")
        cur.execute("DELETE FROM dbo.cashier_transactions")
        cur.execute("UPDATE dbo.voucher_number_tracker SET last_number = 0 WHERE year_val = 2026")
        if cur.rowcount == 0:
            cur.execute("INSERT INTO dbo.voucher_number_tracker (year_val, last_number) VALUES (2026, 0)")
        
        # Seed Opening Cash Balance
        cur.execute("""
            INSERT INTO dbo.cashier_transactions (
                txn_date, txn_type, voucher_id, particulars, party_name, amount, created_by, created_at
            ) VALUES (
                CAST(GETDATE() AS DATE), 'CREDIT', 'INIT-001', 'Opening Cash Balance', 'BHIPL Cash Chest', 20000.00, 1, GETDATE()
            );
        """)

        conn.commit()
    except Exception as e:
        print("Reset error:", e)
        conn.rollback()
    finally:
        conn.close()

    return redirect("/expenses?reset=1")


# ==================================================
# 1. DEFAULT EXPENSES PAGE (INLINE WORKSPACE)
# ==================================================
@expenses_bp.route("/expenses")
def user_expenses():
    if "user_id" not in session:
        return redirect("/")

    mode = request.args.get("mode", "").lower().strip()
    if is_hod_authorized() and mode != "personal":
        return admin_expenses()

    user_id = session.get("user_id")
    emp_id = session.get("emp_id", "")
    full_name = session.get("full_name", "Employee")
    department = session.get("department", "Operations")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT 
            v.trip_id, v.voucher_id, v.purpose, v.from_location, v.to_location,
            v.start_date, v.end_date, v.voucher_amount, v.status, v.accounts_remarks,
            v.created_at, v.submitted_at, v.approved_at, v.settled_at,
            v.visited_company, v.sent_back_reason, v.rejection_reason,
            v.voucher_type, v.hod_approved_at, v.hod_remarks
        FROM dbo.user_trip_vouchers v
        WHERE (v.user_id = ? OR v.emp_id = ?)
          AND v.status NOT IN ('SETTLED', 'CLOSED')
          AND (v.user_cleared IS NULL OR v.user_cleared = 0)
        ORDER BY v.created_at DESC
    """, (user_id, emp_id))

    rows = cur.fetchall()

    vouchers = []
    for r in rows:
        trip_id = r[0]
        voucher_id = r[1]
        purpose = r[2]
        visited_company = r[14] or r[4] or "BHIPL Expense"
        start_date = r[5].strftime("%d-%m-%Y") if isinstance(r[5], datetime) else str(r[5])[:10] if r[5] else datetime.now().strftime("%d-%m-%Y")
        status = (r[8] or "DRAFT").upper()
        voucher_type = r[17] or "Expense"

        fin = get_trip_financials(cur, trip_id)

        usr_msg = purpose if (purpose and purpose.strip() and purpose.strip().lower() not in ['internal expense', 'bhipl expense', 'general expense']) else ""

        vouchers.append({
            'trip_id': trip_id,
            'voucher_id': voucher_id,
            'user_id': user_id,
            'emp_id': emp_id,
            'employee_name': full_name,
            'department': department,
            'visited_company': visited_company,
            'purpose': purpose,
            'user_message': usr_msg,
            'date': start_date,
            'voucher_type': voucher_type,
            'total_expenses': fin['total_expenses'],
            'total_available_advance': fin['total_available_advance'],
            'closing_balance': fin['closing_balance'],
            'status': status,
            'status_label': fin['status_label'],
            'sub_label': fin['sub_label'],
            'rejection_reason': (r[16] or r[19] or r[9] or "") if status == 'REJECTED' else "",
            'sent_back_reason': (r[15] or r[19] or r[9] or "") if status == 'SENT_BACK' else "",
            'hod_remarks': r[19] or "",
            'accounts_remarks': r[9] or ""
        })

    carry_forward_info = get_user_carry_forward(cur, user_id, emp_id)
    today_str = datetime.now().strftime("%Y-%m-%d")

    conn.close()

    return render_template(
        "expenses.html",
        vouchers=vouchers,
        carry_forward_info=carry_forward_info,
        today_date=today_str,
        emp_id=emp_id,
        full_name=full_name,
        department=department,
        is_accounts_user=is_accounts_authorized(),
        is_hod_user=is_hod_authorized()
    )


@expenses_bp.route("/expenses/voucher/<trip_id>/clear", methods=["POST"])
def clear_rejected_voucher(trip_id):
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session.get("user_id")
    emp_id = session.get("emp_id", "")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE dbo.user_trip_vouchers
        SET user_cleared = 1, user_cleared_at = GETDATE()
        WHERE (trip_id = ? OR voucher_id = ?) AND (user_id = ? OR emp_id = ?)
    """, (trip_id, trip_id, user_id, emp_id))

    conn.commit()
    conn.close()

    return jsonify({"success": True, "message": "Rejected voucher cleared successfully"})


# ==================================================
# 2. GET SINGLE VOUCHER JSON API (/expenses/voucher/<trip_id>)
# ==================================================
@expenses_bp.route("/expenses/voucher/<trip_id>")
def get_voucher_details_api(trip_id):
    if "user_id" not in session:
        return jsonify({"error": "Unauthorized"}), 401

    user_id = session.get("user_id")

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT trip_id, voucher_id, user_id, emp_id, employee_name, department,
               purpose, from_location, to_location, start_date, end_date,
               voucher_amount, status, accounts_remarks, submitted_at, visited_company,
               sent_back_reason, rejection_reason, approved_at, settled_at, settlement_status,
               voucher_type, opening_advance, new_advance, previous_trip_id,
               hod_approved_at, hod_remarks, rejected_by, rejected_at,
               payment_mode, payment_reference, created_at, advance_issued_at
        FROM dbo.user_trip_vouchers
        WHERE trip_id = ? OR voucher_id = ?
    """, (trip_id, trip_id))

    r = cur.fetchone()
    if not r:
        conn.close()
        return jsonify({"error": "Voucher not found"}), 404

    owner_id = r[2]
    status = (r[12] or "DRAFT").upper()

    user_role = (session.get("role") or "").lower().strip()
    is_pure_accounts = is_accounts_authorized() and not session.get("is_hod") and user_role != "admin"
    if is_pure_accounts and owner_id != user_id:
        if status not in ['HOD_APPROVED', 'APPROVED', 'SETTLED', 'CLOSED', 'ADVANCE_ISSUED']:
            conn.close()
            return jsonify({"error": "Access Denied: Accounts Team can only access HOD-approved vouchers."}), 403

    if owner_id != user_id and not is_hod_authorized():
        conn.close()
        return jsonify({"error": "Access Denied"}), 403

    real_trip_id = r[0]
    fin = get_trip_financials(cur, real_trip_id)

    cur.execute("""
        SELECT expense_id, expense_date, category, description, qty, amount, receipt_file, remarks
        FROM dbo.trip_daily_expenses
        WHERE trip_id = ?
        ORDER BY expense_id ASC
    """, (real_trip_id,))

    items = []
    for idx, er in enumerate(cur.fetchall(), 1):
        exp_date_str = er[1].strftime("%Y-%m-%d") if isinstance(er[1], datetime) else str(er[1])[:10] if er[1] else ""
        items.append({
            'sl_no': idx,
            'expense_id': er[0],
            'expense_date': exp_date_str,
            'category': er[2] or "General",
            'particulars': er[3] or "",
            'qty': int(er[4] or 1),
            'amount': float(er[5] or 0.0),
            'receipt_file': er[6] or "",
            'remarks': er[7] or ""
        })

    prev_vch_label = None
    if fin['previous_trip_id']:
        cur.execute("SELECT voucher_id FROM dbo.user_trip_vouchers WHERE trip_id = ?", (fin['previous_trip_id'],))
        p_row = cur.fetchone()
        if p_row:
            prev_vch_label = p_row[0]

    status = (r[12] or "DRAFT").upper()
    is_owner = (owner_id == user_id)
    is_editable = is_owner and (status in ['DRAFT', 'SENT_BACK', 'REJECTED'])

    created_at_str = r[31].strftime("%d-%m-%Y %I:%M %p") if isinstance(r[31], datetime) else ""
    submitted_at_str = r[14].strftime("%d-%m-%Y %I:%M %p") if isinstance(r[14], datetime) else ""
    hod_approved_at_str = r[25].strftime("%d-%m-%Y %I:%M %p") if isinstance(r[25], datetime) else ""
    approved_at_str = r[18].strftime("%d-%m-%Y %I:%M %p") if isinstance(r[18], datetime) else ""
    settled_at_str = r[19].strftime("%d-%m-%Y %I:%M %p") if isinstance(r[19], datetime) else ""
    advance_issued_at_str = r[32].strftime("%d-%m-%Y %I:%M %p") if isinstance(r[32], datetime) else ""

    # Fetch Rejection Metadata
    rejected_by_id = r[27]
    rejected_by_name = "HOD / Accounts"
    # Default: If HOD never approved the voucher, the rejection MUST be by HOD
    rejected_by_role = "HOD" if not hod_approved_at_str else "Accounts"

    cur.execute("""
        SELECT TOP 1 actor_role, actor_name, comments
        FROM dbo.voucher_approval_history
        WHERE (trip_id = ? OR voucher_id = ?) AND action_type = 'REJECTED'
        ORDER BY history_id DESC
    """, (real_trip_id, real_trip_id))
    rej_hist_row = cur.fetchone()
    if rej_hist_row:
        if rej_hist_row[0]:
            h_role = rej_hist_row[0].upper()
            if "HOD" in h_role:
                rejected_by_role = "HOD"
            elif "ACCOUNT" in h_role:
                rejected_by_role = "Accounts"
        if rej_hist_row[1]:
            rejected_by_name = rej_hist_row[1]
    elif rejected_by_id:
        cur.execute("SELECT full_name FROM dbo.users WHERE id = ?", (rejected_by_id,))
        usr_r = cur.fetchone()
        if usr_r and usr_r[0]:
            rejected_by_name = usr_r[0]

    rejected_at_str = r[28].strftime("%d-%m-%Y %I:%M %p") if isinstance(r[28], datetime) else ""
    rejection_reason = r[17] or (rej_hist_row[2] if (rej_hist_row and rej_hist_row[2]) else "") or r[26] or r[13] or ""

    # Fetch Approval & Rejection History Audit Trail
    cur.execute("""
        SELECT submission_no, action_type, actor_name, actor_role, comments, created_at
        FROM dbo.voucher_approval_history
        WHERE trip_id = ? OR voucher_id = ?
        ORDER BY history_id ASC
    """, (real_trip_id, real_trip_id))
    history_list = []
    for h in cur.fetchall():
        h_date_str = h[5].strftime("%d-%m-%Y %I:%M %p") if isinstance(h[5], datetime) else str(h[5])
        history_list.append({
            'submission_no': h[0],
            'action_type': h[1],
            'actor_name': h[2] or "User",
            'actor_role': h[3] or "",
            'comments': h[4] or "",
            'created_at': h_date_str
        })

    cur.execute("""
        SELECT TOP 1 comments
        FROM dbo.voucher_approval_history
        WHERE (trip_id = ? OR voucher_id = ?) AND action_type IN ('SUBMITTED', 'RESUBMITTED')
        ORDER BY history_id DESC
    """, (real_trip_id, real_trip_id))
    sub_cmt_row = cur.fetchone()
    user_message = sub_cmt_row[0] if (sub_cmt_row and sub_cmt_row[0] and sub_cmt_row[0] not in ['Initial Submission', 'Resubmitted for approval', 'Resubmitted by creator after correcting details']) else ""

    voucher_data = {
        'trip_id': r[0],
        'voucher_id': r[1],
        'user_id': r[2],
        'emp_id': r[3] or session.get("emp_id", "EMP001"),
        'employee_name': r[4] or session.get("full_name", "Employee"),
        'department': r[5] or session.get("department", "SERVICE"),
        'purpose': r[6] or "Internal Expense",
        'user_message': user_message,
        'visited_company': r[15] or r[8] or "BHIPL Expense",
        'start_date': r[9].strftime("%Y-%m-%d") if isinstance(r[9], datetime) else str(r[9])[:10] if r[9] else datetime.now().strftime("%Y-%m-%d"),
        'voucher_type': r[21] or "Expense",
        'opening_advance': fin['opening_advance'],
        'new_advance': fin['new_advance'],
        'additional_advance': fin['additional_advance'],
        'total_available_advance': fin['total_available_advance'],
        'total_expenses': fin['total_expenses'],
        'closing_balance': fin['closing_balance'],
        'status_label': fin['status_label'],
        'sub_label': fin['sub_label'],
        'status': status,
        'is_editable': is_editable,
        'is_voucher_creator': is_owner,
        'accounts_remarks': r[13] or "",
        'sent_back_reason': r[16] or "",
        'rejection_reason': rejection_reason,
        'rejected_by_name': rejected_by_name,
        'rejected_by_role': rejected_by_role,
        'rejected_at_str': rejected_at_str,
        'created_at_str': created_at_str,
        'submitted_at_str': submitted_at_str,
        'hod_approved_at_str': hod_approved_at_str,
        'approved_at_str': approved_at_str,
        'settled_at_str': settled_at_str,
        'advance_issued_at_str': advance_issued_at_str,
        'hod_remarks': r[26] or "",
        'payment_mode': r[29] or "CASH",
        'payment_reference': r[30] or "",
        'previous_trip_id': fin['previous_trip_id'],
        'previous_voucher_id': prev_vch_label,
        'items': items,
        'approval_history': history_list
    }

    conn.close()

    # Record VIEW_VOUCHER in Audit Log
    AuditService.log_action(
        action="VIEW_VOUCHER",
        module="Expenses",
        description=f"Viewed voucher details for {voucher_data.get('voucher_id') or real_trip_id}.",
        ref_type="Voucher",
        ref_id=voucher_data.get("voucher_id") or real_trip_id
    )

    return jsonify(voucher_data)


# ==================================================
# 3. SAVE / CREATE PHYSICAL EXPENSE VOUCHER
# ==================================================
@expenses_bp.route("/expenses/voucher/save", methods=["POST"])
def save_voucher():
    if "user_id" not in session:
        return redirect("/")

    user_id = session.get("user_id")
    emp_id = session.get("emp_id", "EMP001").strip()
    full_name = session.get("full_name", "Employee").strip()
    department = session.get("department", "SERVICE").strip()

    trip_id = request.form.get("trip_id", "").strip()
    voucher_type = request.form.get("voucher_type", "Expense").strip()
    visited_company = request.form.get("visited_company", "").strip() or "BHIPL Expense"
    purpose = request.form.get("purpose", "").strip() or "Internal Expense"
    start_date = request.form.get("start_date", "").strip() or datetime.now().strftime("%Y-%m-%d")
    action_type = request.form.get("action_type", "SAVE_DRAFT").strip().upper()

    new_advance_str = request.form.get("new_advance", "0").strip()
    try:
        new_advance = float(new_advance_str)
        if new_advance < 0:
            new_advance = 0.0
    except ValueError:
        new_advance = 0.0

    conn = get_connection()
    cur = conn.cursor()

    cf_info = get_user_carry_forward(cur, user_id, emp_id)
    opening_advance = cf_info['opening_advance'] if voucher_type == "Trip Advance" else 0.0
    previous_trip_id = cf_info['previous_trip_id'] if voucher_type == "Trip Advance" else None

    # Submission routes to Department Head (HOD) approval first: status = 'SUBMITTED'
    target_status = "SUBMITTED" if action_type in ["SUBMIT", "SUBMITTED"] else "DRAFT"
    user_message = request.form.get("user_message", "").strip()
    if user_message:
        purpose = user_message

    if not trip_id:
        timestamp_suffix = datetime.now().strftime("%y%m%d%H%M%S")
        trip_id = f"TRIP-{timestamp_suffix}"
        
        year_str = start_date[:4] if len(start_date) >= 4 and start_date[:4].isdigit() else str(datetime.now().year)
        voucher_id = generate_next_voucher_number(cur, year=year_str)

        cur.execute("""
            INSERT INTO dbo.user_trip_vouchers (
                trip_id, voucher_id, user_id, emp_id, employee_name, department,
                purpose, visited_company, from_location, to_location, start_date, voucher_type,
                opening_advance, new_advance, voucher_amount, previous_trip_id,
                status, created_at, submitted_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, GETDATE(), CASE WHEN ? = 'SUBMITTED' THEN GETDATE() ELSE NULL END
            )
        """, (
            trip_id, voucher_id, user_id, emp_id, full_name, department,
            purpose, visited_company, '', '', start_date, voucher_type,
            opening_advance, new_advance, new_advance, previous_trip_id,
            target_status, target_status
        ))

        if new_advance > 0 and voucher_type == "Trip Advance":
            cur.execute("""
                INSERT INTO dbo.trip_advances (
                    trip_id, advance_type, amount, reason, advance_date, created_by, created_at
                ) VALUES (
                    ?, 'INITIAL_ADVANCE', ?, 'Initial Trip Advance', ?, ?, GETDATE()
                )
            """, (trip_id, new_advance, start_date, user_id))

        if target_status == "SUBMITTED":
            sub_comments = user_message or 'Initial Submission'
            cur.execute("""
                INSERT INTO dbo.voucher_approval_history (
                    trip_id, voucher_id, submission_no, action_type, actor_id, actor_name, actor_role, comments, created_at
                ) VALUES (
                    ?, ?, 1, 'SUBMITTED', ?, ?, 'Employee', ?, GETDATE()
                )
            """, (trip_id, voucher_id, user_id, full_name, sub_comments))

    else:
        # Update existing voucher
        cur.execute("SELECT user_id, status, voucher_id, emp_id FROM dbo.user_trip_vouchers WHERE trip_id = ?", (trip_id,))
        t_row = cur.fetchone()
        if not t_row:
            conn.close()
            return jsonify({"error": "Voucher not found"}), 404

        # Security Permission Check: Only original creator can edit
        v_user_id = t_row[0]
        v_emp_id = t_row[3]
        if v_user_id != user_id and emp_id != v_emp_id:
            conn.close()
            return jsonify({"error": "Access Denied: Only the original voucher creator can edit this voucher."}), 403

        curr_st = (t_row[1] or "").upper()
        if curr_st in ['HOD_APPROVED', 'APPROVED', 'SETTLED', 'CLOSED']:
            conn.close()
            return jsonify({"error": "Cannot edit voucher under review or finalized"}), 400

        voucher_id = t_row[2]

        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET visited_company = ?, purpose = ?, start_date = ?, voucher_type = ?,
                new_advance = ?, voucher_amount = ?, status = ?,
                submitted_at = CASE WHEN ? = 'SUBMITTED' THEN GETDATE() ELSE submitted_at END,
                hod_approved_by = NULL, hod_approved_at = NULL, hod_remarks = NULL, accounts_remarks = NULL,
                rejected_by = NULL, rejected_at = NULL, rejection_reason = NULL
            WHERE trip_id = ?
        """, (
            visited_company, purpose, start_date, voucher_type,
            new_advance, new_advance, target_status,
            target_status, trip_id
        ))

        if target_status == "SUBMITTED":
            cur.execute("SELECT ISNULL(MAX(submission_no), 0) + 1 FROM dbo.voucher_approval_history WHERE trip_id = ?", (trip_id,))
            sub_no = cur.fetchone()[0]

            act_type = "RESUBMITTED" if curr_st == "REJECTED" else "SUBMITTED"
            default_comments = "Resubmitted by creator after correcting details" if curr_st == "REJECTED" else "Resubmitted for approval"
            sub_comments = user_message or default_comments

            cur.execute("""
                INSERT INTO dbo.voucher_approval_history (
                    trip_id, voucher_id, submission_no, action_type, actor_id, actor_name, actor_role, comments, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, 'Employee', ?, GETDATE()
                )
            """, (trip_id, voucher_id, sub_no, act_type, user_id, full_name, sub_comments))

    # Process line items
    line_dates = request.form.getlist("line_date[]")
    line_categories = request.form.getlist("line_category[]")
    line_particulars = request.form.getlist("line_particulars[]")
    line_qtys = request.form.getlist("line_qty[]")
    line_amounts = request.form.getlist("line_amount[]")
    receipt_files = request.files.getlist("line_receipt[]")

    cur.execute("DELETE FROM dbo.trip_daily_expenses WHERE trip_id = ?", (trip_id,))

    for i in range(len(line_particulars)):
        particulars = line_particulars[i].strip()
        if not particulars:
            continue

        item_date = line_dates[i].strip() if i < len(line_dates) and line_dates[i].strip() else start_date
        category = line_categories[i].strip() if i < len(line_categories) and line_categories[i].strip() else voucher_type
        qty_str = line_qtys[i].strip() if i < len(line_qtys) else "1"
        amt_str = line_amounts[i].strip() if i < len(line_amounts) else "0"

        try:
            qty = int(qty_str)
            if qty <= 0: qty = 1
        except ValueError:
            qty = 1

        try:
            amount = float(amt_str)
            if amount < 0: amount = 0.0
        except ValueError:
            amount = 0.0

        receipt_filename = None
        if i < len(receipt_files):
            file = receipt_files[i]
            if file and file.filename != "":
                filename = secure_filename(file.filename)
                timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
                receipt_filename = f"{timestamp}_{filename}"
                file.save(os.path.join(UPLOAD_FOLDER, receipt_filename))

        cur.execute("""
            INSERT INTO dbo.trip_daily_expenses (
                trip_id, expense_date, category, description, qty, amount, receipt_file, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, GETDATE()
            )
        """, (trip_id, item_date, category, particulars, qty, amount, receipt_filename))

    # Calculate total line item expenses and sync voucher_amount & new_advance in user_trip_vouchers
    cur.execute("SELECT ISNULL(SUM(amount), 0.00) FROM dbo.trip_daily_expenses WHERE trip_id = ?", (trip_id,))
    sum_items = float(cur.fetchone()[0] or 0.0)

    if sum_items > 0:
        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET voucher_amount = CASE WHEN voucher_amount = 0 THEN ? ELSE voucher_amount END,
                new_advance = CASE WHEN (new_advance = 0 OR voucher_type = 'Trip Advance') THEN ? ELSE new_advance END
            WHERE trip_id = ?
        """, (sum_items, sum_items, trip_id))

    conn.commit()
    conn.close()

    # Record Audit Log for Voucher Creation / Update / Submission
    if target_status == "SUBMITTED":
        act_name = "RESUBMIT_VOUCHER" if (trip_id and locals().get("curr_st") == "REJECTED") else "SUBMIT_VOUCHER"
    else:
        act_name = "CREATE_VOUCHER" if not request.form.get("trip_id", "").strip() else "UPDATE_VOUCHER"

    AuditService.log_action(
        action=act_name,
        module="Expenses",
        description=f"Voucher {voucher_id} for '{purpose}' - Rs. {sum_items or new_advance:,.2f} ({act_name.replace('_', ' ').title()}).",
        ref_type="Voucher",
        ref_id=voucher_id,
        user_id=user_id,
        username=f"{full_name} ({emp_id})",
        role=session.get("role")
    )

    return redirect(f"/expenses?view={trip_id}&success_action={target_status}&vch={voucher_id}")


# ==================================================
# 4. DEPARTMENT HEAD (HOD) ACTIONS API
# ==================================================
@expenses_bp.route("/hod/expenses/trip/<trip_id>/action", methods=["POST"])
def hod_trip_action(trip_id):
    if "user_id" not in session or not is_hod_authorized():
        return redirect("/access_denied")

    user_id = session.get("user_id")
    action = request.form.get("action", "").strip().upper()
    remarks = request.form.get("remarks", "").strip()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT status, trip_id FROM dbo.user_trip_vouchers WHERE trip_id = ? OR voucher_id = ?", (trip_id, trip_id))
    t_row = cur.fetchone()
    if not t_row:
        conn.close()
        return jsonify({"error": "Voucher not found"}), 404

    real_trip_id = t_row[1]

    cur.execute("SELECT ISNULL(voucher_id, trip_id) FROM dbo.user_trip_vouchers WHERE trip_id = ?", (real_trip_id,))
    vch_no_row = cur.fetchone()
    vch_no_val = vch_no_row[0] if vch_no_row else real_trip_id
    reviewer_name = session.get("full_name", "Department Head")

    if action in ["HOD_APPROVE", "APPROVE"]:
        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET status = 'HOD_APPROVED', hod_approved_by = ?, hod_approved_at = GETDATE(), hod_remarks = ?
            WHERE trip_id = ?
        """, (user_id, remarks, real_trip_id))

        cur.execute("SELECT ISNULL(MAX(submission_no), 1) FROM dbo.voucher_approval_history WHERE trip_id = ?", (real_trip_id,))
        sub_no = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO dbo.voucher_approval_history (
                trip_id, voucher_id, submission_no, action_type, actor_id, actor_name, actor_role, comments, created_at
            ) VALUES (
                ?, ?, ?, 'HOD_APPROVED', ?, ?, 'HOD', ?, GETDATE()
            )
        """, (real_trip_id, vch_no_val, sub_no, user_id, reviewer_name, remarks or 'Approved by Department Head'))

    elif action == "SEND_BACK":
        if not remarks:
            conn.close()
            return jsonify({"error": "Reason is required for sending back a voucher"}), 400

        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET status = 'SENT_BACK', sent_back_by = ?, sent_back_at = GETDATE(), sent_back_reason = ?, hod_remarks = ?
            WHERE trip_id = ?
        """, (user_id, remarks, remarks, real_trip_id))

        cur.execute("SELECT ISNULL(MAX(submission_no), 1) FROM dbo.voucher_approval_history WHERE trip_id = ?", (real_trip_id,))
        sub_no = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO dbo.voucher_approval_history (
                trip_id, voucher_id, submission_no, action_type, actor_id, actor_name, actor_role, comments, created_at
            ) VALUES (
                ?, ?, ?, 'SENT_BACK', ?, ?, 'HOD', ?, GETDATE()
            )
        """, (real_trip_id, vch_no_val, sub_no, user_id, reviewer_name, remarks))

    elif action == "REJECT":
        if not remarks:
            remarks = "Rejected by HOD"

        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET status = 'REJECTED', rejected_by = ?, rejected_at = GETDATE(), rejection_reason = ?, hod_remarks = ?
            WHERE trip_id = ?
        """, (user_id, remarks, remarks, real_trip_id))

        cur.execute("SELECT ISNULL(MAX(submission_no), 1) FROM dbo.voucher_approval_history WHERE trip_id = ?", (real_trip_id,))
        sub_no = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO dbo.voucher_approval_history (
                trip_id, voucher_id, submission_no, action_type, actor_id, actor_name, actor_role, comments, created_at
            ) VALUES (
                ?, ?, ?, 'REJECTED', ?, ?, 'HOD', ?, GETDATE()
            )
        """, (real_trip_id, vch_no_val, sub_no, user_id, reviewer_name, remarks))

    conn.commit()
    conn.close()

    # Record Audit Log for HOD action
    hod_act = "HOD_APPROVE" if action in ["HOD_APPROVE", "APPROVE"] else "HOD_REJECT"
    AuditService.log_action(
        action=hod_act,
        module="Expenses",
        description=f"Voucher {vch_no_val} {action.lower()} by HOD. Remarks: {remarks or 'None'}.",
        ref_type="Voucher",
        ref_id=vch_no_val,
        user_id=user_id,
        username=f"{session.get('full_name')} ({session.get('emp_id')})",
        role=session.get("role")
    )

    return redirect(f"/admin/expenses?view={real_trip_id}&success_action={action}&vch={vch_no_val}")


# ==================================================
# 5. ACCOUNTS & HOD APPROVAL WORKSPACE (ROLE-BASED VISIBILITY FILTERING)
# ==================================================
@expenses_bp.route("/admin/expenses")
def admin_expenses():
    if "user_id" not in session or not is_hod_authorized():
        return redirect("/access_denied")

    user_role = (session.get("role") or "").lower().strip()
    is_pure_accounts = is_accounts_authorized() and not session.get("is_hod") and user_role != "admin"

    conn = get_connection()
    cur = conn.cursor()

    if is_pure_accounts:
        # Accounts Team ONLY sees vouchers that have ALREADY been approved by Department Head (HOD)
        cur.execute("""
            SELECT 
                v.trip_id, v.voucher_id, v.user_id, v.emp_id, v.employee_name, v.department,
                v.purpose, v.from_location, v.to_location, v.start_date, v.end_date,
                v.voucher_amount, v.status, v.accounts_remarks, v.created_at, v.submitted_at,
                v.visited_company, v.settlement_status, v.voucher_type,
                v.hod_approved_at, v.hod_remarks, v.rejection_reason, v.sent_back_reason
            FROM dbo.user_trip_vouchers v
            WHERE v.status IN ('HOD_APPROVED', 'APPROVED', 'SETTLED', 'CLOSED', 'ADVANCE_ISSUED')
            ORDER BY v.created_at DESC
        """)
    else:
        # HOD / Admin sees department vouchers (including SUBMITTED vouchers waiting for HOD review)
        cur.execute("""
            SELECT 
                v.trip_id, v.voucher_id, v.user_id, v.emp_id, v.employee_name, v.department,
                v.purpose, v.from_location, v.to_location, v.start_date, v.end_date,
                v.voucher_amount, v.status, v.accounts_remarks, v.created_at, v.submitted_at,
                v.visited_company, v.settlement_status, v.voucher_type,
                v.hod_approved_at, v.hod_remarks, v.rejection_reason, v.sent_back_reason
            FROM dbo.user_trip_vouchers v
            WHERE v.status <> 'DRAFT'
            ORDER BY v.created_at DESC
        """)

    rows = cur.fetchall()
    vouchers = []

    for r in rows:
        trip_id = r[0]
        start_date = r[9].strftime("%d-%m-%Y") if isinstance(r[9], datetime) else str(r[9])[:10] if r[9] else ""
        sub_date = r[15].strftime("%d-%m-%Y %I:%M %p") if isinstance(r[15], datetime) else "-"
        hod_app_date = r[19].strftime("%d-%m-%Y %I:%M %p") if isinstance(r[19], datetime) else "-"
        status = (r[12] or "DRAFT").upper()
        voucher_type = r[18] or "Expense"

        fin = get_trip_financials(cur, trip_id)
        purpose = r[6] or "Internal Expense"
        usr_msg = purpose if (purpose and purpose.strip() and purpose.strip().lower() not in ['internal expense', 'bhipl expense', 'general expense']) else ""

        vouchers.append({
            'trip_id': trip_id,
            'voucher_id': r[1],
            'user_id': r[2],
            'emp_id': r[3] or "-",
            'employee_name': r[4] or "Employee",
            'department': r[5] or "SERVICE",
            'purpose': purpose,
            'user_message': usr_msg,
            'visited_company': r[16] or r[8] or "BHIPL Expense",
            'date': start_date,
            'voucher_type': voucher_type,
            'total_available_advance': fin['total_available_advance'],
            'total_expenses': fin['total_expenses'],
            'closing_balance': fin['closing_balance'],
            'status': status,
            'status_label': fin['status_label'],
            'sub_label': fin['sub_label'],
            'accounts_remarks': r[13] or "",
            'submitted_at': sub_date,
            'hod_approved_at': hod_app_date,
            'hod_remarks': r[20] or "",
            'rejection_reason': (r[21] or r[20] or r[13] or "") if status == 'REJECTED' else "",
            'sent_back_reason': (r[22] or r[20] or r[13] or "") if status == 'SENT_BACK' else ""
        })

    # Fetch all employees for Cashier Credit dropdown selection
    cur.execute("SELECT id, emp_id, full_name, department FROM dbo.users ORDER BY full_name ASC")
    employees = [{'id': r[0], 'emp_id': r[1] or '', 'full_name': r[2] or 'Employee', 'department': r[3] or ''} for r in cur.fetchall()]

    # Fetch recent CREDIT entries history
    cur.execute("""
        SELECT TOP 15 
            t.txn_id, t.txn_date, t.credit_source, t.source_name, t.party_name,
            t.voucher_id, t.payment_reference, t.particulars, t.amount, t.created_at
        FROM dbo.cashier_transactions t
        WHERE t.txn_type = 'CREDIT'
        ORDER BY t.created_at DESC
    """)
    recent_credit_entries = []
    for cr in cur.fetchall():
        t_date_str = cr[1].strftime("%d-%m-%Y") if isinstance(cr[1], (datetime, date)) else str(cr[1])
        src_type = cr[2] or 'other'
        src_label = (
            "Employee Advance Return" if src_type == 'employee_advance_return' else
            "Bank Cash Withdrawal" if src_type == 'bank_cash_withdrawal' else
            "Management / MD" if src_type == 'management' else "Other"
        )
        recent_credit_entries.append({
            'txn_id': cr[0],
            'date': t_date_str,
            'credit_source': src_type,
            'source_label': src_label,
            'source_name': cr[3] or cr[4] or "Cash Receipt",
            'party_name': cr[4] or cr[3] or "-",
            'reference': cr[5] or cr[6] or "—",
            'particulars': cr[7] or "Cash Credit",
            'amount': float(cr[8] or 0.0)
        })

    # Fetch recent DEBIT entries history (Automated Settlement Debits)
    cur.execute("""
        SELECT TOP 20 
            t.txn_id, t.txn_date, t.debit_type, t.party_name, t.voucher_id, t.particulars, t.amount, t.created_at
        FROM dbo.cashier_transactions t
        WHERE t.txn_type = 'DEBIT'
        ORDER BY t.created_at DESC, t.txn_id DESC
    """)
    recent_debit_entries = []
    for dr in cur.fetchall():
        d_date_str = dr[1].strftime("%d-%m-%Y") if isinstance(dr[1], (datetime, date)) else str(dr[1])[:10] if dr[1] else ""
        recent_debit_entries.append({
            'txn_id': dr[0],
            'date': d_date_str,
            'debit_type': dr[2] or 'settlement',
            'party_name': dr[3] or "-",
            'voucher_id': dr[4] or "—",
            'particulars': dr[5] or "Automatic Settlement Debit",
            'amount': float(dr[6] or 0.0)
        })

    cashier_summary = get_cashier_summary_dict(cur)
    today_str = datetime.now().strftime("%Y-%m-%d")

    conn.close()

    return render_template(
        "admin_expenses.html",
        vouchers=vouchers,
        employees=employees,
        recent_credit_entries=recent_credit_entries,
        recent_debit_entries=recent_debit_entries,
        cashier_summary=cashier_summary,
        today_date=today_str,
        is_accounts_user=is_accounts_authorized(),
        is_hod_user=is_hod_authorized()
    )


# ==================================================
# 6. ACCOUNTS GRANULAR ACTIONS (APPROVE / REJECT / SEND BACK / SETTLE & AUTO CASHIER POSTING)
# ==================================================
@expenses_bp.route("/admin/expenses/trip/<trip_id>/action", methods=["POST"])
def admin_trip_action(trip_id):
    if "user_id" not in session or not is_hod_authorized():
        return redirect("/access_denied")

    def handle_action_error(msg, code=400):
        if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
            return jsonify({"error": msg}), code
        from urllib.parse import quote_plus
        referer = request.headers.get("Referer") or "/admin/expenses"
        sep = "&" if "?" in referer else "?"
        return redirect(f"{referer}{sep}error={quote_plus(msg)}")

    user_id = session.get("user_id")
    action = request.form.get("action", "").strip().upper()
    remarks = request.form.get("remarks", "").strip()

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT status, trip_id, voucher_id, employee_name, emp_id, voucher_type FROM dbo.user_trip_vouchers WHERE trip_id = ? OR voucher_id = ?", (trip_id, trip_id))
    t_row = cur.fetchone()
    if not t_row:
        conn.close()
        return handle_action_error("Voucher not found", 404)

    curr_status = (t_row[0] or "DRAFT").upper()
    real_trip_id = t_row[1]
    vch_number = t_row[2]
    emp_name = t_row[3] or "Employee"
    employee_id = t_row[4] or ""
    vch_type = t_row[5] or "Expense"

    user_role = (session.get("role") or "").lower().strip()
    is_pure_accounts = is_accounts_authorized() and not session.get("is_hod") and user_role != "admin"
    if is_pure_accounts:
        if curr_status not in ['HOD_APPROVED', 'APPROVED', 'SETTLED', 'CLOSED', 'ADVANCE_ISSUED']:
            conn.close()
            return handle_action_error("Access Denied: Accounts Team can only process HOD-approved vouchers.")

    fin = get_trip_financials(cur, real_trip_id)

    reviewer_name = session.get("full_name", "Reviewer")
    reviewer_role = "Accounts" if is_accounts_authorized() else "HOD"

    if action == "HOD_APPROVE":
        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET status = 'HOD_APPROVED', hod_approved_by = ?, hod_approved_at = GETDATE(), hod_remarks = ?
            WHERE trip_id = ?
        """, (user_id, remarks, real_trip_id))

        cur.execute("SELECT ISNULL(MAX(submission_no), 1) FROM dbo.voucher_approval_history WHERE trip_id = ?", (real_trip_id,))
        sub_no = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO dbo.voucher_approval_history (
                trip_id, voucher_id, submission_no, action_type, actor_id, actor_name, actor_role, comments, created_at
            ) VALUES (
                ?, ?, ?, 'HOD_APPROVED', ?, ?, 'HOD', ?, GETDATE()
            )
        """, (real_trip_id, vch_number, sub_no, user_id, reviewer_name, remarks or 'Approved by Department Head'))

    elif action == "REJECT":
        rejection_actor_role = "HOD" if curr_status == "SUBMITTED" else "Accounts"
        if not remarks:
            remarks = f"Rejected by {rejection_actor_role}"

        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET status = 'REJECTED', rejected_by = ?, rejected_at = GETDATE(), rejection_reason = ?, accounts_remarks = ?
            WHERE trip_id = ? OR voucher_id = ?
        """, (user_id, remarks, remarks, real_trip_id, real_trip_id))

        cur.execute("SELECT ISNULL(MAX(submission_no), 1) FROM dbo.voucher_approval_history WHERE trip_id = ?", (real_trip_id,))
        sub_no = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO dbo.voucher_approval_history (
                trip_id, voucher_id, submission_no, action_type, actor_id, actor_name, actor_role, comments, created_at
            ) VALUES (
                ?, ?, ?, 'REJECTED', ?, ?, ?, ?, GETDATE()
            )
        """, (real_trip_id, vch_number, sub_no, user_id, reviewer_name, rejection_actor_role, remarks))

    elif action == "APPROVE":
        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET status = 'APPROVED', approved_by = ?, approved_at = GETDATE(), accounts_remarks = ?, settlement_status = 'PENDING'
            WHERE trip_id = ?
        """, (user_id, remarks, real_trip_id))

        cur.execute("SELECT ISNULL(MAX(submission_no), 1) FROM dbo.voucher_approval_history WHERE trip_id = ?", (real_trip_id,))
        sub_no = cur.fetchone()[0]
        cur.execute("""
            INSERT INTO dbo.voucher_approval_history (
                trip_id, voucher_id, submission_no, action_type, actor_id, actor_name, actor_role, comments, created_at
            ) VALUES (
                ?, ?, ?, 'APPROVED', ?, ?, 'Accounts', ?, GETDATE()
            )
        """, (real_trip_id, vch_number, sub_no, user_id, reviewer_name, remarks or 'Approved by Accounts'))

    elif action in ["MARK_RETURN_RECEIVED", "MARK_REIMBURSEMENT_PAID", "CLOSE", "SETTLE", "CONFIRM_SETTLEMENT"]:
        # 1. VOUCHER TYPE SEAMLESS ROUTING (INTERNAL LOGIC WITHOUT ALERT BANNERS)
        if vch_type == "Trip Advance":
            payment_mode = request.form.get("payment_mode", "CASH").strip().upper()
            if payment_mode not in ["ONLINE", "CASH"]:
                payment_mode = "CASH"
            payment_ref = request.form.get("payment_reference", "").strip()
            if payment_mode == "ONLINE" and not payment_ref:
                conn.close()
                return handle_action_error("Transaction ID / UTR Number is required for Online Payment.")
            if payment_mode == "CASH" and not payment_ref:
                payment_ref = "CASH"

            cur.execute("SELECT ISNULL(accounting_status, '') FROM dbo.user_trip_vouchers WHERE trip_id = ? OR voucher_id = ?", (real_trip_id, real_trip_id))
            acc_st_row = cur.fetchone()
            acc_st = acc_st_row[0] if acc_st_row else ''

            if acc_st != 'ISSUED' and curr_status not in ['ADVANCE_ISSUED', 'CLOSED', 'SETTLED']:
                # Issue trip advance
                adv_amount = fin.get('new_advance') or fin.get('total_expenses') or fin.get('total_available_advance') or 0.0
                cur.execute("""
                    SELECT COUNT(*) FROM dbo.cashier_transactions 
                    WHERE (voucher_id = ? OR trip_id = ?) AND txn_type = 'DEBIT'
                """, (vch_number, real_trip_id))
                if cur.fetchone()[0] == 0:
                    ledger_no = generate_next_ledger_no(cur)
                    cur.execute("""
                        INSERT INTO dbo.cashier_transactions (
                            ledger_no, txn_date, txn_type, debit_type, source_name, party_name, voucher_id, trip_id, particulars, emp_id, amount, payment_mode, payment_reference, created_by, created_at
                        ) VALUES (
                            ?, CAST(GETDATE() AS DATE), 'DEBIT', 'trip_advance', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE()
                        )
                    """, (
                        ledger_no, emp_name, emp_name, vch_number, real_trip_id,
                        f"Trip advance issued against {vch_number}", employee_id,
                        adv_amount, payment_mode, payment_ref, user_id
                    ))

                cur.execute("""
                    UPDATE dbo.user_trip_vouchers
                    SET status = 'ADVANCE_ISSUED', accounting_status = 'ISSUED', advance_issued_by = ?, advance_issued_at = GETDATE(), cash_handover_status = 'PENDING', payment_mode = ?, payment_reference = ?
                    WHERE trip_id = ? OR voucher_id = ?
                """, (user_id, payment_mode, payment_ref, real_trip_id, real_trip_id))
            else:
                # Settle / Close trip advance
                cur.execute("""
                    UPDATE dbo.user_trip_vouchers
                    SET status = 'SETTLED', settlement_status = 'SETTLED', settled_by = ?, settled_at = GETDATE(), accounts_remarks = ?, payment_mode = ?, payment_reference = ?
                    WHERE trip_id = ? OR voucher_id = ?
                """, (user_id, remarks or 'Trip Advance Settled', payment_mode, payment_ref, real_trip_id, real_trip_id))

            conn.commit()
            conn.close()
            referer = request.headers.get("Referer") or ""
            target_page = "/expenses" if "/expenses" in referer else "/admin/expenses"
            return redirect(f"{target_page}?view={real_trip_id}")

        # 2. IDEMPOTENCY & ONE-TIME SETTLEMENT CHECK
        if curr_status in ['SETTLED', 'CLOSED']:
            conn.close()
            return handle_action_error("This voucher has already been settled.")

        cur.execute("""
            SELECT COUNT(*) FROM dbo.cashier_transactions 
            WHERE (voucher_id = ? OR trip_id = ?) AND (credit_source = 'employee_advance_return' OR particulars LIKE '%settlement%' OR particulars LIKE '%returned%' OR particulars LIKE '%reimbursement%')
        """, (vch_number, real_trip_id))
        if cur.fetchone()[0] > 0:
            conn.close()
            return handle_action_error("Settlement transaction for this voucher already exists in cashier ledger.")

        # 3. PAYMENT MODE & TRANSACTION ID / UTR VALIDATION
        payment_mode = request.form.get("payment_mode", "CASH").strip().upper()
        if payment_mode not in ["ONLINE", "CASH"]:
            payment_mode = "CASH"

        payment_ref = request.form.get("payment_reference", "").strip()

        if payment_mode == "ONLINE" and not payment_ref:
            conn.close()
            return handle_action_error("Transaction ID / UTR Number is required for Online Payment.")

        if payment_mode == "CASH" and not payment_ref:
            payment_ref = "CASH"

        # 4. NET FINANCIAL DIFFERENCE CALCULATION
        closing_bal = fin['closing_balance']

        # Log to trip_settlements
        settlement_type = "RETURN_RECEIVED" if closing_bal > 0 else "REIMBURSED" if closing_bal < 0 else "FULLY_SETTLED"
        cur.execute("""
            INSERT INTO dbo.trip_settlements (
                trip_id, closing_balance, settlement_type, settlement_amount, payment_reference, settlement_date, remarks, created_by
            ) VALUES (
                ?, ?, ?, ?, ?, GETDATE(), ?, ?
            )
        """, (real_trip_id, closing_bal, settlement_type, abs(closing_bal), payment_ref, remarks or 'Voucher Settlement', user_id))

        # Update Voucher Status & Payment Mode
        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET status = 'SETTLED', settled_by = ?, settled_at = GETDATE(), settlement_status = 'SETTLED', accounts_remarks = ?, payment_mode = ?, payment_reference = ?
            WHERE trip_id = ? OR voucher_id = ?
        """, (user_id, remarks or 'Voucher Settled by Accounts', payment_mode, payment_ref, real_trip_id, real_trip_id))

        # 5. AUTOMATED NET CREDIT / DEBIT LEDGER TRANSACTION CREATION
        if closing_bal > 0:
            # Employee returns unused advance to company -> CREDIT ENTRY
            ledger_no = generate_next_ledger_no(cur)
            cur.execute("""
                INSERT INTO dbo.cashier_transactions (
                    ledger_no, txn_date, txn_type, credit_source, source_name, party_name, voucher_id, trip_id, particulars, emp_id, amount, payment_mode, payment_reference, created_by, created_at
                ) VALUES (
                    ?, CAST(GETDATE() AS DATE), 'CREDIT', 'employee_advance_return', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE()
                )
            """, (
                ledger_no, emp_name, emp_name, vch_number, real_trip_id,
                f"Advance returned against {vch_number}", employee_id,
                closing_bal, payment_mode, payment_ref, user_id
            ))
        elif closing_bal < 0:
            # Company pays additional reimbursement to employee -> DEBIT ENTRY
            ledger_no = generate_next_ledger_no(cur)
            cur.execute("""
                INSERT INTO dbo.cashier_transactions (
                    ledger_no, txn_date, txn_type, debit_type, source_name, party_name, voucher_id, trip_id, particulars, emp_id, amount, payment_mode, payment_reference, created_by, created_at
                ) VALUES (
                    ?, CAST(GETDATE() AS DATE), 'DEBIT', 'settlement', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE()
                )
            """, (
                ledger_no, emp_name, emp_name, vch_number, real_trip_id,
                f"Additional expense settlement against {vch_number}", employee_id,
                abs(closing_bal), payment_mode, payment_ref, user_id
            ))

    elif action in ["ISSUE_ADVANCE", "SETTLE_ADVANCE"]:
        # 1. VOUCHER TYPE VALIDATION
        if vch_type != "Trip Advance":
            conn.close()
            return handle_action_error("This action is applicable only for Trip Advance vouchers.")

        # 2. APPROVAL STATUS CHECK
        if curr_status not in ['APPROVED', 'HOD_APPROVED']:
            conn.close()
            return handle_action_error("Trip Advance must be approved before advance can be issued.")

        # 3. IDEMPOTENCY CHECK
        cur.execute("SELECT accounting_status FROM dbo.user_trip_vouchers WHERE trip_id = ? OR voucher_id = ?", (real_trip_id, real_trip_id))
        acc_st = cur.fetchone()[0]
        if acc_st == 'ISSUED' or curr_status in ['ADVANCE_ISSUED', 'CLOSED']:
            conn.close()
            return handle_action_error("Trip advance has already been issued for this voucher.")

        cur.execute("""
            SELECT COUNT(*) FROM dbo.cashier_transactions 
            WHERE (voucher_id = ? OR trip_id = ?) AND txn_type = 'DEBIT'
        """, (vch_number, real_trip_id))
        if cur.fetchone()[0] > 0:
            conn.close()
            return handle_action_error("Debit transaction for this trip advance already exists in cashier ledger.")

        # 4. PAYMENT MODE & TRANSACTION ID / UTR VALIDATION
        payment_mode = request.form.get("payment_mode", "CASH").strip().upper()
        if payment_mode not in ["ONLINE", "CASH"]:
            payment_mode = "CASH"

        payment_ref = request.form.get("payment_reference", "").strip()

        if payment_mode == "ONLINE" and not payment_ref:
            conn.close()
            return handle_action_error("Transaction ID / UTR Number is required for Online Payment.")

        if payment_mode == "CASH" and not payment_ref:
            payment_ref = "CASH"

        # 5. AUTOMATIC DEBIT ENTRY CREATION
        adv_amount = fin['new_advance'] or fin['total_expenses'] or fin['total_available_advance'] or 0.0

        ledger_no = generate_next_ledger_no(cur)
        cur.execute("""
            INSERT INTO dbo.cashier_transactions (
                ledger_no, txn_date, txn_type, debit_type, source_name, party_name, voucher_id, trip_id, particulars, emp_id, amount, payment_mode, payment_reference, created_by, created_at
            ) VALUES (
                ?, CAST(GETDATE() AS DATE), 'DEBIT', 'trip_advance', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, GETDATE()
            )
        """, (
            ledger_no, emp_name, emp_name, vch_number, real_trip_id,
            f"Trip advance issued against {vch_number}", employee_id,
            adv_amount, payment_mode, payment_ref, user_id
        ))

        # 6. UPDATE VOUCHER STATUS
        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET status = 'ADVANCE_ISSUED', accounting_status = 'ISSUED', advance_issued_by = ?, advance_issued_at = GETDATE(), cash_handover_status = 'PENDING', payment_mode = ?, payment_reference = ?
            WHERE trip_id = ? OR voucher_id = ?
        """, (user_id, payment_mode, payment_ref, real_trip_id, real_trip_id))

    elif action == "HOD_CONFIRM_CASH":
        if vch_type != "Trip Advance":
            conn.close()
            return handle_action_error("Action applicable only for Trip Advance vouchers.")

        if not is_hod_authorized():
            conn.close()
            return handle_action_error("Access Denied: Only authorized HOD/Admin can confirm cash handover.", 403)

        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET hod_cash_confirmed = 1, hod_cash_confirmed_by = ?, hod_cash_confirmed_at = GETDATE()
            WHERE trip_id = ? OR voucher_id = ?
        """, (user_id, real_trip_id, real_trip_id))

        cur.execute("SELECT employee_cash_confirmed FROM dbo.user_trip_vouchers WHERE trip_id = ? OR voucher_id = ?", (real_trip_id, real_trip_id))
        emp_conf = cur.fetchone()[0]
        if emp_conf:
            cur.execute("""
                UPDATE dbo.user_trip_vouchers
                SET status = 'CLOSED', cash_handover_status = 'CONFIRMED'
                WHERE trip_id = ? OR voucher_id = ?
            """, (real_trip_id, real_trip_id))

    elif action == "EMPLOYEE_CONFIRM_CASH":
        if vch_type != "Trip Advance":
            conn.close()
            return handle_action_error("Action applicable only for Trip Advance vouchers.")

        cur.execute("SELECT user_id, emp_id, hod_cash_confirmed FROM dbo.user_trip_vouchers WHERE trip_id = ? OR voucher_id = ?", (real_trip_id, real_trip_id))
        chk_row = cur.fetchone()
        if not chk_row:
            conn.close()
            return handle_action_error("Voucher not found", 404)

        vch_owner_id = chk_row[0]
        vch_emp_id = chk_row[1]
        hod_conf = chk_row[2]

        if user_id != vch_owner_id and session.get("emp_id") != vch_emp_id:
            conn.close()
            return handle_action_error("Access Denied: Only the employee who created this voucher can confirm cash receipt.", 403)

        cur.execute("""
            UPDATE dbo.user_trip_vouchers
            SET employee_cash_confirmed = 1, employee_cash_confirmed_by = ?, employee_cash_confirmed_at = GETDATE()
            WHERE trip_id = ? OR voucher_id = ?
        """, (user_id, real_trip_id, real_trip_id))

        if hod_conf:
            cur.execute("""
                UPDATE dbo.user_trip_vouchers
                SET status = 'CLOSED', cash_handover_status = 'CONFIRMED'
                WHERE trip_id = ? OR voucher_id = ?
            """, (real_trip_id, real_trip_id))

    conn.commit()
    conn.close()

    p_mode = request.form.get("payment_mode", "CASH").strip().upper()
    p_ref = request.form.get("payment_reference", "").strip()
    p_amt = abs(closing_bal) if 'closing_bal' in locals() else (fin.get('total_expenses', 0) if 'fin' in locals() else 0)

    # Record Audit Log for Accounts / Financial / Settlement Actions
    if action in ["MARK_RETURN_RECEIVED", "MARK_REIMBURSEMENT_PAID", "CLOSE", "SETTLE", "CONFIRM_SETTLEMENT"]:
        settle_mode = "ONLINE_SETTLEMENT" if p_mode == "ONLINE" else "CASH_SETTLEMENT"
        AuditService.log_action(
            action="SETTLE_VOUCHER",
            module="Expenses",
            description=f"Voucher {vch_number} settled for Rs. {p_amt:,.2f} via {p_mode} ({settle_mode}). Ref: {p_ref or 'None'}.",
            ref_type="Voucher",
            ref_id=vch_number or real_trip_id,
            user_id=user_id,
            username=f"{session.get('full_name')} ({session.get('emp_id')})",
            role=session.get("role")
        )
    elif action == "HOD_APPROVE":
        AuditService.log_action(
            action="HOD_APPROVE",
            module="Expenses",
            description=f"Voucher {vch_number} approved by Department Head.",
            ref_type="Voucher",
            ref_id=vch_number or real_trip_id
        )
    elif action == "APPROVE":
        AuditService.log_action(
            action="ACCOUNTS_APPROVE",
            module="Expenses",
            description=f"Voucher {vch_number} approved by Accounts Team.",
            ref_type="Voucher",
            ref_id=vch_number or real_trip_id
        )
    elif action == "REJECT":
        act_rej = "HOD_REJECT" if curr_status == "SUBMITTED" else "ACCOUNTS_REJECT"
        AuditService.log_action(
            action=act_rej,
            module="Expenses",
            description=f"Voucher {vch_number} rejected. Remarks: {remarks or 'None'}.",
            ref_type="Voucher",
            ref_id=vch_number or real_trip_id
        )

    from urllib.parse import quote_plus
    referer = request.headers.get("Referer") or "/admin/expenses"
    sep = "&" if "?" in referer else "?"

    succ_params = f"success_action={action}&vch={quote_plus(vch_number or '')}&amt={p_amt}&mode={quote_plus(p_mode)}&ref={quote_plus(p_ref)}"
    return redirect(f"{referer}{sep}{succ_params}")


# ==================================================
# 7. CASHIER ENDPOINTS (SUMMARY, LEDGER, CREDIT, DEBIT)
# ==================================================
@expenses_bp.route("/cashier/summary")
def cashier_summary_api():
    if "user_id" not in session or not is_accounts_authorized():
        return jsonify({"error": "Unauthorized access"}), 403

    conn = get_connection()
    cur = conn.cursor()
    summary = get_cashier_summary_dict(cur)
    conn.close()

    return jsonify(summary)


@expenses_bp.route("/cashier/ledger")
def cashier_ledger_api():
    if "user_id" not in session or not is_accounts_authorized():
        return jsonify({"error": "Unauthorized access"}), 403

    from_date_str = request.args.get("from_date", "").strip()
    to_date_str = request.args.get("to_date", "").strip()

    today_str = datetime.now().strftime("%Y-%m-%d")

    if not from_date_str:
        from_date_str = today_str
    if not to_date_str:
        to_date_str = today_str

    conn = get_connection()
    cur = conn.cursor()

    # 1. Opening balance prior to from_date
    cur.execute("""
        SELECT ISNULL(SUM(CASE WHEN txn_type = 'CREDIT' THEN amount ELSE -amount END), 0.00)
        FROM dbo.cashier_transactions
        WHERE txn_date < ?
    """, (from_date_str,))
    prior_bal = float(cur.fetchone()[0] or 0.0)

    # 2. Today's Cash Position numbers
    summary = get_cashier_summary_dict(cur)

    # 3. Transactions between from_date and to_date
    cur.execute("""
        SELECT txn_id, ledger_no, txn_date, txn_type, voucher_id, particulars, party_name, emp_id, amount, payment_reference, created_at
        FROM dbo.cashier_transactions
        WHERE txn_date >= ? AND txn_date <= ?
        ORDER BY ISNULL(created_at, CAST(txn_date AS DATETIME)) ASC, txn_id ASC
    """, (from_date_str, to_date_str))

    rows = cur.fetchall()
    transactions = []
    running_balance = prior_bal

    for r in rows:
        t_id = r[0]
        l_no = r[1] or f"LED-{t_id:04d}"
        
        # Use actual transaction timestamp (created_at) for date and time
        dt_val = r[10] if isinstance(r[10], datetime) else (r[2] if isinstance(r[2], (datetime, date)) else None)
        if isinstance(dt_val, datetime):
            t_date = dt_val.strftime("%d-%m-%Y")
            t_time = dt_val.strftime("%I:%M %p")
            t_date_time = dt_val.strftime("%d-%m-%Y %I:%M %p")
        elif isinstance(dt_val, date):
            t_date = dt_val.strftime("%d-%m-%Y")
            t_time = ""
            t_date_time = t_date
        else:
            t_date = str(r[2]) if r[2] else ""
            t_time = ""
            t_date_time = t_date

        t_type = (r[3] or "").upper()
        vch_id = r[4] or "-"
        particulars = r[5] or ""
        party_name = r[6] or "-"
        emp_id = r[7] or ""
        amount = float(r[8] or 0.0)
        pay_ref = r[9] or ""

        credit_amt = amount if t_type == 'CREDIT' else 0.0
        debit_amt = amount if t_type == 'DEBIT' else 0.0

        running_balance = running_balance + credit_amt - debit_amt

        transactions.append({
            'txn_id': t_id,
            'ledger_no': l_no,
            'date': t_date,
            'time': t_time,
            'date_time': t_date_time,
            'type': t_type,
            'particulars': particulars,
            'voucher_id': vch_id,
            'party_name': party_name,
            'emp_id': emp_id,
            'credit': credit_amt,
            'debit': debit_amt,
            'balance': running_balance,
            'payment_reference': pay_ref,
            'created_at': t_date_time
        })

    conn.close()

    return jsonify({
        'summary': summary,
        'prior_balance': prior_bal,
        'from_date': from_date_str,
        'to_date': to_date_str,
        'transactions': transactions
    })


@expenses_bp.route("/cashier/credit/save", methods=["POST"])
def cashier_save_credit():
    if "user_id" not in session or not is_accounts_authorized():
        return jsonify({"error": "Unauthorized access"}), 403

    user_id = session.get("user_id")
    txn_date = request.form.get("date", "").strip() or datetime.now().strftime("%Y-%m-%d")
    credit_src = request.form.get("credit_source", "other").strip()
    vch_ref = request.form.get("reference", "").strip()
    ref_gen = request.form.get("reference_gen", "").strip()
    source_name = request.form.get("source_name", "").strip()
    party_name = request.form.get("party_name", "").strip() or "General Cashier"
    amt_str = request.form.get("amount", "0").strip()

    try:
        amount = float(amt_str)
        if amount <= 0:
            return jsonify({"error": "Credit amount must be greater than zero"}), 400
    except ValueError:
        return jsonify({"error": "Invalid credit amount"}), 400

    conn = get_connection()
    cur = conn.cursor()

    ledger_no = generate_next_ledger_no(cur)
    particulars = f"Cash received ({credit_src.replace('_', ' ').title()})"

    cur.execute("""
        INSERT INTO dbo.cashier_transactions (
            ledger_no, txn_date, txn_type, credit_source, source_name, party_name, voucher_id, particulars, amount, payment_reference, created_by, created_at
        ) VALUES (
            ?, ?, 'CREDIT', ?, ?, ?, ?, ?, ?, ?, ?, GETDATE()
        )
    """, (ledger_no, txn_date, credit_src, source_name, party_name, vch_ref or None, particulars, amount, ref_gen or None, user_id))

    conn.commit()
    conn.close()

    # Record CREDIT_ENTRY in Audit Log
    AuditService.log_action(
        action="CREDIT_ENTRY",
        module="Ledger",
        description=f"Cashier credit {ledger_no} of Rs. {amount:,.2f} recorded from {party_name} ({credit_src}).",
        ref_type="Ledger",
        ref_id=ledger_no,
        user_id=user_id,
        username=f"{session.get('full_name')} ({session.get('emp_id')})",
        role=session.get("role")
    )

    return redirect("/admin/expenses?tab=ledger&credited=1")


@expenses_bp.route("/cashier/debit/save", methods=["POST"])
def cashier_save_debit():
    if "user_id" not in session or not is_accounts_authorized():
        return jsonify({"error": "Unauthorized access"}), 403

    user_id = session.get("user_id")
    txn_date = request.form.get("date", "").strip() or datetime.now().strftime("%Y-%m-%d")
    vch_ref = request.form.get("reference", "").strip()
    party_name = request.form.get("party_name", "").strip() or "General Payee"
    particulars = request.form.get("particulars", "").strip() or "Cash Payment"
    amt_str = request.form.get("amount", "0").strip()

    try:
        amount = float(amt_str)
        if amount <= 0:
            return jsonify({"error": "Debit amount must be greater than zero"}), 400
    except ValueError:
        return jsonify({"error": "Invalid debit amount"}), 400

    conn = get_connection()
    cur = conn.cursor()

    ledger_no = generate_next_ledger_no(cur)

    cur.execute("""
        INSERT INTO dbo.cashier_transactions (
            ledger_no, txn_date, txn_type, voucher_id, particulars, party_name, amount, created_by, created_at
        ) VALUES (
            ?, ?, 'DEBIT', ?, ?, ?, ?, ?, GETDATE()
        )
    """, (ledger_no, txn_date, vch_ref or None, particulars, party_name, amount, user_id))

    conn.commit()
    conn.close()

    # Record DEBIT_ENTRY in Audit Log
    AuditService.log_action(
        action="DEBIT_ENTRY",
        module="Ledger",
        description=f"Cashier debit {ledger_no} of Rs. {amount:,.2f} paid to {party_name}.",
        ref_type="Ledger",
        ref_id=ledger_no,
        user_id=user_id,
        username=f"{session.get('full_name')} ({session.get('emp_id')})",
        role=session.get("role")
    )

    return redirect("/admin/expenses?tab=ledger&debited=1")


@expenses_bp.route("/cashier/transaction/<int:txn_id>")
def cashier_get_transaction_api(txn_id):
    if "user_id" not in session or not is_accounts_authorized():
        return jsonify({"error": "Unauthorized access"}), 403

    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT t.txn_id, t.ledger_no, t.txn_date, t.txn_type, t.voucher_id, t.trip_id, t.particulars, t.party_name,
               t.emp_id, t.amount, t.payment_reference, t.created_at, u.full_name
        FROM dbo.cashier_transactions t
        LEFT JOIN dbo.users u ON t.created_by = u.id
        WHERE t.txn_id = ?
    """, (txn_id,))

    r = cur.fetchone()
    if not r:
        conn.close()
        return jsonify({"error": "Transaction not found"}), 404

    dt_val = r[11] if isinstance(r[11], datetime) else (r[2] if isinstance(r[2], (datetime, date)) else None)
    if isinstance(dt_val, datetime):
        t_date = dt_val.strftime("%d-%m-%Y")
        t_time = dt_val.strftime("%I:%M %p")
        t_date_time = dt_val.strftime("%d-%m-%Y %I:%M %p")
    elif isinstance(dt_val, date):
        t_date = dt_val.strftime("%d-%m-%Y")
        t_time = ""
        t_date_time = t_date
    else:
        t_date = str(r[2]) if r[2] else ""
        t_time = ""
        t_date_time = t_date

    detail = {
        'txn_id': r[0],
        'ledger_no': r[1] or f"LED-{r[0]:04d}",
        'date': t_date,
        'time': t_time,
        'date_time': t_date_time,
        'type': r[3],
        'voucher_id': r[4] or "-",
        'trip_id': r[5] or "-",
        'particulars': r[6],
        'party_name': r[7] or "-",
        'emp_id': r[8] or "-",
        'amount': float(r[9] or 0.0),
        'payment_reference': r[10] or "-",
        'created_at': t_date_time,
        'created_by_name': r[12] or "Accounts Staff"
    }

    conn.close()
    return jsonify(detail)


# ==================================================
# 8. SECURE RECEIPT FILE SERVING
# ==================================================
@expenses_bp.route("/expenses/receipt/<path:filename>")
def serve_receipt(filename):
    if "user_id" not in session:
        return redirect("/")

    safe_path = secure_filename(filename)
    file_path = os.path.join(UPLOAD_FOLDER, safe_path)
    if not os.path.exists(file_path):
        return abort(404)

    return send_from_directory(UPLOAD_FOLDER, safe_path)
