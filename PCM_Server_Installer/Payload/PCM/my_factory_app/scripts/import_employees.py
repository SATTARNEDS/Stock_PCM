import sqlite3
import pandas as pd
import os
import traceback

# ==========================================
# ⚙️ ส่วนตั้งค่า
# ==========================================
EXCEL_FILENAME = 'Staff working update 6-2-2026.xlsx'
DB_NAME = 'factory_stock.db'

# ตรวจสอบว่าชื่อ Key ตรงกับที่ใช้ใน Loop ด้านล่างเป๊ะๆ
COLUMN_MAPPING = {
    'emp_id':   ['ID', 'รหัส', 'รหัสพนักงาน','CODE', 'Code'],
    'name':     ['NAME', 'ชื่อ', 'ชื่อ-นามสกุล', 'Employee Name', 'Full Name'],
    'name_eng': ['NAME_ENG', 'ชื่อภาษาอังกฤษ', 'English Name', 'Name (Eng)'],
    'position': ['POSITION', 'ตำแหน่ง', 'Position', 'Job Title'],
    'location': ['LOCATION', 'สถานที่', 'Location', 'Site', 'Area'],
    'dept':     ['Dept', 'แผนก', 'Department', 'SECTION', 'ฝ่าย']
}

def get_column_name(df_columns, possible_names):
    """ฟังก์ชันค้นหาชื่อคอลัมน์ที่มีอยู่จริงใน Excel"""
    for col in df_columns:
        clean_col = str(col).strip()
        for name in possible_names:
            if clean_col.lower() == name.lower():
                return col
    return None

def create_users_table():
    """สร้างตาราง Users ใหม่ (ต้องมี last_seen เพื่อรองรับ app.py เวอร์ชันใหม่)"""
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DROP TABLE IF EXISTS users") 
    c.execute('''
        CREATE TABLE users (
            emp_id TEXT PRIMARY KEY,
            name TEXT,
            name_eng TEXT,
            position TEXT,
            location TEXT,
            department TEXT,
            is_locked INTEGER DEFAULT 0,
            last_seen DATETIME, -- 🕒 จำเป็นสำหรับระบบ Auto Unlock
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    print("✅ สร้างตาราง users (พร้อมคอลัมน์ last_seen) เรียบร้อยแล้ว")

def import_employees():
    if not os.path.exists(EXCEL_FILENAME):
        print(f"❌ ไม่พบไฟล์ {EXCEL_FILENAME}")
        return

    print(f"👥 กำลังอ่านไฟล์พนักงาน: {EXCEL_FILENAME}...")
    
    try:
        create_users_table()
        conn = sqlite3.connect(DB_NAME)
        c = conn.cursor()
        
        # อ่านไฟล์ Excel
        try:
            all_sheets = pd.read_excel(EXCEL_FILENAME, sheet_name=None, header=0)
        except Exception as e:
            print(f"❌ อ่านไฟล์ Excel ไม่ได้: {e}")
            return

        total_count = 0
        
        for sheet_name, df in all_sheets.items():
            print(f"   📂 กำลังประมวลผลชีท: {sheet_name}...")
            
            # ลบช่องว่างหัวตาราง
            df.columns = df.columns.astype(str).str.strip()
            
            # 🔍 Map ชื่อคอลัมน์
            cols = {}
            for key, possible_names in COLUMN_MAPPING.items():
                cols[key] = get_column_name(df.columns, possible_names)

            # เช็คว่ามีคอลัมน์รหัสพนักงานไหม
            if not cols['emp_id']:
                print(f"      ⚠️ ข้ามชีท '{sheet_name}' (ไม่พบคอลัมน์รหัสพนักงาน)")
                continue

            sheet_count = 0
            for index, row in df.iterrows():
                try:
                    # ดึงข้อมูลโดยใช้ .get เพื่อความปลอดภัย (แม้จริงๆ เราเช็คข้างบนแล้ว)
                    emp_id = str(row[cols['emp_id']]).strip()
                    
                    if not emp_id or emp_id.lower() in ['nan', 'code', 'รหัสพนักงาน']: 
                        continue

                    # ใช้ Logic เดิมในการดึงค่า
                    name = str(row[cols['name']]).strip() if cols['name'] else '-'
                    name_eng = str(row[cols['name_eng']]).strip() if cols['name_eng'] else '-'
                    position = str(row[cols['position']]).strip() if cols['position'] else '-'
                    dept = str(row[cols['dept']]).strip() if cols['dept'] else '-'
                    
                    # ถ้าไม่มีคอลัมน์ Location ให้ใช้ชื่อ Sheet แทน
                    location = str(row[cols['location']]).strip() if cols['location'] else sheet_name

                    c.execute('''
                        INSERT INTO users (emp_id, name, name_eng, position, location, department, is_locked)
                        VALUES (?, ?, ?, ?, ?, ?, 0)
                    ''', (emp_id, name, name_eng, position, location, dept))
                    sheet_count += 1
                    
                except Exception as row_err:
                    print(f"      ⚠️ Error ที่แถว {index}: {row_err}")
                    continue

            print(f"      ✅ นำเข้าสำเร็จ {sheet_count} คน")
            total_count += sheet_count

        conn.commit()
        conn.close()
        print(f"\n🎉 สรุป: นำเข้าพนักงานทั้งหมด {total_count} คน เรียบร้อยแล้ว!")

    except Exception as e:
        print(f"\n❌ เกิดข้อผิดพลาดหลัก: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    import_employees()