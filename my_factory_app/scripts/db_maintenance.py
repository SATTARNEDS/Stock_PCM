import sqlite3
from datetime import datetime, timedelta

DB_NAME = 'factory_stock.db'

def run_maintenance():
    # 1. จัดการข้อมูล (Move to Archive)
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    
    print("🧹 เริ่มกระบวนการ Maintenance ฐานข้อมูล...")

    # สร้างตาราง Archive ถ้ายังไม่มี
    c.execute('''
        CREATE TABLE IF NOT EXISTS transaction_logs_archive AS 
        SELECT * FROM transaction_logs WHERE 1=0
    ''')

    retention_days = 365
    cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime('%Y-%m-%d %H:%M:%S')
    
    # ย้ายประวัติเก่า
    c.execute('''
        INSERT INTO transaction_logs_archive 
        SELECT * FROM transaction_logs 
        WHERE (status = 'Approved' OR status = 'Rejected' OR status = 'Completed') 
        AND timestamp < ?
    ''', (cutoff_date,))
    
    moved_count = c.rowcount

    # ลบข้อมูลออกจากตารางหลัก
    c.execute('''
        DELETE FROM transaction_logs 
        WHERE (status = 'Approved' OR status = 'Rejected' OR status = 'Completed') 
        AND timestamp < ?
    ''', (cutoff_date,))

    conn.commit()
    conn.close() # ต้องปิดการเชื่อมต่อนี้ก่อนเพื่อให้ Transaction จบลงจริงๆ
    print(f"✅ ย้ายข้อมูลประวัติเก่า {moved_count} รายการไปยัง Archive สำเร็จ")

    # 2. ทำการ VACUUM (บีบอัดฐานข้อมูล)
    try:
        # เปิดการเชื่อมต่อใหม่แบบพิเศษเพื่อรัน VACUUM โดยเฉพาะ
        conn_v = sqlite3.connect(DB_NAME)
        conn_v.isolation_level = None # ตั้งค่าเป็น Autocommit mode
        conn_v.execute("VACUUM")
        conn_v.close()
        print("✨ บีบอัดฐานข้อมูล (VACUUM) เรียบร้อย")
    except Exception as e:
        print(f"⚠️ ไม่สามารถรัน VACUUM ได้: {e}")

if __name__ == "__main__":
    run_maintenance()