import sqlite3

DB_NAME = 'factory_stock.db'

def upgrade():
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    # 1. สร้างตาราง Lot สินค้า
    c.execute('''
        CREATE TABLE IF NOT EXISTS product_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            lot_number TEXT,
            qty INTEGER DEFAULT 0,
            received_date TEXT,
            expiry_date TEXT,
            FOREIGN KEY (product_id) REFERENCES products (id)
        )
    ''')
    # 2. เพิ่มคอลัมน์ lot_id ใน logs (ถ้ายังไม่มี)
    try:
        c.execute('ALTER TABLE transaction_logs ADD COLUMN lot_id INTEGER')
    except sqlite3.OperationalError: pass
    
    conn.commit()
    conn.close()
    print("✅ ฐานข้อมูลอัปเกรดเป็นระบบ FIFO เรียบร้อยแล้ว")

if __name__ == "__main__":
    upgrade()