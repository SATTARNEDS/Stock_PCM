import sqlite3

def fix_schema():
    conn = sqlite3.connect('factory_stock.db')
    c = conn.cursor()
    try:
        # เพิ่มคอลัมน์ reserved_stock สำหรับเก็บยอดจองในตะกร้า
        c.execute('ALTER TABLE products ADD COLUMN reserved_stock INTEGER DEFAULT 0')
        # เพิ่มคอลัมน์ withdraw สำหรับเก็บยอดเบิกสะสม (ใช้ใน Analytics)
        c.execute('ALTER TABLE products ADD COLUMN withdraw INTEGER DEFAULT 0')
        conn.commit()
        print("✅ เพิ่มคอลัมน์ reserved_stock และ withdraw เรียบร้อยแล้ว")
    except sqlite3.OperationalError as e:
        print(f"ℹ️ แจ้งเตือน: {e} (อาจมีคอลัมน์อยู่แล้ว)")
    finally:
        conn.close()

if __name__ == "__main__":
    fix_schema()