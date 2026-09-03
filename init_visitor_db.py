import pyodbc
from db import get_connection

def init_visitor_db():
    conn = get_connection()
    cur = conn.cursor()

    try:
        # Create Visitor_Master Table
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Visitor_Master' and xtype='U')
            BEGIN
                CREATE TABLE Visitor_Master (
                    VisitorID INT IDENTITY(1,1) PRIMARY KEY,
                    VisitorName NVARCHAR(150) NOT NULL,
                    MobileNumber NVARCHAR(20) NOT NULL,
                    Email NVARCHAR(150),
                    CompanyName NVARCHAR(150),
                    Address NVARCHAR(255),
                    IDType NVARCHAR(50),
                    IDNumber NVARCHAR(100),
                    PhotoPath NVARCHAR(255),
                    CreatedAt DATETIME DEFAULT GETDATE()
                )
            END
        """)

        # Create Visitor_Request Table
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Visitor_Request' and xtype='U')
            BEGIN
                CREATE TABLE Visitor_Request (
                    RequestID INT IDENTITY(1,1) PRIMARY KEY,
                    VisitorID INT FOREIGN KEY REFERENCES Visitor_Master(VisitorID),
                    EmployeeToMeet NVARCHAR(150) NOT NULL,
                    EmployeeID INT,
                    Department NVARCHAR(100),
                    PurposeOfVisit NVARCHAR(255),
                    VisitDate DATE NOT NULL,
                    ExpectedInTime TIME,
                    ExpectedOutTime TIME,
                    VehicleNumber NVARCHAR(50),
                    Status NVARCHAR(50) DEFAULT 'Pending HR Approval', -- 'Pending HR Approval', 'Approved', 'Rejected'
                    PassNumber NVARCHAR(50),
                    HRRemarks NVARCHAR(MAX),
                    CreatedBy INT, -- User ID of the security officer who created it
                    CreatedAt DATETIME DEFAULT GETDATE(),
                    UpdatedAt DATETIME DEFAULT GETDATE()
                )
            END
        """)

        # Create Visitor_EntryExit Table
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Visitor_EntryExit' and xtype='U')
            BEGIN
                CREATE TABLE Visitor_EntryExit (
                    EntryID INT IDENTITY(1,1) PRIMARY KEY,
                    RequestID INT FOREIGN KEY REFERENCES Visitor_Request(RequestID),
                    ActualInTime DATETIME,
                    ActualOutTime DATETIME,
                    SecurityOfficerIn INT, -- User ID of officer who logged entry
                    SecurityOfficerOut INT, -- User ID of officer who logged exit
                    GateNumber NVARCHAR(50),
                    Status NVARCHAR(50) DEFAULT 'Entered' -- 'Entered', 'Exited'
                )
            END
        """)

        # Create Visitor_AuditLog Table
        cur.execute("""
            IF NOT EXISTS (SELECT * FROM sysobjects WHERE name='Visitor_AuditLog' and xtype='U')
            BEGIN
                CREATE TABLE Visitor_AuditLog (
                    LogID INT IDENTITY(1,1) PRIMARY KEY,
                    RequestID INT, -- Not forcing FK here in case request is deleted, though usually soft delete is better
                    UserID INT,
                    UserName NVARCHAR(150),
                    Role NVARCHAR(50),
                    Action NVARCHAR(255), -- e.g., 'Visitor Created', 'HR Approved'
                    DateTime DATETIME DEFAULT GETDATE()
                )
            END
        """)

        conn.commit()
        print("Visitor Management tables initialized successfully!")

    except Exception as e:
        conn.rollback()
        print(f"Error initializing Visitor Management tables: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    init_visitor_db()
