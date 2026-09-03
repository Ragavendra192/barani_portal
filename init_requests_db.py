import pyodbc
from db import get_connection

def init_requests_db():
    conn = get_connection()
    cur = conn.cursor()

    try:
        # 1. Leave_Request
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Leave_Request' AND xtype='U')
            BEGIN
                CREATE TABLE Leave_Request (
                    RequestID INT IDENTITY(1,1) PRIMARY KEY,
                    EmployeeID INT NOT NULL,
                    EmployeeName NVARCHAR(100) NOT NULL,
                    Department NVARCHAR(100) NOT NULL,
                    LeaveType NVARCHAR(50) NOT NULL,
                    FromDate DATE NOT NULL,
                    ToDate DATE NOT NULL,
                    TotalDays INT NOT NULL,
                    Reason NVARCHAR(MAX) NOT NULL,
                    AttachmentPath NVARCHAR(255),
                    Status NVARCHAR(50) DEFAULT 'Pending HOD Approval',
                    HODID INT,
                    HODRemarks NVARCHAR(MAX),
                    CreatedDate DATETIME DEFAULT GETDATE(),
                    ActionDate DATETIME
                )
            END
        """)

        # 2. Permission_Request
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Permission_Request' AND xtype='U')
            BEGIN
                CREATE TABLE Permission_Request (
                    RequestID INT IDENTITY(1,1) PRIMARY KEY,
                    EmployeeID INT NOT NULL,
                    EmployeeName NVARCHAR(100) NOT NULL,
                    Department NVARCHAR(100) NOT NULL,
                    PermissionDate DATE NOT NULL,
                    FromTime TIME NOT NULL,
                    ToTime TIME NOT NULL,
                    TotalDuration NVARCHAR(50) NOT NULL,
                    Reason NVARCHAR(MAX) NOT NULL,
                    Status NVARCHAR(50) DEFAULT 'Pending HOD Approval',
                    HODID INT,
                    HODRemarks NVARCHAR(MAX),
                    CreatedDate DATETIME DEFAULT GETDATE(),
                    ActionDate DATETIME
                )
            END
        """)

        # 3. Outpass_Request
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Outpass_Request' AND xtype='U')
            BEGIN
                CREATE TABLE Outpass_Request (
                    RequestID INT IDENTITY(1,1) PRIMARY KEY,
                    EmployeeID INT NOT NULL,
                    EmployeeName NVARCHAR(100) NOT NULL,
                    Department NVARCHAR(100) NOT NULL,
                    OutpassDate DATE NOT NULL,
                    OutTime TIME NOT NULL,
                    ExpectedReturnTime TIME,
                    Destination NVARCHAR(255) NOT NULL,
                    Purpose NVARCHAR(MAX) NOT NULL,
                    ContactNumber NVARCHAR(20) NOT NULL,
                    Status NVARCHAR(50) DEFAULT 'Pending HOD Approval',
                    HODID INT,
                    HODRemarks NVARCHAR(MAX),
                    CreatedDate DATETIME DEFAULT GETDATE(),
                    ActionDate DATETIME
                )
            END
        """)

        # 4. Request_AuditLog
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Request_AuditLog' AND xtype='U')
            BEGIN
                CREATE TABLE Request_AuditLog (
                    LogID INT IDENTITY(1,1) PRIMARY KEY,
                    UserID INT,
                    EmployeeID INT,
                    Department NVARCHAR(100),
                    RequestType NVARCHAR(50) NOT NULL, -- 'Leave', 'Permission', 'Outpass'
                    Action NVARCHAR(100) NOT NULL, -- e.g., 'Leave Submitted', 'HOD Approved'
                    PerformedBy NVARCHAR(100) NOT NULL,
                    Remarks NVARCHAR(MAX),
                    DateTime DATETIME DEFAULT GETDATE()
                )
            END
        """)

        conn.commit()
        print("Request Management Module tables initialized successfully!")

    except Exception as e:
        conn.rollback()
        print(f"Error initializing tables: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    init_requests_db()
