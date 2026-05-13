# ✅ แก้ไขปัญหา: คำขอเบิกหายเมื่อ User ส่ง แต่ปรากฏเมื่อ Admin ใช้รหัส User

**วันที่:** May 12, 2026

## 🔍 **ปัญหา**
- ผู้ใช้ (User) ส่งคำขอเบิก → คำขอ **ไม่ปรากฏ** ในระบบ
- Admin ล็อคอินด้วยรหัสของ User แล้วส่งคำขอเดียวกัน → คำขอ **ปรากฏปกติ**

## 🎯 **สาเหตุที่พบ**

### **⏱️ Session Timeout (15 นาที)**
```
SESSION_TIMEOUT_MINUTES = 15
```
- User ใช้งาน page นานเกิน 15 นาที → session หมดอายุ
- ตรง `confirm_withdrawal()` เรียก `is_valid_user_session()` 
- Session หมดแล้ว → Check fail → Redirect ไปหน้า login → **คำขอหายไป**
- Database insert **ไม่เกิดขึ้น** เพราะ redirect ก่อน commit

### **✅ ทำไม Admin ไทย ได้?**
```python
session.clear()
session['user_id'] = emp_id  # ← Session ใหม่
session.permanent = True
# ... นับใหม่ 15 นาที
```
- Admin ล็อคอิน = Session ใหม่ = นับเวลาใหม่
- ถ้า admin ทำสำเร็จภายใน 15 นาที → OK ✅

---

## ✅ **สิ่งที่แก้ไขแล้ว**

### **1. เพิ่ม Session Timeout** 
```python
# Before
SESSION_TIMEOUT_MINUTES = 15

# After
SESSION_TIMEOUT_MINUTES = 30  # ← เพิ่มเป็น 30 นาที
```

### **2. Extend Session ทุกครั้งที่ User ทำ Activity**
เพิ่มตรง 3 routes:

#### **a) `/add_to_cart` (เพิ่มของใส่ตะกร้า)**
```python
@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    emp_id = ...
    if not is_valid_user_session(emp_id):
        ...
    
    # ✅ Extend session
    session.permanent = True
    app.permanent_session_lifetime = timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    session.modified = True
    # ... continue
```

#### **b) `/remove_from_cart` (ลบของออกจากตะกร้า)**
```python
@app.route('/remove_from_cart', methods=['POST'])
def remove_from_cart():
    emp_id = ...
    if not is_valid_user_session(emp_id):
        ...
    
    # ✅ Extend session
    session.permanent = True
    app.permanent_session_lifetime = timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    session.modified = True
    # ... continue
```

#### **c) `/update_cart_qty` (อัปเดตจำนวนในตะกร้า)**
```python
@app.route('/update_cart_qty', methods=['POST'])
def update_cart_qty():
    cart_id = ...
    emp_id = ...
    if not is_valid_user_session(emp_id):
        ...
    
    # ✅ Extend session
    session.permanent = True
    app.permanent_session_lifetime = timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    session.modified = True
    # ... continue
```

#### **d) `/confirm_withdrawal` (ยืนยันการเบิก)**
```python
@app.route('/confirm_withdrawal', methods=['POST'])
def confirm_withdrawal():
    emp_id = ...
    if not is_valid_user_session(emp_id):
        ...
    
    # ✅ Extend session เพื่อป้องกัน timeout ระหว่างการ confirm
    session.permanent = True
    app.permanent_session_lifetime = timedelta(minutes=SESSION_TIMEOUT_MINUTES)
    session.modified = True
    # ... continue
```

---

## 📋 **วิธีทำงาน**

### **Timeline ของ User Activity:**

```
[0:00] User ล็อคอิน
       └─ session นับ 15 นาที (timeout: 0:15)

[0:02] User เพิ่มของใส่ตะกร้า
       └─ ✅ session extend อีก 15 นาที (timeout: 0:17)

[0:08] User อัปเดตจำนวน
       └─ ✅ session extend อีก 15 นาที (timeout: 0:23)

[0:18] User ส่งคำขอ (confirm_withdrawal)
       └─ ✅ session extend อีก 15 นาที (timeout: 0:33)
       └─ Database insert สำเร็จ
       └─ commit สำเร็จ ✅

[ก่อนแก้ไข ถ้า user ไม่ active แบบ activity = timeout ที่ 0:15]
```

---

## 🧪 **วิธีทดสอบ**

### **Test Case 1: ทดสอบ Session Extension**
1. User ล็อคอิน
2. ใส่จำนวนที่อักษร (update qty) หลาย ๆ ครั้งในช่วง 25-30 นาที
3. เลือกสินค้าหลาย ๆ ครั้ง (add to cart)
4. ท้ายสุด ส่งคำขอ → **ควรสำเร็จ** ✅ (ไม่ timeout)

### **Test Case 2: ตรวจสอบ Database**
```sql
-- ตรวจสอบว่ามี transaction_logs ที่สถานะ 'Pending'
SELECT COUNT(*) FROM transaction_logs WHERE status = 'Pending';

-- ควรจะเห็นคำขอจาก user ที่เพิ่งส่ง
SELECT id, emp_id, status, timestamp 
FROM transaction_logs 
WHERE status = 'Pending' 
ORDER BY timestamp DESC LIMIT 5;
```

### **Test Case 3: ตรวจสอบ Notification Log**
```sql
-- ตรวจสอบว่า email notification ถูกส่ง
SELECT * FROM notification_delivery_logs 
WHERE created_at >= datetime('now', '-10 minutes')
ORDER BY created_at DESC;
```

---

## 🎁 **เพิ่มเติม**

### **Session Settings ที่ปรับแล้ว:**
| Setting | Before | After | หมายเหตุ |
|---------|--------|-------|---------|
| SESSION_TIMEOUT_MINUTES | 15 | 30 | ยาวขึ้น ป้องกัน timeout |
| Extension on Activity | ❌ ไม่มี | ✅ มี | ทุกครั้งที่ user ทำ activity |

### **Files ที่แก้ไข:**
- ✅ [my_factory_app/app.py](app.py)
  - Line 92: SESSION_TIMEOUT_MINUTES = 30
  - Line 3502-3505: add_to_cart session extension
  - Line 3706-3709: remove_from_cart session extension
  - Line 3936-3939: update_cart_qty session extension
  - Line 3751-3754: confirm_withdrawal session extension

---

## ⚠️ **ข้อควรระวัง**

1. **Session Cookie Size**: การ extend session บ่อยครั้งอาจทำให้ cookie ใหญ่ขึ้น (ปกติไม่มีปัญหา)
2. **Security**: Session 30 นาที = ความเสี่ยง ลดความปลอดภัยเล็กน้อย แต่ยังยอมรับได้
3. **Mobile Users**: ผู้ใช้มือถือหรือ slow network อาจใช้เวลา 15 นาทีจึงจบการเบิก → ไม่ timeout แล้ว ✅

---

## ✅ **ผลลัพธ์ที่คาดหวัง**

**ก่อนแก้ไข:**
- User ส่งคำขอ → Session timeout → Redirect ไปหน้า login → คำขอหายไป ❌

**หลังแก้ไข:**
- User ส่งคำขอ → Session extend ทุกครั้งที่ activity → Confirm สำเร็จ ✅
- Admin ใช้รหัส user → คำขอปรากฏ ✅

---

## 📞 **หากมีปัญหาต่อ**

1. ตรวจสอบ browser console ว่ามี errors
2. ตรวจสอบ server logs สำหรับ "session expired" messages
3. Clear browser cookies และลอง login ใหม่
4. ตรวจสอบ `/api/admin/pending_debug` ว่า pending requests มี status ถูกต้อง

---

**Last Updated:** May 12, 2026  
**Status:** ✅ FIXED
