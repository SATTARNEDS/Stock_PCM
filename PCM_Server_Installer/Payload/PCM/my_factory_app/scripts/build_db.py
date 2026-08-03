import sqlite3
import pandas as pd
import os

# ==========================================
# ⚙️ ส่วนตั้งค่า
# ==========================================
EXCEL_FILENAME = 'DATA_JK.xlsx'
DB_NAME = 'factory_stock.db'

# Mapping คอลัมน์ (เหมือนเดิม)
COLUMN_MAPPING = {
    'code':          ['Code', 'รหัสสินค้า', 'รหัส', 'Part No'],
    'name':          ['Name', 'ชื่อสินค้า', 'รายการ', 'Description'],
    'category':      ['Category', 'หมวดหมู่', 'ประเภท'],
    'unit':          ['Unit', 'หน่วยนับ', 'หน่วย'],
    'location':      ['Location', 'สถานที่จัดเก็บ', 'ที่อยู่', 'Shelf'],
    'expiry_date':   ['ExpDate', 'Exp', 'วันหมดอายุ'],
    'received_date': ['Received Date', 'วันที่รับเข้า', 'Date received'],
    'price':         ['Price', 'ราคา', 'Cost', 'Unit Price'],
    'withdraw':      ['Withdraw', 'จำนวนเบิก', 'เบิก', 'Usage'],
    'receive':       ['Receive', 'จำนวนรับ', 'รับ'],
    'total':         ['Total', 'รวม'],
    'safety_stock':  ['Safety stock', 'Stock ปลอดภัย', 'Stock ขั้นต่ำ', 'safty stock'],
    'last_peak':     ['Last Peak', 'สูงสุด', 'Peak'],
    'stock':         ['Qty', 'In Stock', 'จำนวน', 'คงเหลือ', 'Balance']
}

def safe_int(value):
    try: return int(pd.to_numeric(value, errors='coerce') or 0)
    except: return 0

def safe_float(value):
    try: return float(pd.to_numeric(value, errors='coerce') or 0.0)
    except: return 0.0

def get_column_name(df_columns, possible_names):
    for col in df_columns:
        clean_col = str(col).strip()
        for name in possible_names:
            if clean_col.lower() == name.lower():
                return col
    return None

def create_database():
    if os.path.exists(DB_NAME):
        try:
            os.remove(DB_NAME)
            print(f"🗑️  ล้างข้อมูลเก่า: {DB_NAME}")
        except:
            print(f"❌ ลบไฟล์ไม่ได้ กรุณาปิดโปรแกรมอื่นก่อน")
            return None

    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()

    # 1. ตารางสินค้าหลัก
    c.execute('''
        CREATE TABLE products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE, 
            name TEXT, 
            category TEXT, 
            unit TEXT, 
            location TEXT,
            safety_stock INTEGER DEFAULT 0,
            stock INTEGER DEFAULT 0,
            price REAL DEFAULT 0,
            qr_code_path TEXT
        )
    ''')

    # 2. ตาราง Lot สินค้า (หัวใจของ FIFO)
    c.execute('''
        CREATE TABLE product_lots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER,
            lot_number TEXT,
            qty INTEGER DEFAULT 0,
            received_date TEXT,
            expiry_date TEXT,
            FOREIGN KEY (product_id) REFERENCES products (id)
        )
    ''')

    # 3. ตารางประวัติธุรกรรม (Transaction Logs)
    c.execute('''
        CREATE TABLE transaction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id TEXT,
            product_id INTEGER,
            lot_id INTEGER,
            action TEXT,
            qty INTEGER,
            status TEXT DEFAULT 'Pending',
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # 4. ตารางตะกร้าสินค้า (Carts)
    c.execute('''
        CREATE TABLE carts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            emp_id TEXT,
            product_id INTEGER,
            qty INTEGER,
            FOREIGN KEY (product_id) REFERENCES products (id)
        )
    ''')

    # 5. ตาราง Admin
    c.execute('''
        CREATE TABLE admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT, password TEXT, name TEXT,
            role TEXT DEFAULT 'superadmin' 
        )
    ''')
    
    # เพิ่ม Admin ตัวอย่าง
    c.execute("INSERT INTO admins (username, password, name, role) VALUES ('admin', '1234', 'Super Admin', 'superadmin')")
    c.execute("INSERT INTO admins (username, password, name, role) VALUES ('pc1', '1234', 'Admin PC1', 'admin_pc1')")
    c.execute("INSERT INTO admins (username, password, name, role) VALUES ('cc', '1234', 'Admin CC', 'admin_cc')")

    conn.commit()
    return conn

def import_data():
    if not os.path.exists(EXCEL_FILENAME):
        print(f"❌ ไม่พบไฟล์ {EXCEL_FILENAME}")
        return

    print(f"📊 กำลังอ่านไฟล์: {EXCEL_FILENAME}...")
    try:
        all_sheets = pd.read_excel(EXCEL_FILENAME, sheet_name=None)
        conn = create_database()
        if not conn: return
        c = conn.cursor()
        
        for sheet_name, df in all_sheets.items():
            df.columns = df.columns.str.strip()
            cols = {}
            for key, possible_names in COLUMN_MAPPING.items():
                cols[key] = get_column_name(df.columns, possible_names)

            if not cols['code']: continue

            for index, row in df.iterrows():
                code = str(row[cols['code']]).strip()
                if not code or code.lower() == 'nan': continue

                name = str(row[cols['name']]).strip() if cols['name'] else 'No Name'
                category = str(row[cols['category']]).strip() if cols['category'] else sheet_name
                unit = str(row[cols['unit']]).strip() if cols['unit'] else 'PCS'
                location = str(row[cols['location']]).strip() if cols['location'] else '-'
                
                expiry_date = str(row[cols['expiry_date']]) if cols['expiry_date'] and not pd.isna(row[cols['expiry_date']]) else None
                received_date = str(row[cols['received_date']]) if cols['received_date'] and not pd.isna(row[cols['received_date']]) else None
                
                price = safe_float(row[cols['price']]) if cols['price'] else 0.0
                withdraw = safe_int(row[cols['withdraw']]) if cols['withdraw'] else 0
                receive = safe_int(row[cols['receive']]) if cols['receive'] else 0
                total = safe_int(row[cols['total']]) if cols['total'] else 0
                safety_stock = safe_int(row[cols['safety_stock']]) if cols['safety_stock'] else 0
                last_peak = safe_int(row[cols['last_peak']]) if cols['last_peak'] else 0
                stock = safe_int(row[cols['stock']]) if cols['stock'] else 0

                c.execute('''
                    INSERT INTO products (
                        code, name, category, unit, location, 
                        expiry_date, received_date, price, 
                        withdraw, receive, total, safety_stock, last_peak, stock
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (code, name, category, unit, location, 
                      expiry_date, received_date, price, 
                      withdraw, receive, total, safety_stock, last_peak, stock))
        
        conn.commit()
        conn.close()
        print(f"\n🎉 สร้าง Database สำเร็จ! (อย่าลืมรัน import_employees.py ด้วย)")

    except Exception as e:
        print(f"\n❌ เกิดข้อผิดพลาด: {e}")

if __name__ == "__main__":
    import_data()