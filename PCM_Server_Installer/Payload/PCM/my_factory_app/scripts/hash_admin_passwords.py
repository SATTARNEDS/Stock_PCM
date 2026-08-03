import sqlite3
from werkzeug.security import generate_password_hash

DB_NAME = 'factory_stock.db'

def hash_passwords():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    # ดึงข้อมูล Admin ทั้งหมด
    admins = c.execute('SELECT id, password FROM admins').fetchall()
    
    for admin_id, plain_password in admins:
        # ตรวจสอบว่ารหัสผ่านถูก Hash ไปหรือยัง (รหัส Hash จะขึ้นต้นด้วย pbkdf2: หรือ scrypt:)
        if not plain_password.startswith(('pbkdf2:', 'scrypt:')):
            hashed_pw = generate_password_hash(plain_password)
            c.execute('UPDATE admins SET password = ? WHERE id = ?', (hashed_pw, admin_id))
            print(f"✅ อัปเดตรหัสผ่าน Admin ID: {admin_id} เรียบร้อย")

    conn.commit()
    conn.close()
    print("🎉 แปลงรหัสผ่านทั้งหมดเป็นระบบ Hashed สำเร็จ!")

if __name__ == "__main__":
    hash_passwords()