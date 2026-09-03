
import pyodbc

def append_pdf_route():
    with open(r'd:\BHIPL PORTAL\routes\request.py', 'a', encoding='utf-8') as f:
        f.write("""

# ==================================================
# DOWNLOAD PRINTABLE DOCUMENT
# ==================================================
@request_bp.route("/user/request/download/<req_type>/<int:req_id>")
def download_request_pdf(req_type, req_id):
    if "user_id" not in session: return redirect("/")
    emp_id = get_logged_in_emp_id()

    conn = get_connection()
    cur = conn.cursor()

    table_map = {
        'leave': 'Leave_Request',
        'permission': 'Permission_Request',
        'outpass': 'Outpass_Request'
    }

    if req_type not in table_map:
        conn.close()
        return "Invalid request type.", 400

    table = table_map[req_type]

    # Verify ownership
    cur.execute(f"SELECT EmployeeID FROM dbo.{table} WHERE RequestID = ?", (req_id,))
    row = cur.fetchone()
    if not row or str(row[0]) != str(emp_id):
        conn.close()
        return "Unauthorized or not found.", 403

    cur.execute(f"SELECT * FROM dbo.{table} WHERE RequestID = ?", (req_id,))
    columns = [column[0] for column in cur.description]
    data = cur.fetchone()
    conn.close()
    
    if not data:
        return "Data not found.", 404

    data_dict = dict(zip(columns, data))

    # Generate PDF
    pdf_buffer = BytesIO()
    doc = SimpleDocTemplate(pdf_buffer, pagesize=letter, rightMargin=inch, leftMargin=inch, topMargin=inch, bottomMargin=inch)
    
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        name='TitleStyle',
        parent=styles['Heading1'],
        alignment=1, # Center
        spaceAfter=20,
        textColor=colors.HexColor('#1f2937')
    )
    
    subtitle_style = ParagraphStyle(
        name='SubtitleStyle',
        parent=styles['Heading3'],
        alignment=1, # Center
        spaceAfter=30,
        textColor=colors.HexColor('#4b5563')
    )
    
    normal_style = styles['Normal']

    elements = []
    
    # Title
    elements.append(Paragraph("BHIPL Operation Portal", title_style))
    elements.append(Paragraph(f"{req_type.capitalize()} Request - Approved Document", subtitle_style))

    # Construct Data Table
    table_data = []
    
    # Common fields
    table_data.append(["Request ID", f"{req_type.upper()}-{data_dict['RequestID']}"])
    table_data.append(["Employee Name", data_dict['EmployeeName']])
    table_data.append(["Employee ID", data_dict['EmployeeID']])
    table_data.append(["Department", data_dict['Department']])
    
    # Specific fields
    if req_type == 'leave':
        table_data.append(["Leave Type", data_dict['LeaveType']])
        table_data.append(["From Date", data_dict['FromDate'].strftime('%d-%m-%Y') if data_dict.get('FromDate') else ''])
        table_data.append(["To Date", data_dict['ToDate'].strftime('%d-%m-%Y') if data_dict.get('ToDate') else ''])
        table_data.append(["Total Days", str(data_dict['TotalDays'])])
    elif req_type == 'permission':
        table_data.append(["Date", data_dict['PermissionDate'].strftime('%d-%m-%Y') if data_dict.get('PermissionDate') else ''])
        table_data.append(["From Time", str(data_dict['FromTime'])])
        table_data.append(["To Time", str(data_dict['ToTime'])])
        table_data.append(["Duration", data_dict['TotalDuration']])
    elif req_type == 'outpass':
        table_data.append(["Date", data_dict['OutpassDate'].strftime('%d-%m-%Y') if data_dict.get('OutpassDate') else ''])
        table_data.append(["Out Time", str(data_dict['OutTime'])])
        table_data.append(["Return Time", str(data_dict['ExpectedReturnTime']) if data_dict.get('ExpectedReturnTime') else 'N/A'])
        table_data.append(["Destination", data_dict['Destination']])
        table_data.append(["Contact", data_dict['ContactNumber']])
        table_data.append(["Purpose", data_dict['Purpose']])
        
    table_data.append(["Reason/Purpose", data_dict['Reason'] if req_type != 'outpass' else data_dict['Purpose']])
    
    # Approvals
    table_data.append(["Status", data_dict['Status']])
    table_data.append(["HOD Remarks", data_dict['HODRemarks'] or ''])
    table_data.append(["Applied Date", data_dict['CreatedDate'].strftime('%d-%m-%Y %H:%M') if data_dict.get('CreatedDate') else ''])
    table_data.append(["Approval Date", data_dict['ActionDate'].strftime('%d-%m-%Y %H:%M') if data_dict.get('ActionDate') else ''])

    # Create Table
    t = Table(table_data, colWidths=[2*inch, 4*inch])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f8fafc')),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#1f2937')),
        ('ALIGN', (0, 0), (-1, -1), 'LEFT'),
        ('FONTNAME', (0, 0), (0, -1), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 1, colors.HexColor('#e5e7eb')),
    ]))
    
    elements.append(t)
    
    # Signatures
    elements.append(Spacer(1, 50))
    sig_data = [["_____________________", "_____________________"], 
                ["Employee Signature", "HOD Signature (Digital)"]]
    sig_t = Table(sig_data, colWidths=[3*inch, 3*inch])
    sig_t.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.HexColor('#4b5563')),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
    ]))
    elements.append(sig_t)

    doc.build(elements)
    
    pdf_buffer.seek(0)
    
    filename = f"{req_type.capitalize()}_{req_id}.pdf"
    return send_file(pdf_buffer, download_name=filename, as_attachment=True, mimetype='application/pdf')
""")
        
if __name__ == "__main__":
    append_pdf_route()
