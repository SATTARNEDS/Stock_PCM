"""
🏥 PHARMACEUTICAL UNIT CONVERSION SYSTEM FLOW
Visual Guide for Stock_PCM
"""

# ============================================================
# 📊 SYSTEM ARCHITECTURE
# ============================================================

┌─────────────────────────────────────────────────────────────┐
│                    🏥 Stock_PCM System                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐          ┌──────────────────────┐         │
│  │  add_to_cart │          │ Withdrawal Request   │         │
│  │   (old)      │          │ (23 tablets)          │         │
│  └─────┬────────┘          └──────────┬───────────┘         │
│        │                              │                      │
│        └──────────────┬───────────────┘                      │
│                       │                                      │
│              ┌────────▼──────────┐                           │
│              │ UnitConversion    │                           │
│              │ Manager 💪        │                           │
│              └────────┬──────────┘                           │
│                       │                                      │
│        ┌──────────────┼──────────────┐                       │
│        │              │              │                       │
│        ▼              ▼              ▼                       │
│    ┌────────┐  ┌─────────────┐  ┌────────────┐              │
│    │Calculate│  │Check Stock  │  │Get Product │              │
│    │Withdraw │  │Available    │  │Unit Info   │              │
│    └───┬────┘  └──────┬──────┘  └────┬───────┘              │
│        │               │              │                      │
│        └───────┬───────┴──────────────┘                      │
│               │                                              │
│        ┌──────▼─────────┐                                    │
│        │ Apply Withdraw │                                    │
│        │ (Update DB)    │                                    │
│        └─────┬──────────┘                                    │
│              │                                               │
│    ┌─────────┼────────┬──────────┐                           │
│    │         │        │          │                           │
│    ▼         ▼        ▼          ▼                           │
│  products open_packages transaction_logs  carts             │
│  (stock--)  (INSERT)      (INSERT)      (DELETE)            │
│                                                              │
└─────────────────────────────────────────────────────────────┘


# ============================================================
# 💨 STEP-BY-STEP WITHDRAWAL PROCESS
# ============================================================

┌─────────────────────────────────────────────────────────────┐
│ 1. 👤 USER REQUESTS WITHDRAWAL                              │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   Aspirin Withdrawal Request: 23 tablets                    │
│   (base_unit='tablet', package_unit='bottle', rate=20)      │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 2. 🤖 SYSTEM CHECKS AVAILABILITY                            │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   Current Stock:                                            │
│   ├─ Bottles (full): 5                                      │
│   ├─ Open bottle:    17 tablets (from previous withdrawal)  │
│   └─ Total:         5*20 + 17 = 117 tablets ✓              │
│                                                              │
│   Requested: 23 tablets                                     │
│   Available: 117 tablets                                    │
│   Result: ✅ CAN FULFILL                                    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 3. 🧮 SYSTEM CALCULATES OPTIMAL WITHDRAWAL                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   Strategy: Use open bottle first (FIFO), then full bottles │
│                                                              │
│   Step 1: Take from open bottle                             │
│   ├─ Open bottle has: 17 tablets                            │
│   ├─ Need:            23 tablets                            │
│   ├─ Take:            17 tablets from open bottle ✓        │
│   └─ Still need:      23 - 17 = 6 tablets                   │
│                                                              │
│   Step 2: Take from full bottles                            │
│   ├─ 6 tablets ÷ 20 per bottle = 0 full + 6 remaining       │
│   ├─ Must open 1 new bottle                                 │
│   ├─ Take 6 tablets from new bottle                         │
│   └─ Leave 14 tablets in new open bottle                    │
│                                                              │
│   RESULT:                                                   │
│   ├─ From open bottle:  17 tablets                          │
│   ├─ From new bottle:   6 tablets                           │
│   ├─ New open bottle:   14 tablets (remaining)              │
│   ├─ Full bottles used: 1                                   │
│   └─ Total packages:    1.3 bottles ≈ 1.2 full             │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 4. 🔄 SYSTEM UPDATES DATABASE                               │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   UPDATE products:                                          │
│   ├─ SET stock = 5 - 1 = 4 (took 1 full bottle)            │
│   └─ WHERE id = 1                                           │
│                                                              │
│   UPDATE open_packages:                                     │
│   ├─ SET base_unit_qty = 0 (used all from old open)         │
│   └─ WHERE product_id=1 AND opened_date=... LIMIT 1         │
│                                                              │
│   INSERT open_packages:                                     │
│   ├─ (product_id=1, base_unit_qty=14, opened_date=NOW)     │
│   └─ (status='active')                                      │
│                                                              │
│   INSERT transaction_logs:                                  │
│   ├─ (emp_id='EMP001', action='withdraw')                  │
│   ├─ (qty=1, qty_base_unit=23, qty_package_unit=1.3)       │
│   ├─ (note='เบิกจากขวดเปิด 17 เม็ด + เปิดขวดใหม่ 6 เม็ด') │
│   └─ (timestamp=NOW)                                        │
│                                                              │
│   DELETE carts:                                             │
│   └─ WHERE emp_id='EMP001' AND product_id=1                │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ 5. ✅ RESULT - USER RECEIVES MEDICATION                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│   User receives: 23 tablets Aspirin                         │
│   System shows: "เบิก 23 เม็ด = 1.3 ขวด สำเร็จ! ✓"          │
│                                                              │
│   New Stock Status:                                         │
│   ├─ Bottles (full): 4                                      │
│   ├─ Open bottle #1: Empty (0 tablets)                      │
│   ├─ Open bottle #2: 14 tablets (new!)                      │
│   └─ Total: 4*20 + 0 + 14 = 94 tablets                      │
│                                                              │
│   Audit Trail (for compliance):                             │
│   ├─ WHO:  emp_id = 'EMP001'                                │
│   ├─ WHAT: withdrew 23 tablets, qty_package_unit=1.3        │
│   ├─ WHEN: 2026-04-07 10:30:45                              │
│   └─ HOW:  From open (17) + new open (6), left 14           │
│                                                              │
└─────────────────────────────────────────────────────────────┘


# ============================================================
# 🔄 DATABASE STATE TRANSITIONS
# ============================================================

BEFORE WITHDRAWAL:
┌────────────────────────────────────────────────────┐
│ products (Aspirin)                                 │
├────────────────────────────────────────────────────┤
│ stock: 5 (full bottles)                            │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ open_packages                                      │
├────────────────────────────────────────────────────┤
│ ID=1 | product_id=1 | base_unit_qty=17 | active   │
└────────────────────────────────────────────────────┘

AFTER WITHDRAWAL:
┌────────────────────────────────────────────────────┐
│ products (Aspirin)                                 │
├────────────────────────────────────────────────────┤
│ stock: 4 (full bottles)  ← DECREASED BY 1          │
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ open_packages                                      │
├────────────────────────────────────────────────────┤
│ ID=1 | product_id=1 | base_unit_qty=0  | active   │ ← EMPTY
│ ID=2 | product_id=1 | base_unit_qty=14 | active   │ ← NEW
└────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────┐
│ transaction_logs                                   │
├────────────────────────────────────────────────────┤
│ action='withdraw'                                  │
│ qty=1 (packages)                                   │
│ qty_base_unit=23 (tablets)                         │
│ qty_package_unit=1.3 (bottles)                     │
│ note='เบิกจากขวดเปิด 17 เม็ด + เปิดขวดใหม่ 6 เม็ด' │
└────────────────────────────────────────────────────┘


# ============================================================
# 🌳 DECISION TREE: Calculate Withdrawal
# ============================================================

START: Request 23 tablets
  │    
  ├─ Check Stock Available?
  │  ├─ NO  → "❌ ของไม่พอ: มี 100, ต้องการ 23" → END
  │  └─ YES → Continue
  │
  ├─ Strategy: Use open first (FIFO)
  │  │
  │  ├─ Has open_package?
  │  │  ├─ NO  → qty_from_open = 0
  │  │  └─ YES → qty_from_open = 17, still_need = 6
  │  │
  │  ├─ Calculate full packages needed
  │  │  │
  │  │  ├─ 6 tablets ÷ 20 per bottle = 0 full + 6 remainder
  │  │  ├─ Remainder > 0? 
  │  │  │  └─ YES → Must open 1 new bottle
  │  │  │     └─ new_open_qty = 20 - 6 = 14
  │  │  │
  │  │  └─ full_packages_needed = 1
  │  │
  │  └─ Output: Can fulfill ✓
  │     ├─ from_open_box: 17
  │     ├─ full_packages: 1
  │     ├─ new_open_qty: 14
  │     └─ total_packages: 1.3
  │
  └─ EXECUTE
     ├─ Reduce full packages
     ├─ Reduce open_packages
     ├─ Create new open_packages
     ├─ Log transaction
     └─ Return SUCCESS ✓


# ============================================================
# ✨ KEY IMPROVEMENTS
# ============================================================

✅ Accurate Stock Tracking
   - Stock shown both as bottles AND total tablets

✅ FIFO Compliance  
   - Oldest open bottles used first
   - Batch traceability maintained

✅ Partial Package Handling
   - Open bottles tracked separately
   - No waste from splitting

✅ Audit Trail
   - WHO, WHAT, WHEN, HOW recorded
   - Required for pharmaceutical compliance

✅ Unit Conversion
   - Flexible base_unit and package_unit
   - Supports any medicine type

✅ Error Handling
   - Checks before withdrawal
   - Automatic rollback on failure
"""

print(__doc__)
