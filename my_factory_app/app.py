import math
import os
import sqlite3
import json
import pandas as pd
import requests
import qrcode
import io
import pytz
from io import BytesIO
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from flask_apscheduler import APScheduler

# --- เพิ่ม Config ---
class Config:
    SCHEDULER_API_ENABLED = True
    SCHEDULER_TIMEZONE = "Asia/Bangkok" # บังคับ Timezone ระดับ Global

app = Flask(__name__)
app.config.from_object(Config())
scheduler = APScheduler()
# -------------------------

app.secret_key = 'factory_smart_key_2026'
DB_NAME = 'factory_stock.db'

# สร้างฟังก์ชันสำหรับดึงเวลาไทย
def get_thailand_time():
    tz = pytz.timezone('Asia/Bangkok')
    return datetime.now(tz)

# ==========================================
# 📲 ตั้งค่า LINE Messaging API
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = '3QbgTXY3rgtW3rswVEqu9JRKAJBO4VbacDuVczn+Z+IFPu5BW0FkScnOTbPTtlEAaVj66MPQgwZW3d4OzwvBTD+liN+nWWi9VleKbQtwNU4lgXrfmzihCxLFhikWKBVQ0Ykp8QDK70sfSo5078lTeAdB04t89/1O/w1cDnyilFU=' 
LINE_ADMIN_USER_ID = 'C5220a09d21e6761f29f28985edc0a733' 

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=20)
    # ⚠️ สำคัญมาก: ต้องเปิด Journal Mode เป็น WAL
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn

def send_line_message(message):
    if not LINE_CHANNEL_ACCESS_TOKEN: return
    url = 'https://api.line.me/v2/bot/message/push'
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {LINE_CHANNEL_ACCESS_TOKEN}'}
    payload = {'to': LINE_ADMIN_USER_ID, 'messages': [{'type': 'text', 'text': message}]}
    try:
        requests.post(url, headers=headers, json=payload)
    except Exception as e:
        print(f"❌ Line Connection Error: {e}")

# ==========================================
# 🕒 ระบบ Auto Unlock (User Zombie Check)
# ==========================================
@app.before_request
def update_last_seen():
    emp_id = request.args.get('emp_id') or request.form.get('emp_id')
    if emp_id:
        try:
            conn = get_db_connection()
            # ใช้ datetime('now', '+7 hours') เพื่อให้ตรงกับไทย
            conn.execute("UPDATE users SET last_seen = datetime('now', '+7 hours') WHERE emp_id = ?", (emp_id,))
            conn.commit()
            conn.close()
        except:
            pass

# ==========================================
# 👤 ส่วนของพนักงาน (USER & CART SYSTEM)
# ==========================================

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        emp_id = request.form.get('emp_id', '').strip()
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
        
        if user:
            # 4. ป้องกันล็อกอินซ้ำ (ปรับปรุงใหม่: เช็คเวลา last_seen)
            is_locked = user['is_locked']
            last_seen_str = user['last_seen']
            
            # ถ้า Locked อยู่ แต่เวลา last_seen นานเกิน 5 นาที ให้ถือว่าหลุดไปแล้ว -> ปลดล็อคให้เข้าใหม่ได้
            if is_locked == 1 and last_seen_str:
                last_seen = datetime.strptime(last_seen_str, '%Y-%m-%d %H:%M:%S')
                if datetime.now() - last_seen > timedelta(minutes=5):
                    is_locked = 0 # ปลดล็อคอัตโนมัติ

            if is_locked == 1:
                flash(f'❌ รหัส {emp_id} กำลังใช้งานอยู่ (ต้อง Logout ก่อน หรือรอ 5 นาที)', 'danger')
                conn.close()
                return redirect(url_for('index'))
            
            # ล็อกสถานะ User เป็น Online และอัปเดตเวลา
            conn.execute('UPDATE users SET is_locked = 1, last_seen = CURRENT_TIMESTAMP WHERE emp_id = ?', (emp_id,))
            conn.commit()
            conn.close()
            
            return redirect(url_for('menu', emp_id=emp_id))
        else:
            conn.close()
            flash(f'❌ ไม่พบรหัสพนักงาน: {emp_id}', 'danger')
    return render_template('index.html')

@app.route('/logout_user/<emp_id>')
def logout_user(emp_id):
    conn = get_db_connection()
    conn.execute('UPDATE users SET is_locked = 0 WHERE emp_id = ?', (emp_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('index'))

@app.route('/menu')
def menu():
    emp_id = request.args.get('emp_id')
    search_query = request.args.get('search', '').strip()
    category_filter = request.args.get('category', '').strip()
    open_cart = request.args.get('open_cart')

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    if not user: return redirect(url_for('index'))

    # --- 1. แยกของตาม Location ของ User (แก้ไขข้อ 1) ---
    location_condition = ""
    # ถ้า User อยู่ PC1 -> เห็นของ PC1 + General
    if user['location'] and 'PC1' in user['location']:
        location_condition = " AND (location LIKE '%PC1%' OR location = 'General' OR location IS NULL)"
    # ถ้า User อยู่ CC -> เห็นของ CC + General
    elif user['location'] and ('Coil Center' in user['location'] or 'CC' in user['location']):
        location_condition = " AND (location LIKE '%Coil Center%' OR location LIKE '%CC%' OR location = 'General' OR location IS NULL)"
    
    # ดึงหมวดหมู่ (กรองตาม Location ด้วย)
    cat_query = f'SELECT DISTINCT category FROM products WHERE stock > 0 {location_condition}'
    cat_rows = conn.execute(cat_query).fetchall()
    all_categories = [row['category'] for row in cat_rows]

    # Query ของ (กรองตาม Location ด้วย)
    query = f'SELECT * FROM products WHERE stock > 0 {location_condition}'
    params = []
    
    if search_query:
        query += ' AND (name LIKE ? OR code LIKE ?)'
        params.extend([f'%{search_query}%', f'%{search_query}%'])
    if category_filter:
        query += ' AND category = ?'
        params.append(category_filter)

    products_list = conn.execute(query, params).fetchall()
    # -----------------------------------------------------

    products_by_category = {}
    for item in products_list:
        cat = item['category']
        if cat not in products_by_category: products_by_category[cat] = []
        products_by_category[cat].append(item)

    cart_items = conn.execute('''
        SELECT c.*, p.name, p.code, p.category, p.unit 
        FROM carts c JOIN products p ON c.product_id = p.id 
        WHERE c.emp_id = ?
    ''', (emp_id,)).fetchall()
    
    cart_list = [dict(row) for row in cart_items]
    session['cart'] = cart_list 

    conn.close()
    return render_template('menu.html', 
                           user=user, 
                           products=products_by_category, 
                           all_categories=all_categories,
                           current_category=category_filter,
                           cart_items=cart_list,
                           open_cart=open_cart)

@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    emp_id = request.form.get('emp_id')
    product_id = request.form.get('product_id')
    qty = int(request.form.get('qty', 1))
    current_search = request.form.get('current_search', '')
    current_cat = request.form.get('current_cat', '')

    conn = get_db_connection()
    product = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()

    if product and product['stock'] >= qty:
        existing_item = conn.execute('SELECT * FROM carts WHERE emp_id = ? AND product_id = ?', (emp_id, product_id)).fetchone()
        if existing_item:
            conn.execute('UPDATE carts SET qty = qty + ? WHERE id = ?', (qty, existing_item['id']))
        else:
            conn.execute('INSERT INTO carts (emp_id, product_id, qty) VALUES (?, ?, ?)', (emp_id, product_id, qty))
        
        conn.execute('UPDATE products SET stock = stock - ?, reserved_stock = reserved_stock + ? WHERE id = ?', (qty, qty, product_id))
        conn.commit()
        flash(f'🛒 เพิ่ม {product["name"]} เรียบร้อย', 'success')
    else:
        flash('❌ ของหมดหรือมีไม่พอ', 'danger')
    
    conn.close()
    return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

@app.route('/remove_from_cart', methods=['POST'])
def remove_from_cart():
    cart_id = request.form.get('cart_id')
    emp_id = request.form.get('emp_id')
    product_id = request.form.get('product_id')
    qty = int(request.form.get('qty'))
    current_search = request.form.get('current_search', '')
    current_cat = request.form.get('current_cat', '')

    conn = get_db_connection()
    conn.execute('DELETE FROM carts WHERE id = ?', (cart_id,))
    conn.execute('UPDATE products SET stock = stock + ?, reserved_stock = reserved_stock - ? WHERE id = ?', (qty, qty, product_id))
    conn.commit()
    conn.close()
    
    return redirect(url_for('menu', emp_id=emp_id, open_cart='true', search=current_search, category=current_cat))
    
@app.route('/confirm_withdrawal', methods=['POST'])
def confirm_withdrawal():
    emp_id = request.form.get('emp_id')
    conn = get_db_connection()
    
    user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    cart_items = conn.execute('''
        SELECT c.*, p.name, p.stock, p.unit 
        FROM carts c JOIN products p ON c.product_id = p.id 
        WHERE c.emp_id = ?
    ''', (emp_id,)).fetchall()
    
    if not cart_items: 
        conn.close()
        return redirect(url_for('index'))
    
    msg_list = [f"🚀 *มีคำขอเบิกใหม่* 🚀\n👤 ผู้เบิก: {user['name']}\n📍 แผนก: {user['department']} ({user['location']})"]
    
    thai_now = get_thailand_time().strftime('%Y-%m-%d %H:%M:%S')
    
    for item in cart_items:
        item_name = item['name']
        
        # --- ส่วนที่แก้ไข: เช็คว่าเป็นตระกูลหมวกเซฟตี้หรือไม่ ---
        if "หมวกเซฟตี้" in item_name or "Helmet" in item_name:
            # ใช้ Logic อัปเดต (หรือ Insert ใหม่ถ้ายังไม่มี) เพื่อเก็บวันที่เบิกล่าสุดไว้เช็ค 2 ปี
            # เราจะเช็คจาก emp_id และ product_id เดิมที่มี Action นี้อยู่แล้ว
            existing_helmet = conn.execute('''
                SELECT id FROM transaction_logs 
                WHERE emp_id = ? AND product_id = ? AND action = 'เบิกหมวกเซฟตี้ (รอบ 2 ปี)'
            ''', (emp_id, item['product_id'])).fetchone()

            if existing_helmet:
                # ถ้าเคยเบิกแล้ว ให้ UPDATE วันที่เบิกล่าสุดทับของเดิม
                conn.execute('''
                    UPDATE transaction_logs 
                    SET qty = ?, timestamp = ?, status = 'Pending'
                    WHERE id = ?
                ''', (item['qty'], thai_now, existing_helmet['id']))
            else:
                # ถ้ายังไม่เคยเบิกหมวกใบนี้เลย ให้ INSERT ใหม่
                conn.execute('''
                    INSERT INTO transaction_logs (emp_id, product_id, action, qty, status, timestamp) 
                    VALUES (?, ?, 'เบิกหมวกเซฟตี้ (รอบ 2 ปี)', ?, 'Pending', ?)
                ''', (emp_id, item['product_id'], item['qty'], thai_now))
        else:
            # กรณีของทั่วไป: INSERT เป็นประวัติใหม่เสมอ
            conn.execute('''
                INSERT INTO transaction_logs (emp_id, product_id, action, qty, status, timestamp) 
                VALUES (?, ?, 'ขอเบิกอุปกรณ์', ?, 'Pending', ?)
            ''', (emp_id, item['product_id'], item['qty'], thai_now))
        
        # คำนวณสต็อกคงเหลือโชว์ใน Line
        remain_after = item['stock']
        msg_list.append(f"📦 {item_name}\n   🔹 จำนวน: {item['qty']} {item['unit']}\n   ⚠️ คงเหลือหลังเบิก: {remain_after}")

    # ล้างตะกร้า
    conn.execute('DELETE FROM carts WHERE emp_id = ?', (emp_id,))
    conn.commit()
    conn.close()

    # ส่งข้อความเข้า Line
    send_line_message("\n".join(msg_list))
    
    flash('✅ ส่งคำขอเรียบร้อย! ระบบบันทึกรอบการเบิกหมวกเซฟตี้ให้คุณแล้ว', 'success')
    return redirect(url_for('menu', emp_id=emp_id))
 
# ==========================================
# 🔐 ส่วนของแอดมิน (ADMIN)
# ==========================================

# สูตรคำนวณวันหมดโดยประมาณ
# (สต็อกปัจจุบัน / อัตราการเบิกเฉลี่ยต่อวัน)
def get_estimated_days_left(stock, withdraw_total):
    if withdraw_total == 0: return "ไม่มีการเบิก"
    daily_avg = withdraw_total / 30 # หาร 30 วัน
    days_left = stock / daily_avg
    return math.ceil(days_left)

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db_connection()
        admin = conn.execute('SELECT * FROM admins WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        # ใช้ check_password_hash ในการตรวจสอบ
        if admin and check_password_hash(admin['password'], password):
            session['admin_logged_in'] = True
            session['admin_name'] = admin['name']
            session['admin_role'] = admin['role']
            # ตั้งเวลาหมดอายุของ Session (Security Improvement)
            session.permanent = True
            app.permanent_session_lifetime = timedelta(minutes=60) # ให้ Login ค้างไว้ได้ 60 นาที
            
            return redirect(url_for('admin_dashboard'))
        
        flash('❌ ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง', 'danger')
    return render_template('admin_login.html')

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    role = session.get('admin_role', 'superadmin')
    
    # --- Filter ตาม Role และการเลือกสถานที่ (คงเดิม) ---
    role_log_filter = ""
    if role == 'admin_pc1':
        role_log_filter = " AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')"
    elif role == 'admin_cc':
        role_log_filter = " AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')"

    selected_loc = request.args.get('log_loc', '') 
    super_admin_filter = ""
    if role == 'superadmin':
        if selected_loc == 'PC1':
            super_admin_filter = " AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')"
        elif selected_loc == 'CC':
            super_admin_filter = " AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')"
    
    final_log_filter = role_log_filter + super_admin_filter

    product_loc_filter = ""
    if role == 'admin_pc1':
        product_loc_filter = " AND (location LIKE '%PC1%')"
    elif role == 'admin_cc':
        product_loc_filter = " AND (location LIKE '%Coil Center%' OR location LIKE '%CC%')"

    page = request.args.get('page', 1, type=int)
    per_page = 100
    offset = (page - 1) * per_page
    
    conn = get_db_connection()

    # --- 1. Analytics: กราฟการเบิกจ่ายแยกตามแผนก (30 วันล่าสุด) ---
    # เปลี่ยนจาก COUNT(*) เป็น SUM(qty) เพื่อดูปริมาณการเบิกจริง
    chart_query = f'''
        SELECT u.department, SUM(l.qty) as total_qty 
        FROM transaction_logs l
        JOIN users u ON l.emp_id = u.emp_id
        WHERE l.status = 'Approved' 
        AND l.timestamp >= date('now', '-30 days')
        GROUP BY u.department
    '''
    chart_results = conn.execute(chart_query).fetchall()
    dept_labels = [row['department'] if row['department'] else 'ไม่ระบุ' for row in chart_results]
    dept_values = [int(row['total_qty']) for row in chart_results] # มั่นใจว่าเป็น Integer

    print(f"DEBUG Chart Labels: {dept_labels}") # ดูใน Terminal
    print(f"DEBUG Chart Values: {dept_values}")

    # --- 2. Analytics: ของที่ถูกเบิกสูงสุด 5 อันดับแรก (Top 5 Items) ---
    top_items_query = f'''
        SELECT p.name, SUM(l.qty) as total_qty, p.unit
        FROM transaction_logs l
        JOIN products p ON l.product_id = p.id
        JOIN users u ON l.emp_id = u.emp_id
        WHERE l.status = 'Approved' {final_log_filter}
        GROUP BY p.id 
        ORDER BY total_qty DESC LIMIT 5
    '''
    top_items = conn.execute(top_items_query).fetchall()

    # --- ดึงข้อมูลส่วนอื่นๆ (คงเดิม) ---
    pending_query = f'''
        SELECT l.*, u.name as emp_name, u.department, u.location, p.name as product_name, p.unit
        FROM transaction_logs l
        LEFT JOIN users u ON l.emp_id = u.emp_id
        LEFT JOIN products p ON l.product_id = p.id
        WHERE l.status = 'Pending' {role_log_filter} 
        ORDER BY l.timestamp ASC
    '''
    pending_logs = conn.execute(pending_query).fetchall()

    stock_search = request.args.get('stock_search', '')
    stock_cat = request.args.get('stock_cat', '')
    stock_query = f"SELECT * FROM products WHERE 1=1 {product_loc_filter}"
    stock_params = []
    if stock_search:
        stock_query += " AND (name LIKE ? OR code LIKE ?)"
        stock_params.extend([f'%{stock_search}%', f'%{stock_search}%'])
    if stock_cat:
        stock_query += " AND category = ?"
        stock_params.append(stock_cat)
    stock_query += f" LIMIT {per_page} OFFSET {offset}"
    all_stock = conn.execute(stock_query, stock_params).fetchall()
    
    categories = conn.execute(f"SELECT DISTINCT category FROM products WHERE 1=1 {product_loc_filter}").fetchall()

    logs = conn.execute(f'''
        SELECT l.*, u.name as emp_name, u.department, u.location, p.name as product_name, p.unit
        FROM transaction_logs l
        LEFT JOIN users u ON l.emp_id = u.emp_id
        LEFT JOIN products p ON l.product_id = p.id
        WHERE 1=1 {final_log_filter}
        ORDER BY l.timestamp DESC LIMIT ? OFFSET ?
    ''', (per_page, offset)).fetchall()

    count_query = f'''
        SELECT COUNT(*) FROM transaction_logs l 
        LEFT JOIN users u ON l.emp_id = u.emp_id 
        WHERE 1=1 {final_log_filter}
    '''
    total_logs = conn.execute(count_query).fetchone()[0]
    total_pages = math.ceil(total_logs / per_page)

    low_stock_query = f"SELECT * FROM products WHERE stock < safety_stock {product_loc_filter}"
    low_stock = conn.execute(low_stock_query).fetchall()

    conn.close()
    
    return render_template('admin_dashboard.html',
                           pending_logs=pending_logs,
                           items=all_stock,
                           categories=categories,
                           low_stock=low_stock,
                           logs=logs,
                           page=page, total_pages=total_pages,
                           dept_labels=dept_labels, dept_values=dept_values, # ข้อมูลสำหรับกราฟ
                           top_items=top_items, # ข้อมูลของเบิกสูงสุด
                           role=role,
                           selected_loc=selected_loc)

@app.route('/cron/daily_alert')
def daily_alert():
    # 1. เชื่อมต่อฐานข้อมูล
    conn = get_db_connection()
    
    # --- ส่วนที่ 1: เช็คของใกล้หมดอายุ (ภายใน 30 วัน) ---
    expiry_query = '''
        SELECT name, expiry_date, category FROM products 
        WHERE expiry_date IS NOT NULL AND expiry_date != '' 
        AND expiry_date <= date('now', '+30 days')
        AND (category LIKE '%ยา%' OR name LIKE '%Safety Helmet%' OR name LIKE '%Coffee%' OR name LIKE '%Tea%')
    '''
    expiring_items = conn.execute(expiry_query).fetchall()

    # --- ส่วนที่ 2: เช็คหมวกเซฟตี้ครบ 2 ปี (ย้อนหลัง 23 เดือนขึ้นไป) ---
    helmet_query = '''
        SELECT u.name as emp_name, u.department, p.name as product_name, l.timestamp
        FROM transaction_logs l
        JOIN users u ON l.emp_id = u.emp_id
        JOIN products p ON l.product_id = p.id
        WHERE p.name LIKE '%Helmet%' 
        AND l.status = 'Approved'
        AND l.timestamp <= datetime('now', '+7 hours', '-23 months')
    '''
    helmet_alerts = conn.execute(helmet_query).fetchall()
    conn.close()

    # --- ส่วนที่ 3: รวมข้อความและส่งเข้า LINE ---
    alert_triggered = False
    message = ""

    if expiring_items:
        alert_triggered = True
        message += "\n⚠️ [แจ้งเตือนของใกล้หมดอายุ]\n"
        for item in expiring_items:
            message += f"📦 {item['name']}\n📅 หมดอายุ: {item['expiry_date']}\n"

    if helmet_alerts:
        alert_triggered = True
        message += "\n👷 [ครบกำหนดเปลี่ยนหมวกเซฟตี้]\n"
        for alert in helmet_alerts:
            message += f"👤 คุณ{alert['emp_name']} ({alert['department']})\n📅 เบิกเมื่อ: {alert['timestamp']}\n"

    # ถ้ามีรายการผิดปกติ ให้ส่ง LINE ทันที
    if alert_triggered:
        send_line_message(message)
        return f"Alert sent: {message}", 200
    else:
        return "No alerts today", 200

@app.route('/admin/approve/<int:log_id>') # ฟังก์ชันนี้จะถูกเรียกเมื่อแอดมินกดอนุมัติการเบิก
def approve_request(log_id):
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    # 1. ดึงข้อมูลรายการเบิกที่รออนุมัติ
    log = conn.execute('SELECT * FROM transaction_logs WHERE id=?', (log_id,)).fetchone()
    
    if log and log['status'] == 'Pending':
        product_id = log['product_id']
        qty_to_withdraw = log['qty']
        
        # 2. ค้นหา Lot ของที่เก่าที่สุดที่มีของอยู่ (FIFO)
        # เรียงตาม received_date (วันที่รับ) และ id (ตัวไหนเข้าฐานข้อมูลก่อน)
        lots = conn.execute('''
            SELECT * FROM product_lots 
            WHERE product_id = ? AND qty > 0 
            ORDER BY received_date ASC, id ASC
        ''', (product_id,)).fetchall()

        remaining = qty_to_withdraw
        last_lot_id = None

        # 3. เริ่มวนลูปหักสต็อกทีละล็อตจนกว่าจะครบตามจำนวนที่เบิก
        for lot in lots:
            if remaining <= 0: break
            
            take = min(lot['qty'], remaining)
            
            # ✅ ต้องเอา Comment ออก เพื่อให้หักยอดออกจาก Lot จริงๆ
            conn.execute('UPDATE product_lots SET qty = qty - ? WHERE id = ?', (take, lot['id']))
            
            remaining -= take
            last_lot_id = lot['id'] 

        # 4. อัปเดตตารางหลัก
        # บันทึก lot_id ล่าสุด และใช้เวลาไทยจาก Python แทน SQL เพื่อความแม่นยำ
        thai_now = get_thailand_time().strftime('%Y-%m-%d %H:%M:%S')
        conn.execute('''
            UPDATE transaction_logs 
            SET status = "Approved", lot_id = ?, timestamp = ? 
            WHERE id = ?
        ''', (last_lot_id, thai_now, log_id))
        
        # อัปเดตยอดเบิกสะสมในตารางของหลัก
        conn.execute('UPDATE products SET withdraw = withdraw + ? WHERE id = ?', (qty_to_withdraw, product_id))
        
        conn.commit()
        
        # 5. เช็คแจ้งเตือน Safety Stock หลังตัดสต็อก
        check_safety_alert(product_id)
        
        flash('✅ อนุมัติและตัดสต็อกแบบ FIFO เรียบร้อยแล้ว', 'success')
    
    conn.close()
    return redirect(url_for('admin_dashboard'))

def check_safety_alert(product_id): # ฟังก์ชันนี้จะถูกเรียกหลังจากอนุมัติการเบิก เพื่อเช็คว่าของตัวนั้นๆ ต่ำกว่า Safety Stock หรือไม่
    conn = get_db_connection()
    product = conn.execute('SELECT name, stock, safety_stock, unit FROM products WHERE id = ?', (product_id,)).fetchone()
    conn.close()
    
    if product and product['stock'] <= product['safety_stock']:
        alert_msg = (
            f"⚠️ *แจ้งเตือนสต็อกต่ำกว่าเกณฑ์*\n"
            f"📦 ของ: {product['name']}\n"
            f"📉 คงเหลือปัจจุบัน: {product['stock']} {product['unit']}\n"
            f"🚩 จุดสั่งซื้อ (Safety): {product['safety_stock']} {product['unit']}\n"
            f"--- กรุณาพิจารณาสั่งซื้อเพิ่ม ---"
        )
        send_line_message(alert_msg)

@app.route('/admin/reject/<int:log_id>')
def reject_request(log_id):
    conn = get_db_connection()
    log = conn.execute('SELECT * FROM transaction_logs WHERE id=?', (log_id,)).fetchone()
    if log and log['status'] == 'Pending':
        conn.execute('UPDATE products SET stock = stock + ?, reserved_stock = reserved_stock - ? WHERE id = ?', 
                     (log['qty'], log['qty'], log['product_id']))
        conn.execute('UPDATE transaction_logs SET status = "Rejected" WHERE id = ?', (log_id,))
        conn.commit()
        flash('❌ ปฏิเสธรายการและคืนสต็อกแล้ว', 'warning')
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/get_next_code')
def get_next_code():
    category = request.args.get('category', '')
    location = request.args.get('location', 'PC1') # ค่าเริ่มต้นเป็น PC1 ถ้าไม่ส่งมา

    # 1. กำหนดตัวย่อของหมวดหมู่ (คุณสามารถเพิ่มลดได้ตามต้องการ)
    prefix_map = {
        'แม่บ้าน': 'MAID',
        'Safety': 'SAF',
        'ยา': 'MEDIC',
        'ของใช้สำนักงาน': 'STAT',
        'อื่นๆ': 'ITEM'
    }
    cat_prefix = prefix_map.get(category, 'ITEM') # ถ้าหาไม่เจอให้ใช้ ITEM
    loc_prefix = 'PC1' if 'PC1' in location else 'CC'

    conn = get_db_connection()
    # 2. ค้นหารหัสล่าสุดของ หมวดหมู่ และ สถานที่ นี้
    query = "SELECT code FROM products WHERE category = ? AND location LIKE ? ORDER BY code DESC LIMIT 1"
    row = conn.execute(query, (category, f"%{loc_prefix}%")).fetchone()
    conn.close()

    # 3. คำนวณเลขถัดไป
    next_number = 1
    if row and row['code']:
        last_code = row['code']
        # สมมติรหัสเก่าคือ MAID-PC1-001 เราจะแยกเอาเลข 001 มา +1
        parts = last_code.split('-')
        if len(parts) >= 3 and parts[-1].isdigit():
            next_number = int(parts[-1]) + 1

    # 4. สร้างรหัสใหม่ เช่น MAID-PC1-002
    new_code = f"{cat_prefix}-{loc_prefix}-{next_number:03d}"
    
    return jsonify({'next_code': new_code})

@app.route('/admin/add_product', methods=['POST'])
def add_product():
    if not session.get('admin_logged_in'): 
        return jsonify({'success': False, 'message': 'Unauthorized'})
    
    code = request.form.get('code')
    name = request.form.get('name')
    category = request.form.get('category')
    unit = request.form.get('unit')
    location = request.form.get('location')
    safety_stock = int(request.form.get('safety_stock') or 0)
    stock = int(request.form.get('stock') or 0)
    expiry_date = request.form.get('expiry_date', '').strip()
    
    # ✅ 1. จัดการเรื่องวันที่รับเข้า (ถ้าว่าง ให้ใช้วันนี้)
    received_date = request.form.get('received_date', '').strip()
    if not received_date:
        received_date = get_thailand_time().strftime('%Y-%m-%d')

    # ✅ 2. จัดการเรื่อง Lot No (อิงจาก received_date)
    lot_no = request.form.get('lot_no', '').strip()
    if not lot_no:
        try:
            date_obj = datetime.strptime(received_date, '%Y-%m-%d')
            lot_no = date_obj.strftime('%d%m%Y')
        except:
            lot_no = get_thailand_time().strftime('%d%m%Y')
        else:
            lot_no = get_thailand_time().strftime('%d%m%Y')

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        
        # 1. บันทึกข้อมูลลงตารางหลัก (products)
        cursor.execute('''
            INSERT INTO products (code, name, category, unit, location, safety_stock, stock, expiry_date, received_date, lot_no)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (code, name, category, unit, location, safety_stock, stock, expiry_date, received_date, lot_no))
        
        # ดึง ID ของสินค้าที่เพิ่งถูกสร้างขึ้นมาสดๆ ร้อนๆ
        new_product_id = cursor.lastrowid 

        # 2. ถ้าใส่จำนวนเริ่มต้นมาด้วย ให้บันทึกลงตาราง product_lots ทันที (เป็น Lot ที่ 1)
        if stock > 0:
            cursor.execute('''
                INSERT INTO product_lots (product_id, lot_number, qty, received_date, expiry_date)
                VALUES (?, ?, ?, ?, ?)
            ''', (new_product_id, lot_no, stock, received_date, expiry_date))
            
            # 3. แอบบันทึกประวัติ (Log) ไว้ด้วยว่าแอดมินนำเข้าเป็น Lot แรก
            admin_name = session.get('admin_name')
            thai_now = get_thailand_time().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute('''
                INSERT INTO transaction_logs (emp_id, product_id, action, qty, status, timestamp) 
                VALUES (?, ?, ?, ?, 'Completed', ?)
            ''', (f"ADMIN:{admin_name}", new_product_id, f"รับเข้า Lot แรก: {lot_no}", stock, thai_now))

        conn.commit()
        return jsonify({'success': True, 'message': 'เพิ่มข้อมูลสินค้าและสร้าง Lot สำเร็จ'})
        
    except Exception as e:
        print(f"Error adding product: {e}")
        return jsonify({'success': False, 'message': f"เกิดข้อผิดพลาด: {e}"})
    finally:
        conn.close()

@app.route('/admin/reset_lock')
def reset_lock():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    conn = get_db_connection()
    conn.execute('UPDATE users SET is_locked = 0')
    conn.commit()
    conn.close()
    flash('✅ ปลดล็อกพนักงานทุกคนเรียบร้อยแล้ว', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/export')
def export_excel():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    role = session.get('admin_role', 'superadmin')
    conn = get_db_connection()
    
    # 1. กำหนดเงื่อนไข Location ตามสิทธิ์ (Role)
    location_filter = ""
    if role == 'admin_pc1':
        location_filter = "WHERE location LIKE '%PC1%'"
        filename = "Inventory_PC1.xlsx"
    elif role == 'admin_cc':
        location_filter = "WHERE location LIKE '%Coil Center%' OR location LIKE '%CC%'"
        filename = "Inventory_CC.xlsx"
    else: # superadmin
        location_filter = "" # ดึงทั้งหมด
        filename = "Inventory_ALL.xlsx"
        
    # 2. Query ดึงข้อมูลจากตารางของ (Inventory)
    query = f'''
        SELECT 
            code as 'รหัสของ',
            name as 'ชื่อของ',
            category as 'หมวดหมู่',
            unit as 'หน่วยนับ',
            location as 'สถานที่เก็บ (Location)',
            safety_stock as 'จุดสั่งซื้อ (Safety Stock)',
            stock as 'จำนวนคงเหลือ',
            lot_no as 'Lot No.',
            received_date as 'วันที่รับเข้า',
            expiry_date as 'วันหมดอายุ'
        FROM products
        {location_filter}
        ORDER BY location ASC, code ASC
    '''
    
    # อ่านข้อมูลเข้า Pandas
    df = pd.read_sql_query(query, conn)
    conn.close()
    
   # 3. สร้างไฟล์ Excel ใน Memory
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Inventory_Stock')
        
        # ปรับความกว้างของคอลัมน์ (แก้ Error float no len ตรงนี้)
        worksheet = writer.sheets['Inventory_Stock']
        for i, col in enumerate(df.columns):
            # 1. ความยาวของชื่อหัวคอลัมน์
            header_len = len(str(col))
            
            # 2. ความยาวของข้อมูลในคอลัมน์ (แปลงเป็น str ก่อนเสมอเพื่อป้องกัน Error)
            # ใช้ .astype(str) เพื่อแปลงตัวเลข/ค่าว่าง เป็นข้อความก่อนใช้ .len()
            if len(df) > 0:
                data_len = df[col].astype(str).str.len().max()
            else:
                data_len = 0
                
            # ป้องกันกรณีที่ max() คืนค่าเป็น NaN (ค่าว่าง)
            data_len = 0 if pd.isna(data_len) else data_len
            
            # 3. เลือกความยาวที่มากที่สุด แล้วบวกพื้นที่เผื่อไว้ 2
            column_len = max(header_len, data_len) + 2
            worksheet.set_column(i, i, int(column_len))
            
    output.seek(0)
    
    # โหลดไฟล์ลงเครื่อง
    return send_file(output, as_attachment=True, download_name=filename)

@app.route('/admin/import', methods=['POST'])
def import_excel():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    file = request.files.get('file')
    if file:
        try:
            df = pd.read_excel(file)
            df.columns = df.columns.str.strip()
            conn = get_db_connection()
            updated_count = 0
            inserted_count = 0

            for index, row in df.iterrows():
                code_col = next((col for col in df.columns if col.lower() in ['code', 'รหัส', 'รหัสของ']), None)
                if not code_col: continue

                code = str(row[code_col]).strip()
                if not code or code.lower() == 'nan': continue
                
                name = row.get('Name', row.get('ชื่อของ', 'No Name'))

                try:
                    stock = int(row.get('Qty', row.get('จำนวนคงเหลือ', 0)))
                except:
                    stock = 0
                    
                try:
                    safety_stock = int(row.get('Safety Stock', row.get('จุดสั่งซื้อ', 0))) # ชื่อตัวแปร safety_stock (มี e)
                except:
                    safety_stock = 0

                category = row.get('Category', row.get('หมวดหมู่', 'General'))
                unit = row.get('Unit', row.get('หน่วยนับ', 'PCS'))
                location = row.get('Location', row.get('สถานที่เก็บ (Location)', '-'))

                existing = conn.execute('SELECT id FROM products WHERE code = ?', (code,)).fetchone()
                
                if existing:
                    # แก้ไข safty_stock -> safety_stock
                    conn.execute('UPDATE products SET name=?, stock=?, safety_stock=?, category=?, unit=?, location=? WHERE id=?', 
                                 (name, stock, safety_stock, category, unit, location, existing['id']))
                    updated_count += 1
                else:
                    # แก้ไขเพิ่ม ? ให้ครบ 7 ตัว และแก้ safty_stock -> safety_stock
                    conn.execute('''
                        INSERT INTO products (code, name, stock, safety_stock, category, unit, location, withdraw, reserved_stock) 
                        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)
                    ''', (code, name, stock, safety_stock, category, unit, location))
                    inserted_count += 1

            conn.commit()
            conn.close()
            flash(f'✅ นำเข้าข้อมูลสำเร็จ (อัปเดต {updated_count}, เพิ่มใหม่ {inserted_count})', 'success')
        except Exception as e:
            flash(f'❌ เกิดข้อผิดพลาด: {e}', 'danger')
            
    return redirect(url_for('admin_dashboard'))

# 1. ดึงข้อมูลของเดิมมาแสดงในหน้าต่างแก้ไข
@app.route('/admin/get_product/<code>')
def get_product(code):
    if not session.get('admin_logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM products WHERE code = ?', (code,)).fetchone()
    conn.close()
    if product:
        return jsonify(dict(product))
    return jsonify({'error': 'Product not found'}), 404

@app.route('/admin/edit_product', methods=['POST'])
def edit_product():
    if not session.get('admin_logged_in'): 
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    code = request.form.get('code')
    name = request.form.get('name')
    unit = request.form.get('unit')
    safety_stock = request.form.get('safety_stock', 0)
    stock = request.form.get('stock', 0)
    expiry_date = request.form.get('expiry_date', '')

    conn = get_db_connection()
    try:
        conn.execute('''
            UPDATE products 
            SET name=?, unit=?, safety_stock=?, stock=?, expiry_date=?
            WHERE code=?
        ''', (name, unit, safety_stock, stock, expiry_date, code))
        conn.commit()
        # คืนค่า Success เป็น JSON แทนการ Redirect
        return jsonify({'success': True, 'message': 'แก้ไขข้อมูลของเรียบร้อย'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        conn.close()

@app.route('/admin/filter_low_stock')
def filter_low_stock():
    if not session.get('admin_logged_in'): return "Unauthorized", 401
    
    role = session.get('admin_role', 'superadmin')
    # ต้องดึงค่า low_stock_cat ที่ส่งมาจาก JavaScript
    cat = request.args.get('low_stock_cat', '')
    
    conn = get_db_connection()
    
    # กรองตาม Role ของ Admin (PC1/CC)
    loc_filter = ""
    if role == 'admin_pc1':
        loc_filter = " AND (location LIKE '%PC1%')"
    elif role == 'admin_cc':
        loc_filter = " AND (location LIKE '%Coil Center%' OR location LIKE '%CC%')"

    # Query หาของที่ต่ำกว่าเกณฑ์
    sql = f"SELECT * FROM products WHERE stock <= safety_stock {loc_filter}"
    params = []
    
    if cat:
        sql += " AND category = ?"
        params.append(cat)
        
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    
    # ส่งผลลัพธ์กลับไปที่หน้า HTML (ส่วนของแถวตาราง)
    return render_template('low_stock_row.html', low_stock=rows)

@app.route('/admin/filter_stock')
def filter_stock():
    if not session.get('admin_logged_in'): return "Unauthorized", 401
    
    role = session.get('admin_role', 'superadmin')
    cat = request.args.get('cat', '')
    search = request.args.get('search', '')
    
    conn = get_db_connection()
    
    # 1. กรองสิทธิ์ตาม Role (เหมือนเดิม)
    loc_filter = ""
    if role == 'admin_pc1':
        loc_filter = " AND (location LIKE '%PC1%')"
    elif role == 'admin_cc':
        loc_filter = " AND (location LIKE '%Coil Center%' OR location LIKE '%CC%')"

    # 2. สร้าง Query
    sql = f"SELECT * FROM products WHERE 1=1 {loc_filter}"
    params = []
    
    if cat:
        sql += " AND category = ?"
        params.append(cat)
    
    if search:
        sql += " AND (name LIKE ? OR code LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%'])
        
    # จำกัดผลลัพธ์ 100 รายการเพื่อให้โหลดไว (เพราะเป็น Real-time search)
    sql += " ORDER BY code ASC LIMIT 500" 
    
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    
    # ส่งกลับไปเฉพาะส่วนตาราง
    return render_template('stock_row.html', items=rows)

@app.route('/admin/filter_logs')
def filter_logs():
    if not session.get('admin_logged_in'): return "Unauthorized", 401
    
    role = session.get('admin_role', 'superadmin')
    log_loc = request.args.get('log_loc', '')
    page = request.args.get('page', 1, type=int)
    per_page = 100
    offset = (page - 1) * per_page
    
    conn = get_db_connection()
    
    # 1. Logic การกรอง (เหมือนใน admin_dashboard)
    role_log_filter = ""
    if role == 'admin_pc1':
        role_log_filter = " AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')"
    elif role == 'admin_cc':
        role_log_filter = " AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')"

    super_admin_filter = ""
    if role == 'superadmin':
        if log_loc == 'PC1':
            super_admin_filter = " AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')"
        elif log_loc == 'CC':
            super_admin_filter = " AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')"
    
    final_log_filter = role_log_filter + super_admin_filter

    # 2. Query ข้อมูล
    logs = conn.execute(f'''
        SELECT l.*, u.name as emp_name, u.department, u.location, p.name as product_name, p.unit
        FROM transaction_logs l
        LEFT JOIN users u ON l.emp_id = u.emp_id
        LEFT JOIN products p ON l.product_id = p.id
        WHERE 1=1 {final_log_filter}
        ORDER BY l.timestamp DESC LIMIT ? OFFSET ?
    ''', (per_page, offset)).fetchall()
    
    conn.close()
    
    # ส่งกลับไปเฉพาะส่วนแถวตาราง
    return render_template('admin_log_row.html', logs=logs)

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

# --- ฟังก์ชันเบิกของแบบ FIFO ---
def withdraw_fifo_logic(product_id, qty_to_withdraw, emp_id):
    conn = get_db_connection()
    # ดึง Lot ที่มีของอยู่ เรียงตามวันที่รับเข้าจากเก่าไปใหม่
    lots = conn.execute('''
        SELECT * FROM product_lots 
        WHERE product_id = ? AND qty > 0 
        ORDER BY received_date ASC, id ASC
    ''', (product_id,)).fetchall()

    remaining = qty_to_withdraw
    for lot in lots:
        if remaining <= 0: break
        
        take = min(lot['qty'], remaining)
        # ตัดสต็อกใน Lot
        conn.execute('UPDATE product_lots SET qty = qty - ? WHERE id = ?', (take, lot['id']))
        remaining -= take
        
        # บันทึก Transaction แยกตาม Lot เพื่อความแม่นยำ
        conn.execute('''
            INSERT INTO transaction_logs (emp_id, product_id, lot_id, action, qty, status)
            VALUES (?, ?, ?, 'เบิกของ (FIFO)', ?, 'Approved')
        ''', (emp_id, product_id, lot['id'], take))

    # อัปเดตยอดรวมในตารางหลัก
    conn.execute('UPDATE products SET stock = stock - ? WHERE id = ?', (qty_to_withdraw, product_id))
    conn.commit()
    conn.close()

@app.route('/admin/add_product_ajax', methods=['POST'])
def add_product_ajax():
    if not session.get('admin_logged_in'): 
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    product_id = request.form.get('product_id')
    lot_number = request.form.get('lot_number')
    qty = int(request.form.get('add_qty', 0))
    receive_date = request.form.get('receive_date')
    expire_date = request.form.get('expire_date')

    conn = get_db_connection()
    try:
        # 1. เพิ่มข้อมูลลงใน Table product_lots
        conn.execute('''
            INSERT INTO product_lots (product_id, lot_number, qty, received_date, expiry_date)
            VALUES (?, ?, ?, ?, ?)
        ''', (product_id, lot_number, qty, receive_date, expire_date))

        # 2. อัปเดตยอดรวม Stock ใน Table products
        conn.execute('UPDATE products SET stock = stock + ? WHERE id = ?', (qty, product_id))

        # 3. บันทึก Log การนำเข้า
        admin_name = session.get('admin_name')
        conn.execute('''
            INSERT INTO transaction_logs (emp_id, product_id, action, qty, status) 
            VALUES (?, ?, ?, ?, 'Completed')
        ''', (f"ADMIN:{admin_name}", product_id, f"รับเข้า Lot: {lot_number}", qty))

        conn.commit()
        return jsonify({'success': True, 'message': 'เพิ่ม Lot ของสำเร็จ!'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        conn.close()

@app.route('/admin/monthly_report')
def monthly_report():
    if not session.get('admin_logged_in'): return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    # ดึงข้อมูลการเบิกจ่ายที่ Approved แล้วในเดือนปัจจุบัน
    query = '''
        SELECT 
            p.code AS "รหัสของ", 
            p.name AS "ชื่อของ", 
            u.department AS "แผนกที่เบิก", 
            SUM(l.qty) AS "จำนวนที่เบิกทั้งหมด",
            p.unit AS "หน่วย"
        FROM transaction_logs l
        JOIN products p ON l.product_id = p.id
        JOIN users u ON l.emp_id = u.emp_id
        WHERE l.status = 'Approved' 
        AND strftime('%m', l.timestamp) = strftime('%m', 'now')
        GROUP BY p.id, u.department
    '''
    try:
        df = pd.read_sql_query(query, conn)
        conn.close()

        # สร้างไฟล์ Excel ในหน่วยความจำ
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Monthly_Summary')
        output.seek(0)

        filename = f"Summary_Report_{datetime.now().strftime('%B_%Y')}.xlsx"
        return send_file(output, as_attachment=True, download_name=filename)
    except Exception as e:
        conn.close()
        flash(f"เกิดข้อผิดพลาดในการสร้างรายงาน: {e}", "danger")
        return "กำลังเตรียมไฟล์รายงาน..."

def get_inventory_forecast():
    conn = get_db_connection()
    # คำนวณการเบิกเฉลี่ยต่อวันในช่วง 30 วันที่ผ่านมา
    stats = conn.execute('''
        SELECT 
            p.id, 
            p.name, 
            p.stock, 
            SUM(l.qty) / 30.0 as daily_avg
        FROM products p
        LEFT JOIN transaction_logs l ON p.id = l.product_id 
        WHERE l.status = 'Approved' AND l.timestamp >= date('now', '-30 days')
        GROUP BY p.id
    ''').fetchall()
    conn.close()

    forecast_results = []
    for row in stats:
        daily_avg = row['daily_avg'] or 0
        if daily_avg > 0:
            days_left = row['stock'] / daily_avg
            forecast_results.append({
                'name': row['name'],
                'days_left': math.ceil(days_left)
            })
    return forecast_results

@app.route('/generate_qr/<code>')  # เปลี่ยน parameter เป็น code
def generate_qr(code):
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(code)  # ใช้ตัวแปร code
    qr.make(fit=True)
    
    img = qr.make_image(fill_color="black", back_color="white")
    img_io = io.BytesIO()
    img.save(img_io, 'PNG')
    img_io.seek(0)
    return send_file(img_io, mimetype='image/png')

@app.route('/print_all_qrs')
def print_all_qrs():
    conn = get_db_connection()
    # แก้คำสั่ง SQL ตรงนี้ (เปลี่ยนชื่อตารางให้ถูกด้วยนะครับ เช่น products)
    items = conn.execute('SELECT code, name FROM products').fetchall() 
    conn.close()
    return render_template('print_qrs.html', items=items)

# --- หน้าจอเปิดกล้องสแกน ---
@app.route('/scanner')
def scanner_page():
    # ไม่ต้องล็อกอินพนักงานก็สแกนได้ เพื่อความรวดเร็ว
    return render_template('scanner.html')

# --- ตัวรับค่าจาก QR Code (เมื่อสแกนติด) ---
@app.route('/scan/<product_code>')
def scan_product(product_code):
    conn = get_db_connection()
    # ค้นหา ID ของจากรหัส (Code) ที่สแกนได้
    product = conn.execute('SELECT id FROM products WHERE code = ?', (product_code,)).fetchone()
    conn.close()
    
    if product:
        # ถ้าพบของ ให้ส่งไปหน้าเมนู พร้อม Filter ของตัวนั้นทันที
        # เราส่ง open_item ไปเพื่อให้ JavaScript ในหน้าเมนูรู้ว่าต้องเปิด Popup ตัวไหน
        flash(f'🔍 พบของ: {product_code}', 'success')
        return redirect(url_for('menu', search=product_code, open_item=product['id']))
    
    flash(f'❌ ไม่พบรหัสของ: {product_code} ในระบบ', 'danger')
    return redirect(url_for('index'))

# --- 1. หน้าจอแสดงรายชื่อพนักงานที่สถานะ Online ค้างอยู่ ---
@app.route('/admin/manage_zombies')
def manage_zombies():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    # ดึงรายชื่อพนักงานที่ is_locked = 1 เรียงตามเวลาล่าสุดที่ใช้งาน
    zombie_users = conn.execute('''
        SELECT emp_id, name, department, location, last_seen 
        FROM users 
        WHERE is_locked = 1 
        ORDER BY last_seen DESC
    ''').fetchall()
    conn.close()
    
    # ส่งค่า role ไปด้วยเพื่อให้ Navbar ทำงานได้
    role = session.get('admin_role', 'superadmin')
    return render_template('manage_zombies.html', zombie_users=zombie_users, role=role)

# --- 2. ฟังก์ชันปลดล็อกรายบุคคล ---
@app.route('/admin/unlock_user/<emp_id>')
def unlock_user(emp_id):
    if not session.get('admin_logged_in'): 
        return redirect(url_for('admin_login'))
    
    conn = get_db_connection()
    conn.execute("UPDATE users SET is_locked = 0 WHERE emp_id = ?", (emp_id,))
    conn.commit()
    conn.close()
    
    flash(f'✅ ปลดล็อกรหัส {emp_id} เรียบร้อยแล้ว', 'success')
    return redirect(url_for('manage_zombies'))    

app.permanent_session_lifetime = timedelta(minutes=30) # บังคับให้ Logout หากไม่มีการเคลื่อนไหวใน 30 นาที
@app.before_request
def make_session_permanent():
    session.permanent = True # ทำให้ทุก Session มีวันหมดอายุตามที่ตั้งไว้

@app.route('/admin/list_users')
def list_users():
    if not session.get('admin_logged_in'): 
        return jsonify([])
        
    role = session.get('admin_role')
    conn = get_db_connection()
    
    # --- Logic กรองตามสิทธิ์ ---
    if role == 'admin_pc1':
        query = "SELECT emp_id, name, department, location FROM users WHERE location = 'PC1'"
    elif role == 'admin_cc':
        query = "SELECT emp_id, name, department, location FROM users WHERE location = 'Coil Center'"
    else:
        # Superadmin เห็นทั้งหมด
        query = "SELECT emp_id, name, department, location FROM users"
        
    users = conn.execute(query).fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

@app.route('/admin/add_user_ajax', methods=['POST'])
def add_user_ajax():
    if not session.get('admin_logged_in'): return jsonify({'success': False, 'message': 'Unauthorized'})
    emp_id = request.form.get('emp_id')
    name = request.form.get('name')
    dept = request.form.get('department')
    loc = request.form.get('location')
    
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO users (emp_id, name, department, location, is_locked) VALUES (?, ?, ?, ?, 0)',
                     (emp_id, name, dept, loc))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'message': 'รหัสพนักงานซ้ำหรือข้อมูลผิดพลาด'})
    finally:
        conn.close()

@app.route('/admin/delete_user/<emp_id>', methods=['POST'])
def delete_user(emp_id):
    if not session.get('admin_logged_in'): 
        return jsonify({'success': False, 'message': 'Unauthorized'})
        
    role = session.get('admin_role')
    conn = get_db_connection()
    
    # ตรวจสอบสิทธิ์ก่อนลบจริง
    user = conn.execute('SELECT location FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    
    if not user:
        return jsonify({'success': False, 'message': 'ไม่พบพนักงาน'})
        
    # ถ้าไม่ใช่ Super Admin และพนักงานไม่ได้อยู่โรงงานตัวเอง จะลบไม่ได้
    if role == 'admin_pc1' and user['location'] != 'PC1':
        return jsonify({'success': False, 'message': 'ไม่มีสิทธิ์จัดการพนักงานนอก PC1'})
    if role == 'admin_cc' and user['location'] != 'Coil Center':
        return jsonify({'success': False, 'message': 'ไม่มีสิทธิ์จัดการพนักงานนอก CC'})

    conn.execute('DELETE FROM users WHERE emp_id = ?', (emp_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/update_user_ajax', methods=['POST'])
def update_user_ajax():
    if not session.get('admin_logged_in'): return jsonify({'success': False})
    
    emp_id = request.form.get('emp_id')
    name = request.form.get('name')
    dept = request.form.get('department')
    loc = request.form.get('location')
    
    conn = get_db_connection()
    # อัปเดตข้อมูลพนักงานยกเว้นรหัส (รหัสเป็น Key หลักห้ามแก้)
    conn.execute('UPDATE users SET name=?, department=?, location=? WHERE emp_id=?', 
                 (name, dept, loc, emp_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# 1. ฟังก์ชันสร้างตาราง (รันครั้งเดียว)
@app.route('/setup_dept_table')
def setup_dept_table():
    conn = get_db_connection()
    try:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL
            )
        ''')
        
        count = conn.execute("SELECT COUNT(*) FROM departments").fetchone()[0]
        if count == 0:
            # แก้ไขตรงนี้: เติม , หลังข้อความในทุกวงเล็บ
            sample_depts = [
                ('-',),
                ('Accounting',),
                ('General Affairs & Purchase & IT',),
                ('General Affairs',),
                ('Quality Control',),
                ('Maintenance',),
                ('Administration',),
                ('Sales & BOI ',),
                ('Technical Development & Quality Control',),
                ('Purchase',),
                ('Production control & Logistic',),
                ('Manufacturing',),
                ('IT',)
            ]
            conn.executemany("INSERT INTO departments (name) VALUES (?)", sample_depts)
        
        conn.commit()
        return "✅ ตาราง departments พร้อมใช้งานแล้ว!"
    except Exception as e:
        return f"❌ ข้อผิดพลาด: {e}"
    finally:
        conn.close()

# 2. API สำหรับดึงรายชื่อแผนกไปใช้ใน Dropdown
@app.route('/admin/list_departments')
def list_departments():
    if not session.get('admin_logged_in'): return jsonify([])
    
    conn = get_db_connection()
    # ดึงชื่อแผนกทั้งหมดแบบไม่ซ้ำกัน ไม่ว่าจะแอดมินคนไหนก็เห็นแผนกเหมือนกันเพื่อเลือกใส่ให้พนักงาน
    depts = conn.execute("SELECT DISTINCT name FROM departments ORDER BY name ASC").fetchall()
    conn.close()
    
    return jsonify([dict(d) for d in depts])

# ฟังก์ชันที่จะให้ทำงานอัตโนมัติ (Background Task)
def scheduled_daily_alert():
    with app.app_context():
        print(f"🔔 [SYSTEM] เริ่มรัน Job อัตโนมัติ: {get_thailand_time().strftime('%Y-%m-%d %H:%M:%S')}")
        
        conn = get_db_connection()
        
      # --- ส่วนที่ 1: เช็คของใกล้หมดอายุ ---
        expiry_query = '''
            SELECT name, expiry_date 
            FROM products 
            WHERE expiry_date IS NOT NULL AND expiry_date != '' 
            AND expiry_date <= date('now', '+30 days')
            AND (category LIKE '%ยา%' OR name LIKE '%Helmet%' OR name LIKE '%Coffee%')
        '''
        # มั่นใจว่าได้ใช้ conn.row_factory = sqlite3.Row ใน get_db_connection() แล้ว
        expiring_items = conn.execute(expiry_query).fetchall()

        # --- 2. เช็คหมวกเซฟตี้ (เพิ่มคอลัมน์ที่ขาดไป) ---
        helmet_query = '''
            SELECT 
                u.name AS emp_name, 
                u.department, 
                u.location, 
                l.action AS product_name, 
                l.timestamp
            FROM transaction_logs l
            JOIN users u ON l.emp_id = u.emp_id
            WHERE (l.action LIKE '%หมวก%' OR l.action LIKE '%Helmet%')
            AND l.status = 'Approved'
            AND strftime('%Y-%m', l.timestamp) <= strftime('%Y-%m', 'now', '+7 hours', '-23 months')
        '''
        helmet_alerts = conn.execute(helmet_query).fetchall()
        
        # --- ส่วนของ DEBUG (ดูใน Terminal) ---
        print(f"🔎 [DEBUG SQL] ดึงข้อมูลได้ทั้งหมด: {len(helmet_alerts)} รายการ")
        for row in helmet_alerts:
            print(f"👤 พบพนักงาน: {row['emp_name']} | วันที่เบิก: {row['timestamp']}")
        # ------------------------------------

        conn.close()
        
       # --- 3. รวมข้อความและส่งเข้า LINE (ปรับฟอร์มตามรูปภาพ) ---
        message = ""
        
        # ส่วนของของใกล้หมดอายุ (ถ้ามี)
        if expiring_items:
            message += "⚠️ [แจ้งเตือนของใกล้หมดอายุ]\n"
            for item in expiring_items:
                message += f"📦 {item['name']}\n📅 หมดอายุ: {item['expiry_date']}\n"
            message += "--------------------------\n"

        # ส่วนของหมวกเซฟตี้ (ฟอร์มตามที่คุณต้องการ)
        if helmet_alerts:
            message += "👷 [ครบกำหนดเปลี่ยนหมวกเซฟตี้]\n"
            for alert in helmet_alerts:
                # ดึงชื่อและแผนกมาแสดงในบรรทัดเดียวกัน
                emp_info = f"{alert['emp_name']} ({alert['department']} - {alert['location']})"
                
                message += f"👤 คุณ{emp_info}\n"
                message += f"🗓️ เบิกเมื่อ: {alert['timestamp']}\n"
            
        # ถ้ามีข้อความให้ส่งเข้า LINE
        if message:
            send_line_message(message.strip())
            print("✅ ส่งแจ้งเตือนฟอร์มใหม่เข้า LINE เรียบร้อย")
        else:
            print("💤 ไม่มีรายการแจ้งเตือนที่ตรงเงื่อนไข")
    pass

# --- 2. ตั้ง Job แบบใช้ฟังก์ชันธรรมดา (ย้ายมาไว้ตรงนี้) ---
@scheduler.task('cron', id='Daily_Alert_Job', hour=14, minute=45, timezone='Asia/Bangkok')
def scheduled_daily_alert_task():
    # บังคับให้ใช้ App Context เพื่อให้ดึง DB ได้บน Codespaces
    with app.app_context():
        print(f"⏰ [SCHEDULER] เริ่มทำงานตามเวลาที่ตั้งไว้...")
        scheduled_daily_alert()

# --- 3. Route สำหรับ Test (เพื่อให้ชัวร์ว่า Path ถูก) ---
@app.route('/test_alert')
def test_alert():
    print("🎯 [MANUAL] มีการสั่งรันผ่าน URL")
    scheduled_daily_alert()
    return "🚀 สั่งรันระบบแจ้งเตือนเรียบร้อย! เช็ค LINE และ Terminal"

# --- 4. ส่วนการรัน (ปรับให้ Codespaces ยอมรับ) ---
if __name__ == '__main__':
    # ตรวจสอบเพื่อไม่ให้ Scheduler รันซ้อนกัน (Prevent double execution)
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        scheduler.init_app(app)
        scheduler.start()
        print("🚀 [STATUS] Scheduler is ACTIVE and WAITING...")

    # ตั้งค่าให้เหมาะสมกับ Codespaces
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)