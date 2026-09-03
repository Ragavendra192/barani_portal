from werkzeug.security import generate_password_hash
from db import get_connection

full_name = "Administrator"
emp_id = "admin"
email = "adminatbarani@gmail.com"
phone = "0000000000"
department = "IT"
dob = "2000-01-01"
password = "admin@123"
role = "admin"

password_hash = generate_password_hash(password)

conn = get_connection()
cur = conn.cursor()

cur.execute("""
    INSERT INTO users (full_name, emp_id, email, phone, department, dob, password_hash, role)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
""", full_name, emp_id, email, phone, department, dob, password_hash, role)

conn.commit()
conn.close()

print("Admin user created successfully!")
print("Employee ID:", emp_id)
print("Email:", email)
print("Password:", password)
