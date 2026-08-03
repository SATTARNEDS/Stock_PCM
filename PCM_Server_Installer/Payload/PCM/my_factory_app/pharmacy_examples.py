"""
PHARMACEUTICAL UNIT CONVERSION EXAMPLES
ตัวอย่างการตั้งค่าการแปลงหน่วยสำหรับสินค้าทั่วไปในห้องยา
"""

# ============================================================
# 💊 ตัวอย่าง Conversion Rates สำหรับยาทั่วไป
# ============================================================

PHARMACY_CONVERSIONS = {
    # ยาประเภทเม็ด
    'Aspirin': {
        'base_unit': 'tablet',
        'package_unit': 'bottle',
        'conversion_rate': 20,  # 1 ขวด = 20 เม็ด
        'description': 'สมปวด'
    },
    'Vitamin C': {
        'base_unit': 'tablet',
        'package_unit': 'sheet',
        'conversion_rate': 10,  # 1 แผง = 10 เม็ด
        'description': 'วิตามินซี'
    },
    'Antibiotics': {
        'base_unit': 'capsule',
        'package_unit': 'box',
        'conversion_rate': 30,  # 1 กล่อง = 30 แคปซูล
        'description': 'ปฏิชีวนะ'
    },
    
    # ยาประเภทน้ำ/ยาน้ำ
    'Syrup': {
        'base_unit': 'ml',
        'package_unit': 'bottle',
        'conversion_rate': 100,  # 1 ขวด = 100 มล (กำหนดเองตามขนาড)
        'description': 'ยาน้ำแก้ไอ'
    },
    'Injection': {
        'base_unit': 'ml',
        'package_unit': 'vial',
        'conversion_rate': 10,  # 1 ขวดเล็ก = 10 มล
        'description': 'ยาฉีด'
    },
    
    # ยาประเภทอื่น
    'Cream': {
        'base_unit': 'g',
        'package_unit': 'tube',
        'conversion_rate': 50,  # 1 หลอด = 50 กรัม
        'description': 'ครีมทาแผล'
    },
    'Ointment': {
        'base_unit': 'g',
        'package_unit': 'jar',
        'conversion_rate': 100,  # 1 โหล = 100 กรัม
        'description': 'ยาทายา'
    },
}

# ============================================================
# SQL: INSERT Sample Data
# ============================================================

"""
-- 1. Update products table with conversion info
UPDATE products SET 
    base_unit = 'tablet',
    package_unit = 'bottle',
    conversion_rate = 20
WHERE name LIKE '%Aspirin%';

-- 2. Insert into unit_conversions table
INSERT INTO unit_conversions (product_id, base_unit, package_unit, conversion_rate, created_at)
SELECT id, 'tablet', 'bottle', 20, datetime('now') 
FROM products WHERE name LIKE '%Aspirin%';

-- 3. More examples:
INSERT INTO unit_conversions (product_id, base_unit, package_unit, conversion_rate, created_at)
VALUES 
    ((SELECT id FROM products WHERE code = 'VIT-C'), 'tablet', 'sheet', 10, datetime('now')),
    ((SELECT id FROM products WHERE code = 'ANTIBIOTIC-1'), 'capsule', 'box', 30, datetime('now')),
    ((SELECT id FROM products WHERE code = 'SYRUP-1'), 'ml', 'bottle', 100, datetime('now'));
"""

# ============================================================
# 📋 EXAMPLE SCENARIOS
# ============================================================

"""
🔍 SCENARIO 1: Withdraw Aspirin Tablets from Multiple Packages
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Product: Aspirin (base_unit='tablet', package_unit='bottle', rate=20)
Current Stock: 5 bottles = 100 tablets
Open Box: 0 (no open package)

User Withdrawal Request: 23 tablets

Calculation:
  23 tablets ÷ 20 tablets/bottle = 1 bottle + 3 tablets
  
System Action:
  ✅ Take 1 full bottle (20 tablets)
  ✅ Open 1 new bottle, take 3 tablets (17 tablets remain in open_packages)
  
Result in Database:
  - products.stock: 5 → 4 (took 1 full bottle)
  - open_packages: INSERT (product_id, base_unit_qty=17, status='active')
  - transaction_logs: qty=1, qty_base_unit=23, qty_package_unit=1.15, 
                      note='เบิก 1 ขวดเต็ม + 1 ขวดเปิด (เหลือ 17 เม็ด)'

ผู้เบิกได้รับ: 23 เม็ด Aspirin ✓


🔍 SCENARIO 2: Withdraw Vitamin C - Using Existing Open Sheet
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Product: Vitamin C (base_unit='tablet', package_unit='sheet', rate=10)
Current Stock: 3 sheets (full) + 1 open sheet with 7 tablets
Open Box: 7 tablets (from previous withdrawal)

User Request: 12 tablets

Calculation:
  Strategy: Use open sheet first (FIFO for open boxes)
  - Take 7 tablets from open sheet
  - Still need: 12 - 7 = 5 tablets
  - Take 5 tablets from new sheet (5 tablets left in new open sheet)
  
System Action:
  ✅ Reduce open_packages: 7 → 0 (used all)
  ✅ Take 1 new sheet (10 tablets)
  ✅ Create new open_packages entry with 5 tablets
  
Result:
  - products.stock: 3 → 2
  - open_packages: 
    * Update existing: base_unit_qty = 0 (mark for cleanup)
    * Insert new: base_unit_qty = 5
  
ผู้เบิกได้รับ: 12 เม็ด Vitamin C ✓


🔍 SCENARIO 3: Insufficient Stock
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Product: Antibiotic (base_unit='capsule', package_unit='box', rate=30)
Stock: 0 boxes + 5 capsules in open box
Total: 35 capsules available

User Request: 40 capsules

System Response:
  ❌ ของไม่พอ: เหลือ 35 capsules, ต้องการ 40, ขาด 5 capsules
  
User Option: 
  - Proceed with 35 capsules
  - Cancel order and wait for restock


🔍 SCENARIO 4: Receive with Unit Conversion
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Purchase Order: 10 boxes of Antibiotic (1 box = 30 capsules)
PO total = 10 * 30 = 300 capsules

System Recording:
  qty_package_unit = 10 (boxes)
  qty_base_unit = 300 (capsules)
  products.stock += 10
  
Result:
  Display to user: "รับเข้า 10 box = 300 capsules ✓"
"""

# ============================================================
# ⚙️ Python Setup Function
# ============================================================

def setup_pharmacy_conversions(db_connection):
    """
    Initialize unit conversions for common pharmacy items
    Run once during system setup
    """
    cursor = db_connection.cursor()
    
    conversions = [
        # (product_code, base_unit, package_unit, conversion_rate)
        ('ASPIRIN-500MG', 'tablet', 'bottle', 20),
        ('VITC-500MG', 'tablet', 'sheet', 10),
        ('AMOXICILLIN-250MG', 'capsule', 'box', 30),
        ('COUGH-SYRUP', 'ml', 'bottle', 100),
        ('INJECTION-SALINE', 'ml', 'vial', 10),
        ('CREAM-ANTIBACTERIAL', 'g', 'tube', 50),
        ('OINTMENT-ZINC', 'g', 'jar', 100),
    ]
    
    for code, base_unit, package_unit, rate in conversions:
        try:
            # Get product id by code
            product = cursor.execute(
                'SELECT id FROM products WHERE code = ?', (code,)
            ).fetchone()
            
            if product:
                product_id = product[0]
                
                # Update products table
                cursor.execute('''
                    UPDATE products 
                    SET base_unit = ?, package_unit = ?, conversion_rate = ?
                    WHERE id = ?
                ''', (base_unit, package_unit, rate, product_id))
                
                # Insert into unit_conversions
                cursor.execute('''
                    INSERT INTO unit_conversions 
                    (product_id, base_unit, package_unit, conversion_rate, created_at)
                    VALUES (?, ?, ?, ?, datetime('now'))
                ''', (product_id, base_unit, package_unit, rate))
                
                print(f"✅ Setup {code}: 1 {package_unit} = {rate} {base_unit}")
        except Exception as e:
            print(f"❌ Error setting up {code}: {e}")
    
    db_connection.commit()
    print("✅ Pharmacy conversions initialized!")


# ============================================================
# 🧪 Test Cases
# ============================================================

def test_unit_conversion():
    """
    Test unit conversion logic
    Run: python pharmacy_examples.py
    """
    from unit_conversion import UnitConversionManager
    import sqlite3
    
    # Mock connection for testing
    conn = sqlite3.connect(':memory:')
    
    # Create sample tables
    conn.execute('''CREATE TABLE products (
        id INTEGER PRIMARY KEY,
        code TEXT,
        name TEXT,
        stock INTEGER,
        unit TEXT,
        base_unit TEXT,
        package_unit TEXT,
        conversion_rate REAL
    )''')
    
    conn.execute('''CREATE TABLE product_lots (
        id INTEGER PRIMARY KEY,
        product_id INTEGER,
        lot_number TEXT,
        qty INTEGER,
        received_date TEXT,
        expiry_date TEXT
    )''')
    
    conn.execute('''CREATE TABLE open_packages (
        id INTEGER PRIMARY KEY,
        product_id INTEGER,
        lot_id INTEGER,
        opened_date TEXT,
        base_unit_qty INTEGER,
        package_unit_qty_before REAL,
        status TEXT
    )''')
    
    conn.execute('''CREATE TABLE unit_conversions (
        id INTEGER PRIMARY KEY,
        product_id INTEGER,
        base_unit TEXT,
        package_unit TEXT,
        conversion_rate REAL,
        created_at TEXT,
        updated_at TEXT
    )''')
    
    conn.execute('''CREATE TABLE transaction_logs (
        id INTEGER PRIMARY KEY,
        emp_id TEXT,
        product_id INTEGER,
        lot_id INTEGER,
        action TEXT,
        qty INTEGER,
        qty_base_unit INTEGER,
        qty_package_unit REAL,
        note TEXT,
        timestamp TEXT
    )''')
    
    # Insert test data
    conn.execute('''INSERT INTO products VALUES
        (1, 'ASPIRIN-001', 'Aspirin', 5, 'bottle', 'tablet', 'bottle', 20)
    ''')
    
    conn.execute('''INSERT INTO unit_conversions VALUES
        (1, 1, 'tablet', 'bottle', 20, datetime('now'), NULL)
    ''')
    
    conn.commit()
    
    # Test
    manager = UnitConversionManager(conn)
    
    print("📋 TEST: Aspirin Withdrawal")
    print("─" * 50)
    
    # Test 1: Get product info
    info = manager.get_product_unit_info(1)
    print(f"Product: {info['name']}")
    print(f"Stock: {info['stock_package_unit']} {info['package_unit']}")
    print(f"     = {info['stock_base_unit']} {info['base_unit']}")
    
    # Test 2: Calculate withdrawal
    calc = manager.calculate_withdrawal(1, 23)
    print(f"\nWithdrawal 23 tablets:")
    print(f"  From open boxes: {calc['from_open_box']}")
    print(f"  Full packages: {calc['full_packages_needed']}")
    print(f"  New open package: {calc['new_open_box_qty']} tablets")
    print(f"  Total: {calc['total_packages_used']:.2f} bottles")
    print(f"  ✅ {calc['message']}")
    
    conn.close()


if __name__ == '__main__':
    test_unit_conversion()
