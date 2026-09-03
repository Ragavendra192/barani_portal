import pyodbc
from db import get_connection

def init_db():
    conn = get_connection()
    cur = conn.cursor()
    
    # 1. Create Supervisor_Master table
    cur.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Supervisor_Master' AND schema_id = SCHEMA_ID('dbo'))
        BEGIN
            CREATE TABLE dbo.Supervisor_Master (
                SupervisorID INT IDENTITY(1,1) PRIMARY KEY,
                EmployeeID VARCHAR(50) NOT NULL UNIQUE,
                SupervisorName NVARCHAR(100) NOT NULL,
                EmailID VARCHAR(100),
                Department NVARCHAR(100) NOT NULL,
                AssignedDate DATETIME DEFAULT GETDATE()
            );
            PRINT 'Created Supervisor_Master table';
        END
    """)
    
    # 2. Create HOD_Master table
    cur.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'HOD_Master' AND schema_id = SCHEMA_ID('dbo'))
        BEGIN
            CREATE TABLE dbo.HOD_Master (
                HODID INT IDENTITY(1,1) PRIMARY KEY,
                EmployeeID VARCHAR(50) NOT NULL UNIQUE,
                EmployeeName NVARCHAR(100) NOT NULL,
                EmailID VARCHAR(100),
                Department NVARCHAR(100) NOT NULL,
                AssignedDate DATETIME DEFAULT GETDATE()
            );
            PRINT 'Created HOD_Master table';
        END
    """)
    
    # 3. Create LeaveRequests table
    cur.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'LeaveRequests' AND schema_id = SCHEMA_ID('dbo'))
        BEGIN
            CREATE TABLE dbo.LeaveRequests (
                LeaveID INT IDENTITY(1,1) PRIMARY KEY,
                EmployeeID VARCHAR(50) NOT NULL,
                EmployeeName NVARCHAR(100) NOT NULL,
                Department NVARCHAR(100) NOT NULL,
                SupervisorID VARCHAR(50),
                HODID VARCHAR(50),
                LeaveType NVARCHAR(50) NOT NULL,
                FromDate DATE NOT NULL,
                ToDate DATE NOT NULL,
                TotalDays INT NOT NULL,
                Reason NVARCHAR(MAX) NOT NULL,
                AttachmentPath NVARCHAR(500),
                Status VARCHAR(50) NOT NULL DEFAULT 'Pending Supervisor Approval',
                SupervisorStatus VARCHAR(50) DEFAULT 'Pending',
                SupervisorRemarks NVARCHAR(MAX),
                SupervisorApprovedDate DATETIME,
                HODStatus VARCHAR(50) DEFAULT 'Pending',
                HODRemarks NVARCHAR(MAX),
                HODApprovedDate DATETIME,
                CreatedDate DATETIME DEFAULT GETDATE()
            );
            PRINT 'Created LeaveRequests table';
        END
    """)
    
    # 4. Create Notifications table
    cur.execute("""
        IF NOT EXISTS (SELECT * FROM sys.tables WHERE name = 'Notifications' AND schema_id = SCHEMA_ID('dbo'))
        BEGIN
            CREATE TABLE dbo.Notifications (
                NotificationID INT IDENTITY(1,1) PRIMARY KEY,
                EmployeeID VARCHAR(50) NOT NULL,
                Message NVARCHAR(MAX) NOT NULL,
                IsRead BIT DEFAULT 0,
                CreatedDate DATETIME DEFAULT GETDATE()
            );
            PRINT 'Created Notifications table';
        END
    """)
    
    conn.commit()
    conn.close()
    print("Database initialization complete.")

if __name__ == '__main__':
    init_db()
