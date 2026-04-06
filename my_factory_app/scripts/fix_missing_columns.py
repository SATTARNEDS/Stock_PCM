import sqlite3

DB_NAME = 'factory_stock.db'

def fix_database():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    print("🛠️ กำลังตรวจสอบและซ่อมแซมคอลัมน์ที่ขาดหายไป...")

    # รายชื่อคอลัมน์ที่ระบบใหม่ต้องการแต่ระบบเก่าอาจไม่มี
    missing_columns = [
        ('expiry_date', 'TEXT'),
        ('received_date', 'TEXT'),
        ('received_date_2', 'TEXT'),
        ('expiry_date_2', 'TEXT'),
        ('qr_code_path', 'TEXT')
    ]

    for col_name, col_type in missing_columns:
        try:
            # พยายามเพิ่มคอลัมน์
            c.execute(f'ALTER TABLE products ADD COLUMN {col_name} {col_type}')
            print(f"✅ เพิ่มคอลัมน์ '{col_name}' สำเร็จ")
        except sqlite3.OperationalError:
            # ถ้ามีคอลัมน์อยู่แล้วจะเกิด Error นี้ ให้ข้ามไป
            print(f"ℹ️ คอลัมน์ '{col_name}' มีอยู่แล้วในระบบ")

    conn.commit()
    conn.close()
    print("🎉 ซ่อมแซมฐานข้อมูลเสร็จสมบูรณ์! ลองรันโปรแกรมอีกครั้ง")

if __name__ == "__main__":
    fix_database()