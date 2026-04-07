-- ✅ PHARMACEUTICAL UNIT CONVERSION SCHEMA
-- ปัญหา: จัดเก็บเป็น ขวด/แผง/กระปุก แต่เบิกเป็นเม็ด
-- วิธีแก้: เพิ่มตาราง unit_conversions และ open_boxes tracking

-- ============================================
-- 1. เพิ่มคอลัมน์ใหม่ให้ products table
-- ============================================
ALTER TABLE products ADD COLUMN base_unit TEXT DEFAULT 'tablet';  -- หน่วยพื้นฐาน (tablet, pill, ml, etc.)
ALTER TABLE products ADD COLUMN package_unit TEXT;                 -- หน่วยบรรจุ (bottle, sheet, box, etc.)
ALTER TABLE products ADD COLUMN conversion_rate REAL DEFAULT 1;    -- 1 package_unit = กี่ base_unit

-- ตัวอย่าง:
-- Aspirin: base_unit='tablet', package_unit='bottle', conversion_rate=20 (1 bottle = 20 tablets)
-- Vitamin C: base_unit='tablet', package_unit='sheet', conversion_rate=10 (1 sheet = 10 tablets)

-- ============================================
-- 2. ตารางจัดเก็บ Opened Boxes (ขวดที่เปิดไปแล้ว)
-- ============================================
CREATE TABLE IF NOT EXISTS open_packages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL,
    lot_id INTEGER,
    opened_date TEXT,
    base_unit_qty INTEGER DEFAULT 0,  -- จำนวนเม็ดที่เหลือในขวดที่เปิดแล้ว
    package_unit_qty_before REAL DEFAULT 1,  -- กี่ขวด (ถ้า = 0.5 แปลว่า ขวดแบ่งไป 50%)
    status TEXT DEFAULT 'active',  -- 'active', 'archived'
    FOREIGN KEY (product_id) REFERENCES products(id),
    FOREIGN KEY (lot_id) REFERENCES product_lots(id)
);

-- ============================================
-- 3. ตาราง Unit Conversions (สำหรับดูแบบ master data)
-- ============================================
CREATE TABLE IF NOT EXISTS unit_conversions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL UNIQUE,
    base_unit TEXT NOT NULL,          -- tablet, pill, ml, capsule
    package_unit TEXT NOT NULL,       -- bottle, sheet, box, sachet
    conversion_rate REAL NOT NULL,    -- 1 package = กี่ base units
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY (product_id) REFERENCES products(id)
);

-- ============================================
-- 4. แก้ไข transaction_logs ให้บันทึก unit info
-- ============================================
ALTER TABLE transaction_logs ADD COLUMN qty_base_unit INTEGER;     -- จำนวน base_unit (เม็ด)
ALTER TABLE transaction_logs ADD COLUMN qty_package_unit REAL;     -- จำนวน package_unit (ขวด)
ALTER TABLE transaction_logs ADD COLUMN note TEXT;                 -- Note: "Opened 1 box, took 3 tablets"

-- ============================================
-- ข้อมูลตัวอย่าง
-- ============================================
-- INSERT INTO unit_conversions (product_id, base_unit, package_unit, conversion_rate, created_at)
-- VALUES 
-- (1, 'tablet', 'bottle', 20, datetime('now')),    -- Aspirin: 1 bottle = 20 tablets
-- (2, 'tablet', 'sheet', 10, datetime('now')),     -- Vitamin C: 1 sheet = 10 tablets
-- (3, 'ml', 'bottle', 100, datetime('now'));       -- Syrup: 1 bottle = 100 ml
