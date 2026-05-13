# 🔍 คู่มือแก้ไขปัญหา: คำขอเบิกหายจากระบบ

## 📋 ปัญหา
- ผู้ใช้ส่งคำขอเบิก แต่ admin dashboard ไม่แสดงรายการรออนุมัติ
- ไม่มีการแจ้งเตือนไปยัง admin
- คำขอหายไปจากระบบ

---

## 🎯 สาเหตุที่พบและแก้ไขแล้ว (May 2026)

### ✅ 1. LINE Notifications ถูกลบออกแล้ว
**ปัญหา:** ระบบ LINE อาจมีปัญหา credentials หมดอายุ หรือ GROUP ID ผิด
**แก้ไข:** 
- ✅ ลบ `send_line_message()` และ `resolve_line_targets()` ออกจากระบบ
- ✅ เปลี่ยนเป็น **Email-only notification**
- ✅ ลบ LINE configuration variables

### ✅ 2. Async Notification Error Handling
**ปัญหา:** Threading daemon ล้มแต่ไม่ log error
**แก้ไข:**
- ✅ เพิ่ม try-catch ใน notification worker thread
- ✅ เพิ่ม detailed logging สำหรับ async failures
- ✅ Log ทุก notification attempt ใน `notification_delivery_logs`

### ✅ 3. Location-Based Visibility
**ปัญหา:** Pending requests ถูก filter ตาม location แต่ไม่สามารถเห็นได้
**แก้ไข:**
- ✅ Superadmin สามารถเห็นรายการทั้งหมด (NO filter)
- ✅ admin_pc1 เห็นเฉพาะ PC1 location
- ✅ admin_cc เห็นเฉพาะ Coil Center location

---

## 🛠️ วิธีการ DEBUG

### **ขั้นตอนที่ 1: ใช้ Diagnostic API**
เปิดให้ superadmin เข้าไปแล้วไป URL นี้:
```
http://your-server/api/admin/pending_debug
```

Response จะแสดง:
```json
{
  "success": true,
  "current_admin_role": "superadmin",
  "visible_pending_count": 5,
  "total_pending_count": 5,
  "location_breakdown": {
    "PC1": 3,
    "Coil Center": 2,
    "NO-LOCATION": 0
  },
  "users_without_location": {
    "count": 0,
    "details": []
  },
  "notification_delivery_stats": {
    "sent": {"count": 15, "channels": "email"},
    "failed": {"count": 2, "channels": "email"}
  },
  "diagnostic_tips": [...]
}
```

### **ขั้นตอนที่ 2: ตรวจสอบ Users Location**
Run SQL query:
```sql
-- หา users ที่ไม่มี location
SELECT emp_id, name, location, department 
FROM users 
WHERE location IS NULL OR TRIM(location) = ''
ORDER BY emp_id;

-- ตรวจสอบ location ที่มี
SELECT DISTINCT location FROM users ORDER BY location;
```

### **ขั้นตอนที่ 3: ตรวจสอบ Pending Requests ในฐานข้อมูล**
```sql
-- นับทั้งหมด
SELECT COUNT(*) as total FROM transaction_logs WHERE status = 'Pending';

-- โดยละเอียด
SELECT l.id, l.emp_id, l.product_id, l.qty, l.timestamp, 
       u.name, u.location, u.department,
       p.name as product_name
FROM transaction_logs l
LEFT JOIN users u ON l.emp_id = u.emp_id
LEFT JOIN products p ON l.product_id = p.id
WHERE l.status = 'Pending'
ORDER BY l.timestamp DESC;
```

### **ขั้นตอนที่ 4: ตรวจสอบ Notification Logs**
```sql
-- ตรวจสอบการส่งแจ้งเตือน
SELECT * FROM notification_delivery_logs 
WHERE created_at >= datetime('now', '-1 day')
ORDER BY created_at DESC;

-- ตรวจสอบ test email
SELECT * FROM email_test_logs 
ORDER BY created_at DESC LIMIT 10;
```

---

## ⚙️ การตั้งค่าแจ้งเตือนอีเมล

### ในหน้า Email Settings (`/email_settings`):

1. **ตั้งค่า Email Recipients:**
   - General: `admin@example.com,manager@example.com`
   - PC1 Only: `pc1-admin@example.com`
   - Coil Center Only: `cc-admin@example.com`

2. **เปิดให้ส่งแจ้งเตือน:**
   - ✅ Request Approval (เมื่ออนุมัติ)
   - ✅ Request Rejection (เมื่อปฏิเสธ)
   - ✅ Low Stock Alert (เมื่อสต็อกต่ำ)

3. **Test Email:**
   - คลิก "Send Test Email" เพื่อทดสอบว่า SMTP ทำงานถูกต้อง

---

## 📋 Checklist: ตรวจสอบให้เต็มที่

- [ ] ✅ superadmin เห็น pending requests ทั้งหมด
- [ ] ✅ admin_pc1 เห็นเฉพาะ PC1 requests
- [ ] ✅ admin_cc เห็นเฉพาะ Coil Center requests
- [ ] ✅ Users ทั้งหมดมี location ถูกต้อง (PC1 / Coil Center / CC)
- [ ] ✅ Email SMTP settings ตั้งค่าถูกต้อง
- [ ] ✅ Test email สามารถส่งได้
- [ ] ✅ Notification logs บันทึก "sent" หรือ "failed"
- [ ] ✅ ไม่มี async thread errors ใน logs
- [ ] ✅ Database transaction commit สำเร็จ

---

## 🚀 คำแนะนำเพิ่มเติม

### ถ้ามี pending requests แต่ admin ไม่เห็น:
1. ตรวจสอบว่า **user location ตรงกับ admin role**
2. ตรวจสอบ pending_receive_filter (immediate vs scheduled)
3. ตรวจสอบ browser cache

### ถ้าไม่มีการส่งแจ้งเตือน:
1. ตรวจสอบ SMTP configuration ใน Email Settings
2. ตรวจสอบ `notification_delivery_logs` ว่า status "failed"
3. อ่าน error message ใน `notification_delivery_logs.error_message`
4. ตรวจสอบ email recipients ใน settings

### ถ้า pending requests หายไปทั้งหมด:
1. ตรวจสอบ database backup
2. ตรวจสอบ transaction commit error ใน logs
3. รันการ restore ถ้าจำเป็น

---

## 📞 Debug Endpoints ใหม่

| Endpoint | วิธี | จุดประสงค์ |
|----------|------|-----------|
| `/api/admin/pending_debug` | GET | ดึงข้อมูล diagnostic รายละเอียด |
| `/api/admin/pending_requests` | GET | ดึง pending requests ทั้งหมด |
| `/email_settings` | GET | ดูและแก้ไขการตั้งค่าอีเมล |

---

## 📝 ตัวอย่าง Log Entry

```
[2026-05-12 14:23:45] [INFO] Notification sent: type=withdrawal_confirmed, scope=pc1, recipients=2, status=sent
[2026-05-12 14:24:10] [INFO] Notification sent: type=approval, scope=cc, recipients=1, status=sent
[2026-05-12 14:25:15] [ERROR] Async notification thread failed: type=low_stock, error=Connection timeout
```

---

## ✅ สิ่งที่แก้ไขในเวอร์ชันนี้

- ❌ **REMOVED:** LINE Messaging API code
- ❌ **REMOVED:** `send_line_message()` function
- ❌ **REMOVED:** `resolve_line_targets()` function
- ✅ **ADDED:** Better async thread error logging
- ✅ **ADDED:** `/api/admin/pending_debug` endpoint
- ✅ **CHANGED:** All notifications to email-only
- ✅ **IMPROVED:** Error handling and logging

---

**Last Updated:** May 12, 2026
**System Version:** Stock_PCM v2.0+ (Email-Only Notifications)
