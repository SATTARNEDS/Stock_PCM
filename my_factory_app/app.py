import io
import math
import re
import secrets
import sqlite3
from datetime import datetime, date, timedelta

import os
import pandas as pd
import pytz
import qrcode
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify, make_response
from flask_apscheduler import APScheduler
from io import BytesIO
from unit_conversion import UnitConversionManager  # ✅ Unit Conversion Support
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(BASE_DIR, '.env')

def load_env_file(env_path=ENV_FILE):
    """โหลด environment variables จากไฟล์ .env แบบง่าย ๆ โดยไม่ต้องพึ่ง dependency เพิ่ม"""
    if not os.path.exists(env_path):
        return

    try:
        with open(env_path, 'r', encoding='utf-8') as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue

                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except Exception as e:
        print(f"Warning loading .env: {e}")

load_env_file()

# --- เพิ่ม Config ---
class Config:
    SCHEDULER_API_ENABLED = True
    SCHEDULER_TIMEZONE = "Asia/Bangkok" # บังคับ Timezone ระดับ Global

app = Flask(__name__)
app.config.from_object(Config())
scheduler = APScheduler()
# -------------------------

configured_secret = os.environ.get('FLASK_SECRET_KEY') or os.environ.get('SECRET_KEY')
if not configured_secret:
    configured_secret = secrets.token_hex(32)
    print('Warning: FLASK_SECRET_KEY not set; using a temporary random secret for this process.')

app.secret_key = configured_secret
app.config.update(
    MAX_CONTENT_LENGTH=int(os.environ.get('MAX_UPLOAD_MB', '5')) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '0') == '1'
)
DB_NAME = 'factory_stock.db'
THAILAND_TZ = 'Asia/Bangkok'
SESSION_TIMEOUT_MINUTES = 15
USER_LOCK_TIMEOUT_MINUTES = 5
ALLOWED_IMPORT_EXTENSIONS = {'xlsx', 'xlsm', 'xls'}
SENSITIVE_POST_ENDPOINTS = {
    'add_to_cart', 'remove_from_cart', 'update_cart_qty', 'confirm_withdrawal',
    'approve_request', 'reject_request', 'import_excel', 'clear_system_data',
    'toggle_product_status', 'add_product', 'edit_product', 'add_product_ajax',
    'write_off_ajax', 'unlock_user_ajax', 'add_user_ajax', 'delete_user',
    'update_user_ajax', 'save_alert_time', 'daily_alert'
}
NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}

def generate_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_hex(16)
        session['_csrf_token'] = token
    return token

app.jinja_env.globals['csrf_token'] = generate_csrf_token

def validate_csrf_token():
    expected = session.get('_csrf_token')
    provided = request.form.get('csrf_token') or request.headers.get('X-CSRF-Token')
    return bool(expected and provided and secrets.compare_digest(expected, provided))

def is_valid_user_session(emp_id):
    return bool(emp_id and session.get('user_id') == emp_id)

def user_can_access_product(user_row, product_row):
    if not user_row or not product_row:
        return False

    product_location = str(product_row['location'] or '').strip().lower()
    user_location = str(user_row['location'] or '').strip().lower()

    if not product_location or product_location in ('-', 'general', 'ห้องยา', 'medicine room'):
        return True
    if 'general' in product_location:
        return True
    if 'pc1' in user_location:
        return 'pc1' in product_location
    if 'coil center' in user_location or user_location == 'cc' or ' cc' in f' {user_location}':
        return ('coil center' in product_location) or (product_location == 'cc') or (' cc' in f' {product_location}')
    return False

# ==========================================
# 🕒 ระบบจัดการ Request (ยุบรวมทุกอย่างที่นี่)
# ==========================================
@app.before_request
def handle_before_request():
    # 1. ตั้งค่า Session ให้ถาวร (Zombie Check)
    session.permanent = True
    app.permanent_session_lifetime = timedelta(minutes=SESSION_TIMEOUT_MINUTES)

    # 1.5 ป้องกัน CSRF สำหรับคำขอแก้ไขข้อมูลที่สำคัญ
    if request.method == 'POST' and request.endpoint in SENSITIVE_POST_ENDPOINTS:
        if not validate_csrf_token():
            if request.path.startswith('/api/') or request.endpoint == 'update_cart_qty':
                return jsonify({'success': False, 'message': 'คำขอไม่ปลอดภัยหรือ session หมดอายุ'}), 400
            flash('❌ คำขอไม่ปลอดภัยหรือ session หมดอายุ กรุณาลองใหม่', 'danger')
            return redirect(request.referrer or url_for('index'))

    # 2. อัปเดตเวลาใช้งานล่าสุดของพนักงานเฉพาะ session ของตนเอง
    emp_id = request.args.get('emp_id') or request.form.get('emp_id')
    if not emp_id or session.get('user_id') != emp_id:
        return
    try:
        update_user_last_seen(emp_id)
    except Exception:
        pass

@app.after_request
def add_header(response):
    # ป้องกัน Cache เพื่อความปลอดภัย
    response.headers.update(NO_CACHE_HEADERS)
    return response

# สร้างฟังก์ชันสำหรับดึงเวลาไทย
def get_thailand_time():
    tz = pytz.timezone(THAILAND_TZ)
    return datetime.now(tz)

def is_medicine_product(product_row):
    """True เมื่อสินค้าเป็นกลุ่มยา โดยหลีกเลี่ยง false positive เช่น น้ำยาล้างจาน"""
    category = str(product_row['category'] or '').strip().lower() if product_row else ''
    name = str(product_row['name'] or '').strip().lower() if product_row else ''

    category_keywords = ('ยา', 'medicine', 'medic', 'drug', 'pharma')
    if any(keyword in category for keyword in category_keywords):
        return True

    non_medicine_keywords = (
        'น้ำยา', 'dish washing', 'washing liquid', 'hand soap', 'softener',
        'air freshener', 'tissue', 'garbage bag', 'ถุงขยะ', 'กระดาษทิชชู่'
    )
    if any(keyword in name for keyword in non_medicine_keywords):
        return False

    medicine_name_keywords = (
        'ยาอม', 'ยาแก้', 'ยาเม็ด', 'ยาน้ำ', 'tablet', 'capsule', 'pill',
        'lozenge', 'paracetamol', 'antacid', 'decolgen', 'oral rehydration', 'medicine'
    )
    return any(keyword in name for keyword in medicine_name_keywords)

def is_split_tablet_medicine(product_row):
    """ยาแบบแพ็ค/กล่อง/กระปุก/ขวด ที่ต้องแตกหน่วยเป็นเม็ด"""
    if not product_row or not is_medicine_product(product_row):
        return False

    row_keys = product_row.keys() if hasattr(product_row, 'keys') else []
    package_label = str(
        (product_row['package_unit'] if 'package_unit' in row_keys else None)
        or (product_row['unit'] if 'unit' in row_keys else None)
        or ''
    ).strip().lower()
    conversion_rate = int((product_row['conversion_rate'] if 'conversion_rate' in row_keys else 1) or 1)
    package_keywords = ('pack', 'package', 'box', 'jar', 'bottle', 'strip', 'sheet', 'sachet', 'แพ็ค', 'ห่อ', 'แผง', 'ซอง', 'กล่อง', 'กระปุก', 'ขวด')
    return conversion_rate > 1 and any(k in package_label for k in package_keywords)

def enrich_products_for_display(conn, products_list):
    """เติมข้อมูลสต็อกแสดงผลสำหรับ frontend/backend โดยไม่แก้ค่าจริงใน DB"""
    if not products_list:
        return []

    product_ids = [item['id'] for item in products_list]
    placeholders = ','.join(['?'] * len(product_ids))
    open_rows = conn.execute(f'''
        SELECT product_id, COALESCE(SUM(base_unit_qty), 0) as open_base_qty
        FROM open_packages
        WHERE status = 'active' AND product_id IN ({placeholders})
        GROUP BY product_id
    ''', product_ids).fetchall()
    open_qty_map = {row['product_id']: int(row['open_base_qty'] or 0) for row in open_rows}

    enriched = []
    for row in products_list:
        item = dict(row)
        split_medicine = is_split_tablet_medicine(row)
        package_unit = str(item.get('package_unit') or item.get('unit') or 'กล่อง')
        base_unit = str(item.get('base_unit') or 'เม็ด')
        conversion_rate = int(item.get('conversion_rate') or 1)
        open_base_qty = open_qty_map.get(item['id'], 0)
        package_stock = int(item.get('stock') or 0)
        total_base_qty = (package_stock * conversion_rate) + open_base_qty

        item['is_split_tablet_medicine'] = split_medicine
        item['package_unit_label'] = package_unit
        item['base_unit_label'] = base_unit
        item['open_base_qty'] = open_base_qty
        item['stock_base_total'] = total_base_qty
        item['frontend_stock_text'] = f"{total_base_qty} {base_unit}" if split_medicine else f"{package_stock} {item.get('unit', '')}".strip()
        item['backend_stock_text'] = f"{package_stock} {package_unit} + {open_base_qty} {base_unit}" if split_medicine else f"{package_stock} {item.get('unit', '')}".strip()
        item['max_withdraw_qty'] = total_base_qty if split_medicine else package_stock
        enriched.append(item)

    return enriched

def standardize_date(date_value):
    """แปลงวันที่ให้เป็น YYYY-MM-DD เสมอก่อนลง Database"""
    if date_value is None:
        return ""
    if isinstance(date_value, float) and math.isnan(date_value):
        return ""
    if isinstance(date_value, (datetime, date)):
        return date_value.strftime('%Y-%m-%d')

    date_str = str(date_value).strip()
    if not date_str:
        return ""

    # ถ้ามีเครื่องหมาย / แปลว่าเป็น DD/MM/YYYY ให้พยายามแปลง
    if '/' in date_str:
        try:
            return datetime.strptime(date_str, '%d/%m/%Y').strftime('%Y-%m-%d')
        except ValueError:
            pass

    return date_str # ถ้าเป็น YYYY-MM-DD อยู่แล้ว หรือแปลงไม่ได้ ก็คืนค่าเดิมไป

# ==========================================
# 📲 ตั้งค่า LINE Messaging API
# ==========================================
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_CHANNEL_ACCESS_TOKEN', '')
LINE_ADMIN_USER_ID = os.environ.get('LINE_ADMIN_USER_ID', '')
LINE_TEST_CHANNEL_ACCESS_TOKEN = os.environ.get('LINE_TEST_CHANNEL_ACCESS_TOKEN', '')
LINE_TEST_ADMIN_USER_ID = os.environ.get('LINE_TEST_ADMIN_USER_ID', '')

def get_db_connection():
    conn = sqlite3.connect(DB_NAME, timeout=20)
    # ⚠️ สำคัญมาก: ต้องเปิด Journal Mode เป็น WAL
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    conn.row_factory = sqlite3.Row
    return conn

def start_write_transaction(conn):
    """ล็อกฐานข้อมูลสำหรับธุรกรรมเขียน เพื่อลด race condition จากหลาย request พร้อมกัน"""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as e:
        if 'within a transaction' not in str(e).lower():
            raise

def update_user_last_seen(emp_id):
    conn = get_db_connection()
    try:
        conn.execute(
            "UPDATE users SET last_seen = datetime('now', '+7 hours') WHERE emp_id = ?",
            (emp_id,),
        )
        conn.commit()
    finally:
        conn.close()

def is_user_currently_locked(user_row):
    """คืนค่า True ถ้ายังอยู่ในช่วงล็อกอินซ้ำ"""
    if user_row['is_locked'] != 1:
        return False

    last_seen_str = user_row['last_seen']
    if not last_seen_str:
        return False

    try:
        last_seen = datetime.strptime(last_seen_str, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return False

    return (datetime.now() - last_seen) <= timedelta(minutes=USER_LOCK_TIMEOUT_MINUTES)

def resolve_line_targets(target_group=None, location=None, role=None):
    """เลือกกลุ่ม LINE ปลายทางตาม location / role"""
    text = ' '.join(str(v or '') for v in [target_group, location, role]).lower()

    if 'coil center' in text or 'admin_cc' in text or ' cc' in f' {text}' or text == 'cc':
        return [{'group': 'cc', 'token': LINE_CHANNEL_ACCESS_TOKEN, 'user_id': LINE_ADMIN_USER_ID}]

    if 'pc1' in text or 'admin_pc1' in text:
        return [{'group': 'pc1', 'token': LINE_TEST_CHANNEL_ACCESS_TOKEN, 'user_id': LINE_TEST_ADMIN_USER_ID}]

    return [
        {'group': 'cc', 'token': LINE_CHANNEL_ACCESS_TOKEN, 'user_id': LINE_ADMIN_USER_ID},
        {'group': 'pc1', 'token': LINE_TEST_CHANNEL_ACCESS_TOKEN, 'user_id': LINE_TEST_ADMIN_USER_ID},
    ]

def send_line_message(message, target_group=None, location=None, role=None):
    url = 'https://api.line.me/v2/bot/message/push'
    targets = resolve_line_targets(target_group=target_group, location=location, role=role)

    for target in targets:
        if not target['token'] or not target['user_id']:
            continue

        headers = {'Content-Type': 'application/json', 'Authorization': f"Bearer {target['token']}"}
        payload = {'to': target['user_id'], 'messages': [{'type': 'text', 'text': message}]}
        try:
            requests.post(url, headers=headers, json=payload)
        except Exception as e:
            print(f"Error sending LINE message ({target['group']}): {e}")

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
            # เพิ่มการเช็ค: ถ้าเป็นคนเดิมที่ถือ Session อยู่ ให้เข้าได้เลยไม่ติด Lock
            if session.get('user_id') == emp_id:
                conn.close()
                return redirect(url_for('menu', emp_id=emp_id))

            # 4. ป้องกันล็อกอินซ้ำ (ปรับปรุงใหม่: เช็คเวลา last_seen)
            if is_user_currently_locked(user):
                # ✅ เปลี่ยน category เป็น 'user_error'
                flash(f'❌ รหัส {emp_id} กำลังใช้งานอยู่ (ต้อง Logout หรือรอ 5 นาที)', 'user_error')
                conn.close()
                return render_template('index.html') # 👈 ใช้ render เพื่อให้ Flash แสดงทันที
            
            # ถ้าผ่าน ให้ตั้งค่า Session และ Lock
            session['user_id'] = emp_id 
            conn.execute("UPDATE users SET is_locked = 1, last_seen = datetime('now', '+7 hours') WHERE emp_id = ?", (emp_id,))
            conn.commit()
            conn.close()
            return redirect(url_for('menu', emp_id=emp_id))
        else:
            conn.close()
            # ✅ เปลี่ยน category เป็น 'user_error'
            flash(f'❌ ไม่พบรหัสพนักงาน: {emp_id}', 'user_error')
            return render_template('index.html') # 👈 ใช้ render เพื่อความชัวร์
            
    return render_template('index.html')

@app.route('/logout_user/<emp_id>')
def logout_user(emp_id):
    if session.get('user_id') != emp_id and not session.get('admin_logged_in'):
        flash('⚠️ ไม่สามารถออกจากระบบแทนผู้ใช้อื่นได้', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    conn.execute('UPDATE users SET is_locked = 0 WHERE emp_id = ?', (emp_id,))
    conn.commit()
    conn.close()

    session.clear()
    return redirect(url_for('index'))

@app.route('/menu')
def menu():
    emp_id = request.args.get('emp_id')
    search_query = request.args.get('search', '').strip()
    category_filter = request.args.get('category', '').strip()
    open_cart = request.args.get('open_cart')

    # --- ส่วนที่เพิ่ม: ถ้าไม่มี Session หรือรหัสไม่ตรงกัน ให้เด้งกลับหน้า Login ---
    if not session.get('user_id') or session.get('user_id') != emp_id:
        flash('⚠️ กรุณาเข้าสู่ระบบใหม่', 'user_error')
        return redirect(url_for('index'))
    # ------------------------------------------------------------------

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
    
    # --- แก้ไขจุดที่ 1: ดึงหมวดหมู่ทั้งหมดโดยไม่สนว่าสต็อกเป็น 0 หรือไม่ ---
    cat_query = f'SELECT DISTINCT category FROM products WHERE 1=1 AND is_active = 1 {location_condition}'
    cat_rows = conn.execute(cat_query).fetchall()
    all_categories = [row['category'] for row in cat_rows]

    # --- แก้ไขจุดที่ 2: ดึงสินค้าทั้งหมด (รวมที่สต็อกเป็น 0) ---
    # เดิม: query = f'SELECT * FROM products WHERE stock > 0 {location_condition}'
    query = f'SELECT * FROM products WHERE 1=1 AND is_active = 1 {location_condition}' 
    params = []
    
    if search_query:
        query += ' AND (name LIKE ? OR code LIKE ?)'
        params.extend([f'%{search_query}%', f'%{search_query}%'])
    if category_filter:
        query += ' AND category = ?'
        params.append(category_filter)

    products_list = conn.execute(query, params).fetchall()
    products_list = enrich_products_for_display(conn, products_list)
    # -----------------------------------------------------

    products_by_category = {}
    for item in products_list:
        cat = item['category']
        if cat not in products_by_category: products_by_category[cat] = []
        products_by_category[cat].append(item)

    cart_items = conn.execute('''
        SELECT c.*, p.name, p.code, p.category, p.unit, p.base_unit, p.package_unit
        FROM carts c JOIN products p ON c.product_id = p.id 
        WHERE c.emp_id = ?
    ''', (emp_id,)).fetchall()
    
    cart_list = [dict(row) for row in cart_items]
    session['cart'] = cart_list 

    # การดึงประวัติการเบิก (History)
    history = conn.execute('''
        SELECT l.*, p.name as product_name, p.unit, p.category, p.base_unit, p.package_unit
        FROM transaction_logs l 
        JOIN products p ON l.product_id = p.id 
        WHERE l.emp_id = ? 
        ORDER BY l.timestamp DESC LIMIT 5
    ''', (emp_id,)).fetchall()

    conn.close()
    return render_template('menu.html', 
                           user=user, 
                           products=products_by_category, 
                           all_categories=all_categories,
                           current_category=category_filter,
                           cart_items=cart_list,
                           open_cart=open_cart,
                           history=history)

@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    emp_id = (request.form.get('emp_id') or '').strip()
    product_id = request.form.get('product_id', type=int)
    qty_unit = request.form.get('qty_unit', 'package')  # ✅ NEW: base or package unit
    current_search = request.form.get('current_search', '')
    current_cat = request.form.get('current_cat', '')

    if not is_valid_user_session(emp_id):
        flash('⚠️ session ไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่', 'danger')
        return redirect(url_for('index'))

    try:
        qty = int(request.form.get('qty', 1))
    except (TypeError, ValueError):
        flash('❌ จำนวนที่เบิกไม่ถูกต้อง', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

    if not product_id or qty <= 0:
        flash('❌ จำนวนที่เบิกต้องมากกว่า 0', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

    conn = get_db_connection()
    start_write_transaction(conn)
    user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    product = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    if not user or not product:
        conn.close()
        flash('❌ ไม่พบผู้ใช้หรือสินค้า', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

    if not user_can_access_product(user, product):
        conn.close()
        flash('❌ คุณไม่มีสิทธิ์เบิกรายการนี้', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

    split_medicine = is_split_tablet_medicine(product)
    manager = UnitConversionManager(conn)

    if split_medicine:
        if qty_unit not in ('base', 'package'):
            conn.close()
            flash('❌ หน่วยเบิกยาไม่ถูกต้อง', 'danger')
            return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

        product_info = manager.get_product_unit_info(product_id)
        if qty_unit == 'package':
            qty_to_reserve = int(qty * product_info['conversion_rate'])
            requested_unit_label = product_info.get('package_unit') or product['unit'] or 'แพ็ก'
        else:
            qty_to_reserve = qty
            requested_unit_label = product_info.get('base_unit') or 'เม็ด'

        stock_check = manager.check_stock_available(product_id, qty_to_reserve)
        can_add = stock_check['available']
    else:
        qty_unit = 'package'
        qty_to_reserve = qty
        requested_unit_label = product['unit']
        can_add = product['stock'] >= qty

    if can_add:
        if split_medicine:
            existing_item = conn.execute('SELECT * FROM carts WHERE emp_id = ? AND product_id = ?', (emp_id, product_id)).fetchone()
            if existing_item:
                safe_existing_qty = max(0, int(existing_item['qty'] or 0))
                conn.execute('UPDATE carts SET qty = ? WHERE id = ?', (safe_existing_qty + qty_to_reserve, existing_item['id']))
            else:
                conn.execute('INSERT INTO carts (emp_id, product_id, qty) VALUES (?, ?, ?)', (emp_id, product_id, qty_to_reserve))

            conn.execute('UPDATE products SET reserved_stock = reserved_stock + ? WHERE id = ?', (qty_to_reserve, product_id))
            conn.commit()
            flash(f'🛒 เพิ่ม {product["name"]} ({qty} {requested_unit_label}) เรียบร้อย', 'success')
        else:
            stock_update = conn.execute(
                'UPDATE products SET stock = stock - ?, reserved_stock = reserved_stock + ? WHERE id = ? AND stock >= ?',
                (qty, qty_to_reserve, product_id, qty)
            )
            if stock_update.rowcount == 0:
                conn.rollback()
                flash('❌ ของหมดหรือมีไม่พอ', 'danger')
                conn.close()
                return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

            existing_item = conn.execute('SELECT * FROM carts WHERE emp_id = ? AND product_id = ?', (emp_id, product_id)).fetchone()
            if existing_item:
                safe_existing_qty = max(0, int(existing_item['qty'] or 0))
                conn.execute('UPDATE carts SET qty = ? WHERE id = ?', (safe_existing_qty + qty_to_reserve, existing_item['id']))
            else:
                conn.execute('INSERT INTO carts (emp_id, product_id, qty) VALUES (?, ?, ?)', (emp_id, product_id, qty_to_reserve))
            conn.commit()
            flash(f'🛒 เพิ่ม {product["name"]} ({qty} {requested_unit_label}) เรียบร้อย', 'success')
    else:
        conn.rollback()
        flash('❌ ของหมดหรือมีไม่พอ', 'danger')
    
    conn.close()
    return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

@app.route('/api/get_product_unit_info', methods=['GET'])  # ✅ NEW: Get unit info for AJAX
def api_get_product_unit_info():
    """API endpoint to get unit conversion info"""
    try:
        product_id = request.args.get('product_id', type=int)
        conn = get_db_connection()
        manager = UnitConversionManager(conn)
        info = manager.get_product_unit_info(product_id)
        conn.close()
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/api/preview_withdrawal', methods=['POST'])  # ✅ NEW: Preview unit conversion
def api_preview_withdrawal():
    """API to preview withdrawal calculation"""
    try:
        product_id = request.form.get('product_id', type=int)
        qty_requested = int(request.form.get('qty'))
        qty_unit = request.form.get('qty_unit', 'base')  # 'base' or 'package'
        
        conn = get_db_connection()
        manager = UnitConversionManager(conn)
        info = manager.get_product_unit_info(product_id)
        
        # Convert to base units if user specified package units
        if qty_unit == 'package':
            qty_base_unit = int(qty_requested * info['conversion_rate'])
        else:
            qty_base_unit = qty_requested
        
        # Calculate withdrawal
        result = manager.calculate_withdrawal(product_id, qty_base_unit)
        conn.close()
        
        return jsonify({
            'success': result['can_fulfill'],
            'message': result['message'],
            'full_packages': result['full_packages_needed'],
            'new_open_qty': result['new_open_box_qty'],
            'total_packages': result['total_packages_used'],
            'from_open_box': result['from_open_box']
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400

@app.route('/remove_from_cart', methods=['POST'])
def remove_from_cart():
    cart_id = request.form.get('cart_id', type=int)
    emp_id = (request.form.get('emp_id') or '').strip()
    current_search = request.form.get('current_search', '')
    current_cat = request.form.get('current_cat', '')

    if not is_valid_user_session(emp_id):
        flash('⚠️ session ไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    start_write_transaction(conn)
    cart_item = conn.execute('SELECT * FROM carts WHERE id = ? AND emp_id = ?', (cart_id, emp_id)).fetchone()
    if not cart_item:
        conn.close()
        flash('❌ ไม่พบรายการในตะกร้า', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, open_cart='true', search=current_search, category=current_cat))

    product = conn.execute('SELECT * FROM products WHERE id = ?', (cart_item['product_id'],)).fetchone()
    is_medicine = is_split_tablet_medicine(product) if product else False
    qty = max(0, int(cart_item['qty'] or 0))

    conn.execute('DELETE FROM carts WHERE id = ? AND emp_id = ?', (cart_id, emp_id))
    if is_medicine:
        conn.execute('UPDATE products SET reserved_stock = MAX(0, reserved_stock - ?) WHERE id = ?', (qty, cart_item['product_id']))
    else:
        conn.execute('UPDATE products SET stock = stock + ?, reserved_stock = MAX(0, reserved_stock - ?) WHERE id = ?', (qty, qty, cart_item['product_id']))
    conn.commit()
    conn.close()
    
    return redirect(url_for('menu', emp_id=emp_id, open_cart='true', search=current_search, category=current_cat))
    
@app.route('/confirm_withdrawal', methods=['POST'])
def confirm_withdrawal():
    emp_id = (request.form.get('emp_id') or '').strip()
    symptom = (request.form.get('symptom') or '').strip()

    if not is_valid_user_session(emp_id):
        flash('⚠️ session ไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    start_write_transaction(conn)
    
    user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    cart_items = conn.execute('''
        SELECT c.*, p.name, p.stock, p.unit, p.category, p.base_unit, p.package_unit, p.conversion_rate
        FROM carts c JOIN products p ON c.product_id = p.id 
        WHERE c.emp_id = ?
    ''', (emp_id,)).fetchall()
    
    if not cart_items: 
        conn.close()
        return redirect(url_for('index'))

    if any(int(item['qty'] or 0) <= 0 for item in cart_items):
        conn.close()
        flash('❌ พบจำนวนสินค้าในตะกร้าไม่ถูกต้อง กรุณาลบและเลือกใหม่', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, open_cart='true'))
    
    has_medicine = any(is_split_tablet_medicine(item) for item in cart_items)
    if has_medicine and not symptom:
        conn.close()
        flash('❌ รายการเบิกยาต้องระบุอาการก่อนยืนยัน', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, open_cart='true'))

    msg_list = [f"🚀 *มีคำขอเบิกใหม่* 🚀\n👤 ผู้เบิก: {user['name']}\n📍 แผนก: {user['department']} ({user['location']})"]
    if has_medicine:
        msg_list.append(f"🩺 อาการ: {symptom}")
    
    thai_now = get_thailand_time().strftime('%d/%m/%Y %H:%M:%S')
    
    # ✅ NEW: Initialize UnitConversionManager
    manager = UnitConversionManager(conn)
    
    for item in cart_items:
        item_name = item['name']
        
        # ✅ NEW: Use UnitConversionManager to apply withdrawal (FIFO + open packages)
        try:
            is_medicine = is_split_tablet_medicine(item)
            if is_medicine:
                withdrawal_result = manager.apply_withdrawal(
                    product_id=item['product_id'],
                    qty_base_unit=item['qty'],  # ยาเก็บในตะกร้าเป็น base unit
                    emp_id=emp_id,
                    lot_id=None,
                    autocommit=False
                )
                if not withdrawal_result.get('success'):
                    raise RuntimeError(withdrawal_result.get('message', 'ไม่สามารถตัดสต็อกยาได้'))
            else:
                withdrawal_result = {
                    'full_packages_used': item['qty'],
                    'total_packages_used': item['qty'],
                    'note': ''
                }
            
            # ✅ Log transaction with unit info
            if "หมวกเซฟตี้" in item_name or "Helmet" in item_name:
                existing_helmet = conn.execute('''
                    SELECT id FROM transaction_logs 
                    WHERE emp_id = ? AND product_id = ? AND action = 'เบิกหมวกเซฟตี้ (รอบ 2 ปี)'
                ''', (emp_id, item['product_id'])).fetchone()

                result_qty = withdrawal_result.get('full_packages_used', item['qty'])
                result_note = withdrawal_result.get('note') or withdrawal_result.get('transaction_note', '')
                if existing_helmet:
                    conn.execute('''
                        UPDATE transaction_logs 
                        SET qty = ?, qty_base_unit = ?, timestamp = ?, status = 'Pending', note = ?
                        WHERE id = ?
                    ''', (result_qty, item['qty'], thai_now, result_note, existing_helmet['id']))
                else:
                    conn.execute('''
                        INSERT INTO transaction_logs (emp_id, product_id, action, qty, qty_base_unit, qty_package_unit, status, timestamp, note) 
                        VALUES (?, ?, 'เบิกหมวกเซฟตี้ (รอบ 2 ปี)', ?, ?, ?, 'Pending', ?, ?)
                    ''', (emp_id, item['product_id'], result_qty, item['qty'], withdrawal_result.get('total_packages_used', result_qty), thai_now, result_note))
            else:
                result_qty = withdrawal_result.get('full_packages_used', item['qty'])
                result_note = withdrawal_result.get('note') or withdrawal_result.get('transaction_note', '')
                if has_medicine and is_medicine:
                    result_note = f"อาการ: {symptom}" + (f" | {result_note}" if result_note else "")
                conn.execute('''
                    INSERT INTO transaction_logs (emp_id, product_id, action, qty, qty_base_unit, qty_package_unit, status, timestamp, note) 
                    VALUES (?, ?, 'ขอเบิกอุปกรณ์', ?, ?, ?, 'Pending', ?, ?)
                ''', (emp_id, item['product_id'], result_qty, item['qty'], withdrawal_result.get('total_packages_used', result_qty), thai_now, result_note))
            
            log_note = withdrawal_result.get('note') or withdrawal_result.get('transaction_note', '')
            display_qty = item['qty']
            display_unit = item['unit']
            if is_medicine:
                display_unit = item['base_unit'] if 'base_unit' in item.keys() and item['base_unit'] else 'เม็ด'
            msg_list.append(f"📦 {item_name}\n   🔹 จำนวน: {display_qty} {display_unit}\n   ℹ️ {log_note}")
            
        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f'❌ ไม่สามารถยืนยันการเบิกได้: {str(e)}', 'danger')
            return redirect(url_for('menu', emp_id=emp_id, open_cart='true'))

    # ปลด reserved_stock ออกจากรายการที่ยืนยันแล้ว ก่อนล้างตะกร้า
    conn.execute('''
        UPDATE products
        SET reserved_stock = MAX(0, reserved_stock - COALESCE((
            SELECT SUM(c.qty) FROM carts c WHERE c.emp_id = ? AND c.product_id = products.id
        ), 0))
        WHERE id IN (SELECT product_id FROM carts WHERE emp_id = ?)
    ''', (emp_id, emp_id))

    # ล้างตะกร้า
    conn.execute('DELETE FROM carts WHERE emp_id = ?', (emp_id,))
    conn.commit()
    conn.close()

    # ส่งข้อความเข้า Line ตามกลุ่มของผู้เบิก
    send_line_message("\n".join(msg_list), location=(user['location'] if user and 'location' in user.keys() else ''))
    
    flash('✅ ส่งคำขอเรียบร้อย! ระบบบันทึกรอบการเบิกหมวกเซฟตี้ให้คุณแล้ว', 'success')
    return redirect(url_for('menu', emp_id=emp_id))
 
 # --- เพิ่ม Route สำหรับอัปเดตจำนวนในตะกร้า (AJAX) ---
@app.route('/update_cart_qty', methods=['POST'])
def update_cart_qty():
    cart_id = request.form.get('cart_id', type=int)
    emp_id = (request.form.get('emp_id') or '').strip()

    if not is_valid_user_session(emp_id):
        return jsonify({'success': False, 'message': 'session ไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่'}), 403

    try:
        new_qty = int(request.form.get('qty', 1))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'จำนวนไม่ถูกต้อง'}), 400

    if not cart_id or new_qty <= 0:
        return jsonify({'success': False, 'message': 'จำนวนต้องมากกว่า 0'}), 400

    conn = get_db_connection()
    start_write_transaction(conn)
    item = conn.execute('SELECT * FROM carts WHERE id = ? AND emp_id = ?', (cart_id, emp_id)).fetchone()
    if item:
        product = conn.execute('SELECT * FROM products WHERE id = ?', (item['product_id'],)).fetchone()
        is_medicine = is_split_tablet_medicine(product)
        diff = new_qty - item['qty']

        if is_medicine:
            manager = UnitConversionManager(conn)
            stock_check = manager.check_stock_available(item['product_id'], new_qty)
            can_update = stock_check['available']
        else:
            can_update = (diff <= 0) or (product and product['stock'] >= diff)

        if can_update:
            if is_medicine:
                conn.execute('UPDATE carts SET qty = ? WHERE id = ? AND emp_id = ?', (new_qty, cart_id, emp_id))
                conn.execute('UPDATE products SET reserved_stock = MAX(0, reserved_stock + ?) WHERE id = ?',
                             (diff, item['product_id']))
            else:
                if diff > 0:
                    stock_update = conn.execute(
                        'UPDATE products SET stock = stock - ?, reserved_stock = reserved_stock + ? WHERE id = ? AND stock >= ?',
                        (diff, diff, item['product_id'], diff)
                    )
                    if stock_update.rowcount == 0:
                        conn.rollback()
                        conn.close()
                        return jsonify({'success': False, 'message': 'สินค้าในคลังไม่พอ'}), 409
                else:
                    conn.execute('UPDATE products SET stock = stock - ?, reserved_stock = MAX(0, reserved_stock + ?) WHERE id = ?', 
                                 (diff, diff, item['product_id']))
                conn.execute('UPDATE carts SET qty = ? WHERE id = ? AND emp_id = ?', (new_qty, cart_id, emp_id))
            conn.commit()
            res = {'success': True}
        else:
            res = {'success': False, 'message': 'สินค้าในคลังไม่พอ'}
    else:
        res = {'success': False, 'message': 'ไม่พบรายการในตะกร้า'}
    
    conn.close()
    return jsonify(res)

@app.route('/api/search_products')
def api_search_products():
    emp_id = request.args.get('emp_id')
    if not is_valid_user_session(emp_id):
        return jsonify({'html': '', 'has_more': False, 'next_page': 1, 'message': 'Unauthorized'}), 401
    search_query = request.args.get('search', '').strip()
    category_filter = request.args.get('category', '').strip()
    page = request.args.get('page', 1, type=int)
    per_page = 30

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    
    # Logic การกรอง Location (ยกมาจาก menu เดิมของคุณ)
    location_condition = ""
    if user and user['location']:
        if 'PC1' in user['location']:
            location_condition = " AND (location LIKE '%PC1%' OR location = 'General' OR location IS NULL)"
        elif 'CC' in user['location'] or 'Coil Center' in user['location']:
            location_condition = " AND (location LIKE '%Coil Center%' OR location LIKE '%CC%' OR location = 'General' OR location IS NULL)"

    if category_filter:
        # Pagination ในหมวดหมู่เฉพาะ
        base_query = f'SELECT * FROM products WHERE category = ? AND is_active = 1 {location_condition}'
        count_query = f'SELECT COUNT(*) as count FROM products WHERE category = ? AND is_active = 1 {location_condition}'
        params = [category_filter]
        count_params = [category_filter]
        
        if search_query:
            base_query += ' AND (name LIKE ? OR code LIKE ?)'
            count_query += ' AND (name LIKE ? OR code LIKE ?)'
            params.extend([f'%{search_query}%', f'%{search_query}%'])
            count_params.extend([f'%{search_query}%', f'%{search_query}%'])
        
        total_count = conn.execute(count_query, count_params).fetchone()['count']
        offset = (page - 1) * per_page
        paged_query = f"{base_query} ORDER BY code ASC LIMIT ? OFFSET ?"
        params.extend([per_page, offset])
        
        products_list = conn.execute(paged_query, params).fetchall()
        products_list = enrich_products_for_display(conn, products_list)
        products_by_category = {category_filter: products_list}
        has_more = offset + per_page < total_count
        
    elif search_query:
        # Pagination ในผลการค้นหา
        base_query = f'SELECT * FROM products WHERE is_active = 1 {location_condition} AND (name LIKE ? OR code LIKE ?)'
        count_query = f'SELECT COUNT(*) as count FROM products WHERE is_active = 1 {location_condition} AND (name LIKE ? OR code LIKE ?)'
        params = [f'%{search_query}%', f'%{search_query}%']
        count_params = [f'%{search_query}%', f'%{search_query}%']
        
        total_count = conn.execute(count_query, count_params).fetchone()['count']
        offset = (page - 1) * per_page
        paged_query = f"{base_query} ORDER BY code ASC LIMIT ? OFFSET ?"
        params.extend([per_page, offset])
        
        products_list = conn.execute(paged_query, params).fetchall()
        products_list = enrich_products_for_display(conn, products_list)
        # จัดกลุ่มตามหมวดหมู่
        products_by_category = {}
        for item in products_list:
            cat = item['category']
            if cat not in products_by_category: products_by_category[cat] = []
            products_by_category[cat].append(item)
        has_more = offset + per_page < total_count
        
    else:
        # หมวดหมู่ต่อหน้า (เมื่อเลือก "ทั้งหมด")
        categories = conn.execute(f"SELECT DISTINCT category FROM products WHERE is_active = 1 {location_condition} ORDER BY category").fetchall()
        categories_list = [row['category'] for row in categories]
        
        if page > len(categories_list):
            conn.close()
            return jsonify({'html': '', 'has_more': False, 'next_page': page})
        
        selected_category = categories_list[page - 1]
        query = f'SELECT * FROM products WHERE category = ? AND is_active = 1 {location_condition} ORDER BY code ASC'
        products_list = conn.execute(query, [selected_category]).fetchall()
        products_list = enrich_products_for_display(conn, products_list)
        products_by_category = {selected_category: products_list}
        has_more = page < len(categories_list)

    conn.close()
    html = render_template('product_list_partial.html', products=products_by_category, user=user)
    return jsonify({
        'html': html,
        'has_more': has_more,
        'next_page': page + 1
    })

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
        
        # ตรวจสอบรหัสผ่าน
        if admin and check_password_hash(admin['password'], password):
            session['admin_logged_in'] = True
            session['admin_name'] = admin['name']
            session['admin_role'] = admin['role']
            session.permanent = True
            # แก้ไข: ย้ายการตั้งค่า lifetime ไปไว้ที่ตอน config app จะดีกว่า 
            # แต่ถ้าจะไว้ตรงนี้ให้ใช้ timedelta(minutes=60)
            return redirect(url_for('admin_dashboard'))
        
        flash('❌ ชื่อผู้ใช้หรือรหัสผ่านแอดมินไม่ถูกต้อง', 'admin_error')
        
    # สำคัญ: ต้อง render_template กลับไปหน้า index.html (หน้าที่มีทั้ง 2 ฟอร์ม)
    return render_template('index.html')

@app.route('/admin')
def admin_dashboard():
    if not session.get('admin_logged_in'): return redirect(url_for('index'))
    
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

    log_per_page = 10 
    log_offset = (page - 1) * log_per_page
    
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
        SELECT l.*, u.name as emp_name, u.department, u.location,
               p.name as product_name, p.unit, p.category, p.base_unit, p.package_unit, p.conversion_rate
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
    stock_query += " ORDER BY code ASC LIMIT 500" 
    all_stock = conn.execute(stock_query, stock_params).fetchall()
    all_stock = enrich_products_for_display(conn, all_stock)
    
    categories = conn.execute(f"SELECT DISTINCT category FROM products WHERE 1=1 {product_loc_filter}").fetchall()

    logs = conn.execute(f'''
        SELECT l.*, u.name as emp_name, u.department, u.location, p.name as product_name, p.unit
        FROM transaction_logs l
        LEFT JOIN users u ON l.emp_id = u.emp_id
        LEFT JOIN products p ON l.product_id = p.id
        WHERE 1=1 {final_log_filter}
        ORDER BY l.timestamp DESC LIMIT ? OFFSET ?
    ''', (log_per_page, log_offset)).fetchall()

    count_query = f'''
        SELECT COUNT(*) FROM transaction_logs l 
        LEFT JOIN users u ON l.emp_id = u.emp_id 
        WHERE 1=1 {final_log_filter}
    '''
    total_logs = conn.execute(count_query).fetchone()[0]
    total_pages = math.ceil(total_logs / log_per_page)

    low_stock_query = f"SELECT * FROM products WHERE stock < safety_stock {product_loc_filter}"
    low_stock = conn.execute(low_stock_query).fetchall()
    low_stock = enrich_products_for_display(conn, low_stock)

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

@app.route('/cron/daily_alert', methods=['POST'])
def daily_alert():
    if not session.get('admin_logged_in'):
        return "Unauthorized", 401

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
            message += f"👤 คุณ{alert['emp_name']} ({alert['department']})\n📦 {alert['product_name']}\n📅 เบิกเมื่อ: {alert['timestamp']}\n"

    # ถ้ามีรายการผิดปกติ ให้ส่ง LINE ทันที
    if alert_triggered:
        send_line_message(message)
        return f"Alert sent: {message}", 200
    else:
        return "No alerts today", 200

@app.route('/api/admin/pending_requests')
def get_pending_requests():
    if not session.get('admin_logged_in'):
        return "Unauthorized", 401
    
    role = session.get('admin_role', 'superadmin')
    conn = get_db_connection()
    
    # กรองตามสิทธิ์ Admin (PC1 / CC)
    role_log_filter = ""
    if role == 'admin_pc1':
        role_log_filter = " AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')"
    elif role == 'admin_cc':
        role_log_filter = " AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')"

    query = f'''
        SELECT l.*, u.name as emp_name, u.department,
               p.name as product_name, p.unit, p.category, p.base_unit, p.package_unit, p.conversion_rate
        FROM transaction_logs l
        LEFT JOIN users u ON l.emp_id = u.emp_id
        LEFT JOIN products p ON l.product_id = p.id
        WHERE l.status = 'Pending' {role_log_filter} 
        ORDER BY l.timestamp ASC
    '''
    
    pending_logs = conn.execute(query).fetchall()
    conn.close()
    
    # ส่งกลับเป็น HTML เฉพาะส่วนของแถวตาราง (Partial)
    return render_template('pending_requests_partial.html', pending_logs=pending_logs)

@app.route('/admin/approve/<int:log_id>', methods=['POST']) # ฟังก์ชันนี้จะถูกเรียกเมื่อแอดมินกดอนุมัติการเบิก
def approve_request(log_id):
    if not session.get('admin_logged_in'): return redirect(url_for('index'))
    role = session.get('admin_role', 'superadmin')

    conn = get_db_connection()
    start_write_transaction(conn)
    start_write_transaction(conn)
    if role == 'admin_pc1':
        permission_check = conn.execute('''
            SELECT l.id FROM transaction_logs l
            LEFT JOIN users u ON l.emp_id = u.emp_id
            WHERE l.id = ? AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')
        ''', (log_id,)).fetchone()
        if not permission_check:
            conn.close()
            flash('❌ คุณไม่มีสิทธิ์อนุมัติรายการนี้', 'danger')
            return redirect(url_for('admin_dashboard'))
    elif role == 'admin_cc':
        permission_check = conn.execute('''
            SELECT l.id FROM transaction_logs l
            LEFT JOIN users u ON l.emp_id = u.emp_id
            WHERE l.id = ? AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')
        ''', (log_id,)).fetchone()
        if not permission_check:
            conn.close()
            flash('❌ คุณไม่มีสิทธิ์อนุมัติรายการนี้', 'danger')
            return redirect(url_for('admin_dashboard'))

    # 1. ดึงข้อมูลรายการเบิกที่รออนุมัติ
    log = conn.execute('SELECT * FROM transaction_logs WHERE id=? AND status = "Pending"', (log_id,)).fetchone()
    
    if log:
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

        if remaining > 0:
            conn.rollback()
            conn.close()
            flash('❌ จำนวนสินค้าใน lot ไม่พอสำหรับการอนุมัติ', 'danger')
            return redirect(url_for('admin_dashboard'))

        # 4. อัปเดตตารางหลัก
        thai_now = get_thailand_time().strftime('%d/%m/%Y %H:%M:%S')
        update_result = conn.execute('''
            UPDATE transaction_logs 
            SET status = "Approved", lot_id = ?, timestamp = ? 
            WHERE id = ? AND status = "Pending"
        ''', (last_lot_id, thai_now, log_id))

        if update_result.rowcount == 0:
            conn.rollback()
            conn.close()
            flash('⚠️ รายการนี้ถูกดำเนินการไปแล้ว', 'warning')
            return redirect(url_for('admin_dashboard'))
        
        # อัปเดตยอดเบิกสะสมในตารางของหลัก
        conn.execute('UPDATE products SET withdraw = withdraw + ? WHERE id = ?', (qty_to_withdraw, product_id))
        
        conn.commit()

        # 5. เช็คแจ้งเตือน Safety Stock หลังตัดสต็อก
        check_safety_alert(product_id)

        user_info = conn.execute('SELECT name, department, location FROM users WHERE emp_id = ?', (log['emp_id'],)).fetchone()
        product_info = conn.execute('SELECT name, unit, base_unit FROM products WHERE id = ?', (product_id,)).fetchone()
        admin_label = 'Admin CC' if role == 'admin_cc' else ('Admin PC1' if role == 'admin_pc1' else 'Super Admin')
        is_split_medicine_log = product_info and log['qty_base_unit'] and product_info['base_unit'] and product_info['base_unit'] != product_info['unit']
        approved_qty = log['qty_base_unit'] if is_split_medicine_log else qty_to_withdraw
        approved_unit = (product_info['base_unit'] if is_split_medicine_log else (product_info['unit'] if product_info else 'หน่วย'))
        approval_message = (
            f"✅ Admin ได้ทำการยืนยันรายการแล้ว\n"
            f"👤 ผู้เบิก: {user_info['name'] if user_info else log['emp_id']}\n"
            f"📍 แผนก: {user_info['department'] if user_info else '-'} ({user_info['location'] if user_info else '-'})\n"
            f"📦 รายการ: {product_info['name'] if product_info else product_id}\n"
            f"🔢 จำนวน: {approved_qty} {approved_unit}\n"
            f"🧾 ผู้อนุมัติ: {admin_label}\n"
            f"🕒 เวลาอนุมัติ: {thai_now}"
        )
        send_line_message(approval_message, location=(user_info['location'] if user_info else ''), role=role)

        flash('✅ อนุมัติและตัดสต็อกแบบ FIFO เรียบร้อยแล้ว', 'success')
    
    conn.close()
    return redirect(url_for('admin_dashboard'))

def check_safety_alert(product_id): # ฟังก์ชันนี้จะถูกเรียกหลังจากอนุมัติการเบิก เพื่อเช็คว่าของตัวนั้นๆ ต่ำกว่า Safety Stock หรือไม่
    conn = get_db_connection()
    product = conn.execute('SELECT name, stock, safety_stock, unit, location FROM products WHERE id = ?', (product_id,)).fetchone()
    conn.close()
    
    if product and product['stock'] <= product['safety_stock']:
        alert_msg = (
            f"⚠️ *แจ้งเตือนสต็อกต่ำกว่าเกณฑ์*\n"
            f"📦 ของ: {product['name']}\n"
            f"📉 คงเหลือปัจจุบัน: {product['stock']} {product['unit']}\n"
            f"🚩 จุดสั่งซื้อ (Safety): {product['safety_stock']} {product['unit']}\n"
            f"--- กรุณาพิจารณาสั่งซื้อเพิ่ม ---"
        )
        send_line_message(alert_msg, location=(product['location'] if product and 'location' in product.keys() else ''))

@app.route('/admin/reject/<int:log_id>', methods=['POST'])
def reject_request(log_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('index'))
    role = session.get('admin_role', 'superadmin')

    start_write_transaction(conn)
    conn = get_db_connection()
    start_write_transaction(conn)
    if role == 'admin_pc1':
        permission_check = conn.execute('''
            SELECT l.id FROM transaction_logs l
            LEFT JOIN users u ON l.emp_id = u.emp_id
            WHERE l.id = ? AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')
        ''', (log_id,)).fetchone()
        if not permission_check:
            conn.close()
            flash('❌ คุณไม่มีสิทธิ์ปฏิเสธรายการนี้', 'danger')
            return redirect(url_for('admin_dashboard'))
    elif role == 'admin_cc':
        permission_check = conn.execute('''
            SELECT l.id FROM transaction_logs l
            LEFT JOIN users u ON l.emp_id = u.emp_id
            WHERE l.id = ? AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')
        ''', (log_id,)).fetchone()
        if not permission_check:
            conn.close()
            flash('❌ คุณไม่มีสิทธิ์ปฏิเสธรายการนี้', 'danger')
            return redirect(url_for('admin_dashboard'))

    log = conn.execute('SELECT * FROM transaction_logs WHERE id=? AND status = "Pending"', (log_id,)).fetchone()
    if log:
        product = conn.execute('SELECT * FROM products WHERE id = ?', (log['product_id'],)).fetchone()
        is_medicine = is_split_tablet_medicine(product) if product else False

        if is_medicine:
            conn.execute('UPDATE products SET reserved_stock = MAX(0, reserved_stock - ?) WHERE id = ?', (log['qty'], log['product_id']))
        else:
            conn.execute('UPDATE products SET stock = stock + ?, reserved_stock = MAX(0, reserved_stock - ?) WHERE id = ?', 
                         (log['qty'], log['qty'], log['product_id']))

        update_result = conn.execute('UPDATE transaction_logs SET status = "Rejected" WHERE id = ? AND status = "Pending"', (log_id,))
        if update_result.rowcount == 0:
            conn.rollback()
            conn.close()
            flash('⚠️ รายการนี้ถูกดำเนินการไปแล้ว', 'warning')
            return redirect(url_for('admin_dashboard'))

        conn.commit()
        flash('❌ ปฏิเสธรายการเรียบร้อยแล้ว', 'warning')
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

@app.route('/admin/toggle_product_status/<int:product_id>', methods=['POST'])
def toggle_product_status(product_id):
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    # ดึงสถานะปัจจุบันมาเช็ค
    product = conn.execute('SELECT is_active FROM products WHERE id = ?', (product_id,)).fetchone()
    if product:
        new_status = 0 if product['is_active'] == 1 else 1
        conn.execute('UPDATE products SET is_active = ? WHERE id = ?', (new_status, product_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'new_status': new_status})
    conn.close()
    return jsonify({'success': False, 'message': 'ไม่พบสินค้า'})

@app.route('/admin/add_product', methods=['POST'])
def add_product():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    admin_name = 'ADMIN:' + session.get('admin_name', 'Unknown')
    
    # 1. รับค่าจากฟอร์ม (อ้างอิงตามชื่อ name ใน HTML)
    code = request.form.get('code')
    name = request.form.get('name')
    category = request.form.get('category')
    unit = request.form.get('unit')
    location = request.form.get('location')
    safety_stock = request.form.get('safety_stock', 0, type=int)
    stock = request.form.get('stock', 0, type=int)
    expiry_date = standardize_date(request.form.get('expiry_date', ''))

    conn = get_db_connection()

    try:
        # 2. เพิ่มสินค้าลงตารางหลัก พร้อมบันทึกข้อมูล Lot ถ้ามี
        if stock > 0:
            from datetime import datetime
            lot_number = datetime.now().strftime('%d%m%Y') + "-NEW"
            receive_date = datetime.now().strftime('%Y-%m-%d')
            cursor = conn.execute('''
                INSERT INTO products (code, name, category, unit, location, safety_stock, stock, expiry_date, lot_no, received_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (code, name, category, unit, location, safety_stock, stock, expiry_date, lot_number, receive_date))
        else:
            cursor = conn.execute('''
                INSERT INTO products (code, name, category, unit, location, safety_stock, stock, expiry_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (code, name, category, unit, location, safety_stock, stock, ''))
        
        product_id = cursor.lastrowid

        # 3. ถ้ามีการใส่สต็อกเริ่มต้นมาด้วย ให้สร้าง "Lot แรก" อัตโนมัติ
        if stock > 0:
            conn.execute('''
                INSERT INTO product_lots (product_id, lot_number, qty, received_date, expiry_date)
                VALUES (?, ?, ?, ?, ?)
            ''', (product_id, lot_number, stock, receive_date, expiry_date))

            # 4. บันทึกประวัติ
            conn.execute('''
                INSERT INTO transaction_logs (emp_id, product_id, action, qty, status, timestamp)
                VALUES (?, ?, ?, ?, 'Approved', datetime('now', '+7 hours'))
            ''', (admin_name, product_id, f'รับเข้า Lot แรก: {lot_number}', stock))

        conn.commit()
        return jsonify({'success': True})
        
    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'message': 'รหัสสินค้านี้มีซ้ำในระบบแล้ว'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        conn.close()

@app.route('/admin/reset_lock')
def reset_lock():
    if not session.get('admin_logged_in'): return redirect(url_for('index'))
    conn = get_db_connection()
    conn.execute('UPDATE users SET is_locked = 0')
    conn.commit()
    conn.close()
    flash('✅ ปลดล็อกพนักงานทุกคนเรียบร้อยแล้ว', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/export')
def export_excel():
    if not session.get('admin_logged_in'): 
        return redirect(url_for('index'))
    
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
        
    # 2. Query ดึงข้อมูลสินค้าและ Lot ที่เกี่ยวข้อง
    query = f'''
        SELECT 
            p.code as 'รหัสของ',
            p.name as 'ชื่อของ',
            p.category as 'หมวดหมู่',
            p.unit as 'หน่วยนับ',
            p.location as 'สถานที่เก็บ (Location)',
            p.safety_stock as 'จุดสั่งซื้อ (Safety Stock)',
            p.stock as 'จำนวนคงเหลือ',
            CASE WHEN p.is_active = 1 THEN 'เปิดใช้งาน' ELSE 'ปิดใช้งาน' END as 'สถานะการใช้งาน',
            COALESCE(pl.lot_number, p.lot_no, '') as 'Lot No.',
            COALESCE(pl.received_date, p.received_date, '') as 'วันที่รับเข้า',
            COALESCE(pl.expiry_date, p.expiry_date, '') as 'วันหมดอายุ',
            COALESCE(pl.qty, 0) as 'จำนวนใน Lot'
        FROM products p
        LEFT JOIN product_lots pl ON pl.product_id = p.id
        {location_filter}
        ORDER BY p.location ASC, p.code ASC, pl.received_date ASC
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
    if not session.get('admin_logged_in'):
        return redirect(url_for('index'))

    file = request.files.get('file')
    if not file:
        flash('❌ กรุณาเลือกไฟล์ก่อนนำเข้า', 'danger')
        return redirect(url_for('admin_dashboard'))

    filename = secure_filename(file.filename or '')
    file_ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if file_ext not in ALLOWED_IMPORT_EXTENSIONS:
        flash('❌ รองรับเฉพาะไฟล์ Excel .xlsx, .xlsm, .xls', 'danger')
        return redirect(url_for('admin_dashboard'))

    def safe_int(value):
        if pd.isna(value) or str(value).strip() == '':
            return 0
        try:
            return int(float(value))
        except Exception:
            return 0

    def clean_medicine_name(value):
        text = str(value or '').strip()
        if not text or text.lower() == 'nan':
            return ''
        text = re.sub(
            r'\s*1\s*(?:กป|กระปุก|ห่อ|แผง|ซอง|ขวด|pack|box|bottle|bott\.?)?\s*/\s*\d+\s*$',
            '',
            text,
            flags=re.IGNORECASE
        )
        text = re.sub(r'\s+', ' ', text)
        return text.strip(' -/')

    def normalize_lookup_name(value):
        text = clean_medicine_name(value).lower()
        text = text.replace('(', ' ').replace(')', ' ')
        text = re.sub(r'[^0-9a-zA-Zก-๙\s/._-]', '', text)
        text = re.sub(r'\s+', ' ', text)
        return text.strip()

    def normalize_medicine_unit(name, unit):
        normalized_unit = str(unit or '').strip() or 'ชิ้น'
        lower_name = str(name or '').lower()
        if 'มายบาซิน' in lower_name and normalized_unit == 'แผง':
            return 'ห่อ'
        return normalized_unit

    def infer_medicine_setup(name, unit):
        raw_unit = normalize_medicine_unit(name, unit)
        lower_name = str(name or '').lower()
        lower_unit = raw_unit.lower()
        pack_match = re.search(
            r'1\s*(?:กป|กระปุก|ห่อ|แผง|ซอง|ขวด|pack|box|bottle|bott\.?)?\s*/\s*(\d+)',
            lower_name,
            flags=re.IGNORECASE
        )
        conversion_rate = int(pack_match.group(1)) if pack_match else 1

        tablet_keywords = (
            'tablet', 'capsule', 'pill', 'lozenge', 'ยาอม', 'ชนิดเม็ด',
            'paracetamol', 'antacid', 'anti-allergy', 'ดีคอลเจน'
        )
        non_tablet_keywords = ('counting dish', 'tray', 'ถาด')

        if any(keyword in lower_name for keyword in non_tablet_keywords):
            base_unit = raw_unit
        elif 'เม็ด' in lower_unit or any(keyword in lower_name for keyword in tablet_keywords):
            base_unit = 'เม็ด'
        elif conversion_rate > 1:
            base_unit = 'ชิ้น'
        else:
            base_unit = raw_unit

        return base_unit, raw_unit, conversion_rate

    def import_medicine_file(conn, df):
        rows_to_import = []
        for _, row in df.iterrows():
            raw_name = str(row.iloc[1]).strip() if len(row) > 1 and pd.notna(row.iloc[1]) else ''
            if not raw_name or raw_name.lower() in ('nan', 'รายการ/list'):
                continue

            raw_no = str(row.iloc[0]).strip() if len(row) > 0 and pd.notna(row.iloc[0]) else ''
            if raw_no.lower() == 'no.':
                continue

            name = clean_medicine_name(raw_name)
            if not name:
                continue

            stock = safe_int(row.iloc[2]) if len(row) > 2 else 0
            unit = str(row.iloc[3]).strip() if len(row) > 3 and pd.notna(row.iloc[3]) else 'ชิ้น'
            unit = normalize_medicine_unit(raw_name, unit)
            rows_to_import.append({
                'raw_name': raw_name,
                'name': name,
                'stock': stock,
                'unit': unit
            })

        updated_count = 0
        inserted_count = 0
        candidates = conn.execute('SELECT id, code, name FROM products').fetchall()
        next_cc_code = conn.execute('''
            SELECT COALESCE(MAX(CAST(SUBSTR(code, 10) AS INTEGER)), 0)
            FROM products
            WHERE code LIKE 'MEDIC-CC-%'
        ''').fetchone()[0]

        for index, item in enumerate(rows_to_import, start=1):
            normalized_name = normalize_lookup_name(item['name'])
            existing = conn.execute(
                'SELECT id, code, name FROM products WHERE TRIM(name) = TRIM(?)',
                (item['name'],)
            ).fetchone()

            if not existing:
                for product in candidates:
                    candidate_name = normalize_lookup_name(product['name'])
                    if not candidate_name or not normalized_name:
                        continue
                    if (
                        candidate_name == normalized_name or
                        normalized_name in candidate_name or
                        candidate_name in normalized_name
                    ):
                        existing = product
                        break

            base_unit, package_unit, conversion_rate = infer_medicine_setup(item['raw_name'], item['unit'])

            if existing:
                conn.execute('''
                    UPDATE products
                    SET name=?, category='ยา', unit=?, stock=?, base_unit=?, package_unit=?, conversion_rate=?, is_active=1
                    WHERE id=?
                ''', (item['name'], item['unit'], item['stock'], base_unit, package_unit, conversion_rate, existing['id']))
                updated_count += 1
            else:
                next_cc_code += 1
                generated_code = f"MEDIC-CC-{next_cc_code:03d}"
                while conn.execute('SELECT 1 FROM products WHERE code = ?', (generated_code,)).fetchone():
                    next_cc_code += 1
                    generated_code = f"MEDIC-CC-{next_cc_code:03d}"

                conn.execute('''
                    INSERT INTO products (
                        code, name, stock, safety_stock, category, unit, location,
                        withdraw, reserved_stock, is_active, base_unit, package_unit, conversion_rate
                    )
                    VALUES (?, ?, ?, 0, 'ยา', ?, 'ห้องยา', 0, 0, 1, ?, ?, ?)
                ''', (generated_code, item['name'], item['stock'], item['unit'], base_unit, package_unit, conversion_rate))
                inserted_count += 1

        return updated_count, inserted_count

    conn = None
    try:
        df = pd.read_excel(file)
        df.columns = df.columns.astype(str).str.strip()

        conn = get_db_connection()
        updated_count = 0
        inserted_count = 0

        code_col = next((col for col in df.columns if 'รหัสของ' in col or 'code' in col.lower()), None)
        preview_text = ' '.join(
            str(v).strip()
            for v in df.head(5).fillna('').astype(str).values.flatten().tolist()
            if str(v).strip()
        ).lower()
        is_medicine_file = 'รายการ/list' in preview_text or 'medicine' in str(df.columns[0]).lower()

        if not code_col and is_medicine_file:
            updated_count, inserted_count = import_medicine_file(conn, df)
            conn.commit()
            flash(f'✅ นำเข้ารายการยาสำเร็จ: อัปเดต {updated_count}, เพิ่มใหม่ {inserted_count}', 'success')
            return redirect(url_for('admin_dashboard'))

        if not code_col:
            flash('❌ ไม่พบคอลัมน์รหัสสินค้าในไฟล์ที่อัปโหลด', 'danger')
            return redirect(url_for('admin_dashboard'))

        for _, row in df.iterrows():
            code = str(row[code_col]).strip()
            if not code or code.lower() == 'nan':
                continue

            name_col = next((col for col in df.columns if 'ชื่อของ' in col), None)
            cat_col = next((col for col in df.columns if 'หมวดหมู่' in col), None)
            unit_col = next((col for col in df.columns if 'หน่วยนับ' in col), None)
            loc_col = next((col for col in df.columns if 'สถานที่เก็บ' in col or 'location' in col.lower()), None)
            safe_col = next((col for col in df.columns if 'จุดสั่งซื้อ' in col or 'safety stock' in col.lower()), None)
            stock_col = next((col for col in df.columns if 'จำนวนคงเหลือ' in col), None)

            name = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else 'No Name'
            category = str(row[cat_col]).strip() if cat_col and pd.notna(row[cat_col]) else 'General'
            unit = str(row[unit_col]).strip() if unit_col and pd.notna(row[unit_col]) else 'PCS'
            location = str(row[loc_col]).strip() if loc_col and pd.notna(row[loc_col]) else '-'
            safety_stock = safe_int(row[safe_col]) if safe_col else 0
            stock = safe_int(row[stock_col]) if stock_col else 0

            lot_col = next((col for col in df.columns if 'lot' in col.lower()), None)
            received_col = next((col for col in df.columns if 'วันที่รับเข้า' in col or 'received_date' in col.lower() or 'received date' in col.lower()), None)
            expiry_col = next((col for col in df.columns if 'วันหมดอายุ' in col or 'expiry_date' in col.lower() or 'expiry date' in col.lower()), None)
            lot_qty_col = next((col for col in df.columns if 'จำนวนใน lot' in col.lower() or 'lot qty' in col.lower() or 'lot quantity' in col.lower()), None)

            lot_no = str(row[lot_col]).strip() if lot_col and pd.notna(row[lot_col]) else ''
            received_date = standardize_date(row[received_col]) if received_col and pd.notna(row[received_col]) else ''
            expiry_date = standardize_date(row[expiry_col]) if expiry_col and pd.notna(row[expiry_col]) else ''
            lot_qty = safe_int(row[lot_qty_col]) if lot_qty_col and pd.notna(row[lot_qty_col]) else None

            active_col = next((col for col in df.columns if 'สถานะ' in col), None)
            is_active = 1
            if active_col and pd.notna(row[active_col]):
                status_text = str(row[active_col]).strip()
                is_active = 1 if status_text == 'เปิดใช้งาน' else 0

            existing = conn.execute('SELECT id FROM products WHERE code = ?', (code,)).fetchone()
            if existing:
                conn.execute('''
                    UPDATE products
                    SET name=?, stock=?, safety_stock=?, category=?, unit=?, location=?, is_active=?, lot_no=?, received_date=?, expiry_date=?
                    WHERE id=?
                ''', (name, stock, safety_stock, category, unit, location, is_active, lot_no, received_date, expiry_date, existing['id']))
                product_id = existing['id']
                updated_count += 1
            else:
                cursor = conn.execute('''
                    INSERT INTO products (code, name, stock, safety_stock, category, unit, location, withdraw, reserved_stock, is_active, lot_no, received_date, expiry_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
                ''', (code, name, stock, safety_stock, category, unit, location, is_active, lot_no, received_date, expiry_date))
                product_id = cursor.lastrowid
                inserted_count += 1

            if lot_no:
                lot_qty = stock if lot_qty is None else lot_qty
                existing_lot = conn.execute('SELECT id FROM product_lots WHERE product_id = ? AND lot_number = ?', (product_id, lot_no)).fetchone()
                if existing_lot:
                    conn.execute('''
                        UPDATE product_lots
                        SET qty = ?, received_date = ?, expiry_date = ?
                        WHERE id = ?
                    ''', (lot_qty, received_date, expiry_date, existing_lot['id']))
                else:
                    conn.execute('''
                        INSERT INTO product_lots (product_id, lot_number, qty, received_date, expiry_date)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (product_id, lot_no, lot_qty, received_date, expiry_date))

        conn.commit()
        flash(f'✅ นำเข้าสำเร็จ: อัปเดต {updated_count}, เพิ่มใหม่ {inserted_count}', 'success')
    except Exception as e:
        flash(f'❌ ผิดพลาด: {str(e)}', 'danger')
    finally:
        if conn:
            conn.close()

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
    base_unit = request.form.get('base_unit', '').strip() or 'เม็ด'
    package_unit = request.form.get('package_unit', '').strip() or unit
    conversion_rate = int(request.form.get('conversion_rate', 1) or 1)
    safety_stock = request.form.get('safety_stock', 0)
    stock = request.form.get('stock', 0)
    expiry_date = standardize_date(request.form.get('expiry_date', ''))

    conn = get_db_connection()
    try:
        conn.execute('''
            UPDATE products 
            SET name=?, unit=?, base_unit=?, package_unit=?, conversion_rate=?, safety_stock=?, stock=?, expiry_date=?
            WHERE code=?
        ''', (name, unit, base_unit, package_unit, conversion_rate, safety_stock, stock, expiry_date, code))
        conn.commit()
        # คืนค่า Success เป็น JSON แทนการ Redirect
        return jsonify({'success': True, 'message': 'แก้ไขข้อมูลของเรียบร้อย'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)})
    finally:
        conn.close()

@app.route('/admin/filter_low_stock')
def filter_low_stock():
    if not session.get('admin_logged_in'):
        return "Unauthorized", 401  # <--- สำคัญมาก: คืนค่า 401
    
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
    rows = enrich_products_for_display(conn, rows)
    conn.close()
    
    # ส่งผลลัพธ์กลับไปที่หน้า HTML (ส่วนของแถวตาราง)
    return render_template('low_stock_row.html', low_stock=rows)

@app.route('/admin/filter_stock')
def filter_stock():
    if not session.get('admin_logged_in'):
        return "Unauthorized", 401
    
    cat = request.args.get('cat', '')
    search = request.args.get('search', '')
    role = session.get('admin_role', 'superadmin')
    
    conn = get_db_connection()
    
    # 1. กรองสิทธิ์สถานที่ตาม Role ของแอดมิน
    loc_filter = ""
    if role == 'admin_pc1':
        loc_filter = " AND (location LIKE '%PC1%')"
    elif role == 'admin_cc':
        loc_filter = " AND (location LIKE '%Coil Center%' OR location LIKE '%CC%')"

    # 2. สร้างคำสั่ง SQL ค้นหา
    sql = f"SELECT * FROM products WHERE 1=1 {loc_filter}"
    params = []
    
    if cat:
        sql += " AND category = ?"
        params.append(cat)
    
    if search:
        sql += " AND (name LIKE ? OR code LIKE ?)"
        params.extend([f'%{search}%', f'%{search}%'])
        
    sql += " ORDER BY code ASC"
    
    rows = conn.execute(sql, params).fetchall()
    rows = enrich_products_for_display(conn, rows)
    conn.close()
    
    # ส่งกลับไปที่ Template ย่อยสำหรับแสดงแถวตาราง
    return render_template('stock_row.html', items=rows)

@app.route('/admin/filter_logs')
def filter_logs():
    # 1. ตรวจสอบ Session
    if not session.get('admin_logged_in'):
        return "Unauthorized", 401
    
    role = session.get('admin_role', 'superadmin')
    log_loc = request.args.get('log_loc', '')
    page = request.args.get('page', 1, type=int)
    per_page = 10
    offset = (page - 1) * per_page
    
    conn = get_db_connection()
    
    # 2. สร้าง Filter (ใช้โค้ดเดิมของคุณ)
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

    # --- ส่วนที่เพิ่ม: นับจำนวนหน้าใหม่ให้สัมพันธ์กับ Filter ---
    count_query = f'''
        SELECT COUNT(*) 
        FROM transaction_logs l
        LEFT JOIN users u ON (
            CASE 
                WHEN l.emp_id LIKE 'ADMIN:%' THEN SUBSTR(l.emp_id, 7) = u.name
                ELSE l.emp_id = u.emp_id 
            END
        )
        WHERE 1=1 {final_log_filter}
    '''
    total_logs = conn.execute(count_query).fetchone()[0]
    total_pages = math.ceil(total_logs / per_page) #

    # 3. Query ข้อมูล (ใช้โค้ดเดิมของคุณ)
    query = f'''
        SELECT l.*, 
               COALESCE(u.name, SUBSTR(l.emp_id, 7)) as emp_name, 
               u.department, u.location, p.name as product_name, p.unit
        FROM transaction_logs l
        LEFT JOIN users u ON (
            CASE 
                WHEN l.emp_id LIKE 'ADMIN:%' THEN SUBSTR(l.emp_id, 7) = u.name
                ELSE l.emp_id = u.emp_id 
            END
        )
        LEFT JOIN products p ON l.product_id = p.id
        WHERE 1=1 {final_log_filter}
        ORDER BY l.timestamp DESC LIMIT ? OFFSET ?
    '''
    
    logs = conn.execute(query, (per_page, offset)).fetchall()
    conn.close()
    
    # 4. ส่ง HTML พร้อมค่า total_pages กลับไปทาง Header
    response = make_response(render_template('admin_log_row.html', logs=logs))
    response.headers['X-Total-Pages'] = total_pages # ส่งเลขหน้าใหม่ไปให้ JS
    return response

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('index'))

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
    expire_date = standardize_date(request.form.get('expire_date', ''))

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

@app.route('/admin/write_off_ajax', methods=['POST'])
def write_off_ajax():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    admin_name = 'ADMIN:' + session.get('admin_name', 'Unknown')
    product_id = request.form.get('product_id')
    qty = request.form.get('qty', type=int)
    reason = request.form.get('reason', 'หมดอายุ')

    if not qty or qty <= 0:
        return jsonify({'success': False, 'message': 'จำนวนต้องมากกว่า 0'})

    conn = get_db_connection()
    product = conn.execute("SELECT stock FROM products WHERE id = ?", (product_id,)).fetchone()

    if not product or product['stock'] < qty:
        conn.close()
        return jsonify({'success': False, 'message': 'จำนวนสต็อกไม่เพียงพอให้ตัดจำหน่าย'})

    remaining_qty = qty

    # --- 1. ไล่ตัดสต็อกจากตาราง Lot แบบ FIFO ---
    # (เพิ่มการดึง lot_number ออกมาด้วยเพื่อเอาไปเขียนใน Log)
    lots = conn.execute('''
        SELECT id, qty, lot_number 
        FROM product_lots 
        WHERE product_id = ? AND qty > 0
        ORDER BY 
            CASE 
                WHEN expiry_date LIKE '%/%/%' THEN substr(expiry_date, 7, 4) || '-' || substr(expiry_date, 4, 2) || '-' || substr(expiry_date, 1, 2)
                ELSE expiry_date 
            END ASC
    ''', (product_id,)).fetchall()

    for lot in lots:
        if remaining_qty <= 0:
            break
        
        cut_qty = min(lot['qty'], remaining_qty)
        
        # 1.1 อัปเดตจำนวนในตาราง product_lots
        conn.execute("UPDATE product_lots SET qty = qty - ? WHERE id = ?", (cut_qty, lot['id']))
        
        # 1.2 บันทึกประวัติลง transaction_logs (แยกตาม Lot ที่ถูกตัด)
        # นำเลข Lot มาโชว์ในช่อง action ด้วยเพื่อให้แอดมินอ่านง่าย และเก็บ lot_id ลงฐานข้อมูล
        action_text = f"ตัดจำหน่าย (Scrap) - {reason} [Lot: {lot['lot_number']}]"
        conn.execute('''
            INSERT INTO transaction_logs (emp_id, product_id, lot_id, action, qty, status, timestamp)
            VALUES (?, ?, ?, ?, ?, 'Approved', datetime('now', '+7 hours'))
        ''', (admin_name, product_id, lot['id'], action_text, cut_qty))

        remaining_qty -= cut_qty

    # --- 2. อัปเดตสต็อกรวมในตารางหลัก (products) ---
    conn.execute("UPDATE products SET stock = stock - ? WHERE id = ?", (qty, product_id))

    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/admin/clear_system_data', methods=['POST'])
def clear_system_data():
    # 1. เช็คสิทธิ์ ต้องเป็น superadmin เท่านั้น
    if session.get('admin_role') != 'superadmin':
        return jsonify({'success': False, 'message': 'ไม่อนุญาต! ฟีเจอร์นี้สำหรับ Super Admin เท่านั้น'}), 403

    target = (request.form.get('target') or '').strip().lower()
    password = request.form.get('password') or ''

    SECURE_CLEAR_PASSWORD = os.environ.get('STOCK_PCM_CLEAR_PASSWORD', '')

    if target not in {'logs', 'lots'}:
        return jsonify({'success': False, 'message': 'target ไม่ถูกต้อง'}), 400

    if not SECURE_CLEAR_PASSWORD:
        return jsonify({'success': False, 'message': 'ยังไม่ได้ตั้งค่ารหัสผ่านพิเศษใน environment'}), 503

    # 2. ตรวจสอบรหัสผ่านพิเศษ
    if not secrets.compare_digest(password, SECURE_CLEAR_PASSWORD):
        return jsonify({'success': False, 'message': 'รหัสผ่านยืนยันไม่ถูกต้อง!'}), 403

    conn = get_db_connection()
    try:
        if target == 'logs':
            # ล้างประวัติการเบิกจ่าย
            conn.execute("DELETE FROM transaction_logs")
            # รีเซ็ตเลข Auto Increment ID ให้กลับไปเริ่มที่ 1 ใหม่
            conn.execute("DELETE FROM sqlite_sequence WHERE name='transaction_logs'")
            
        elif target == 'lots':
            # 1. ล้างตาราง Lot
            conn.execute("DELETE FROM product_lots")
            conn.execute("DELETE FROM sqlite_sequence WHERE name='product_lots'")
            # 2. รีเซ็ตจำนวนสินค้าและวันหมดอายุในตารางหลักให้กลับเป็นศูนย์
            conn.execute("UPDATE products SET stock = 0, expiry_date = ''")

        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        return jsonify({'success': False, 'message': str(e)}), 500
    finally:
        conn.close()

@app.route('/admin/monthly_report')
def monthly_report():
    if not session.get('admin_logged_in'): return redirect(url_for('index'))
    
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
        return redirect(url_for('index'))
    
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

# 1. API สำหรับดึงรายชื่อพนักงานค้างเป็น JSON
@app.route('/admin/list_zombies_json')
def list_zombies_json():
    if not session.get('admin_logged_in'): return jsonify([]), 401
    conn = get_db_connection()
    users = conn.execute('SELECT emp_id, name, department, location, last_seen FROM users WHERE is_locked = 1').fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

# 2. API สำหรับปลดล็อกแบบ AJAX
@app.route('/admin/unlock_user_ajax/<emp_id>', methods=['POST'])
def unlock_user_ajax(emp_id):
    if not session.get('admin_logged_in'): return jsonify({'success': False}), 401
    conn = get_db_connection()
    conn.execute("UPDATE users SET is_locked = 0 WHERE emp_id = ?", (emp_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# --- 2. ฟังก์ชันปลดล็อกรายบุคคล ---
@app.route('/admin/unlock_user/<emp_id>')
def unlock_user(emp_id):
    if not session.get('admin_logged_in'): 
        return redirect(url_for('index'))
    
    conn = get_db_connection()
    conn.execute("UPDATE users SET is_locked = 0 WHERE emp_id = ?", (emp_id,))
    conn.commit()
    conn.close()
    
    flash(f'✅ ปลดล็อกรหัส {emp_id} เรียบร้อยแล้ว', 'success')
    return redirect(url_for('manage_zombies'))
    
# --- เพิ่ม API สำหรับดึงจำนวนพนักงานที่ออนไลน์อยู่จริง ---
# แก้ไข Route ที่เกี่ยวข้องกับ API ทั้งหมด (ตัวอย่าง)
@app.route('/api/admin/online_count')
def get_online_count():
    if not session.get('admin_logged_in'):
        return "Unauthorized", 401  # <--- สำคัญมาก: คืนค่า 401
    
    conn = get_db_connection()
    count = conn.execute('SELECT COUNT(*) FROM users WHERE is_locked = 1').fetchone()[0]
    conn.close()
    return jsonify({'count': count})

# ทำแบบเดียวกันใน /admin/filter_stock และ /admin/filter_logs

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
    if session.get('admin_role') != 'superadmin':
        return "Unauthorized", 401

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
        conn = get_db_connection()
        
        # ==========================================
        # 1. แจ้งเตือนของใกล้หมดอายุ (ครอบคลุมของเก่าและ Lot ใหม่)
        # ==========================================
        # ใช้ Subquery แปลงวันที่ให้เป็น YYYY-MM-DD ก่อน แล้วค่อยเอามาเปรียบเทียบ
        expiry_query = '''
            SELECT name, formatted_expiry AS expiry_date, category FROM (
                -- ส่วนที่ 1: ของเก่าจากตารางหลัก (products)
                SELECT 
                    name, 
                    CASE 
                        WHEN expiry_date LIKE '%/%/%' THEN substr(expiry_date, 7, 4) || '-' || substr(expiry_date, 4, 2) || '-' || substr(expiry_date, 1, 2)
                        ELSE trim(expiry_date)
                    END AS formatted_expiry, 
                    category 
                FROM products 
                WHERE stock > 0 
                AND expiry_date IS NOT NULL AND trim(expiry_date) != ''
                AND (category LIKE '%ยา%' OR name LIKE '%Helmet%' OR name LIKE '%Coffee%')
                
                UNION
                
                -- ส่วนที่ 2: ของใหม่จากตาราง Lot (product_lots)
                SELECT 
                    p.name, 
                    CASE 
                        WHEN pl.expiry_date LIKE '%/%/%' THEN substr(pl.expiry_date, 7, 4) || '-' || substr(pl.expiry_date, 4, 2) || '-' || substr(pl.expiry_date, 1, 2)
                        ELSE trim(pl.expiry_date)
                    END AS formatted_expiry, 
                    p.category 
                FROM product_lots pl
                JOIN products p ON pl.product_id = p.id
                WHERE pl.qty > 0 
                AND pl.expiry_date IS NOT NULL AND trim(pl.expiry_date) != ''
                AND (p.category LIKE '%ยา%' OR p.name LIKE '%Helmet%' OR p.name LIKE '%Coffee%')
            )
            -- กรองเอาเฉพาะอันที่หมดอายุหรือใกล้หมดอายุ (ภายใน 30 วัน)
            WHERE formatted_expiry <= date('now', '+7 hours', '+30 days')
            ORDER BY formatted_expiry ASC
        '''
        expiring_items = conn.execute(expiry_query).fetchall()

        # ==========================================
        # 2. เช็คหมวกเซฟตี้ (แยกตามพนักงาน, สินค้า และ Lot)
        # ==========================================
        helmet_query = '''
            SELECT 
                u.name AS emp_name, 
                u.department, 
                u.location, 
                p.name AS product_name,
                l.lot_id, -- ดึง Lot ID ออกมาด้วย
                MAX(
                    CASE 
                        WHEN l.timestamp LIKE '%/%/%' THEN 
                            substr(l.timestamp, 7, 4) || '-' || substr(l.timestamp, 4, 2) || '-' || substr(l.timestamp, 1, 2) || substr(l.timestamp, 11)
                        ELSE l.timestamp 
                    END
                ) AS last_timestamp
            FROM transaction_logs l
            JOIN users u ON l.emp_id = u.emp_id
            JOIN products p ON l.product_id = p.id
            WHERE (p.name LIKE '%หมวก%' OR p.name LIKE '%Helmet%' OR l.action LIKE '%หมวก%')
            AND l.status = 'Approved'
            GROUP BY u.emp_id, p.id, l.lot_id  -- 🎯 จุดสำคัญ: สั่งให้แยกการเช็คอายุตาม คน + สินค้า + Lot
            HAVING last_timestamp <= datetime('now', '+7 hours', '-23 months')
        '''
        helmet_alerts = conn.execute(helmet_query).fetchall()
        conn.close()
        
        # ==========================================
        # 3. รวมข้อความและส่งเข้า LINE
        # ==========================================
        message = ""
        
        if expiring_items:
            message += "⚠️ [แจ้งเตือนของใกล้หมดอายุ]\n"
            for item in expiring_items:
                # 🎯 สลับตำแหน่งวันที่จาก YYYY-MM-DD เป็น DD/MM/YYYY
                date_parts = item['expiry_date'].split('-')
                show_date = f"{date_parts[2]}/{date_parts[1]}/{date_parts[0]}" if len(date_parts) == 3 else item['expiry_date']

                message += f"📦 {item['name']} ({item['category']})\n"
                message += f"🗓️ หมดอายุ: {show_date}\n"
                message += "--------------------------\n"

        if helmet_alerts:
            if message != "": message += "\n" 
            message += "👷 [ครบกำหนดเปลี่ยนหมวกเซฟตี้]\n"
            for alert in helmet_alerts:
                emp_info = f"{alert['emp_name']} ({alert['department']} - {alert['location']})"
                lot_text = f" [Lot: {alert['lot_id']}]" if alert['lot_id'] else ""
                
                # 🎯 ตัดเวลาทิ้งแล้วสลับตำแหน่งวันที่จาก YYYY-MM-DD เป็น DD/MM/YYYY
                last_date = alert['last_timestamp'][:10]
                date_parts = last_date.split('-')
                show_date = f"{date_parts[2]}/{date_parts[1]}/{date_parts[0]}" if len(date_parts) == 3 else last_date
                
                message += f"👤 {emp_info}\n"
                message += f"📦 รายการ: {alert['product_name']}{lot_text}\n"
                message += f"🗓️ เบิกล่าสุด: {show_date}\n"
                message += "--------------------------\n"
            
        if message:
            send_line_message(message.strip())

def update_scheduler_time(new_time):
    """
    new_time: รูปแบบ "HH:MM" (24 ชม.) เช่น "15:30"
    """
    try:
        hour, minute = new_time.split(':')
        
        # ใช้ scheduler.scheduler เพื่อเข้าถึงเมธอดของ APScheduler โดยตรง
        scheduler.scheduler.reschedule_job(
            'Daily_Alert_Job', 
            trigger='cron', 
            hour=int(hour), 
            minute=int(minute),
            timezone='Asia/Bangkok'
        )
        return True
    except Exception as e:
        # หาก Job ยังไม่ถูกสร้าง (หา ID ไม่เจอ) ให้ใช้ add_job แทน
        try:
            hour, minute = new_time.split(':')
            scheduler.add_job(
                id='Daily_Alert_Job',
                func=scheduled_daily_alert,
                trigger='cron',
                hour=int(hour),
                minute=int(minute),
                timezone='Asia/Bangkok',
                replace_existing=True
            )
            return True
        except Exception as ex:
            return False

# 1. API ดึงเวลาปัจจุบันมาโชว์ในช่อง Input
@app.route('/admin/get_alert_time')
def get_alert_time():
    if not session.get('admin_logged_in'): 
        return jsonify({'error': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    # ดึงค่าจากตาราง settings
    row = conn.execute("SELECT value FROM settings WHERE key = 'daily_alert_time'").fetchone()
    conn.close()
    
    current_time = row['value'] if row else "08:30"
    return jsonify({'time': current_time}) #

# 2. API บันทึกเวลาใหม่
@app.route('/admin/save_alert_time', methods=['POST'])
def save_alert_time():
    if session.get('admin_role') != 'superadmin': return jsonify({'success': False, 'message': 'No Permission'})
    new_time = request.form.get('alert_time')
    
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('daily_alert_time', ?)", (new_time,))
    conn.commit()
    conn.close()

    # 💡 จุดสำคัญ: สั่งให้ Scheduler อัปเดตเวลาทำงานใหม่ทันที
    update_scheduler_time(new_time)
    
    return jsonify({'success': True, 'message': f'เปลี่ยนเวลาเป็น {new_time} น. เรียบร้อย'})

def init_settings_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')
    # ตั้งค่าเวลาเริ่มต้นเป็น 08:30 น. ถ้ายังไม่มีข้อมูล
    conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('daily_alert_time', '08:30')")
    conn.commit()
    conn.close()

@app.route('/setup_settings')
def setup_settings():
    if session.get('admin_role') != 'superadmin':
        return "Unauthorized", 401

    try:
        init_settings_db()
        return "✅ ตาราง settings ถูกสร้างและตั้งค่าเริ่มต้นเรียบร้อยแล้ว!"
    except Exception as e:
        return f"❌ เกิดข้อผิดพลาด: {str(e)}"

# --- 2. ตั้ง Job แบบใช้ฟังก์ชันธรรมดา ---
@scheduler.task('cron', id='Daily_Alert_Job', hour=14, minute=45, timezone='Asia/Bangkok')
def scheduled_daily_alert_task():
    # บังคับให้ใช้ App Context เพื่อให้ดึง DB ได้บน Codespaces
    with app.app_context():
        scheduled_daily_alert()

# --- 3. Route สำหรับ Test (เพื่อให้ชัวร์ว่า Path ถูก) ---
@app.route('/test_alert')
def test_alert():
    if not session.get('admin_logged_in'):
        return "Unauthorized", 401

    scheduled_daily_alert()
    return "🚀 สั่งรันระบบแจ้งเตือนเรียบร้อย! เช็ค LINE และ Terminal"

@app.route('/admin/get_monthly_report_data')
def get_monthly_report_data():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    month = request.args.get('month')
    year = request.args.get('year')
    
    # ดึงข้อมูลวันที่ทั้ง 2 รูปแบบที่พบใน DB ของคุณ
    pattern1 = f'{year}-{month}-%'   # yyyy-mm-dd
    pattern2 = f'%/{month}/{year} %' # dd/mm/yyyy
    
    conn = get_db_connection()
    query = '''
        SELECT p.name as item_name, SUM(l.qty) as total, p.unit
        FROM transaction_logs l
        JOIN products p ON l.product_id = p.id
        WHERE (l.timestamp LIKE ? OR l.timestamp LIKE ?) 
        AND l.status = 'Approved'
        GROUP BY p.id
        ORDER BY total DESC
    '''
    results = conn.execute(query, (pattern1, pattern2)).fetchall()
    conn.close()
    
    return jsonify({
        'labels': [r['item_name'] for r in results],
        'values': [r['total'] for r in results],
        'units': [r['unit'] for r in results]
    })

@app.route('/admin/export_monthly_excel')
def export_monthly_excel():
    if not session.get('admin_logged_in'): return redirect(url_for('index'))
    
    month = request.args.get('month')
    year = request.args.get('year')
    pattern1 = f'{year}-{month}-%'
    pattern2 = f'%/{month}/{year} %'

    conn = get_db_connection()
    query = '''
        SELECT 
            l.timestamp as "วัน/เวลา",
            u.name as "ผู้เบิก",
            u.department as "แผนก",
            p.code as "รหัสสินค้า",
            p.name as "รายการสินค้า",
            l.qty as "จำนวน",
            p.unit as "หน่วย",
            l.status as "สถานะ"
        FROM transaction_logs l
        JOIN products p ON l.product_id = p.id
        JOIN users u ON l.emp_id = u.emp_id
        WHERE (l.timestamp LIKE ? OR l.timestamp LIKE ?) 
        AND l.status = 'Approved'
        ORDER BY l.timestamp ASC
    '''
    df = pd.read_sql_query(query, conn, params=(pattern1, pattern2))
    conn.close()

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Monthly_Report')
    output.seek(0)

    return send_file(output, as_attachment=True, download_name=f"Report_{month}_{year}.xlsx")
    
if __name__ == '__main__':
    # 1. เริ่มต้นระบบจัดการ Job
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        scheduler.init_app(app)
        
        # 2. ดึงเวลาจาก Database มาตั้งค่าเริ่มต้น
        conn = get_db_connection()
        saved_time = conn.execute("SELECT value FROM settings WHERE key = 'daily_alert_time'").fetchone()
        conn.close()
        
        alert_time = saved_time['value'] if saved_time else "08:30"
        h, m = alert_time.split(':')

        # 3. เพิ่ม Job เข้าไปในระบบ (ถ้ามีอยู่แล้วให้ทับของเก่า)
        scheduler.add_job(
            id='Daily_Alert_Job',
            func=scheduled_daily_alert, # ชื่อฟังก์ชันส่ง LINE ที่คุณมีอยู่แล้ว
            trigger='cron',
            hour=int(h),
            minute=int(m),
            timezone='Asia/Bangkok',
            replace_existing=True
        )
        
        scheduler.start()

    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode, use_reloader=debug_mode) # ควบคุมผ่าน environment