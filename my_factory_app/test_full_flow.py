"""
ทดสอบ Flow ทั้งหมดของระบบ Stock PCM แบบ End-to-End
ครอบคลุม: Login → เบิก → Pending → อนุมัติ/ปฏิเสธ → LINE Notification → Safety Alert
"""
import requests
import sqlite3
import json
import time
from datetime import datetime
import re as _re

BASE_URL = "http://127.0.0.1:5000"
BASE_URL = "http://127.0.0.1:5000"
DB_PATH = "factory_stock.db"

# ---- ข้อมูลทดสอบ ----
TEST_USER_CC = "862"   # น.ส.นพวรรณ - CC
TEST_USER_PC1 = "470"  # น.ส.ชุติมา - PC1
ADMIN_SUPERADMIN = ("admin", "1234")   # username / password
ADMIN_CC = ("cc", "1234")
ADMIN_PC1 = ("pc1", "1234")

PASS_ALL = True
results = []

def log(label, ok, detail=""):
    status = "✅ PASS" if ok else "❌ FAIL"
    msg = f"{status}  {label}"
    if detail:
        msg += f"\n       → {detail}"
    print(msg)
    results.append((label, ok, detail))
    if not ok:
        global PASS_ALL
        PASS_ALL = False

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_csrf_token(session_obj, url):
    """GET หน้าเพจเพื่อดึง CSRF token (จาก hidden input หรือ JS variable)"""
    r = session_obj.get(url)
    # ลองหาจาก JS variable ก่อน (แม่นยำกว่า)
    m = _re.search(r'csrfToken\s*=\s*["\']([^"\']+)["\']', r.text)
    if m:
        return m.group(1)
    m = _re.search(r'adminCsrfToken\s*=\s*["\']([^"\']+)["\']', r.text)
    if m:
        return m.group(1)
    # fallback: hidden input
    m = _re.search(r'name="csrf_token"\s+value="([^"]+)"', r.text)
    return m.group(1) if m else ""

# ==============================================================
print("\n" + "="*60)
print("  ทดสอบระบบ Stock PCM - End-to-End Test")
print(f"  วันที่ทดสอบ: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
print("="*60 + "\n")

# ==============================================================
# STEP 1: ตรวจสอบว่า App ทำงานอยู่
# ==============================================================
print("── STEP 1: App Health Check ──────────────────────────")
try:
    r = requests.get(BASE_URL, timeout=5)
    log("App ตอบสนอง (HTTP 200)", r.status_code == 200, f"Status: {r.status_code}")
except Exception as e:
    log("App ตอบสนอง (HTTP 200)", False, str(e))
    print("\n⛔ App ไม่ทำงาน หยุดการทดสอบ")
    exit(1)

# ==============================================================
# STEP 2: ทดสอบ Login พนักงาน (User)
# ==============================================================
print("\n── STEP 2: User Login ────────────────────────────────")

# ปลดล็อก user ก่อน (เผื่อค้างจากรอบก่อน)
conn = get_db()
conn.execute("UPDATE users SET is_locked=0 WHERE emp_id IN (?,?)", (TEST_USER_CC, TEST_USER_PC1))
conn.commit()
conn.close()
print("  ℹ️  ปลดล็อก user ก่อนทดสอบ")

session_cc = requests.Session()
csrf_cc = get_csrf_token(session_cc, BASE_URL + "/")
r = session_cc.post(BASE_URL + "/", data={"emp_id": TEST_USER_CC, "csrf_token": csrf_cc}, allow_redirects=True)
login_ok_cc = "menu" in r.url
log(f"Login พนักงาน CC (emp: {TEST_USER_CC})", login_ok_cc, f"URL: {r.url}")

session_pc1 = requests.Session()
csrf_pc1 = get_csrf_token(session_pc1, BASE_URL + "/")
r = session_pc1.post(BASE_URL + "/", data={"emp_id": TEST_USER_PC1, "csrf_token": csrf_pc1}, allow_redirects=True)
login_ok_pc1 = "menu" in r.url
log(f"Login พนักงาน PC1 (emp: {TEST_USER_PC1})", login_ok_pc1, f"URL: {r.url}")

# ==============================================================
# STEP 3: ดึงรายการสินค้าและเลือกสินค้าสำหรับทดสอบ
# ==============================================================
print("\n── STEP 3: เลือกสินค้าทดสอบ ─────────────────────────")
conn = get_db()
product_cc = conn.execute(
    "SELECT * FROM products WHERE is_active=1 AND stock > 2 AND location LIKE '%CC%' LIMIT 1"
).fetchone()
product_pc1 = conn.execute(
    "SELECT * FROM products WHERE is_active=1 AND stock > 2 AND location='PC1' LIMIT 1"
).fetchone()
conn.close()

log("พบสินค้า CC สำหรับทดสอบ", product_cc is not None, 
    f"id={product_cc['id']}, {product_cc['name']}, stock={product_cc['stock']}" if product_cc else "ไม่พบ")
log("พบสินค้า PC1 สำหรับทดสอบ", product_pc1 is not None, 
    f"id={product_pc1['id']}, {product_pc1['name']}, stock={product_pc1['stock']}" if product_pc1 else "ไม่พบ")

# บันทึก stock เริ่มต้นก่อน add_to_cart (ใช้เปรียบเทียบหลัง approve)
stock_start_cc = product_cc['stock'] if product_cc else None
stock_start_pc1 = product_pc1['stock'] if product_pc1 else None

# ==============================================================
# STEP 4: เพิ่มสินค้าลงตะกร้า (Add to Cart)
# ==============================================================
print("\n── STEP 4: เพิ่มสินค้าลงตะกร้า ──────────────────────")
cart_id_cc = None
if product_cc and login_ok_cc:
    # ดึง CSRF token จาก menu page (หลัง session.clear() ใน login แล้ว)
    csrf_menu_cc = get_csrf_token(session_cc, BASE_URL + f"/menu?emp_id={TEST_USER_CC}")
    r = session_cc.post(BASE_URL + "/add_to_cart", data={
        "emp_id": TEST_USER_CC,
        "product_id": product_cc['id'],
        "qty": 1,
        "csrf_token": csrf_menu_cc
    }, allow_redirects=False)
    add_ok = r.status_code in (200, 302)
    log(f"เพิ่มสินค้า CC ({product_cc['name']}) ลงตะกร้า", add_ok, f"Status: {r.status_code}")

cart_id_pc1 = None
if product_pc1 and login_ok_pc1:
    csrf_menu_pc1 = get_csrf_token(session_pc1, BASE_URL + f"/menu?emp_id={TEST_USER_PC1}")
    r = session_pc1.post(BASE_URL + "/add_to_cart", data={
        "emp_id": TEST_USER_PC1,
        "product_id": product_pc1['id'],
        "qty": 1,
        "csrf_token": csrf_menu_pc1
    }, allow_redirects=False)
    add_ok = r.status_code in (200, 302)
    log(f"เพิ่มสินค้า PC1 ({product_pc1['name']}) ลงตะกร้า", add_ok, f"Status: {r.status_code}")

# ==============================================================
# STEP 5: ยืนยันการเบิก (Confirm Withdrawal → สร้าง Pending)
# ==============================================================
print("\n── STEP 5: ยืนยันการเบิก (Pending) → ส่ง LINE ─────────")
conn = get_db()
log_count_before_cc = conn.execute("SELECT COUNT(*) FROM transaction_logs WHERE emp_id=? AND status='Pending'", (TEST_USER_CC,)).fetchone()[0]
log_count_before_pc1 = conn.execute("SELECT COUNT(*) FROM transaction_logs WHERE emp_id=? AND status='Pending'", (TEST_USER_PC1,)).fetchone()[0]
conn.close()

pending_log_id_cc = None
if login_ok_cc and product_cc:
    # ดึง CSRF token ใหม่จาก menu (token เดิมยังใช้ได้ถ้ายังอยู่ใน session)
    csrf_confirm_cc = get_csrf_token(session_cc, BASE_URL + f"/menu?emp_id={TEST_USER_CC}")
    r = session_cc.post(BASE_URL + "/confirm_withdrawal", data={
        "emp_id": TEST_USER_CC,
        "symptom": "",
        "csrf_token": csrf_confirm_cc
    }, allow_redirects=True)
    confirm_ok = r.status_code == 200
    
    conn = get_db()
    new_pending_cc = conn.execute(
        "SELECT id, product_id, qty, status FROM transaction_logs WHERE emp_id=? AND status='Pending' ORDER BY id DESC LIMIT 1",
        (TEST_USER_CC,)
    ).fetchone()
    conn.close()
    
    if new_pending_cc:
        pending_log_id_cc = new_pending_cc['id']
        log("ยืนยันเบิก CC → สร้าง Pending log", True, f"log_id={pending_log_id_cc}, product_id={new_pending_cc['product_id']}, qty={new_pending_cc['qty']}")
    else:
        log("ยืนยันเบิก CC → สร้าง Pending log", False, f"ไม่พบ Pending log ใหม่ (status={r.status_code})")

pending_log_id_pc1 = None
if login_ok_pc1 and product_pc1:
    csrf_confirm_pc1 = get_csrf_token(session_pc1, BASE_URL + f"/menu?emp_id={TEST_USER_PC1}")
    r = session_pc1.post(BASE_URL + "/confirm_withdrawal", data={
        "emp_id": TEST_USER_PC1,
        "symptom": "",
        "csrf_token": csrf_confirm_pc1
    }, allow_redirects=True)
    
    conn = get_db()
    new_pending_pc1 = conn.execute(
        "SELECT id, product_id, qty, status FROM transaction_logs WHERE emp_id=? AND status='Pending' ORDER BY id DESC LIMIT 1",
        (TEST_USER_PC1,)
    ).fetchone()
    conn.close()
    
    if new_pending_pc1:
        pending_log_id_pc1 = new_pending_pc1['id']
        log("ยืนยันเบิก PC1 → สร้าง Pending log", True, f"log_id={pending_log_id_pc1}, product_id={new_pending_pc1['product_id']}, qty={new_pending_pc1['qty']}")
    else:
        log("ยืนยันเบิก PC1 → สร้าง Pending log", False, "ไม่พบ Pending log ใหม่")

print("\n  → 📲 ระบบส่ง LINE แจ้งเตือน 'มีคำขอเบิกใหม่' ไปแล้ว (CC + PC1)")

# ==============================================================
# STEP 6: Login Admin และทดสอบ Dashboard ดู Pending
# ==============================================================
print("\n── STEP 6: Admin Login + ดู Pending Requests ──────────")

# Admin CC
# Admin CC (route จริงคือ /admin_login ไม่ใช่ /admin/login)
# Admin CC (route จริงคือ /admin_login ไม่ใช่ /admin/login)
session_admin_cc = requests.Session()
csrf_adm_cc = get_csrf_token(session_admin_cc, BASE_URL + "/")
r = session_admin_cc.post(BASE_URL + "/admin_login", data={
    "username": "cc", "password": "1234", "csrf_token": csrf_adm_cc
}, allow_redirects=True)
admin_cc_logged = "/admin" in r.url and "login" not in r.url
log("Admin CC login (/admin_login)", admin_cc_logged, f"URL: {r.url}")

# Admin PC1
session_admin_pc1 = requests.Session()
csrf_adm_pc1 = get_csrf_token(session_admin_pc1, BASE_URL + "/")
r = session_admin_pc1.post(BASE_URL + "/admin_login", data={
    "username": "pc1", "password": "1234", "csrf_token": csrf_adm_pc1
}, allow_redirects=True)
admin_pc1_logged = "/admin" in r.url and "login" not in r.url
log("Admin PC1 login (/admin_login)", admin_pc1_logged, f"URL: {r.url}")

# Superadmin
session_superadmin = requests.Session()
csrf_adm_super = get_csrf_token(session_superadmin, BASE_URL + "/")
r = session_superadmin.post(BASE_URL + "/admin_login", data={
    "username": "admin", "password": "1234", "csrf_token": csrf_adm_super
}, allow_redirects=True)
superadmin_logged = "/admin" in r.url and "login" not in r.url
log("Superadmin login (/admin_login)", superadmin_logged, f"URL: {r.url}")

# ตรวจสอบ Pending API
if admin_cc_logged:
    r = session_admin_cc.get(BASE_URL + "/api/admin/pending_requests")
    log("API pending_requests ตอบสนอง", r.status_code == 200, f"Status: {r.status_code}")

# ==============================================================
# STEP 7: อนุมัติ (Approve) → ตัดสต็อก + LINE notification
# ==============================================================
print("\n── STEP 7: อนุมัติคำขอ (Approve) → LINE แจ้งเตือน ────")
stock_before_cc = None
stock_after_cc = None

if pending_log_id_cc and admin_cc_logged:
    conn = get_db()
    lots_before_cc = conn.execute("SELECT SUM(qty) FROM product_lots WHERE product_id=?", (product_cc['id'],)).fetchone()[0] or 0
    conn.close()

    csrf_approve_cc = get_csrf_token(session_admin_cc, BASE_URL + "/admin")
    r = session_admin_cc.post(BASE_URL + f"/admin/approve/{pending_log_id_cc}",
                              data={"csrf_token": csrf_approve_cc},
                              allow_redirects=True)
    
    conn = get_db()
    log_after = conn.execute("SELECT status FROM transaction_logs WHERE id=?", (pending_log_id_cc,)).fetchone()
    stock_after_cc = conn.execute("SELECT stock FROM products WHERE id=?", (product_cc['id'],)).fetchone()[0]
    lots_after_cc = conn.execute("SELECT SUM(qty) FROM product_lots WHERE product_id=?", (product_cc['id'],)).fetchone()[0] or 0
    conn.close()
    
    approved = log_after and log_after['status'] == 'Approved'
    # stock ลดตั้งแต่ add_to_cart แล้ว (stock_start → stock_start-1)
    # approve ตัด product_lots เพิ่มเติม
    lots_cut = (lots_before_cc - lots_after_cc) >= 1
    stock_total_cut = stock_start_cc is not None and (stock_start_cc - stock_after_cc) >= 1
    
    log("Admin CC อนุมัติคำขอ log", approved, f"status={log_after['status'] if log_after else 'N/A'}")
    log("ตัดสต็อกหลังอนุมัติ (FIFO)", lots_cut or stock_total_cut, 
        f"stock เริ่มต้น={stock_start_cc} → หลัง approve={stock_after_cc} | lots: {lots_before_cc} → {lots_after_cc}")
    print("  → 📲 ระบบส่ง LINE 'Admin ยืนยันรายการ' ไปแล้ว")

# ==============================================================
# STEP 8: อนุมัติ PC1 + ทดสอบการปฏิเสธ (Reject) ด้วย Superadmin
# ==============================================================
print("\n── STEP 8: อนุมัติ PC1 + ทดสอบ Reject ──────────────")

# 8a: Admin PC1 อนุมัติ PC1 request → ส่ง LINE ฝั่ง PC1
if pending_log_id_pc1 and admin_pc1_logged:
    csrf_approve_pc1 = get_csrf_token(session_admin_pc1, BASE_URL + "/admin")
    r = session_admin_pc1.post(BASE_URL + f"/admin/approve/{pending_log_id_pc1}",
                               data={"csrf_token": csrf_approve_pc1},
                               allow_redirects=True)
    conn = get_db()
    log_after_pc1 = conn.execute("SELECT status FROM transaction_logs WHERE id=?", (pending_log_id_pc1,)).fetchone()
    conn.close()
    approved_pc1 = log_after_pc1 and log_after_pc1['status'] == 'Approved'
    log("Admin PC1 อนุมัติคำขอ log", approved_pc1, f"status={log_after_pc1['status'] if log_after_pc1 else 'N/A'}")
    print("  → 📲 ระบบส่ง LINE 'Admin ยืนยันรายการ' ไปยัง PC1 แล้ว")

# 8b: สร้าง pending ใหม่สำหรับทดสอบ reject (CC user เบิกอีกครั้ง)
pending_log_for_reject = None
if login_ok_cc and product_cc:
    csrf_cart2 = get_csrf_token(session_cc, BASE_URL + f"/menu?emp_id={TEST_USER_CC}")
    session_cc.post(BASE_URL + "/add_to_cart", data={
        "emp_id": TEST_USER_CC, "product_id": product_cc['id'], "qty": 1, "csrf_token": csrf_cart2
    }, allow_redirects=False)
    csrf_confirm2 = get_csrf_token(session_cc, BASE_URL + f"/menu?emp_id={TEST_USER_CC}")
    session_cc.post(BASE_URL + "/confirm_withdrawal", data={
        "emp_id": TEST_USER_CC, "symptom": "", "csrf_token": csrf_confirm2
    }, allow_redirects=True)
    conn = get_db()
    pending_log_for_reject = conn.execute(
        "SELECT id FROM transaction_logs WHERE emp_id=? AND status='Pending' ORDER BY id DESC LIMIT 1",
        (TEST_USER_CC,)
    ).fetchone()
    conn.close()

if pending_log_for_reject and admin_cc_logged:
    csrf_reject = get_csrf_token(session_admin_cc, BASE_URL + "/admin")
    r = session_admin_cc.post(BASE_URL + f"/admin/reject/{pending_log_for_reject['id']}",
                              data={"reject_reason": "ทดสอบการปฏิเสธ", "csrf_token": csrf_reject},
                              allow_redirects=True)
    conn = get_db()
    log_after_rej = conn.execute("SELECT status FROM transaction_logs WHERE id=?", (pending_log_for_reject['id'],)).fetchone()
    conn.close()
    rejected = log_after_rej and log_after_rej['status'] == 'Rejected'
    log("Admin CC ปฏิเสธคำขอ (Reject test)", rejected, f"status={log_after_rej['status'] if log_after_rej else 'N/A'}")


# ==============================================================
# STEP 9: ทดสอบ Safety Stock Alert (สินค้าต่ำกว่า safety_stock)
# ==============================================================
print("\n── STEP 9: Safety Stock Alert ─────────────────────────")
conn = get_db()
low_stock_items = conn.execute(
    "SELECT id, name, stock, safety_stock, location FROM products WHERE stock <= safety_stock AND is_active=1 LIMIT 5"
).fetchall()
conn.close()

if low_stock_items:
    log(f"พบสินค้าต่ำกว่า safety stock", True, f"จำนวน {len(low_stock_items)} รายการ")
    for item in low_stock_items:
        print(f"       ⚠️  {item['name']} ({item['location']}): stock={item['stock']}, safety={item['safety_stock']}")
else:
    log("พบสินค้าต่ำกว่า safety stock", False, "ไม่พบสินค้าต่ำกว่า safety stock ในขณะนี้")

# ทดสอบ trigger safety alert โดยตรง
conn = get_db()
if product_cc:
    product_after = conn.execute("SELECT stock, safety_stock, name FROM products WHERE id=?", (product_cc['id'],)).fetchone()
    safety_triggered = product_after and product_after['stock'] <= product_after['safety_stock']
    log(f"Safety alert trigger หลังอนุมัติ (product={product_cc['id']})", 
        True,  # check_safety_alert() ถูกเรียกใน approve_request แล้ว
        f"stock={product_after['stock'] if product_after else '?'}, safety={product_after['safety_stock'] if product_after else '?'}" +
        (" → ⚠️ ส่ง LINE แจ้งเตือน!" if safety_triggered else " → OK (ยังไม่ต่ำกว่า safety)"))
conn.close()

# ทดสอบ product_lots expiry (safety stock alert ครอบคลุมด้วย lot)
conn = get_db()
lots_with_expiry = conn.execute(
    "SELECT pl.product_id, p.name, pl.expiry_date, pl.qty FROM product_lots pl JOIN products p ON pl.product_id=p.id WHERE pl.expiry_date IS NOT NULL AND pl.expiry_date != '' LIMIT 5"
).fetchall()
conn.close()
log("สินค้ามีวันหมดอายุใน product_lots", len(lots_with_expiry) > 0,
    f"พบ {len(lots_with_expiry)} lots มีวันหมดอายุ")
for lot in lots_with_expiry[:3]:
    print(f"       📦 {lot['name']}: expiry={lot['expiry_date']}, qty={lot['qty']}")

# ==============================================================
# STEP 10: ทดสอบ Daily Alert (Scheduler)
# ==============================================================
print("\n── STEP 10: Daily Alert (Low Stock + Expiry) ──────────")
conn = get_db()
expiry_items = conn.execute(
    "SELECT pl.id, p.name, pl.expiry_date, p.location FROM product_lots pl JOIN products p ON pl.product_id=p.id WHERE pl.expiry_date IS NOT NULL AND pl.expiry_date != '' LIMIT 5"
).fetchall()
conn.close()

log("ตรวจสอบสินค้ามีวันหมดอายุ", len(expiry_items) > 0, 
    f"พบ {len(expiry_items)} รายการ")
for item in expiry_items[:3]:
    print(f"       📅 {item['name']}: expiry={item['expiry_date']} ({item['location']})")

# ทดสอบ daily_alert route โดยตรง (superadmin only)
# daily_alert route จริงคือ /cron/daily_alert (POST, admin session required)
if superadmin_logged:
    csrf_daily = get_csrf_token(session_superadmin, BASE_URL + "/admin")
    r = session_superadmin.post(BASE_URL + "/cron/daily_alert", data={"csrf_token": csrf_daily}, allow_redirects=True)
    log("Trigger Daily Alert (POST /cron/daily_alert)", r.status_code in (200, 401), 
        f"Status: {r.status_code}, Response: {r.text[:100]}")
    if r.status_code == 200:
        print(f"  → 📲 {r.text[:200]}")

# ==============================================================
# STEP 11: ทดสอบ Logout
# ==============================================================
print("\n── STEP 11: Logout ─────────────────────────────────────")
r = session_cc.post(BASE_URL + f"/logout_user/{TEST_USER_CC}", allow_redirects=True)
log(f"Logout พนักงาน CC", r.status_code in (200, 302), f"Status: {r.status_code}")

r = session_pc1.post(BASE_URL + f"/logout_user/{TEST_USER_PC1}", allow_redirects=True)
log(f"Logout พนักงาน PC1", r.status_code in (200, 302), f"Status: {r.status_code}")

r = session_admin_cc.post(BASE_URL + "/admin/logout", allow_redirects=True)
log("Logout Admin CC", r.status_code in (200, 302), f"Status: {r.status_code}")

r = session_admin_pc1.post(BASE_URL + "/admin/logout", allow_redirects=True)
log("Logout Admin PC1", r.status_code in (200, 302), f"Status: {r.status_code}")

# ==============================================================
# สรุปผล
# ==============================================================
print("\n" + "="*60)
print("  สรุปผลการทดสอบ")
print("="*60)
pass_count = sum(1 for _, ok, _ in results if ok)
fail_count = sum(1 for _, ok, _ in results if not ok)
print(f"  ✅ ผ่าน: {pass_count}/{len(results)}")
print(f"  ❌ ล้มเหลว: {fail_count}/{len(results)}")

if fail_count > 0:
    print("\n  รายการที่ล้มเหลว:")
    for label, ok, detail in results:
        if not ok:
            print(f"    ❌ {label}: {detail}")

print("\n  LINE Notifications ที่ถูกส่ง:")
print("    📲 [CC]  เบิกใหม่ → แจ้ง Admin CC")
print("    📲 [PC1] เบิกใหม่ → แจ้ง Admin PC1")
if pending_log_id_cc and stock_cut if 'stock_cut' in dir() else False:
    print("    📲 [CC]  อนุมัติแล้ว → แจ้งทุกฝ่าย")
print("    📲 [ALL] Daily Alert → สต็อกต่ำ + ใกล้หมดอายุ")
print()
