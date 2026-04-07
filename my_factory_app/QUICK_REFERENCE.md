"""
🏥 PHARMACEUTICAL INVENTORY UNIT CONVERSION - QUICK REFERENCE
Stock_PCM ระบบจัดเก็บยาอัจฉริยะ
"""

# ============================================================
# ❓ ปัญหา
# ============================================================
❌ เบิกยาเป็นเม็ด แต่จัดเก็บเป็น ขวด/แผง/กระปุก
   เช่น เบิก 23 เม็ด Aspirin แต่มีแค่ขวด (1 ขวด = 20 เม็ด)
   → เต้องเบิก 2 ขวด แล้ว 1 ขวดจะเปิดแบ่ง

# ============================================================
# ✅ วิธีแก้
# ============================================================

📦 สร้าง "Unit Conversion System"
├─ base_unit = หน่วยเล็กเด (เม็ด) 
├─ package_unit = หน่วยบรรจุ (ขวด)
├─ conversion_rate = 1 ขวด = กี่ เม็ด
├─ open_packages = ขวดที่เปิดไปแล้ว (เก็บจำนวนเม็ดที่เหลือ)
└─ transaction_logs = บันทึกรายละเอียด

# ============================================================
# 🛠️ ขั้นการติดตั้ง
# ============================================================

STEP 1: เพิ่มไฟล์ใหม่
├─ unit_conversion.py         ← โปรแกรมสำคัญ!
├─ pharmacy_examples.py       ← ตัวอย่าง
├─ add_unit_conversion.sql    ← SQL updates
└─ UNIT_CONVERSION_INTEGRATION.md ← วิธีใช้

STEP 2: ปรับปรุง Database
  1. เพิ่มคอลัมน์ใหม่ใน products table:
     - base_unit (เม็ด)
     - package_unit (ขวด)
     - conversion_rate (20)
  
  2. สร้าง 3 ตารางใหม่:
     - unit_conversions
     - open_packages
     - transaction_logs (update)

STEP 3: อัปเดต app.py
  - Import: from unit_conversion import UnitConversionManager
  - เปลี่ยน add_to_cart() route
  - เพิ่ม confirm_withdrawal_with_units() route
  - เพิ่ม /api/preview_withdrawal endpoint

STEP 4: ปรับปรุง HTML Templates
  - แสดง base_unit และ package_unit
  - เพิ่ม selector (เม็ด vs ขวด)
  - Show preview ของการแปลง

# ============================================================
# 💡 ตัวอย่างการใช้
# ============================================================

🔹 SCENARIO 1: เบิก 23 เม็ด Aspirin
   👤 ผู้ใช้: "เบิก Aspirin 23 เม็ด"
   🤖 ระบบ: 
      ✓ Check: มี 5 ขวด = 100 เม็ด → มีพอ ✓
      ✓ Calculate: 23 ÷ 20 = 1 ขวดเต็ม + 3 เม็ด
      ✓ Action: 
         - เบิก 1 ขวด
         - เปิด 1 ขวด, เบิก 3 เม็ด (เหลือ 17 เม็ด)
      ✓ Update: stock 5 → 4, open_packages INSERT (17 เม็ด)
   ✅ ผลลัพธ์: "ได้รับ 23 เม็ด Aspirin"

🔹 SCENARIO 2: เบิกต่อจากเดิม 12 เม็ด Vitamin C
   👤 ผู้ใช้: "เบิก Vitamin C 12 เม็ด"
   🤖 ระบบ:
      ✓ Check: 3 แผง + 1 แผงเปิดมี 7 เม็ด = 37 เม็ด → มีพอ ✓
      ✓ Strategy: ใช้ open sheet ก่อน (FIFO)
         - เบิก 7 เม็ด จากแผงเปิด
         - ต้อง 5 เม็ดอีก → เปิดแผงใหม่ (เหลือ 5 เม็ด)
      ✓ Update: stock 3 → 2, open_packages UPDATE + INSERT
   ✅ ผลลัพธ์: "ได้รับ 12 เม็ด Vitamin C"

🔹 SCENARIO 3: ของไม่พอ
   👤 ผู้ใช้: "เบิก 50 เม็ด"
   🤖 ระบบ:
      ❌ Check: มี 35 เม็ด → ไม่พอ!
      ✗ Message: "❌ ของไม่พอ: เหลือ 35 เม็ด, ต้องการ 50, ขาด 15"
   ❌ ผลลัพธ์: ไม่สามารถเบิก

# ============================================================
# 📊 ตัวอย่าง Database
# ============================================================

products table:
┌─────┬──────────┬────────────┬──────────────┬──────────────┬─────────────────┐
│ id  │ name     │ stock      │ base_unit    │ package_unit │ conversion_rate │
├─────┼──────────┼────────────┼──────────────┼──────────────┼─────────────────┤
│ 1   │ Aspirin  │ 4          │ tablet       │ bottle       │ 20              │
│ 2   │ Vit C    │ 2          │ tablet       │ sheet        │ 10              │
│ 3   │ Syrup    │ 5          │ ml           │ bottle       │ 100             │
└─────┴──────────┴────────────┴──────────────┴──────────────┴─────────────────┘

open_packages table:
┌────┬────────────┬───────────────┬──────────────┬─────────┐
│ id │ product_id │ base_unit_qty │ opened_date  │ status  │
├────┼────────────┼───────────────┼──────────────┼─────────┤
│ 1  │ 1          │ 17            │ 2026-04-07   │ active  │
│ 2  │ 2          │ 5             │ 2026-04-07   │ active  │
└────┴────────────┴───────────────┴──────────────┴─────────┘

transaction_logs (updated):
┌────┬─────────┬────────────┬─────────────────┬──────────────────┐
│ id │ action  │ qty        │ qty_base_unit   │ note             │
├────┼─────────┼────────────┼─────────────────┼──────────────────┤
│ 1  │ withdraw│ 1          │ 23              │ 1 bottle + 3 tab │
│ 2  │ withdraw│ 1          │ 12              │ 1 sheet + 2 tab  │
└────┴─────────┴────────────┴─────────────────┴──────────────────┘

# ============================================================
# 🔧 API Endpoints (ใหม่)
# ============================================================

1️⃣ /api/preview_withdrawal (POST)
   Request: { product_id: 1, qty_base_unit: 23 }
   Response: {
     "success": true,
     "product": {...},
     "calculation": {
       "total_packages_used": 1.15,
       "message": "✅ เบิก 23 เม็ด = 1.15 ขวด"
     }
   }

2️⃣ /confirm_withdrawal_with_units (POST)
   Confirms all cart items with unit conversion
   Reduces inventory + creates open_packages entries

# ============================================================
# 🚀 เริ่มใช้
# ============================================================

1. Run SQL migrations:
   sqlite3 factory_stock.db < add_unit_conversion.sql

2. Setup conversions:
   python3 pharmacy_examples.py

3. Import into app.py:
   from unit_conversion import UnitConversionManager

4. Use in routes:
   manager = UnitConversionManager(conn)
   info = manager.get_product_unit_info(product_id)
   calc = manager.calculate_withdrawal(product_id, qty_base_unit)

# ============================================================
# ❓ FAQ
# ============================================================

Q: ถ้า open_packages เหลือบ้าง จะเกิดอะไร?
A: ระบบ FIFO จะใช้ open_packages ก่อน แล้วค่อยเปิดขวดใหม่

Q: ขณะบันทึกเบิก แล้ว Connection drop จะเกิดไม่?
A: ได้ try/catch ไว้ already + transaction rollback

Q: จะเปลี่ยน conversion_rate ได้ไหม?
A: ได้ แต่ควรใหม่เบิกเบิกและจดหมายเหตุ (อาจทำให้หำนวณไม่ตรง)

Q: ยา 1 ยา ให้หรือหลาย conversion_rate ได้?
A: ได้ (ผ่าน relationship ใน ORM) แต่ Design ปัจจุบันเป็น 1:1

# ============================================================
# ✍️ TODO
# ============================================================

- [ ] Test withdrawal logic ทั้ง 5 scenarios
- [ ] Update templates show unit info
- [ ] Add AJAX preview_withdrawal
- [ ] Document for team
- [ ] Train staff on new system
- [ ] Setup conversions for all products
- [ ] Monitor open_packages (cleanup old ones)

# ============================================================
# 📞 ติดต่อ
# ============================================================

Issues atau questions?
1. Check UNIT_CONVERSION_INTEGRATION.md
2. Run pharmacy_examples.py ทั้งหมดเพื่อ test
3. Check unit_conversion.py docstrings
"""

print(__doc__)
