import sqlite3
import pandas as pd
import os
from datetime import datetime

# ตั้งค่าไฟล์
EXCEL_FILENAME = 'DATA_JK.xlsx'
DB_NAME = 'factory_stock.db'

COLUMN_MAPPING = {
    'code':          ['Code', 'รหัสสินค้า', 'รหัส', 'Part No'],
    'name':          ['Name', 'ชื่อสินค้า', 'รายการ', 'Description'],
    'category':      ['Category', 'หมวดหมู่', 'ประเภท'],
    'unit':          ['Unit', 'หน่วยนับ', 'หน่วย'],
    'location':      ['Location', 'สถานที่จัดเก็บ', 'ที่อยู่', 'Shelf'],
    'expiry_date':   ['ExpDate', 'Exp', 'วันหมดอายุ'],
    'received_date': ['Received Date', 'วันที่รับเข้า', 'Date received'],
    'stock':         ['Qty', 'In Stock', 'จำนวน', 'คงเหลือ', 'Balance'],
    'safety_stock':  ['Safety stock', 'Stock ปลอดภัย', 'safty stock']
}

# --- ฟังก์ชันช่วยแปลงค่าให้ปลอดภัย ---
def safe_int(value):
    """ แปลงค่าเป็น int อย่างปลอดภัย ถ้าเป็น NaN หรือ error ให้คืนค่า 0 """
    val = pd.to_numeric(value, errors='coerce')
    if pd.isna(val):
        return 0
    return int(val)

def get_column_name(df_columns, possible_names):
    for col in df_columns:
        clean_col = str(col).strip()
        for name in possible_names:
            if clean_col.lower() == name.lower():
                return col
    return None

def import_to_fifo():
    if not os.path.exists(EXCEL_FILENAME):
        print(f"❌ ไม่พบไฟล์ {EXCEL_FILENAME}")
        return

    print(f"📊 กำลังเริ่มนำเข้าข้อมูลจาก {EXCEL_FILENAME}...")
    
    try:
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        all_sheets = pd.read_excel(EXCEL_FILENAME, sheet_name=None)
        
        today_str = datetime.now().strftime('%Y-%m-%d')
        total_products = 0
        total_lots = 0

        for sheet_name, df in all_sheets.items():
            df.columns = df.columns.str.strip()
            cols = {key: get_column_name(df.columns, names) for key, names in COLUMN_MAPPING.items()}

            if not cols['code']: continue

            for _, row in df.iterrows():
                code = str(row[cols['code']]).strip()
                if not code or code.lower() == 'nan': continue

                # ใช้ safe_int แทนการแปลงแบบปกติ
                name = str(row[cols['name']]).strip() if cols['name'] else 'No Name'
                category = str(row[cols['category']]).strip() if cols['category'] and not pd.isna(row[cols['category']]) else sheet_name
                unit = str(row[cols['unit']]).strip() if cols['unit'] else 'PCS'
                location = str(row[cols['location']]).strip() if cols['location'] else '-'
                
                stock = safe_int(row[cols['stock']]) if cols['stock'] else 0
                safety = safe_int(row[cols['safety_stock']]) if cols['safety_stock'] else 0
                
                r_date = str(row[cols['received_date']]) if cols['received_date'] and not pd.isna(row[cols['received_date']]) else today_str
                e_date = str(row[cols['expiry_date']]) if cols['expiry_date'] and not pd.isna(row[cols['expiry_date']]) else None

                # บันทึกข้อมูล
                c.execute('''
                    INSERT INTO products (code, name, category, unit, location, stock, safety_stock)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (code, name, category, unit, location, stock, safety))
                
                product_id = c.lastrowid
                total_products += 1

                if stock > 0:
                    c.execute('''
                        INSERT INTO product_lots (product_id, lot_number, qty, received_date, expiry_date)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (product_id, 'INITIAL-LOT', stock, r_date, e_date))
                    total_lots += 1

        conn.commit()
        conn.close()
        print(f"\n🎉 นำเข้าสำเร็จ! เพิ่มสินค้า {total_products} รายการ และ {total_lots} ล็อต")

    except Exception as e:
        print(f"❌ เกิดข้อผิดพลาด: {e}")

if __name__ == "__main__":
    import_to_fifo()