import io
import calendar
import math
import mimetypes
import re
import secrets
import smtplib
import sqlite3
import tempfile
import time
from collections import defaultdict
from datetime import datetime, date, timedelta, timezone
from email.message import EmailMessage
from html import escape
from urllib.parse import urljoin

import os
import pandas as pd
import pytz
import qrcode
import requests
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify, make_response, abort, has_request_context
from flask_apscheduler import APScheduler
from io import BytesIO
from unit_conversion import UnitConversionManager  # ✅ Unit Conversion Support
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.exceptions import RequestEntityTooLarge
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

app.secret_key = configured_secret
app.config.update(
    MAX_CONTENT_LENGTH=int(os.environ.get('MAX_UPLOAD_MB', '15')) * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=os.environ.get('SESSION_COOKIE_SECURE', '0') == '1',
    TEMPLATES_AUTO_RELOAD=True
)
app.jinja_env.auto_reload = True
DB_NAME = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'factory_stock.db')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
THAILAND_TZ = 'Asia/Bangkok'
SESSION_TIMEOUT_MINUTES = 15
USER_LOCK_TIMEOUT_MINUTES = 5
ALLOWED_IMPORT_EXTENSIONS = {'xlsx', 'xlsm', 'xls'}
ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
_default_ga_upload_root = os.path.join(os.environ.get('LOCALAPPDATA', BASE_DIR), 'PCM', 'ga_uploads')
GA_REQUEST_UPLOAD_DIR = os.environ.get('GA_REQUEST_UPLOAD_DIR', _default_ga_upload_root)
GA_REQUEST_TARGET_TEAMS = ('GA', 'IT')
GA_REQUEST_STATUS_OPTIONS = ('Pending', 'In Progress', 'Resolved', 'Rejected')
SENSITIVE_POST_ENDPOINTS = {
    'index', 'admin_login', 'logout_user', 'admin_logout',
    'add_to_cart', 'remove_from_cart', 'update_cart_qty', 'confirm_withdrawal',
    'approve_request', 'reject_request', 'cancel_scheduled_withdrawal', 'reschedule_withdrawal', 'import_excel', 'clear_system_data',
    'toggle_product_status', 'add_product', 'edit_product', 'add_product_ajax',
    'write_off_ajax', 'unlock_user_ajax', 'unlock_user', 'add_user_ajax', 'delete_user',
    'update_user_ajax', 'save_alert_time', 'daily_alert', 'reset_lock',
    'email_settings', 'email_settings_test',
    'ga_request_portal', 'update_ga_request'
}
NO_CACHE_HEADERS = {
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache",
    "Expires": "0",
}
MAX_LOGIN_ATTEMPTS = int(os.environ.get('MAX_LOGIN_ATTEMPTS', '5'))
LOGIN_BLOCK_MINUTES = int(os.environ.get('LOGIN_BLOCK_MINUTES', '5'))
FAILED_LOGIN_ATTEMPTS = {}

def utc_now_naive():
    """UTC time แบบ naive เพื่อเข้ากับข้อมูลเดิมในระบบ rate-limit."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

def get_client_ip():
    forwarded_for = (request.headers.get('X-Forwarded-For') or '').split(',')[0].strip()
    return forwarded_for or (request.remote_addr or 'unknown')

def is_auth_rate_limited(scope):
    key = f"{scope}:{get_client_ip()}"
    now = utc_now_naive()
    entry = FAILED_LOGIN_ATTEMPTS.get(key)
    if not entry:
        return False, 0

    blocked_until = entry.get('blocked_until')
    if blocked_until and now < blocked_until:
        remaining_seconds = max(0, int((blocked_until - now).total_seconds()))
        return True, max(1, math.ceil(remaining_seconds / 60))

    if blocked_until and now >= blocked_until:
        FAILED_LOGIN_ATTEMPTS.pop(key, None)

    return False, 0

def register_failed_attempt(scope):
    key = f"{scope}:{get_client_ip()}"
    now = utc_now_naive()
    entry = FAILED_LOGIN_ATTEMPTS.get(key, {'count': 0, 'blocked_until': None})

    if entry.get('blocked_until') and now >= entry['blocked_until']:
        entry = {'count': 0, 'blocked_until': None}

    entry['count'] += 1
    if entry['count'] >= MAX_LOGIN_ATTEMPTS:
        entry['blocked_until'] = now + timedelta(minutes=LOGIN_BLOCK_MINUTES)

    FAILED_LOGIN_ATTEMPTS[key] = entry
    return entry

def clear_failed_attempts(scope):
    FAILED_LOGIN_ATTEMPTS.pop(f"{scope}:{get_client_ip()}", None)

def clean_input_text(value, max_length=100):
    text = re.sub(r'\s+', ' ', str(value or '').strip())
    return text[:max_length]

def clean_multiline_text(value, max_length=2000):
    text = str(value or '').replace('\x00', '')
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    text = '\n'.join(line.strip() for line in text.split('\n'))
    text = re.sub(r'\n{3,}', '\n\n', text).strip()
    return text[:max_length]

def normalize_email_value(value):
    return clean_input_text(value, 255).lower()

def is_valid_email_address(value):
    if not value:
        return True
    return bool(re.fullmatch(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', value))

def normalize_location_value(value):
    raw = clean_input_text(value, 30)
    lower = raw.lower()
    if lower == 'pc1':
        return 'PC1'
    if lower in ('cc', 'coil center', 'coilcenter'):
        return 'Coil Center'
    if lower in ('general', 'ทั่วไป'):
        return 'General'
    return raw

def is_cc_location_value(value):
    text = str(value or '').strip().lower()
    return text == 'cc' or 'coil center' in text or ' cc' in f' {text}'

def is_valid_emp_id(emp_id):
    return bool(re.fullmatch(r'[A-Za-z0-9_-]{1,20}', str(emp_id or '').strip()))

def is_valid_alert_time(value):
    return bool(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', str(value or '').strip()))

def generate_csrf_token():
    token = session.get('_csrf_token')
    if not token:
        token = secrets.token_hex(16)
        session['_csrf_token'] = token
    return token

app.jinja_env.globals['csrf_token'] = generate_csrf_token

def format_timestamp(ts):
    """แปลง timestamp ทุกรูปแบบให้เป็น DD/MM/YYYY HH:MM เสมอ"""
    if not ts:
        return ''
    ts = str(ts).strip()
    # รูปแบบ DD/MM/YYYY HH:MM:SS หรือ DD/MM/YYYY HH:MM
    if len(ts) >= 10 and ts[2:3] == '/' and ts[5:6] == '/':
        return ts[:16]
    # รูปแบบ YYYY-MM-DD HH:MM:SS หรือ YYYY-MM-DDTHH:MM:SS
    ts_norm = ts[:19].replace('T', ' ')
    try:
        dt = datetime.strptime(ts_norm, '%Y-%m-%d %H:%M:%S')
        return dt.strftime('%d/%m/%Y %H:%M')
    except ValueError:
        return ts[:16]

def normalize_request_receive_mode(value):
    return 'scheduled' if str(value or '').strip().lower() == 'scheduled' else 'immediate'

def parse_requested_receive_at(value):
    raw_value = str(value or '').strip()
    if not raw_value:
        return ''
    normalized = raw_value.replace('T', ' ')
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S'):
        try:
            dt = datetime.strptime(normalized, fmt)
            return dt.strftime('%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
    return ''

def build_receive_plan_text(receive_mode, requested_receive_at=''):
    if normalize_request_receive_mode(receive_mode) == 'scheduled' and requested_receive_at:
        return f"เบิกล่วงหน้า รับวันที่ {format_timestamp(requested_receive_at)} น."
    return 'รับของทันที'

app.jinja_env.filters['format_ts'] = format_timestamp

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
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('Referrer-Policy', 'same-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=()')
    return response

# สร้างฟังก์ชันสำหรับดึงเวลาไทย
def get_thailand_time():
    tz = pytz.timezone(THAILAND_TZ)
    return datetime.now(tz)

def is_medicine_product(product_row):
    """True เมื่อของเป็นกลุ่มยา โดยหลีกเลี่ยง false positive เช่น น้ำยาล้างจาน"""
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
    package_keywords = ('pack', 'package', 'box', 'jar', 'bottle', 'tube', 'strip', 'sheet', 'sachet', 'แพ็ค', 'ห่อ', 'แผง', 'ซอง', 'กล่อง', 'กระปุก', 'ขวด', 'หลอด')
    return conversion_rate > 1 and any(k in package_label for k in package_keywords)

def get_split_unit_hint_text(product_row):
    """ข้อความกำกับหน่วยย่อย เช่น 1 ซอง = 10 เม็ด สำหรับรายการที่แยกหน่วยได้"""
    if not product_row or not is_split_tablet_medicine(product_row):
        return ''

    row_keys = product_row.keys() if hasattr(product_row, 'keys') else []
    package_unit = str(
        (product_row['package_unit'] if 'package_unit' in row_keys else None)
        or (product_row['unit'] if 'unit' in row_keys else None)
        or 'ซอง'
    ).strip()
    base_unit = str(
        (product_row['base_unit'] if 'base_unit' in row_keys else None)
        or 'เม็ด'
    ).strip()
    conversion_rate = int((product_row['conversion_rate'] if 'conversion_rate' in row_keys else 1) or 1)
    base_to_tablet_rate = int((product_row['base_unit_to_tablet_rate'] if 'base_unit_to_tablet_rate' in row_keys else 0) or 0)
    package_tablet_total = int((product_row['package_tablet_total'] if 'package_tablet_total' in row_keys else 0) or 0)
    product_name = str((product_row['name'] if 'name' in row_keys else '') or '').strip()
    lower_base = base_unit.lower()

    tablet_like_units = {'เม็ด', 'tablet', 'tablets', 'pill', 'pills', 'capsule', 'capsules'}
    wrapper_units = {'ซอง', 'ห่อ', 'แผง', 'ตลับ', 'strip', 'sachet', 'pack'}

    if base_to_tablet_rate > 0:
        if package_tablet_total <= 0:
            package_tablet_total = conversion_rate * base_to_tablet_rate

        # กรณีฐานเป็นเม็ด แต่กำหนด "ห่อละกี่เม็ด" ไว้: แสดงคำนวณจำนวนห่อต่อแพ็กให้ชัดเจน
        if lower_base in tablet_like_units:
            bundle_count = package_tablet_total // base_to_tablet_rate
            bundle_remainder = package_tablet_total % base_to_tablet_rate
            if bundle_count > 0:
                if bundle_remainder > 0:
                    return (
                        f"1 {package_unit} = {package_tablet_total} เม็ด | "
                        f"จัดได้ {bundle_count} ห่อ (ห่อละ {base_to_tablet_rate} เม็ด) + เหลือ {bundle_remainder} เม็ด"
                    )
                return (
                    f"1 {package_unit} = {package_tablet_total} เม็ด | "
                    f"จัดได้ {bundle_count} ห่อ (ห่อละ {base_to_tablet_rate} เม็ด)"
                )
            return f"1 {package_unit} = {package_tablet_total} เม็ด"

        bundle_count = package_tablet_total // base_to_tablet_rate
        bundle_remainder = package_tablet_total % base_to_tablet_rate
        if bundle_remainder > 0:
            return f"1 {base_unit} = {base_to_tablet_rate} เม็ด | 1 {package_unit} = {bundle_count} {base_unit} + เหลือ {bundle_remainder} เม็ด"
        return f"1 {base_unit} = {base_to_tablet_rate} เม็ด | 1 {package_unit} = {bundle_count} {base_unit}"

    # กรณีหน่วยย่อยเป็นเม็ดอยู่แล้ว: แสดงตรง ๆ แบบที่ผู้เบิกเข้าใจง่าย
    if lower_base in tablet_like_units:
        return f"1 {package_unit} = {conversion_rate} {base_unit}"

    # พยายามดึง "จำนวนเม็ดต่อหน่วยย่อย" จากชื่อสินค้า (ถ้าชื่อระบุไว้)
    base_to_tablet = None
    if lower_base in wrapper_units and product_name:
        escaped_base = re.escape(base_unit)
        patterns = [
            rf"(\d+)\s*เม็ด\s*/\s*{escaped_base}",
            rf"{escaped_base}\s*/\s*(\d+)\s*เม็ด",
            rf"{escaped_base}\s*(\d+)\s*เม็ด",
        ]
        for pattern in patterns:
            match = re.search(pattern, product_name, flags=re.IGNORECASE)
            if match:
                try:
                    base_to_tablet = int(match.group(1))
                except (TypeError, ValueError):
                    base_to_tablet = None
                if base_to_tablet and base_to_tablet > 0:
                    break

    # ถ้าระบุเม็ดต่อหน่วยย่อยได้: โชว์แบบ user-centric ก่อน แล้วค่อยบอกโครงสร้างแพ็ก
    if base_to_tablet:
        return f"1 {base_unit} = {base_to_tablet} เม็ด | 1 {package_unit} = {conversion_rate} {base_unit}"

    # ไม่มีข้อมูลเม็ดต่อหน่วยย่อย: บอกหน่วยเบิกที่ต้องใช้ก่อน เพื่อไม่ให้ผู้เบิกสับสน
    return f"หน่วยเบิก: {base_unit} | 1 {package_unit} = {conversion_rate} {base_unit}"

def enrich_products_for_display(conn, products_list):
    """เติมข้อมูลสต็อกแสดงผลสำหรับ frontend/backend โดยไม่แก้ค่าจริงใน DB"""
    if not products_list:
        return []

    product_ids = [item['id'] for item in products_list]
    placeholders = ','.join(['?'] * len(product_ids))
    open_rows = conn.execute(f'''
        SELECT product_id,
               COALESCE(SUM(base_unit_qty), 0) as open_base_qty,
               COALESCE(SUM(extra_tablet_qty), 0) as open_extra_tablet_qty
        FROM open_packages
        WHERE status = 'active' AND product_id IN ({placeholders})
        GROUP BY product_id
    ''', product_ids).fetchall()
    open_qty_map = {row['product_id']: int(row['open_base_qty'] or 0) for row in open_rows}
    open_extra_tablet_map = {row['product_id']: int(row['open_extra_tablet_qty'] or 0) for row in open_rows}

    enriched = []
    for row in products_list:
        item = dict(row)
        split_medicine = is_split_tablet_medicine(row)
        split_hint_text = get_split_unit_hint_text(row)
        package_unit = str(item.get('package_unit') or item.get('unit') or 'กล่อง')
        base_unit = str(item.get('base_unit') or 'เม็ด')
        conversion_rate = int(item.get('conversion_rate') or 1)
        package_tablet_total = int(item.get('package_tablet_total') or 0)
        open_base_qty = open_qty_map.get(item['id'], 0)
        open_extra_tablet_qty = open_extra_tablet_map.get(item['id'], 0)
        package_stock = int(item.get('stock') or 0)
        total_base_qty = (package_stock * conversion_rate) + open_base_qty
        base_to_tablet_rate = int(item.get('base_unit_to_tablet_rate') or 0)
        package_remainder_tablets = 0
        total_remainder_tablets = open_extra_tablet_qty
        stock_tablet_total = 0
        pooled_tablet_remainder = 0

        # ลบ reserved_stock ออกจาก total เพื่อแสดงยอดที่ยังเบิกได้จริง
        reserved_stock = int(item.get('reserved_stock') or 0)
        if split_medicine and base_to_tablet_rate > 0 and package_tablet_total > 0:
            package_remainder_tablets = package_stock * (package_tablet_total % base_to_tablet_rate)
            total_remainder_tablets = package_remainder_tablets + open_extra_tablet_qty
            stock_tablet_total = (package_stock * package_tablet_total) + (open_base_qty * base_to_tablet_rate) + open_extra_tablet_qty
            total_base_qty = stock_tablet_total // base_to_tablet_rate
            pooled_tablet_remainder = stock_tablet_total % base_to_tablet_rate

        if split_medicine:
            available_base = max(0, total_base_qty - reserved_stock)
        else:
            available_base = max(0, package_stock - reserved_stock)

        item['is_split_tablet_medicine'] = split_medicine
        item['split_unit_hint_label'] = split_hint_text
        item['split_unit_hint_text'] = f" ({split_hint_text})" if split_hint_text else ''
        item['name_with_unit_hint'] = item.get('name', '')
        item['base_unit_to_tablet_rate'] = base_to_tablet_rate
        if package_tablet_total <= 0:
            if base_to_tablet_rate > 0:
                package_tablet_total = conversion_rate * base_to_tablet_rate
            elif base_unit.lower() in {'เม็ด', 'tablet', 'tablets', 'pill', 'pills', 'capsule', 'capsules'}:
                package_tablet_total = conversion_rate
        item['package_tablet_total'] = package_tablet_total
        item['package_unit_label'] = package_unit
        item['base_unit_label'] = base_unit
        item['open_base_qty'] = open_base_qty
        item['open_extra_tablet_qty'] = open_extra_tablet_qty
        item['package_remainder_tablets'] = package_remainder_tablets

        extra_bundles_from_open_remainder = 0
        true_tablet_remainder = 0
        effective_base_for_withdraw = available_base
        if split_medicine and base_to_tablet_rate > 0:
            # รวมเศษทันทีจากทุกแพ็ค + เศษที่เปิดแล้ว
            if package_tablet_total > 0:
                true_tablet_remainder = pooled_tablet_remainder
                extra_bundles_from_open_remainder = total_remainder_tablets // base_to_tablet_rate
                effective_base_for_withdraw = available_base
            else:
                extra_bundles_from_open_remainder = open_extra_tablet_qty // base_to_tablet_rate
                true_tablet_remainder = open_extra_tablet_qty % base_to_tablet_rate
                effective_base_for_withdraw = available_base + extra_bundles_from_open_remainder

        item['stock_base_total'] = effective_base_for_withdraw if split_medicine else total_base_qty
        item['effective_stock'] = effective_base_for_withdraw if split_medicine else available_base

        if split_medicine:
            frontend_stock_text = f"{effective_base_for_withdraw} {base_unit}"
            if base_to_tablet_rate > 0 and true_tablet_remainder > 0:
                frontend_stock_text = f"{frontend_stock_text} + เศษ {true_tablet_remainder} เม็ด"

            backend_stock_text = f"{package_stock} {package_unit} + {open_base_qty} {base_unit}"

            item['frontend_stock_text'] = frontend_stock_text
            item['backend_stock_text'] = backend_stock_text
        else:
            item['frontend_stock_text'] = f"{available_base} {item.get('unit', '')}".strip()
            item['backend_stock_text'] = f"{package_stock} {item.get('unit', '')}".strip()

        item['max_withdraw_qty'] = effective_base_for_withdraw if split_medicine else available_base
        if split_medicine and base_to_tablet_rate > 0:
            per_package_bundle_count = package_tablet_total // base_to_tablet_rate
            per_package_tablet_remainder = package_tablet_total % base_to_tablet_rate
            if stock_tablet_total <= 0:
                stock_tablet_total = (package_stock * package_tablet_total) + (open_base_qty * base_to_tablet_rate) + open_extra_tablet_qty
            item['stock_tablet_total'] = stock_tablet_total
            # รวมเศษจากแพ็ค + เศษเปิดแล้ว เพื่อแปลงเป็นหน่วยย่อยได้ทันที
            # จำนวนหน่วยย่อยเพิ่มเติมที่จัดได้จากเศษรวม
            extra_bundles_from_remainder = total_remainder_tablets // base_to_tablet_rate
            # เศษสุดท้ายที่ไม่สามารถจัดเพิ่มได้อีก
            true_tablet_remainder = pooled_tablet_remainder if package_tablet_total > 0 else (total_remainder_tablets % base_to_tablet_rate)
            item['packable_bundle_count'] = effective_base_for_withdraw
            item['packable_tablet_remainder'] = true_tablet_remainder
            item['extra_bundles_from_remainder'] = extra_bundles_from_remainder
            item['total_remainder_tablets'] = total_remainder_tablets
            item['per_package_bundle_count'] = per_package_bundle_count
            item['per_package_tablet_remainder'] = per_package_tablet_remainder
        else:
            item['stock_tablet_total'] = 0
            item['packable_bundle_count'] = 0
            item['packable_tablet_remainder'] = 0
            item['extra_bundles_from_remainder'] = 0
            item['total_remainder_tablets'] = 0
            item['per_package_bundle_count'] = 0
            item['per_package_tablet_remainder'] = 0
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

def normalize_lot_number(value):
    """Normalize lot number from manual input / Excel to avoid duplicate logical lots."""
    lot = clean_input_text(value, 50)
    if not lot:
        return ''

    # Remove spaces so variants like "06 04 2026" map to one value.
    lot = re.sub(r'\s+', '', lot)

    # Excel often turns lot values into floats like "6042026.0".
    numeric_match = re.fullmatch(r'(\d+)(?:\.0+)?', lot)
    if numeric_match:
        digits = numeric_match.group(1)
        # Date-like lot numbers should keep 8 digits (DDMMYYYY).
        if len(digits) in (7, 8):
            return digits.zfill(8)
        return digits

    return lot.upper()

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

def ensure_notification_settings_columns(conn):
    """Ensure recipient split columns exist for older databases."""
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(notification_settings)").fetchall()}
    except sqlite3.OperationalError:
        columns = set()

    if columns and 'email_recipients_pc1' not in columns:
        conn.execute("ALTER TABLE notification_settings ADD COLUMN email_recipients_pc1 TEXT")
        columns.add('email_recipients_pc1')
    if columns and 'email_recipients_cc' not in columns:
        conn.execute("ALTER TABLE notification_settings ADD COLUMN email_recipients_cc TEXT")
        columns.add('email_recipients_cc')

    # Normalize legacy NULL values to empty string so textarea retains predictable content.
    if columns and 'email_recipients_pc1' in columns:
        conn.execute("UPDATE notification_settings SET email_recipients_pc1 = '' WHERE email_recipients_pc1 IS NULL")
    if columns and 'email_recipients_cc' in columns:
        conn.execute("UPDATE notification_settings SET email_recipients_cc = '' WHERE email_recipients_cc IS NULL")

def ensure_application_schema():
    os.makedirs(GA_REQUEST_UPLOAD_DIR, exist_ok=True)

    conn = sqlite3.connect(DB_NAME, timeout=20)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")

        user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if user_columns and 'email' not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")

        product_columns = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        if product_columns and 'base_unit_to_tablet_rate' not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN base_unit_to_tablet_rate INTEGER DEFAULT 0")
        if product_columns and 'package_tablet_total' not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN package_tablet_total INTEGER DEFAULT 0")
        if product_columns and 'status' not in product_columns:
            conn.execute("ALTER TABLE products ADD COLUMN status TEXT DEFAULT 'Active'")
            product_columns.add('status')

        # Normalize active status for cross-machine schema compatibility.
        if product_columns and 'status' in product_columns:
            if 'is_active' in product_columns:
                conn.execute("""
                    UPDATE products
                    SET status = CASE
                        WHEN COALESCE(is_active, 1) = 1 THEN 'Active'
                        ELSE 'Inactive'
                    END
                """)
            else:
                conn.execute("""
                    UPDATE products
                    SET status = 'Active'
                    WHERE status IS NULL OR TRIM(status) = ''
                """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_products_status ON products(status)")

        open_pkg_columns = {row[1] for row in conn.execute("PRAGMA table_info(open_packages)").fetchall()}
        if open_pkg_columns and 'extra_tablet_qty' not in open_pkg_columns:
            conn.execute("ALTER TABLE open_packages ADD COLUMN extra_tablet_qty INTEGER DEFAULT 0")

        transaction_log_columns = {row[1] for row in conn.execute("PRAGMA table_info(transaction_logs)").fetchall()}
        if transaction_log_columns and 'request_receive_mode' not in transaction_log_columns:
            conn.execute("ALTER TABLE transaction_logs ADD COLUMN request_receive_mode TEXT DEFAULT 'immediate'")
        if transaction_log_columns and 'requested_receive_at' not in transaction_log_columns:
            conn.execute("ALTER TABLE transaction_logs ADD COLUMN requested_receive_at TEXT")

        conn.execute('''
            CREATE TABLE IF NOT EXISTS ga_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                emp_id TEXT NOT NULL,
                requester_name TEXT NOT NULL,
                department TEXT,
                location TEXT,
                requester_email_snapshot TEXT,
                target_team TEXT NOT NULL,
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                image_path TEXT,
                status TEXT NOT NULL DEFAULT 'Pending',
                admin_note TEXT,
                handled_by TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS ga_request_attachments (
                request_id INTEGER PRIMARY KEY,
                mime_type TEXT NOT NULL,
                image_data BLOB NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(request_id) REFERENCES ga_requests(id) ON DELETE CASCADE
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ga_requests_status_location ON ga_requests(status, location, created_at DESC)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_ga_requests_emp_id ON ga_requests(emp_id, created_at DESC)')
        
        # 📬 Notification Settings Table
        conn.execute('''
            CREATE TABLE IF NOT EXISTS notification_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id TEXT NOT NULL UNIQUE,
                approval_email BOOLEAN DEFAULT 1,
                approval_line BOOLEAN DEFAULT 1,
                rejection_email BOOLEAN DEFAULT 1,
                rejection_line BOOLEAN DEFAULT 1,
                low_stock_email BOOLEAN DEFAULT 1,
                low_stock_line BOOLEAN DEFAULT 1,
                email_recipients TEXT,
                email_recipients_pc1 TEXT,
                email_recipients_cc TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        ensure_notification_settings_columns(conn)
        conn.execute('CREATE INDEX IF NOT EXISTS idx_notification_settings_admin_id ON notification_settings(admin_id)')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS email_test_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id TEXT NOT NULL,
                recipients TEXT,
                subject TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_email_test_logs_created_at ON email_test_logs(created_at DESC)')

        conn.execute('''
            CREATE TABLE IF NOT EXISTS notification_delivery_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id TEXT,
                notification_type TEXT,
                scope TEXT,
                channel TEXT,
                recipients TEXT,
                status TEXT,
                error_message TEXT,
                location TEXT,
                role TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_notification_delivery_logs_created_at ON notification_delivery_logs(created_at DESC)')
        conn.commit()
    finally:
        conn.close()

ensure_application_schema()

def start_write_transaction(conn):
    """ล็อกฐานข้อมูลสำหรับธุรกรรมเขียน เพื่อลด race condition จากหลาย request พร้อมกัน"""
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as e:
        if 'within a transaction' not in str(e).lower():
            raise

def transaction_timestamp_expr(alias='l'):
    prefix = f"{alias}." if alias else ""
    return f"""
        CASE
            WHEN {prefix}timestamp LIKE '__/__/____ __:__:__' THEN
                substr({prefix}timestamp, 7, 4) || '-' || substr({prefix}timestamp, 4, 2) || '-' || substr({prefix}timestamp, 1, 2) || ' ' || substr({prefix}timestamp, 12, 8)
            ELSE REPLACE(substr(COALESCE({prefix}timestamp, ''), 1, 19), 'T', ' ')
        END
    """

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
            response = requests.post(url, headers=headers, json=payload, timeout=15)
            if response.status_code >= 400:
                print(
                    f"LINE push failed ({target['group']}): "
                    f"HTTP {response.status_code} - {response.text[:300]}"
                )
        except Exception as e:
            print(f"Error sending LINE message ({target['group']}): {e}")

def parse_email_recipients(value):
    recipients = []
    for item in re.split(r'[;,，、\s]+', str(value or '').strip()):
        email = normalize_email_value(item)
        if email and is_valid_email_address(email):
            recipients.append(email)
    return list(dict.fromkeys(recipients))

def list_invalid_email_entries(value):
    invalid_entries = []
    for item in re.split(r'[;,，、\s]+', str(value or '').strip()):
        email = normalize_email_value(item)
        if email and not is_valid_email_address(email):
            invalid_entries.append(email)
    return invalid_entries

def resolve_notification_scope(location=None, role=None):
    role_text = str(role or '').strip().lower()
    normalized_location = normalize_location_value(location)

    if role_text == 'admin_pc1' or normalized_location == 'PC1':
        return 'pc1'
    if role_text == 'admin_cc' or is_cc_location_value(normalized_location):
        return 'cc'
    return 'general'

def resolve_notification_email_recipients(settings, location=None, role=None, recipients=None):
    recipient_list = []

    if recipients:
        if isinstance(recipients, (list, tuple, set)):
            for entry in recipients:
                recipient_list.extend(parse_email_recipients(entry))
        else:
            recipient_list.extend(parse_email_recipients(recipients))

    scope = resolve_notification_scope(location=location, role=role)
    scoped_field = 'email_recipients_pc1' if scope == 'pc1' else 'email_recipients_cc' if scope == 'cc' else ''
    scoped_recipients = parse_email_recipients(settings.get(scoped_field, '')) if scoped_field else []

    # Always include default recipients, then append scope-specific recipients.
    # This keeps runtime notifications consistent with test behavior expectations.
    recipient_list.extend(parse_email_recipients(settings.get('email_recipients', '')))
    recipient_list.extend(scoped_recipients)

    return list(dict.fromkeys([email for email in recipient_list if is_valid_email_address(email)]))

def get_settings_values(keys):
    if not keys:
        return {}

    conn = get_db_connection()
    try:
        placeholders = ','.join(['?'] * len(keys))
        rows = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            tuple(keys)
        ).fetchall()
        return {row['key']: row['value'] for row in rows}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()

def resolve_ga_request_recipients(target_team, location):
    team = clean_input_text(target_team, 10).upper()
    normalized_location = normalize_location_value(location)
    location_key = 'PC1' if normalized_location == 'PC1' else 'CC' if is_cc_location_value(normalized_location) else 'GENERAL'

    setting_keys = [
        f'ga_recipients_{team.lower()}_{location_key.lower()}',
        f'ga_recipients_{team.lower()}',
        'ga_recipients_default'
    ]
    settings_map = get_settings_values(setting_keys)

    env_keys = [
        f'GA_REQUEST_RECIPIENTS_{team}_{location_key}',
        f'GA_REQUEST_RECIPIENTS_{team}',
        'GA_REQUEST_RECIPIENTS_DEFAULT'
    ]

    recipients = []
    for key in setting_keys:
        recipients.extend(parse_email_recipients(settings_map.get(key, '')))
    for key in env_keys:
        recipients.extend(parse_email_recipients(os.environ.get(key, '')))
    return list(dict.fromkeys(recipients))

def send_email_message(subject, body, recipients, attachment_path=None, html_body=''):
    recipients = [email for email in recipients if is_valid_email_address(email)]
    if not recipients:
        return False, 'no-recipients'

    smtp_setting_keys = [
        'smtp_host', 'smtp_port', 'smtp_username', 'smtp_password', 'smtp_from', 'smtp_use_tls'
    ]
    smtp_settings = get_settings_values(smtp_setting_keys)

    smtp_host = (smtp_settings.get('smtp_host') or os.environ.get('SMTP_HOST', '')).strip()
    smtp_username = (smtp_settings.get('smtp_username') or os.environ.get('SMTP_USERNAME', '')).strip()
    smtp_password = smtp_settings.get('smtp_password') or os.environ.get('SMTP_PASSWORD', '')
    smtp_from = (smtp_settings.get('smtp_from') or os.environ.get('SMTP_FROM', smtp_username)).strip()
    smtp_port_raw = (smtp_settings.get('smtp_port') or os.environ.get('SMTP_PORT', '587')).strip()
    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        smtp_port = 587
    smtp_use_tls_raw = (smtp_settings.get('smtp_use_tls') or os.environ.get('SMTP_USE_TLS', '1')).strip().lower()
    smtp_use_tls = smtp_use_tls_raw not in ('0', 'false', 'no', 'off')

    if not smtp_host or not smtp_from:
        return False, 'mail-not-configured'

    message = EmailMessage()
    message['Subject'] = subject
    message['From'] = smtp_from
    message['To'] = ', '.join(recipients)
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype='html')

    if attachment_path and os.path.exists(attachment_path):
        mime_type, _ = mimetypes.guess_type(attachment_path)
        maintype, subtype = (mime_type or 'application/octet-stream').split('/', 1)
        with open(attachment_path, 'rb') as attachment_file:
            message.add_attachment(
                attachment_file.read(),
                maintype=maintype,
                subtype=subtype,
                filename=os.path.basename(attachment_path)
            )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
            if smtp_use_tls:
                smtp.starttls()
            if smtp_username:
                smtp.login(smtp_username, smtp_password)
            smtp.send_message(message)
        return True, ''
    except Exception as exc:
        print(f'Error sending email: {exc}')
        return False, str(exc)

def log_email_test_result(admin_id, recipients, subject, status, error_message=''):
    conn = get_db_connection()
    try:
        conn.execute(
            '''
            INSERT INTO email_test_logs (admin_id, recipients, subject, status, error_message)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (
                clean_input_text(admin_id, 100) or 'unknown',
                clean_input_text(', '.join(recipients), 500),
                clean_input_text(subject, 300),
                clean_input_text(status, 30),
                clean_input_text(error_message, 1000),
            )
        )
        conn.commit()
    finally:
        conn.close()

def log_notification_delivery(admin_id, notification_type, scope, channel, recipients, status, error_message='', location='', role=''):
    conn = get_db_connection()
    try:
        recipient_text = ', '.join([str(r).strip() for r in (recipients or []) if str(r).strip()])
        conn.execute(
            '''
            INSERT INTO notification_delivery_logs
            (admin_id, notification_type, scope, channel, recipients, status, error_message, location, role)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                clean_input_text(admin_id, 100),
                clean_input_text(notification_type, 50),
                clean_input_text(scope, 20),
                clean_input_text(channel, 20),
                clean_input_text(recipient_text, 800),
                clean_input_text(status, 30),
                clean_input_text(error_message, 1000),
                clean_input_text(location, 50),
                clean_input_text(role, 30),
            )
        )
        conn.commit()
    except Exception as e:
        print(f'Error logging notification delivery: {e}')
    finally:
        conn.close()

def get_email_base_url():
    settings_map = get_settings_values(['app_base_url'])
    base_url = (
        settings_map.get('app_base_url')
        or os.environ.get('APP_BASE_URL', '')
        or os.environ.get('BASE_URL', '')
    )
    base_url = str(base_url or '').strip()

    if not base_url and has_request_context():
        base_url = str(request.host_url or '').strip()

    if base_url and not base_url.endswith('/'):
        base_url += '/'
    return base_url

def build_email_link(endpoint_name, **values):
    try:
        relative_url = url_for(endpoint_name, **values)
    except Exception:
        return ''

    base_url = get_email_base_url()
    if not base_url:
        return ''

    return urljoin(base_url, relative_url.lstrip('/'))

def save_uploaded_image(file_storage):
    if not file_storage or not file_storage.filename:
        return ''

    filename = secure_filename(file_storage.filename)
    if '.' not in filename:
        raise ValueError('ไฟล์แนบต้องเป็นรูปภาพเท่านั้น')

    extension = filename.rsplit('.', 1)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError('รองรับไฟล์รูปภาพเฉพาะ png, jpg, jpeg, gif, webp')

    month_folder = datetime.now().strftime('%Y%m')

    # อ่านไฟล์เข้าหน่วยความจำก่อน เพื่อสามารถลองเขียนซ้ำหลายปลายทางได้
    try:
        file_storage.stream.seek(0)
    except Exception:
        pass
    binary = file_storage.read()
    if not binary:
        raise ValueError('ไฟล์รูปภาพว่างหรืออ่านข้อมูลไม่ได้')

    configured_root = GA_REQUEST_UPLOAD_DIR
    appdata_root = _default_ga_upload_root
    temp_root = os.path.join(tempfile.gettempdir(), 'PCM', 'ga_uploads')

    candidate_roots = [
        ('primary', configured_root),
        ('appdata', appdata_root),
        ('temp', temp_root),
    ]

    # ตัด path ซ้ำออก (เช่น primary == appdata)
    unique_candidates = []
    seen_roots = set()
    for key, root in candidate_roots:
        normalized_root = os.path.normcase(os.path.normpath(root))
        if normalized_root in seen_roots:
            continue
        seen_roots.add(normalized_root)
        unique_candidates.append((key, root))

    last_error = None
    for storage_key, root_dir in unique_candidates:
        target_dir = os.path.join(root_dir, month_folder)
        for _ in range(5):
            stored_filename = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}.{extension}"
            absolute_path = os.path.join(target_dir, stored_filename)
            try:
                os.makedirs(target_dir, exist_ok=True)
                with open(absolute_path, 'wb') as out_file:
                    out_file.write(binary)
                return f"{storage_key}:{month_folder}/{stored_filename}"
            except (PermissionError, OSError) as exc:
                last_error = exc
                print(f"[GA_UPLOAD] write failed ({storage_key}) {absolute_path}: {exc}")
                time.sleep(0.15)

    if isinstance(last_error, PermissionError):
        raise ValueError('ไม่สามารถบันทึกไฟล์ได้ กรุณาลองใหม่อีกครั้ง (Permission denied)')
    if last_error:
        raise ValueError(f'ไม่สามารถบันทึกไฟล์ได้: {last_error}')
    raise ValueError('ไม่สามารถบันทึกไฟล์ได้ กรุณาลองใหม่อีกครั้ง')

def _ga_storage_roots():
    return {
        'primary': GA_REQUEST_UPLOAD_DIR,
        'appdata': _default_ga_upload_root,
        'temp': os.path.join(tempfile.gettempdir(), 'PCM', 'ga_uploads')
    }

def extract_uploaded_image_blob(file_storage):
    if not file_storage or not file_storage.filename:
        return None

    filename = secure_filename(file_storage.filename)
    if '.' not in filename:
        raise ValueError('ไฟล์แนบต้องเป็นรูปภาพเท่านั้น')

    extension = filename.rsplit('.', 1)[1].lower()
    if extension not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError('รองรับไฟล์รูปภาพเฉพาะ png, jpg, jpeg, gif, webp')

    mime_map = {
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'png': 'image/png',
        'gif': 'image/gif',
        'webp': 'image/webp'
    }

    try:
        file_storage.stream.seek(0)
    except Exception:
        pass
    binary = file_storage.read()
    if not binary:
        raise ValueError('ไฟล์รูปภาพว่างหรืออ่านข้อมูลไม่ได้')

    return {
        'mime_type': mime_map.get(extension, 'application/octet-stream'),
        'image_data': binary
    }

def _resolve_ga_storage_and_relative_path(image_path):
    image_path = (image_path or '').strip().replace('\\', '/')
    if not image_path:
        return None, None

    if image_path.startswith('db:'):
        db_id = image_path[3:].strip()
        if db_id.isdigit():
            return 'db', db_id
        return None, None

    if image_path.startswith('uploads/ga_requests/'):
        return 'static', image_path

    storage_key = 'primary'
    relative = image_path
    if ':' in image_path and not image_path.startswith('C:/') and not image_path.startswith('D:/'):
        maybe_key, maybe_relative = image_path.split(':', 1)
        if maybe_key in _ga_storage_roots():
            storage_key = maybe_key
            relative = maybe_relative

    relative = os.path.normpath(relative).replace('\\', '/')
    if not relative or relative.startswith('..'):
        return None, None

    return storage_key, relative

def resolve_ga_attachment_absolute_path(image_path):
    storage_key, relative = _resolve_ga_storage_and_relative_path(image_path)
    if not storage_key:
        return None

    if storage_key == 'static':
        legacy_path = os.path.join(BASE_DIR, 'static', relative)
        return legacy_path if os.path.exists(legacy_path) else None

    roots = _ga_storage_roots()
    base_root = roots.get(storage_key)
    if not base_root:
        return None

    absolute = os.path.join(base_root, relative)
    return absolute if os.path.exists(absolute) else None

def ga_image_url(image_path):
    storage_key, relative = _resolve_ga_storage_and_relative_path(image_path)
    if not storage_key:
        return ''
    if storage_key == 'db':
        return url_for('ga_request_image_db', request_id=int(relative))
    if storage_key == 'static':
        return url_for('static', filename=relative)
    return url_for('ga_request_image', storage=storage_key, image_rel_path=relative)

app.jinja_env.globals['ga_image_url'] = ga_image_url

@app.errorhandler(RequestEntityTooLarge)
def handle_request_entity_too_large(_error):
    emp_id = (request.args.get('emp_id') or request.form.get('emp_id') or '').strip()
    max_mb = int(app.config.get('MAX_CONTENT_LENGTH', 0) / (1024 * 1024)) if app.config.get('MAX_CONTENT_LENGTH') else 0
    if max_mb > 0:
        flash(f'❌ ไฟล์ใหญ่เกินกำหนด (สูงสุด {max_mb} MB)', 'danger')
    else:
        flash('❌ ไฟล์ใหญ่เกินกำหนด', 'danger')

    if request.path.startswith('/ga-request') and emp_id:
        return redirect(url_for('ga_request_portal', emp_id=emp_id))
    return redirect(url_for('index'))

@app.route('/ga-request-image/<storage>/<path:image_rel_path>')
def ga_request_image(storage, image_rel_path):
    roots = _ga_storage_roots()
    if storage not in roots:
        abort(404)

    normalized = os.path.normpath((image_rel_path or '').strip()).replace('\\', '/')
    if not normalized or normalized.startswith('..'):
        abort(404)

    absolute = os.path.join(roots[storage], normalized)
    if not os.path.isfile(absolute):
        abort(404)
    return send_file(absolute)

@app.route('/ga-request-image/db/<int:request_id>')
def ga_request_image_db(request_id):
    conn = get_db_connection()
    row = conn.execute(
        'SELECT mime_type, image_data FROM ga_request_attachments WHERE request_id = ?',
        (request_id,)
    ).fetchone()
    conn.close()

    if not row or not row['image_data']:
        abort(404)

    return send_file(
        BytesIO(row['image_data']),
        mimetype=row['mime_type'] or 'application/octet-stream'
    )

def build_ga_request_email_body(request_row):
    description = str(request_row.get('description') or '').strip()
    lines = [
        'มี GA Request ใหม่จากระบบ PCM',
        '',
        f"เลขที่คำร้อง: GA-{int(request_row['id']):05d}",
        f"ผู้แจ้ง: {request_row['requester_name']}",
        f"รหัสพนักงาน: {request_row['emp_id']}",
        f"แผนก: {request_row['department'] or '-'}",
        f"Location: {request_row['location'] or '-'}",
        f"ส่วนงานที่รับผิดชอบ: {request_row['target_team']}",
        f"หัวข้อ: {request_row['title']}",
        '',
        'รายละเอียดปัญหา:',
        description or '-',
    ]
    return '\n'.join(lines)

def build_ga_request_email_html(request_row):
    request_no = f"GA-{int(request_row['id']):05d}"
    requester_name = escape(str(request_row.get('requester_name') or '-'))
    emp_id = escape(str(request_row.get('emp_id') or '-'))
    department = escape(str(request_row.get('department') or '-'))
    location = escape(str(request_row.get('location') or '-'))
    target_team = escape(str(request_row.get('target_team') or '-'))
    title = escape(str(request_row.get('title') or '-'))
    description = escape(str(request_row.get('description') or '').strip() or '-')
    description_html = description.replace('\n', '<br>')

    admin_link = build_email_link('admin_dashboard', module='ga', ga_loc=str(request_row.get('location') or ''))
    action_section = ''
    if admin_link:
        action_section = (
            '<tr>'
            '<td style="padding:4px 22px 18px 22px;">'
            f'<a href="{escape(admin_link)}" style="display:inline-block;background:#0b6cc7;color:#ffffff;text-decoration:none;padding:11px 16px;border-radius:9px;font-size:13px;font-weight:700;">เปิดหน้าจัดการ GA Request</a>'
            '</td>'
            '</tr>'
        )

    return f"""<!doctype html>
<html lang=\"th\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>GA Request ใหม่</title>
</head>
<body style=\"margin:0;padding:0;background:#f4f6fb;font-family:'Segoe UI',Tahoma,sans-serif;color:#243040;\">
    <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#f4f6fb;padding:20px 10px;\">
        <tr>
            <td align=\"center\">
                <table role=\"presentation\" width=\"640\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width:640px;width:100%;background:#ffffff;border:1px solid #dde3ef;border-radius:14px;overflow:hidden;\">
                    <tr>
                        <td style=\"padding:18px 22px;background:linear-gradient(135deg,#0b6cc7,#0e91d8);color:#ffffff;\">
                            <div style=\"font-size:13px;opacity:.92;\">PCM Notification</div>
                            <div style=\"margin-top:4px;font-size:22px;font-weight:700;\">มี GA Request ใหม่</div>
                            <div style=\"margin-top:8px;display:inline-block;background:#ffffff;color:#0b6cc7;border-radius:999px;padding:6px 12px;font-size:13px;font-weight:700;\">{escape(request_no)}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style=\"padding:20px 22px 12px 22px;\">
                            <div style=\"font-size:16px;font-weight:700;color:#0f1f3a;\">{title}</div>
                            <div style=\"margin-top:6px;color:#52607a;font-size:13px;\">กรุณาตรวจสอบและดำเนินการตามส่วนงานที่รับผิดชอบ</div>
                        </td>
                    </tr>
                    <tr>
                        <td style=\"padding:0 22px 6px 22px;\">
                            <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;\">
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;width:34%;\">ผู้แจ้ง</td>
                                    <td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{requester_name}</td>
                                </tr>
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">รหัสพนักงาน</td>
                                    <td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{emp_id}</td>
                                </tr>
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">แผนก</td>
                                    <td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{department}</td>
                                </tr>
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">Location</td>
                                    <td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{location}</td>
                                </tr>
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;\">ส่วนงานที่รับผิดชอบ</td>
                                    <td style=\"padding:9px 0;color:#1d2a44;font-size:13px;\">{target_team}</td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style=\"padding:12px 22px 18px 22px;\">
                            <div style=\"font-size:13px;color:#6a768f;margin-bottom:7px;\">รายละเอียดปัญหา</div>
                            <div style=\"padding:12px 13px;background:#f7f9fd;border:1px solid #e5ebf5;border-radius:10px;color:#1d2a44;font-size:13px;line-height:1.6;\">{description_html}</div>
                        </td>
                    </tr>
                    {action_section}
                    <tr>
                        <td style=\"padding:12px 22px 22px 22px;background:#fafcff;color:#7a879e;font-size:12px;border-top:1px solid #edf1f7;\">
                            อีเมลนี้ถูกส่งอัตโนมัติจากระบบ PCM
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""

def build_ga_status_email_body(request_row, new_status, admin_note=''):
    lines = [
        'สถานะคำร้องของคุณถูกอัปเดตแล้ว',
        '',
        f"เลขที่คำร้อง: GA-{int(request_row['id']):05d}",
        f"หัวข้อ: {request_row['title']}",
        f"สถานะใหม่: {new_status}",
        f"ผู้ดำเนินการ: {request_row.get('handled_by') or '-'}",
    ]
    if admin_note:
        lines.extend(['', 'หมายเหตุจากผู้ดูแล:', admin_note])
    return '\n'.join(lines)

def build_ga_status_email_html(request_row, new_status, admin_note=''):
    request_no = f"GA-{int(request_row['id']):05d}"
    title = escape(str(request_row.get('title') or '-'))
    handled_by = escape(str(request_row.get('handled_by') or '-'))
    safe_status = escape(str(new_status or '-'))
    safe_note = escape(str(admin_note or '').strip())
    note_html = safe_note.replace('\n', '<br>')

    requester_link = ''
    if request_row.get('emp_id'):
        requester_link = build_email_link('ga_request_portal', emp_id=str(request_row.get('emp_id')))

    status_style = {
        'Pending': 'background:#fff6da;color:#8a6400;border:1px solid #f1dd98;',
        'In Progress': 'background:#e9f3ff;color:#0b5cab;border:1px solid #b7d4fa;',
        'Resolved': 'background:#e8f9f0;color:#197a45;border:1px solid #b4e6c8;',
        'Done': 'background:#e8f9f0;color:#197a45;border:1px solid #b4e6c8;',
        'Rejected': 'background:#ffecef;color:#a22c37;border:1px solid #f3b6c0;',
    }.get(new_status, 'background:#eef2fb;color:#405375;border:1px solid #d4deef;')

    note_section = ''
    if safe_note:
        note_section = (
            '<tr>'
            '<td style="padding:0 22px 20px 22px;">'
            '<div style="font-size:13px;color:#6a768f;margin-bottom:7px;">หมายเหตุจากผู้ดูแล</div>'
            f'<div style="padding:12px 13px;background:#f7f9fd;border:1px solid #e5ebf5;border-radius:10px;color:#1d2a44;font-size:13px;line-height:1.6;">{note_html}</div>'
            '</td>'
            '</tr>'
        )

    action_section = ''
    if requester_link:
        action_section = (
            '<tr>'
            '<td style="padding:4px 22px 18px 22px;">'
            f'<a href="{escape(requester_link)}" style="display:inline-block;background:#124f96;color:#ffffff;text-decoration:none;padding:11px 16px;border-radius:9px;font-size:13px;font-weight:700;">เปิดหน้าคำร้องของฉัน</a>'
            '</td>'
            '</tr>'
        )

    return f"""<!doctype html>
<html lang=\"th\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>อัปเดตสถานะ GA Request</title>
</head>
<body style=\"margin:0;padding:0;background:#f4f6fb;font-family:'Segoe UI',Tahoma,sans-serif;color:#243040;\">
    <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#f4f6fb;padding:20px 10px;\">
        <tr>
            <td align=\"center\">
                <table role=\"presentation\" width=\"640\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width:640px;width:100%;background:#ffffff;border:1px solid #dde3ef;border-radius:14px;overflow:hidden;\">
                    <tr>
                        <td style=\"padding:18px 22px;background:linear-gradient(135deg,#124f96,#1669bf);color:#ffffff;\">
                            <div style=\"font-size:13px;opacity:.92;\">PCM Notification</div>
                            <div style=\"margin-top:4px;font-size:22px;font-weight:700;\">อัปเดตสถานะ GA Request</div>
                            <div style=\"margin-top:8px;display:inline-block;background:#ffffff;color:#124f96;border-radius:999px;padding:6px 12px;font-size:13px;font-weight:700;\">{escape(request_no)}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style=\"padding:20px 22px 10px 22px;\">
                            <div style=\"font-size:16px;font-weight:700;color:#0f1f3a;\">{title}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style=\"padding:0 22px 6px 22px;\">
                            <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;\">
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;width:34%;\">สถานะใหม่</td>
                                    <td style=\"padding:9px 0;border-bottom:1px solid #edf1f7;\"><span style=\"display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;font-weight:700;{status_style}\">{safe_status}</span></td>
                                </tr>
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;\">ผู้ดำเนินการ</td>
                                    <td style=\"padding:9px 0;color:#1d2a44;font-size:13px;\">{handled_by}</td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    {note_section}
                    {action_section}
                    <tr>
                        <td style=\"padding:12px 22px 22px 22px;background:#fafcff;color:#7a879e;font-size:12px;border-top:1px solid #edf1f7;\">
                            อีเมลนี้ถูกส่งอัตโนมัติจากระบบ PCM
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""

def build_withdrawal_email_body(payload):
    requester_name = str(payload.get('requester_name') or '-')
    emp_id = str(payload.get('emp_id') or '-')
    department = str(payload.get('department') or '-')
    location = str(payload.get('location') or '-')
    symptom = str(payload.get('symptom') or '').strip()
    receive_plan = str(payload.get('receive_plan') or '-')
    created_at = str(payload.get('created_at') or '-')
    items = payload.get('items') or []

    lines = [
        'มีคำขอเบิกใหม่จากระบบ PCM',
        '',
        f"ผู้เบิก: {requester_name}",
        f"รหัสพนักงาน: {emp_id}",
        f"แผนก: {department}",
        f"Location: {location}",
        f"เวลารายการ: {created_at}",
        f"แผนการรับของ: {receive_plan}",
    ]

    if symptom:
        lines.append(f"อาการ: {symptom}")

    lines.extend(['', 'รายการที่ขอเบิก:'])
    for idx, item in enumerate(items, start=1):
        item_name = str(item.get('name') or '-')
        qty = item.get('qty')
        unit = str(item.get('unit') or '')
        note = str(item.get('note') or '').strip()
        lines.append(f"{idx}. {item_name} - {qty} {unit}".strip())
        if note:
            lines.append(f"   หมายเหตุ: {note}")

    return '\n'.join(lines)

def build_withdrawal_email_html(payload):
    requester_name = escape(str(payload.get('requester_name') or '-'))
    emp_id = escape(str(payload.get('emp_id') or '-'))
    department = escape(str(payload.get('department') or '-'))
    location = escape(str(payload.get('location') or '-'))
    symptom = escape(str(payload.get('symptom') or '').strip())
    receive_plan = escape(str(payload.get('receive_plan') or '-'))
    created_at = escape(str(payload.get('created_at') or '-'))
    items = payload.get('items') or []

    items_rows = ''
    for idx, item in enumerate(items, start=1):
        item_name = escape(str(item.get('name') or '-'))
        qty = escape(str(item.get('qty') or '0'))
        unit = escape(str(item.get('unit') or ''))
        note = escape(str(item.get('note') or '').strip())
        note_html = f"<div style=\"font-size:12px;color:#6a768f;margin-top:4px;\">{note}</div>" if note else ''
        items_rows += (
            '<tr>'
            f'<td style="padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;width:8%;">{idx}</td>'
            f'<td style="padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;">{item_name}{note_html}</td>'
            f'<td style="padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;text-align:right;white-space:nowrap;">{qty} {unit}</td>'
            '</tr>'
        )

    symptom_section = ''
    if symptom:
        symptom_section = (
            '<tr>'
            '<td style="padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;">อาการ</td>'
            f'<td style="padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;" colspan="2">{symptom}</td>'
            '</tr>'
        )

    action_link = build_email_link('admin_dashboard', module='stock')
    action_section = ''
    if action_link:
        action_section = (
            '<tr>'
            '<td style="padding:4px 22px 18px 22px;">'
            f'<a href="{escape(action_link)}" style="display:inline-block;background:#0b6cc7;color:#ffffff;text-decoration:none;padding:11px 16px;border-radius:9px;font-size:13px;font-weight:700;">เปิดหน้าจัดการคำขอเบิก</a>'
            '</td>'
            '</tr>'
        )

    return f"""<!doctype html>
<html lang=\"th\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>คำขอเบิกใหม่</title>
</head>
<body style=\"margin:0;padding:0;background:#f4f6fb;font-family:'Segoe UI',Tahoma,sans-serif;color:#243040;\">
    <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#f4f6fb;padding:20px 10px;\">
        <tr>
            <td align=\"center\">
                <table role=\"presentation\" width=\"640\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width:640px;width:100%;background:#ffffff;border:1px solid #dde3ef;border-radius:14px;overflow:hidden;\">
                    <tr>
                        <td style=\"padding:18px 22px;background:linear-gradient(135deg,#0b6cc7,#0e91d8);color:#ffffff;\">
                            <div style=\"font-size:13px;opacity:.92;\">PCM Notification</div>
                            <div style=\"margin-top:4px;font-size:22px;font-weight:700;\">มีคำขอเบิกใหม่</div>
                            <div style=\"margin-top:8px;font-size:13px;opacity:.95;\">เวลารายการ: {created_at}</div>
                        </td>
                    </tr>
                    <tr>
                        <td style=\"padding:20px 22px 10px 22px;\">
                            <div style=\"font-size:16px;font-weight:700;color:#0f1f3a;\">ผู้เบิก: {requester_name}</div>
                            <div style=\"margin-top:6px;color:#52607a;font-size:13px;\">กรุณาตรวจสอบคำขอและอนุมัติในระบบ</div>
                        </td>
                    </tr>
                    <tr>
                        <td style=\"padding:0 22px 6px 22px;\">
                            <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;\">
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;width:34%;\">รหัสพนักงาน</td>
                                    <td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\" colspan=\"2\">{emp_id}</td>
                                </tr>
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">แผนก</td>
                                    <td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\" colspan=\"2\">{department}</td>
                                </tr>
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">Location</td>
                                    <td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\" colspan=\"2\">{location}</td>
                                </tr>
                                <tr>
                                    <td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">การรับของ</td>
                                    <td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\" colspan=\"2\">{receive_plan}</td>
                                </tr>
                                {symptom_section}
                            </table>
                        </td>
                    </tr>
                    <tr>
                        <td style=\"padding:12px 22px 4px 22px;\">
                            <div style=\"font-size:13px;color:#6a768f;margin-bottom:8px;\">รายการที่ขอเบิก</div>
                            <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;\">
                                <tr>
                                    <td style=\"padding:8px 0;color:#6a768f;font-size:12px;font-weight:700;border-bottom:1px solid #dfe7f3;width:8%;\">#</td>
                                    <td style=\"padding:8px 0;color:#6a768f;font-size:12px;font-weight:700;border-bottom:1px solid #dfe7f3;\">รายการ</td>
                                    <td style=\"padding:8px 0;color:#6a768f;font-size:12px;font-weight:700;border-bottom:1px solid #dfe7f3;text-align:right;\">จำนวน</td>
                                </tr>
                                {items_rows or '<tr><td colspan="3" style="padding:10px 0;color:#6a768f;font-size:13px;">-</td></tr>'}
                            </table>
                        </td>
                    </tr>
                    {action_section}
                    <tr>
                        <td style=\"padding:12px 22px 22px 22px;background:#fafcff;color:#7a879e;font-size:12px;border-top:1px solid #edf1f7;\">
                            อีเมลนี้ถูกส่งอัตโนมัติจากระบบ PCM
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>
"""

def build_approval_email_body(payload):
    return "\n".join([
        'มีการอนุมัติรายการเบิกจากระบบ PCM',
        '',
        f"ผู้เบิก: {payload.get('requester_name', '-')}",
        f"แผนก/พื้นที่: {payload.get('department', '-')} ({payload.get('location', '-')})",
        f"รายการ: {payload.get('product_name', '-')}",
        f"จำนวน: {payload.get('qty', '-')} {payload.get('unit', '')}".strip(),
        f"ผู้อนุมัติ: {payload.get('approver', '-')}",
        f"เวลาอนุมัติ: {payload.get('approved_at', '-')}",
    ])

def build_approval_email_html(payload):
    requester_name = escape(str(payload.get('requester_name') or '-'))
    department = escape(str(payload.get('department') or '-'))
    location = escape(str(payload.get('location') or '-'))
    product_name = escape(str(payload.get('product_name') or '-'))
    qty = escape(str(payload.get('qty') or '-'))
    unit = escape(str(payload.get('unit') or ''))
    approver = escape(str(payload.get('approver') or '-'))
    approved_at = escape(str(payload.get('approved_at') or '-'))
    action_link = build_email_link('admin_dashboard', module='stock')
    action_section = ''
    if action_link:
        action_section = (
            '<tr>'
            '<td style="padding:4px 22px 18px 22px;">'
            f'<a href="{escape(action_link)}" style="display:inline-block;background:#0b6cc7;color:#ffffff;text-decoration:none;padding:11px 16px;border-radius:9px;font-size:13px;font-weight:700;">เปิดหน้ารายการอนุมัติ</a>'
            '</td>'
            '</tr>'
        )

    return f"""<!doctype html>
<html lang=\"th\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>แจ้งเตือนอนุมัติรายการ</title>
</head>
<body style=\"margin:0;padding:0;background:#f4f6fb;font-family:'Segoe UI',Tahoma,sans-serif;color:#243040;\">
    <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#f4f6fb;padding:20px 10px;\">
        <tr><td align=\"center\">
            <table role=\"presentation\" width=\"640\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width:640px;width:100%;background:#ffffff;border:1px solid #dde3ef;border-radius:14px;overflow:hidden;\">
                <tr>
                    <td style=\"padding:18px 22px;background:linear-gradient(135deg,#0b6cc7,#0e91d8);color:#ffffff;\">
                        <div style=\"font-size:13px;opacity:.92;\">PCM Notification</div>
                        <div style=\"margin-top:4px;font-size:22px;font-weight:700;\">อนุมัติรายการสำเร็จ</div>
                        <div style=\"margin-top:8px;font-size:13px;opacity:.95;\">เวลาอนุมัติ: {approved_at}</div>
                    </td>
                </tr>
                <tr><td style=\"padding:20px 22px 10px 22px;\"><div style=\"font-size:16px;font-weight:700;color:#0f1f3a;\">ผู้เบิก: {requester_name}</div></td></tr>
                <tr>
                    <td style=\"padding:0 22px 12px 22px;\">
                        <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;\">
                            <tr><td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;width:36%;\">แผนก/พื้นที่</td><td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{department} ({location})</td></tr>
                            <tr><td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">รายการ</td><td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{product_name}</td></tr>
                            <tr><td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">จำนวน</td><td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{qty} {unit}</td></tr>
                            <tr><td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">ผู้อนุมัติ</td><td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{approver}</td></tr>
                        </table>
                    </td>
                </tr>
                {action_section}
                <tr><td style=\"padding:12px 22px 22px 22px;background:#fafcff;color:#7a879e;font-size:12px;border-top:1px solid #edf1f7;\">อีเมลนี้ถูกส่งอัตโนมัติจากระบบ PCM</td></tr>
            </table>
        </td></tr>
    </table>
</body>
</html>
"""

def build_low_stock_email_body(payload):
    return "\n".join([
        'แจ้งเตือนสต็อกต่ำกว่าเกณฑ์',
        '',
        f"รายการ: {payload.get('product_name', '-')}",
        f"คงเหลือปัจจุบัน: {payload.get('stock', '-')} {payload.get('unit', '')}".strip(),
        f"จุดสั่งซื้อ (Safety): {payload.get('safety_stock', '-')} {payload.get('unit', '')}".strip(),
        f"พื้นที่: {payload.get('location', '-')}",
        'กรุณาพิจารณาสั่งซื้อเพิ่ม',
    ])

def build_low_stock_email_html(payload):
    product_name = escape(str(payload.get('product_name') or '-'))
    stock = escape(str(payload.get('stock') or '-'))
    safety_stock = escape(str(payload.get('safety_stock') or '-'))
    unit = escape(str(payload.get('unit') or ''))
    location = escape(str(payload.get('location') or '-'))
    action_link = build_email_link('admin_dashboard', module='stock')
    action_section = ''
    if action_link:
        action_section = (
            '<tr>'
            '<td style="padding:4px 22px 18px 22px;">'
            f'<a href="{escape(action_link)}" style="display:inline-block;background:#0b6cc7;color:#ffffff;text-decoration:none;padding:11px 16px;border-radius:9px;font-size:13px;font-weight:700;">เปิดหน้าสต็อกต่ำกว่าเกณฑ์</a>'
            '</td>'
            '</tr>'
        )

    return f"""<!doctype html>
<html lang=\"th\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>แจ้งเตือนสต็อกต่ำกว่าเกณฑ์</title>
</head>
<body style=\"margin:0;padding:0;background:#f4f6fb;font-family:'Segoe UI',Tahoma,sans-serif;color:#243040;\">
    <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#f4f6fb;padding:20px 10px;\">
        <tr><td align=\"center\">
            <table role=\"presentation\" width=\"640\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width:640px;width:100%;background:#ffffff;border:1px solid #dde3ef;border-radius:14px;overflow:hidden;\">
                <tr>
                    <td style=\"padding:18px 22px;background:linear-gradient(135deg,#bc6b07,#e79717);color:#ffffff;\">
                        <div style=\"font-size:13px;opacity:.92;\">PCM Notification</div>
                        <div style=\"margin-top:4px;font-size:22px;font-weight:700;\">แจ้งเตือนสต็อกต่ำกว่าเกณฑ์</div>
                    </td>
                </tr>
                <tr><td style=\"padding:20px 22px 10px 22px;\"><div style=\"font-size:16px;font-weight:700;color:#0f1f3a;\">{product_name}</div></td></tr>
                <tr>
                    <td style=\"padding:0 22px 12px 22px;\">
                        <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"border-collapse:collapse;\">
                            <tr><td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;width:42%;\">คงเหลือปัจจุบัน</td><td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{stock} {unit}</td></tr>
                            <tr><td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">จุดสั่งซื้อ (Safety)</td><td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{safety_stock} {unit}</td></tr>
                            <tr><td style=\"padding:9px 0;color:#6a768f;font-size:13px;border-bottom:1px solid #edf1f7;\">พื้นที่</td><td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{location}</td></tr>
                        </table>
                    </td>
                </tr>
                {action_section}
                <tr><td style=\"padding:12px 22px 22px 22px;background:#fafcff;color:#7a879e;font-size:12px;border-top:1px solid #edf1f7;\">อีเมลนี้ถูกส่งอัตโนมัติจากระบบ PCM</td></tr>
            </table>
        </td></tr>
    </table>
</body>
</html>
"""

def build_periodic_alert_email_body(payload):
    location_label = str(payload.get('location_label') or '-')
    expiring_items = payload.get('expiring_items') or []
    helmet_alerts = payload.get('helmet_alerts') or []

    lines = [f"แจ้งเตือนประจำวัน [{location_label}]", '']
    if expiring_items:
        lines.append('รายการใกล้หมดอายุ:')
        for item in expiring_items:
            lines.append(f"- {item.get('name', '-')} ({item.get('category', '-')}) หมดอายุ {item.get('show_date', '-')}")
    if helmet_alerts:
        if expiring_items:
            lines.append('')
        lines.append('ครบกำหนดเปลี่ยนหมวกเซฟตี้:')
        for alert in helmet_alerts:
            lines.append(
                f"- {alert.get('emp_name', '-')} ({alert.get('department', '-')}) | "
                f"{alert.get('product_name', '-')} | เบิกล่าสุด {alert.get('show_date', '-')}"
            )
    if not expiring_items and not helmet_alerts:
        lines.append('ไม่มีรายการแจ้งเตือนในวันนี้')
    return '\n'.join(lines)

def build_periodic_alert_email_html(payload):
    location_label = escape(str(payload.get('location_label') or '-'))
    expiring_items = payload.get('expiring_items') or []
    helmet_alerts = payload.get('helmet_alerts') or []
    scheduled_withdrawals = payload.get('scheduled_withdrawals') or []
    generated_at = escape(str(payload.get('generated_at') or '-'))

    expiry_rows = ''
    for item in expiring_items:
        expiry_rows += (
            '<tr>'
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{escape(str(item.get('name') or '-'))}</td>"
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{escape(str(item.get('category') or '-'))}</td>"
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;white-space:nowrap;\">{escape(str(item.get('show_date') or '-'))}</td>"
            '</tr>'
        )

    helmet_rows = ''
    for alert in helmet_alerts:
        helmet_rows += (
            '<tr>'
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{escape(str(alert.get('emp_name') or '-'))}</td>"
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{escape(str(alert.get('department') or '-'))}</td>"
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{escape(str(alert.get('product_name') or '-'))}</td>"
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;white-space:nowrap;\">{escape(str(alert.get('show_date') or '-'))}</td>"
            '</tr>'
        )

    expiry_section = ''
    if expiry_rows:
        expiry_section = (
            '<tr><td style="padding:6px 22px 0 22px;font-size:14px;font-weight:700;color:#0f1f3a;">รายการใกล้หมดอายุ</td></tr>'
            '<tr><td style="padding:4px 22px 8px 22px;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">'
            '<tr>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">สินค้า</th>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">หมวด</th>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">วันหมดอายุ</th>'
            '</tr>'
            f'{expiry_rows}'
            '</table>'
            '</td></tr>'
        )

    helmet_section = ''
    if helmet_rows:
        helmet_section = (
            '<tr><td style="padding:6px 22px 0 22px;font-size:14px;font-weight:700;color:#0f1f3a;">ครบกำหนดเปลี่ยนหมวกเซฟตี้</td></tr>'
            '<tr><td style="padding:4px 22px 8px 22px;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">'
            '<tr>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">พนักงาน</th>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">แผนก</th>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">รายการ</th>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">เบิกล่าสุด</th>'
            '</tr>'
            f'{helmet_rows}'
            '</table>'
            '</td></tr>'
        )

    scheduled_rows = ''
    for sw in scheduled_withdrawals:
        qty_text = f"{sw.get('qty', '')} {sw.get('unit', '')}".strip()
        note_text = escape(str(sw.get('note') or ''))
        scheduled_rows += (
            '<tr>'
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{escape(str(sw.get('emp_name') or '-'))}</td>"
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{escape(str(sw.get('department') or '-'))}</td>"
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{escape(str(sw.get('product_name') or '-'))}</td>"
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{escape(qty_text)}</td>"
            f"<td style=\"padding:9px 0;color:#e25900;font-size:13px;border-bottom:1px solid #edf1f7;white-space:nowrap;font-weight:600;\">{escape(str(sw.get('show_dt') or '-'))}</td>"
            f"<td style=\"padding:9px 0;color:#1d2a44;font-size:13px;border-bottom:1px solid #edf1f7;\">{note_text}</td>"
            '</tr>'
        )

    scheduled_section = ''
    if scheduled_rows:
        scheduled_section = (
            '<tr><td style="padding:10px 22px 0 22px;">'
            '<div style="background:#fff7ed;border-left:4px solid #e25900;border-radius:0 8px 8px 0;padding:8px 12px;margin-bottom:4px;">'
            '<span style="font-size:14px;font-weight:700;color:#9a3300;">&#9200; การเบิกล่วงหน้าที่ครบกำหนดภายใน 24 ชั่วโมง</span>'
            '</div>'
            '</td></tr>'
            '<tr><td style="padding:4px 22px 8px 22px;">'
            '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;">'
            '<tr>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">พนักงาน</th>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">แผนก</th>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">รายการ</th>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">จำนวน</th>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">กำหนดรับ</th>'
            '<th align="left" style="padding:8px 0;color:#6a768f;font-size:12px;border-bottom:1px solid #edf1f7;">หมายเหตุ</th>'
            '</tr>'
            f'{scheduled_rows}'
            '</table>'
            '</td></tr>'
        )

    no_alert_section = ''
    if not expiry_rows and not helmet_rows and not scheduled_rows:
        no_alert_section = (
            '<tr><td style="padding:14px 22px 10px 22px;">'
            '<div style="background:#f8fbff;border:1px dashed #c6d7ee;border-radius:10px;padding:12px 14px;color:#304a68;font-size:13px;">'
            'วันนี้ไม่มีรายการแจ้งเตือนของใกล้หมดอายุหรือหมวกเซฟตี้'
            '</div>'
            '</td></tr>'
        )

    action_link = build_email_link('admin_dashboard', module='stock')
    action_section = ''
    if action_link:
        action_section = (
            '<tr>'
            '<td style="padding:4px 22px 18px 22px;">'
            f'<a href="{escape(action_link)}" style="display:inline-block;background:#0b6cc7;color:#ffffff;text-decoration:none;padding:11px 16px;border-radius:9px;font-size:13px;font-weight:700;">เปิดหน้าแจ้งเตือนสต็อก</a>'
            '</td>'
            '</tr>'
        )

    return f"""<!doctype html>
<html lang=\"th\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>แจ้งเตือนประจำวัน</title>
</head>
<body style=\"margin:0;padding:0;background:#f4f6fb;font-family:'Segoe UI',Tahoma,sans-serif;color:#243040;\">
    <table role=\"presentation\" width=\"100%\" cellspacing=\"0\" cellpadding=\"0\" style=\"background:#f4f6fb;padding:20px 10px;\">
        <tr><td align=\"center\">
            <table role=\"presentation\" width=\"720\" cellspacing=\"0\" cellpadding=\"0\" style=\"max-width:720px;width:100%;background:#ffffff;border:1px solid #dde3ef;border-radius:14px;overflow:hidden;\">
                <tr>
                    <td style=\"padding:18px 22px;background:linear-gradient(135deg,#0b6cc7,#0e91d8);color:#ffffff;\">
                        <div style=\"font-size:13px;opacity:.92;\">PCM Notification</div>
                        <div style=\"margin-top:4px;font-size:22px;font-weight:700;\">แจ้งเตือนประจำวัน [{location_label}]</div>
                        <div style=\"margin-top:8px;font-size:13px;opacity:.95;\">เวลาสร้าง: {generated_at}</div>
                    </td>
                </tr>
                {expiry_section}
                {helmet_section}
                {scheduled_section}
                {no_alert_section}
                {action_section}
                <tr><td style=\"padding:12px 22px 22px 22px;background:#fafcff;color:#7a879e;font-size:12px;border-top:1px solid #edf1f7;\">อีเมลนี้ถูกส่งอัตโนมัติจากระบบ PCM</td></tr>
            </table>
        </td></tr>
    </table>
</body>
</html>
"""

# ==================== 📊 Stock Audit & Notification Functions ====================

def get_notification_settings(admin_id):
    """ดึงการตั้งค่าแจ้งเตือนจากบัญชี superadmin เพียงชุดเดียว"""
    conn = get_db_connection()
    try:
        ensure_notification_settings_columns(conn)
        settings = conn.execute(
            'SELECT * FROM notification_settings WHERE admin_id = ?',
            ('superadmin',)
        ).fetchone()
        if not settings:
            # Return defaults if not configured
            return {
                'approval_email': True,
                'approval_line': False,
                'rejection_email': True,
                'rejection_line': False,
                'low_stock_email': True,
                'low_stock_line': False,
                'email_recipients': '',
                'email_recipients_pc1': '',
                'email_recipients_cc': ''
            }
        return dict(settings)
    finally:
        conn.close()

def send_smart_notification(notification_type, message, location=None, role=None, email_body='', html_body='', recipients=None, admin_id=None):
    """
    ส่งแจ้งเตือนผ่าน Email ตามการตั้งค่า (LINE ถูกยกเลิกถาวร)
    notification_type: 'approval', 'rejection', 'low_stock', 'withdrawal_confirmed', 'pending_request'
    admin_id: ถ้าไม่ระบุ ให้ใช้ session.get('admin_id') หรือ 'superadmin'
    """
    # ใช้ค่ากลางของ superadmin เพียงชุดเดียวทั้งระบบ
    admin_id = 'superadmin'
    
    settings = get_notification_settings(admin_id)
    scope = resolve_notification_scope(location=location, role=role)
    
    # ตรวจสอบการตั้งค่าตามประเภท
    setting_key_email = f'{notification_type}_email'
    # ถ้าไม่มีคีย์เฉพาะประเภท ให้ยึดตามสวิตช์รวมที่มีอยู่ในหน้า settings
    email_master_enabled = any(bool(settings.get(k, True)) for k in ('approval_email', 'rejection_email', 'low_stock_email'))

    send_email = settings.get(setting_key_email, email_master_enabled)
    
    # ส่ง Email
    if send_email:
        try:
            email_recipients = resolve_notification_email_recipients(
                settings=settings,
                location=location,
                role=role,
                recipients=recipients
            )

            # fallback: เพิ่มผู้รับจาก GA settings และ SMTP From เสมอ เพื่อป้องกันตั้งค่าผู้รับผิดปลายทาง
            fallback_settings = get_settings_values(['ga_recipients_default', 'smtp_from'])
            email_recipients.extend(parse_email_recipients(fallback_settings.get('ga_recipients_default', '')))
            email_recipients.extend(parse_email_recipients(fallback_settings.get('smtp_from', '')))

            email_recipients = list(dict.fromkeys([e for e in email_recipients if is_valid_email_address(e)]))
            
            if email_recipients:
                subject = f'[PCM] แจ้งเตือน - {notification_type}'
                sent, error = send_email_message(
                    subject=subject,
                    body=email_body or message,
                    recipients=email_recipients,
                    html_body=html_body
                )
                log_notification_delivery(
                    admin_id=admin_id,
                    notification_type=notification_type,
                    scope=scope,
                    channel='email',
                    recipients=email_recipients,
                    status='sent' if sent else 'failed',
                    error_message=error,
                    location=str(location or ''),
                    role=str(role or '')
                )
            else:
                log_notification_delivery(
                    admin_id=admin_id,
                    notification_type=notification_type,
                    scope=scope,
                    channel='email',
                    recipients=[],
                    status='failed',
                    error_message='no-recipients',
                    location=str(location or ''),
                    role=str(role or '')
                )
        except Exception as e:
            print(f'Error sending email notification: {e}')
            log_notification_delivery(
                admin_id=admin_id,
                notification_type=notification_type,
                scope=scope,
                channel='email',
                recipients=[],
                status='failed',
                error_message=str(e),
                location=str(location or ''),
                role=str(role or '')
            )

def get_stock_audit_data(product_id=None):
    """
    ดึงข้อมูล Stock Audit: ยอดเดิม + ยอดเบิก + คงเหลือ
    """
    conn = get_db_connection()
    try:
        product_columns = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        if 'is_active' in product_columns:
            status_filter = " AND COALESCE(p.is_active, 1) = 1"
        elif 'status' in product_columns:
            status_filter = " AND p.status = 'Active'"
        else:
            status_filter = ""

        # รองรับ action เบิกทั้งแบบเก่า/ใหม่ และหมวกเซฟตี้
        withdrawal_filter = """
            AND status = 'Approved'
            AND (
                action = 'Withdrawn'
                OR action = 'ขอเบิกอุปกรณ์'
                OR action LIKE 'เบิกหมวกเซฟตี้%'
            )
        """

        if product_id:
            # สำหรับ product เดียว
            query = f'''
                SELECT 
                    p.id,
                    p.code,
                    p.name,
                    '' AS name_eng,
                    p.stock AS current_balance,
                    p.unit,
                    p.location,
                    COALESCE((
                        SELECT SUM(COALESCE(qty, 0))
                        FROM transaction_logs
                        WHERE product_id = p.id AND action = 'Received'
                    ), 0) AS total_received,
                    COALESCE((
                        SELECT SUM(COALESCE(qty, 0))
                        FROM transaction_logs
                        WHERE product_id = p.id {withdrawal_filter}
                    ), 0) AS total_withdrawn,
                    COALESCE((
                        SELECT COUNT(*)
                        FROM transaction_logs
                        WHERE product_id = p.id {withdrawal_filter}
                    ), 0) AS withdraw_count,
                    (
                        SELECT MAX(timestamp)
                        FROM transaction_logs
                        WHERE product_id = p.id {withdrawal_filter}
                    ) AS last_withdraw_at,
                    COALESCE((
                        SELECT SUM(COALESCE(qty, 0))
                        FROM transaction_logs
                        WHERE product_id = p.id AND action = 'Adjusted'
                    ), 0) AS total_adjusted
                FROM products p
                WHERE p.id = ?
                ORDER BY p.code ASC
            '''
            rows = conn.execute(query, (product_id,)).fetchall()
        else:
            # สำหรับทั้งหมด
            query = f'''
                SELECT 
                    p.id,
                    p.code,
                    p.name,
                    '' AS name_eng,
                    p.stock AS current_balance,
                    p.unit,
                    p.location,
                    COALESCE((
                        SELECT SUM(COALESCE(qty, 0))
                        FROM transaction_logs
                        WHERE product_id = p.id AND action = 'Received'
                    ), 0) AS total_received,
                    COALESCE((
                        SELECT SUM(COALESCE(qty, 0))
                        FROM transaction_logs
                        WHERE product_id = p.id {withdrawal_filter}
                    ), 0) AS total_withdrawn,
                    COALESCE((
                        SELECT COUNT(*)
                        FROM transaction_logs
                        WHERE product_id = p.id {withdrawal_filter}
                    ), 0) AS withdraw_count,
                    (
                        SELECT MAX(timestamp)
                        FROM transaction_logs
                        WHERE product_id = p.id {withdrawal_filter}
                    ) AS last_withdraw_at,
                    COALESCE((
                        SELECT SUM(COALESCE(qty, 0))
                        FROM transaction_logs
                        WHERE product_id = p.id AND action = 'Adjusted'
                    ), 0) AS total_adjusted
                FROM products p
                WHERE 1=1{status_filter}
                ORDER BY p.code ASC
            '''
            rows = conn.execute(query).fetchall()
        
        # Process rows to calculate discrepancies
        data = []
        for row in rows:
            current = dict(row)
            current.setdefault('withdraw_count', 0)
            current.setdefault('last_withdraw_at', None)
            expected_balance = current['total_received'] - current['total_withdrawn'] + current['total_adjusted']
            discrepancy = current['current_balance'] - expected_balance
            current['expected_balance'] = expected_balance
            current['discrepancy'] = discrepancy
            current['status'] = '✅ OK' if discrepancy == 0 else f'⚠️ Mismatch ({discrepancy:+d})'
            data.append(current)
        
        return data
    finally:
        conn.close()


def get_stock_audit_available_years():
    conn = get_db_connection()
    try:
        ts_expr = transaction_timestamp_expr('l')
        rows = conn.execute(f'''
            SELECT DISTINCT substr({ts_expr}, 1, 4) AS audit_year
            FROM transaction_logs l
            WHERE l.product_id IS NOT NULL
              AND length(substr({ts_expr}, 1, 4)) = 4
            ORDER BY audit_year DESC
        ''').fetchall()
    finally:
        conn.close()

    years = [int(row['audit_year']) for row in rows if str(row['audit_year']).isdigit()]
    current_year = get_thailand_time().year
    if current_year not in years:
        years.insert(0, current_year)
    return sorted(set(years), reverse=True)


def iter_month_keys(start_year, start_month, end_year, end_month):
    cursor = date(start_year, start_month, 1)
    end_cursor = date(end_year, end_month, 1)
    while cursor <= end_cursor:
        yield cursor.strftime('%Y-%m')
        if cursor.month == 12:
            cursor = date(cursor.year + 1, 1, 1)
        else:
            cursor = date(cursor.year, cursor.month + 1, 1)


def get_stock_audit_monthly_snapshot(selected_year, selected_month=None):
    selected_year = int(selected_year)
    today = get_thailand_time().date()
    current_year = today.year
    current_month = today.month
    export_end_month = 12 if selected_year < current_year else current_month
    if export_end_month < 1:
        export_end_month = 1

    if selected_month is None:
        selected_month = export_end_month
    selected_month = max(1, min(int(selected_month), export_end_month))

    analysis_months = list(iter_month_keys(selected_year, 1, current_year, current_month))
    if not analysis_months:
        analysis_months = [f'{selected_year}-01']
    analysis_months_desc = list(reversed(analysis_months))
    month_key = f'{selected_year}-{selected_month:02d}'

    conn = get_db_connection()
    try:
        product_columns = {row[1] for row in conn.execute("PRAGMA table_info(products)").fetchall()}
        if 'is_active' in product_columns:
            status_filter = " AND COALESCE(p.is_active, 1) = 1"
        elif 'status' in product_columns:
            status_filter = " AND p.status = 'Active'"
        else:
            status_filter = ""

        products = conn.execute(f'''
            SELECT
                p.id,
                p.code,
                p.name,
                COALESCE(p.category, '') AS category,
                COALESCE(p.location, '') AS location,
                COALESCE(p.unit, '') AS unit,
                COALESCE(p.safety_stock, 0) AS safety_stock,
                COALESCE(p.stock, 0) AS current_balance
            FROM products p
            WHERE 1=1{status_filter}
            ORDER BY p.location ASC, p.code ASC
        ''').fetchall()

        ts_expr = transaction_timestamp_expr('l')
        log_rows = conn.execute(f'''
            SELECT
                l.product_id,
                l.action,
                l.status,
                COALESCE(l.qty, 0) AS qty,
                {ts_expr} AS normalized_ts
            FROM transaction_logs l
            WHERE l.product_id IS NOT NULL
              AND substr({ts_expr}, 1, 10) >= ?
              AND (
                    l.action = 'Received'
                    OR l.action = 'Adjusted'
                    OR (
                        l.status = 'Approved'
                        AND (
                            l.action = 'Withdrawn'
                            OR l.action = 'ขอเบิกอุปกรณ์'
                            OR l.action LIKE 'เบิกหมวกเซฟตี้%'
                        )
                    )
              )
            ORDER BY normalized_ts DESC
        ''', (f'{selected_year}-01-01',)).fetchall()
    finally:
        conn.close()

    monthly_metrics = defaultdict(lambda: {
        'received': 0,
        'adjusted': 0,
        'withdrawn': 0,
        'daily_withdrawals': defaultdict(int),
        'last_received_at': ''
    })

    for row in log_rows:
        normalized_ts = str(row['normalized_ts'] or '').strip()
        if len(normalized_ts) < 10:
            continue

        row_month_key = normalized_ts[:7]
        try:
            day_number = int(normalized_ts[8:10])
        except ValueError:
            day_number = 0

        metrics = monthly_metrics[(row['product_id'], row_month_key)]
        qty = int(row['qty'] or 0)
        action = str(row['action'] or '').strip()

        if action == 'Received':
            metrics['received'] += qty
            if normalized_ts > metrics['last_received_at']:
                metrics['last_received_at'] = normalized_ts[:10]
        elif action == 'Adjusted':
            metrics['adjusted'] += qty
        else:
            metrics['withdrawn'] += qty
            if 1 <= day_number <= 31:
                metrics['daily_withdrawals'][day_number] += qty

    rows = []
    for index, product in enumerate(products, start=1):
        running_balance = int(product['current_balance'] or 0)
        month_snapshot = None

        for reverse_month_key in analysis_months_desc:
            metrics = monthly_metrics[(product['id'], reverse_month_key)]
            closing_balance = running_balance
            opening_balance = closing_balance - metrics['received'] - metrics['adjusted'] + metrics['withdrawn']

            if reverse_month_key == month_key:
                month_snapshot = {
                    'opening_balance': opening_balance,
                    'received': metrics['received'],
                    'adjusted': metrics['adjusted'],
                    'total': opening_balance + metrics['received'] + metrics['adjusted'],
                    'withdrawn': metrics['withdrawn'],
                    'closing_balance': closing_balance,
                    'last_received_at': metrics['last_received_at'],
                    'order_qty': max(int(product['safety_stock'] or 0) - closing_balance, 0),
                    'daily_withdrawals': dict(metrics['daily_withdrawals'])
                }
                break

            running_balance = opening_balance

        if month_snapshot is None:
            current_balance = int(product['current_balance'] or 0)
            month_snapshot = {
                'opening_balance': current_balance,
                'received': 0,
                'adjusted': 0,
                'total': current_balance,
                'withdrawn': 0,
                'closing_balance': current_balance,
                'last_received_at': '',
                'order_qty': max(int(product['safety_stock'] or 0) - current_balance, 0),
                'daily_withdrawals': {}
            }

        rows.append({
            'no': index,
            'id': product['id'],
            'code': product['code'],
            'name': product['name'],
            'category': product['category'],
            'location': product['location'],
            'unit': product['unit'],
            'safety_stock': int(product['safety_stock'] or 0),
            **month_snapshot,
        })

    summary = {
        'total_products': len(rows),
        'total_opening': sum(item['opening_balance'] for item in rows),
        'total_received': sum(item['received'] for item in rows),
        'total_withdrawn': sum(item['withdrawn'] for item in rows),
        'total_closing': sum(item['closing_balance'] for item in rows),
        'items_to_order': sum(1 for item in rows if item['order_qty'] > 0),
        'total_order_qty': sum(item['order_qty'] for item in rows),
    }

    month_options = [
        {
            'value': month,
            'label': datetime(selected_year, month, 1).strftime('%B'),
            'short_label': datetime(selected_year, month, 1).strftime('%b')
        }
        for month in range(1, export_end_month + 1)
    ]

    return {
        'selected_year': selected_year,
        'selected_month': selected_month,
        'month_key': month_key,
        'month_name': datetime(selected_year, selected_month, 1).strftime('%B'),
        'month_short_name': datetime(selected_year, selected_month, 1).strftime('%b'),
        'rows': rows,
        'summary': summary,
        'month_options': month_options,
    }


def export_stock_audit_monthly_excel(selected_year):
    selected_year = int(selected_year)
    today = get_thailand_time().date()
    current_year = today.year
    current_month = today.month
    export_end_month = 12 if selected_year < current_year else current_month

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        workbook = writer.book
        title_format = workbook.add_format({
            'bold': True,
            'font_size': 16,
            'align': 'center',
            'valign': 'vcenter',
            'bg_color': '#D9EAF7',
            'border': 1
        })
        header_format = workbook.add_format({
            'bold': True,
            'align': 'center',
            'valign': 'vcenter',
            'bg_color': '#F4CCCC',
            'border': 1,
            'text_wrap': True
        })
        month_header_format = workbook.add_format({
            'bold': True,
            'align': 'center',
            'valign': 'vcenter',
            'bg_color': '#FFF2CC',
            'border': 1
        })
        cell_format = workbook.add_format({'border': 1})
        number_format = workbook.add_format({'border': 1, 'align': 'center'})
        text_center_format = workbook.add_format({'border': 1, 'align': 'center'})
        low_stock_format = workbook.add_format({'border': 1, 'align': 'center', 'bg_color': '#FCE5CD'})

        column_widths = {
            0: 6,
            1: 14,
            2: 38,
            3: 12,
            4: 12,
            5: 12,
            6: 12,
            7: 12,
            8: 12,
            9: 16,
            10: 12,
            11: 12,
            12: 10,
        }

        for month_number in range(1, export_end_month + 1):
            month_payload = get_stock_audit_monthly_snapshot(selected_year, month_number)
            month_name = month_payload['month_name']
            sheet_name = month_payload['month_short_name']
            worksheet = workbook.add_worksheet(sheet_name)
            writer.sheets[sheet_name] = worksheet

            total_columns = 44
            worksheet.merge_range(0, 0, 0, total_columns - 1, f'Stock Audit Month {month_name} {selected_year}', title_format)
            worksheet.set_row(0, 26)
            worksheet.set_row(3, 26)
            worksheet.set_row(4, 22)

            top_headers = [
                'No.', 'รหัสสินค้า', 'รายการ / List', 'ยอดยกมา', 'รับเข้า', 'ปรับยอด',
                'รวม', 'ยอดเบิก', 'คงเหลือ', 'วันที่รับเข้า', 'Safety stock', 'สั่งซื้อ', 'หน่วย'
            ]
            for col_idx, label in enumerate(top_headers):
                worksheet.write(3, col_idx, label, header_format)
            worksheet.merge_range(3, 13, 3, 43, f'Month {month_name} {selected_year}', month_header_format)
            for day in range(1, 32):
                worksheet.write(4, 12 + day, day, month_header_format)

            for col_idx, width in column_widths.items():
                worksheet.set_column(col_idx, col_idx, width)
            worksheet.set_column(13, 43, 5)

            row_cursor = 5
            for item in month_payload['rows']:
                worksheet.write_number(row_cursor, 0, item['no'], number_format)
                worksheet.write(row_cursor, 1, item['code'], cell_format)
                worksheet.write(row_cursor, 2, item['name'], cell_format)
                worksheet.write_number(row_cursor, 3, item['opening_balance'], number_format)
                worksheet.write_number(row_cursor, 4, item['received'], number_format)
                worksheet.write_number(row_cursor, 5, item['adjusted'], number_format)
                worksheet.write_number(row_cursor, 6, item['total'], number_format)
                worksheet.write_number(row_cursor, 7, item['withdrawn'], number_format)
                worksheet.write_number(row_cursor, 8, item['closing_balance'], number_format)
                worksheet.write(row_cursor, 9, item['last_received_at'], text_center_format)
                worksheet.write_number(row_cursor, 10, item['safety_stock'], number_format)
                worksheet.write_number(
                    row_cursor,
                    11,
                    item['order_qty'],
                    low_stock_format if item['order_qty'] > 0 else number_format
                )
                worksheet.write(row_cursor, 12, item['unit'], text_center_format)

                for day in range(1, 32):
                    day_value = item['daily_withdrawals'].get(day, '')
                    if day_value == '':
                        worksheet.write_blank(row_cursor, 12 + day, None, cell_format)
                    else:
                        worksheet.write_number(row_cursor, 12 + day, day_value, number_format)

                row_cursor += 1

            if row_cursor > 5:
                worksheet.autofilter(4, 0, row_cursor - 1, total_columns - 1)
            worksheet.freeze_panes(5, 0)

    output.seek(0)
    return send_file(
        output,
        as_attachment=True,
        download_name=f'Stock_Audit_{selected_year}.xlsx',
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    )

# ==========================================
# 👤 ส่วนของพนักงาน (USER & CART SYSTEM)
# ==========================================

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        is_limited, wait_minutes = is_auth_rate_limited('user_login')
        if is_limited:
            flash(f'⚠️ มีการพยายามเข้าสู่ระบบถี่เกินไป กรุณารอ {wait_minutes} นาที', 'user_error')
            return render_template('index.html')

        emp_id = request.form.get('emp_id', '').strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,20}', emp_id):
            flash('❌ รูปแบบรหัสพนักงานไม่ถูกต้อง', 'user_error')
            return render_template('index.html')

        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
        
        if user:
            # เพิ่มการเช็ค: ถ้าเป็นคนเดิมที่ถือ Session อยู่ ให้เข้าได้เลยไม่ติด Lock
            if session.get('user_id') == emp_id:
                conn.close()
                return redirect(url_for('user_services', emp_id=emp_id))

            if is_user_currently_locked(user):
                flash(f'❌ รหัส {emp_id} กำลังใช้งานอยู่ (ต้อง Logout หรือรอ 5 นาที)', 'user_error')
                conn.close()
                return render_template('index.html')
            
            clear_failed_attempts('user_login')
            session.clear()
            session['user_id'] = emp_id
            session.permanent = True
            conn.execute("UPDATE users SET is_locked = 1, last_seen = datetime('now', '+7 hours') WHERE emp_id = ?", (emp_id,))
            conn.commit()
            conn.close()
            return redirect(url_for('user_services', emp_id=emp_id))
        else:
            conn.close()
            register_failed_attempt('user_login')
            flash(f'❌ ไม่พบรหัสพนักงาน: {emp_id}', 'user_error')
            return render_template('index.html')
            
    return render_template('index.html')

@app.route('/logout_user/<emp_id>', methods=['POST'])
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

    # --- แก้ไขจุดที่ 2: ดึงของทั้งหมด (รวมที่สต็อกเป็น 0) ---
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

    # --- การแจ้งเตือน: คำขอที่ถูกปฏิเสธใน 7 วันล่าสุด ---
    # เก็บ dismissed IDs ใน DB เพื่อไม่ขึ้นทุก login
    conn.execute('''
        CREATE TABLE IF NOT EXISTS dismissed_notifications
        (emp_id TEXT, log_id INTEGER, PRIMARY KEY (emp_id, log_id))
    ''')
    dismissed_rows = conn.execute(
        'SELECT log_id FROM dismissed_notifications WHERE emp_id = ?', (emp_id,)
    ).fetchall()
    dismissed_ids = {row['log_id'] for row in dismissed_rows}
    rejected_rows = conn.execute(f'''
        SELECT l.id, p.name as product_name, l.qty, p.unit, l.timestamp
        FROM transaction_logs l
        JOIN products p ON l.product_id = p.id
        WHERE l.emp_id = ? AND l.status = 'Rejected'
          AND l.action NOT LIKE 'withdraw%'
          AND datetime({transaction_timestamp_expr('l')}) >= datetime('now', 'localtime', '-7 days')
        ORDER BY l.timestamp DESC
    ''', (emp_id,)).fetchall()
    rejected_notifications = [dict(r) for r in rejected_rows if r['id'] not in dismissed_ids]

    # --- การแจ้งเตือน: หมวกนิรภัยครบกำหนดเปลี่ยน (>= 23 เดือน) ---
    helmet_due = False
    helmet_last_date = None
    if not session.get('helmet_due_dismissed'):
        helmet_log = conn.execute(f'''
            SELECT MAX(datetime({transaction_timestamp_expr('l')})) as last_issue
            FROM transaction_logs l
            JOIN products p ON l.product_id = p.id
            WHERE l.emp_id = ?
              AND (p.name LIKE '%หมวก%' OR p.name LIKE '%Helmet%'
                   OR l.action LIKE '%หมวก%' OR l.action LIKE '%Helmet%')
              AND l.status = 'Approved'
        ''', (emp_id,)).fetchone()
        if helmet_log and helmet_log['last_issue']:
            try:
                last_dt = datetime.strptime(helmet_log['last_issue'][:19], '%Y-%m-%d %H:%M:%S')
                months_elapsed = (datetime.now() - last_dt).days / 30.0
                helmet_due = months_elapsed >= 23
                if helmet_due:
                    helmet_last_date = last_dt.strftime('%d/%m/%Y')
            except Exception:
                pass

    conn.close()
    return render_template('menu.html',
                           user=user,
                           products=products_by_category,
                           all_categories=all_categories,
                           current_category=category_filter,
                           cart_items=cart_list,
                           open_cart=open_cart,
                           history=history,
                           rejected_notifications=rejected_notifications,
                           helmet_due=helmet_due,
                           helmet_last_date=helmet_last_date)

@app.route('/user-services')
def user_services():
    emp_id = (request.args.get('emp_id') or '').strip()
    if not is_valid_user_session(emp_id):
        flash('⚠️ กรุณาเข้าสู่ระบบใหม่', 'user_error')
        return redirect(url_for('index'))

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    if not user:
        conn.close()
        flash('❌ ไม่พบข้อมูลพนักงาน', 'danger')
        return redirect(url_for('index'))

    # Badge: GA requests ของ user นี้ที่ยัง Pending
    ga_pending = conn.execute(
        "SELECT COUNT(*) as cnt FROM ga_requests WHERE emp_id = ? AND status = 'Pending'",
        (emp_id,)
    ).fetchone()['cnt']

    # Badge: GA requests ที่ถูกดำเนินการแล้ว (In Progress / Done) ของ user นี้ (แจ้งให้รู้ว่ามีอัปเดต)
    ga_done = conn.execute(
        "SELECT COUNT(*) as cnt FROM ga_requests WHERE emp_id = ? AND status IN ('In Progress','Done','Rejected')",
        (emp_id,)
    ).fetchone()['cnt']

    conn.close()

    return render_template('user_services.html', user=user,
                           ga_pending=ga_pending, ga_done=ga_done)

@app.route('/vehicle-booking')
def vehicle_booking_portal():
    emp_id = (request.args.get('emp_id') or '').strip()
    if not is_valid_user_session(emp_id):
        flash('⚠️ กรุณาเข้าสู่ระบบใหม่', 'user_error')
        return redirect(url_for('index'))

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    conn.close()
    if not user:
        flash('❌ ไม่พบข้อมูลพนักงาน', 'danger')
        return redirect(url_for('index'))

    return render_template('vehicle_booking.html', user=user)

@app.route('/ga-request', methods=['GET', 'POST'])
def ga_request_portal():
    emp_id = (request.args.get('emp_id') or request.form.get('emp_id') or '').strip()
    if not is_valid_user_session(emp_id):
        flash('⚠️ กรุณาเข้าสู่ระบบใหม่', 'user_error')
        return redirect(url_for('index'))

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    if not user:
        conn.close()
        flash('❌ ไม่พบข้อมูลพนักงาน', 'danger')
        return redirect(url_for('index'))

    if request.method == 'POST':
        target_team = clean_input_text(request.form.get('target_team'), 10).upper()
        title = clean_input_text(request.form.get('title'), 150)
        description = clean_multiline_text(request.form.get('description'), 2000)

        if target_team not in GA_REQUEST_TARGET_TEAMS:
            conn.close()
            flash('❌ กรุณาเลือกส่วนงานผู้รับผิดชอบ', 'danger')
            return redirect(url_for('ga_request_portal', emp_id=emp_id))
        if not title:
            conn.close()
            flash('❌ กรุณาระบุหัวข้อปัญหา', 'danger')
            return redirect(url_for('ga_request_portal', emp_id=emp_id))
        if len(description) < 10:
            conn.close()
            flash('❌ กรุณาระบุรายละเอียดปัญหาอย่างน้อย 10 ตัวอักษร', 'danger')
            return redirect(url_for('ga_request_portal', emp_id=emp_id))

        image_path = ''
        image_save_warning = ''
        db_attachment_payload = None
        uploaded_image = request.files.get('image')
        try:
            image_path = save_uploaded_image(uploaded_image)
        except ValueError as exc:
            # ถ้าเขียนไฟล์ลงดิสก์ไม่ได้ ให้ fallback ไปเก็บเป็น BLOB ใน DB
            if uploaded_image and uploaded_image.filename:
                try:
                    db_attachment_payload = extract_uploaded_image_blob(uploaded_image)
                    image_save_warning = 'ไฟล์แนบถูกบันทึกด้วยโหมดสำรอง (DB attachment)'
                except ValueError as blob_exc:
                    image_save_warning = str(blob_exc)
            else:
                image_save_warning = str(exc)
            image_path = ''
            print(f"[GA_UPLOAD] value error while saving attachment: {exc}")
        except Exception as exc:
            image_save_warning = 'ไม่สามารถบันทึกไฟล์แนบได้ กรุณาลองใหม่อีกครั้ง'
            image_path = ''
            print(f"[GA_UPLOAD] unexpected error while saving attachment: {exc}")

        requester_email = normalize_email_value(user['email']) if 'email' in user.keys() else ''
        if requester_email and not is_valid_email_address(requester_email):
            requester_email = ''

        created_at = get_thailand_time().strftime('%Y-%m-%d %H:%M:%S')
        cursor = conn.execute('''
            INSERT INTO ga_requests (
                emp_id, requester_name, department, location, requester_email_snapshot,
                target_team, title, description, image_path, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', ?, ?)
        ''', (
            emp_id,
            user['name'],
            user['department'],
            user['location'],
            requester_email,
            target_team,
            title,
            description,
            image_path,
            created_at,
            created_at
        ))

        request_id = cursor.lastrowid
        if db_attachment_payload and request_id:
            try:
                conn.execute(
                    'INSERT OR REPLACE INTO ga_request_attachments (request_id, mime_type, image_data) VALUES (?, ?, ?)',
                    (request_id, db_attachment_payload['mime_type'], db_attachment_payload['image_data'])
                )
                conn.execute('UPDATE ga_requests SET image_path = ? WHERE id = ?', (f'db:{request_id}', request_id))
            except Exception as exc:
                print(f"[GA_UPLOAD] failed to persist DB attachment for request {request_id}: {exc}")
                if not image_save_warning:
                    image_save_warning = 'บันทึกคำร้องแล้ว แต่ไม่สามารถแนบรูปได้'

        conn.commit()

        request_payload = {
            'id': request_id,
            'emp_id': emp_id,
            'requester_name': user['name'],
            'department': user['department'],
            'location': user['location'],
            'target_team': target_team,
            'title': title,
            'description': description,
        }
        recipients = resolve_ga_request_recipients(target_team, user['location'])
        attachment_absolute_path = resolve_ga_attachment_absolute_path(image_path)
        email_sent, email_error = send_email_message(
            subject=f"[PCM] GA Request ใหม่ {target_team} - {title}",
            body=build_ga_request_email_body(request_payload),
            html_body=build_ga_request_email_html(request_payload),
            recipients=recipients,
            attachment_path=attachment_absolute_path
        )

        flash_message = '✅ ส่งคำร้องเรียบร้อยแล้ว'
        if not recipients:
            flash_message += ' (ยังไม่มีอีเมลผู้รับผิดชอบในระบบ environment)'
        elif not email_sent and email_error == 'mail-not-configured':
            flash_message += ' (บันทึกคำร้องแล้ว แต่ยังไม่ได้ตั้งค่า SMTP)'
        elif not email_sent:
            flash_message += ' (บันทึกคำร้องแล้ว แต่ส่งอีเมลไม่สำเร็จ)'

        if image_save_warning:
            flash_message += f' (บันทึกรายการแล้ว แต่แนบรูปไม่สำเร็จ: {image_save_warning})'

        conn.close()
        flash(flash_message, 'success')
        return redirect(url_for('ga_request_portal', emp_id=emp_id))

    recent_requests = conn.execute('''
        SELECT * FROM ga_requests
        WHERE emp_id = ?
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 10
    ''', (emp_id,)).fetchall()
    conn.close()

    return render_template(
        'ga_request.html',
        user=user,
        recent_requests=recent_requests,
        target_teams=GA_REQUEST_TARGET_TEAMS
    )

@app.route('/add_to_cart', methods=['POST'])
def add_to_cart():
    emp_id = (request.form.get('emp_id') or '').strip()
    product_id = request.form.get('product_id', type=int)
    qty_unit = request.form.get('qty_unit', 'package')  # ✅ NEW: base or package unit
    current_search = request.form.get('current_search', '')
    current_cat = request.form.get('current_cat', '')
    is_ajax = request.form.get('_ajax') == '1'

    if not is_valid_user_session(emp_id):
        if is_ajax:
            return jsonify({'success': False, 'message': '⚠️ session ไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่'}), 401
        flash('⚠️ session ไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่', 'danger')
        return redirect(url_for('index'))

    try:
        qty = int(request.form.get('qty', 1))
    except (TypeError, ValueError):
        if is_ajax:
            return jsonify({'success': False, 'message': '❌ จำนวนที่เบิกไม่ถูกต้อง'}), 400
        flash('❌ จำนวนที่เบิกไม่ถูกต้อง', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

    if not product_id or qty <= 0:
        if is_ajax:
            return jsonify({'success': False, 'message': '❌ จำนวนที่เบิกต้องมากกว่า 0'}), 400
        flash('❌ จำนวนที่เบิกต้องมากกว่า 0', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

    conn = get_db_connection()
    start_write_transaction(conn)
    user = conn.execute('SELECT * FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    product = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
    if not user or not product:
        conn.close()
        if is_ajax:
            return jsonify({'success': False, 'message': '❌ ไม่พบผู้ใช้หรือของ'}), 400
        flash('❌ ไม่พบผู้ใช้หรือของ', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

    if not user_can_access_product(user, product):
        conn.close()
        if is_ajax:
            return jsonify({'success': False, 'message': '❌ คุณไม่มีสิทธิ์เบิกรายการนี้'}), 403
        flash('❌ คุณไม่มีสิทธิ์เบิกรายการนี้', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

    split_medicine = is_split_tablet_medicine(product)
    manager = UnitConversionManager(conn)

    if split_medicine:
        if qty_unit not in ('base', 'package'):
            conn.close()
            if is_ajax:
                return jsonify({'success': False, 'message': '❌ หน่วยเบิกยาไม่ถูกต้อง'}), 400
            flash('❌ หน่วยเบิกยาไม่ถูกต้อง', 'danger')
            return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

        product_info = manager.get_product_unit_info(product_id)
        # บังคับเบิกยาแบบหน่วยย่อยเท่านั้น
        qty_unit = 'base'
        qty_to_reserve = qty
        requested_unit_label = product_info.get('base_unit') or 'เม็ด'

        stock_check = manager.check_stock_available(product_id, qty_to_reserve)
        can_add = stock_check['available']
    else:
        qty_unit = 'package'
        qty_to_reserve = qty
        requested_unit_label = product['unit']
        available_stock = max(0, int(product['stock'] or 0) - int(product['reserved_stock'] or 0))
        can_add = available_stock >= qty

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
            success_msg = f'🛒 เพิ่ม {product["name"]} ({qty_to_reserve} {requested_unit_label} - หน่วยย่อย) เรียบร้อย'
            if is_ajax:
                conn.close()
                return jsonify({'success': True, 'message': success_msg})
            flash(success_msg, 'success')
        else:
            stock_update = conn.execute(
                'UPDATE products SET reserved_stock = reserved_stock + ? WHERE id = ? AND (stock - reserved_stock) >= ?',
                (qty_to_reserve, product_id, qty)
            )
            if stock_update.rowcount == 0:
                conn.rollback()
                conn.close()
                if is_ajax:
                    return jsonify({'success': False, 'message': '❌ ของหมดหรือมีไม่พอ'}), 400
                flash('❌ ของหมดหรือมีไม่พอ', 'danger')
                return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

            existing_item = conn.execute('SELECT * FROM carts WHERE emp_id = ? AND product_id = ?', (emp_id, product_id)).fetchone()
            if existing_item:
                safe_existing_qty = max(0, int(existing_item['qty'] or 0))
                conn.execute('UPDATE carts SET qty = ? WHERE id = ?', (safe_existing_qty + qty_to_reserve, existing_item['id']))
            else:
                conn.execute('INSERT INTO carts (emp_id, product_id, qty) VALUES (?, ?, ?)', (emp_id, product_id, qty_to_reserve))
            conn.commit()
            success_msg = f'🛒 เพิ่ม {product["name"]} ({qty} {requested_unit_label}) เรียบร้อย'
            if is_ajax:
                conn.close()
                return jsonify({'success': True, 'message': success_msg})
            flash(success_msg, 'success')
    else:
        conn.rollback()
        if is_ajax:
            conn.close()
            return jsonify({'success': False, 'message': '❌ ของหมดหรือมีไม่พอ'}), 400
        flash('❌ ของหมดหรือมีไม่พอ', 'danger')

    conn.close()
    return redirect(url_for('menu', emp_id=emp_id, search=current_search, category=current_cat))

@app.route('/api/dismiss_notification', methods=['POST'])
def dismiss_notification():
    """เก็บ ID คำขอที่ถูกปฏิเสธที่ผู้ใช้กด dismiss แล้ว หรือ dismiss แจ้งเตือนหมวก"""
    emp_id = session.get('user_id')
    if not emp_id:
        return jsonify({'success': False}), 401
    notif_type = request.form.get('type', 'rejection')
    if notif_type == 'helmet':
        session['helmet_due_dismissed'] = True
    else:
        log_id = request.form.get('log_id', type=int)
        if log_id:
            conn = get_db_connection()
            conn.execute('''
                CREATE TABLE IF NOT EXISTS dismissed_notifications
                (emp_id TEXT, log_id INTEGER, PRIMARY KEY (emp_id, log_id))
            ''')
            conn.execute(
                'INSERT OR IGNORE INTO dismissed_notifications (emp_id, log_id) VALUES (?, ?)',
                (emp_id, log_id)
            )
            conn.commit()
            conn.close()
    return jsonify({'success': True})

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
        
        # บังคับ preview สำหรับยาแบบหน่วยย่อยเท่านั้น
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
    is_ajax = request.form.get('_ajax') == '1'

    if not is_valid_user_session(emp_id):
        if is_ajax:
            return jsonify({'success': False, 'message': '⚠️ session ไม่ถูกต้อง'}), 401
        flash('⚠️ session ไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่', 'danger')
        return redirect(url_for('index'))

    conn = get_db_connection()
    start_write_transaction(conn)
    cart_item = conn.execute('SELECT * FROM carts WHERE id = ? AND emp_id = ?', (cart_id, emp_id)).fetchone()
    if not cart_item:
        conn.close()
        if is_ajax:
            return jsonify({'success': False, 'message': '❌ ไม่พบรายการในตะกร้า'}), 400
        flash('❌ ไม่พบรายการในตะกร้า', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, open_cart='true', search=current_search, category=current_cat))

    product = conn.execute('SELECT * FROM products WHERE id = ?', (cart_item['product_id'],)).fetchone()
    is_medicine = is_split_tablet_medicine(product) if product else False
    qty = max(0, int(cart_item['qty'] or 0))

    conn.execute('DELETE FROM carts WHERE id = ? AND emp_id = ?', (cart_id, emp_id))
    if is_medicine:
        conn.execute('UPDATE products SET reserved_stock = MAX(0, reserved_stock - ?) WHERE id = ?', (qty, cart_item['product_id']))
    else:
        conn.execute('UPDATE products SET reserved_stock = MAX(0, reserved_stock - ?) WHERE id = ?', (qty, cart_item['product_id']))
    conn.commit()

    if is_ajax:
        new_count = conn.execute('SELECT COUNT(*) FROM carts WHERE emp_id = ?', (emp_id,)).fetchone()[0]
        conn.close()
        return jsonify({'success': True, 'new_count': new_count})

    conn.close()
    return redirect(url_for('menu', emp_id=emp_id, open_cart='true', search=current_search, category=current_cat))
    
@app.route('/confirm_withdrawal', methods=['POST'])
def confirm_withdrawal():
    emp_id = (request.form.get('emp_id') or '').strip()
    symptom = (request.form.get('symptom') or '').strip()
    receive_mode = normalize_request_receive_mode(request.form.get('receive_mode'))
    requested_receive_at = parse_requested_receive_at(request.form.get('receive_at'))

    if not is_valid_user_session(emp_id):
        flash('⚠️ session ไม่ถูกต้อง กรุณาเข้าสู่ระบบใหม่', 'danger')
        return redirect(url_for('index'))

    if receive_mode == 'scheduled':
        if not requested_receive_at:
            flash('❌ กรุณาระบุวันและเวลารับของสำหรับการเบิกล่วงหน้า', 'danger')
            return redirect(url_for('menu', emp_id=emp_id, open_cart='true'))
        if datetime.strptime(requested_receive_at, '%Y-%m-%d %H:%M:%S') <= get_thailand_time():
            flash('❌ วันเวลารับของล่วงหน้าต้องมากกว่าเวลาปัจจุบัน', 'danger')
            return redirect(url_for('menu', emp_id=emp_id, open_cart='true'))
    else:
        requested_receive_at = ''

    receive_plan_text = build_receive_plan_text(receive_mode, requested_receive_at)

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
        flash('❌ พบจำนวนของในตะกร้าไม่ถูกต้อง กรุณาลบและเลือกใหม่', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, open_cart='true'))
    
    has_medicine = any(is_medicine_product(item) for item in cart_items)  # ยาทุกประเภทต้องระบุอาการ
    if has_medicine and not symptom:
        conn.close()
        flash('❌ รายการเบิกยาต้องระบุอาการก่อนยืนยัน', 'danger')
        return redirect(url_for('menu', emp_id=emp_id, open_cart='true'))

    msg_list = [f"🚀 *มีคำขอเบิกใหม่* 🚀\n👤 ผู้เบิก: {user['name']}\n📍 แผนก: {user['department']} ({user['location']})"]
    notification_items = []
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
            # สำหรับยาแบบแยกหน่วย: qty = จำนวนแพ็ก (full_packages_needed), qty_base_unit = จำนวนหน่วยย่อย (เม็ด)
            # สำหรับรายการอื่น: qty = จำนวนที่ขอเบิก (หน่วยเดียวกับ unit ของสินค้า)
            if is_medicine:
                result_qty = withdrawal_result.get('full_packages_needed', item['qty'])
            else:
                result_qty = withdrawal_result.get('full_packages_used', item['qty'])
            result_pkg_qty = withdrawal_result.get('total_packages_used', result_qty)

            if "หมวกเซฟตี้" in item_name or "Helmet" in item_name:
                existing_helmet = conn.execute('''
                    SELECT id FROM transaction_logs 
                    WHERE emp_id = ? AND product_id = ? AND action = 'เบิกหมวกเซฟตี้ (รอบ 2 ปี)'
                ''', (emp_id, item['product_id'])).fetchone()

                result_note = withdrawal_result.get('note') or withdrawal_result.get('transaction_note', '')
                if existing_helmet:
                    conn.execute('''
                        UPDATE transaction_logs 
                        SET qty = ?, qty_base_unit = ?, timestamp = ?, status = 'Pending', note = ?,
                            request_receive_mode = ?, requested_receive_at = ?
                        WHERE id = ?
                    ''', (result_qty, item['qty'], thai_now, result_note, receive_mode, requested_receive_at or None, existing_helmet['id']))
                else:
                    conn.execute('''
                        INSERT INTO transaction_logs (emp_id, product_id, action, qty, qty_base_unit, qty_package_unit, status, timestamp, note, request_receive_mode, requested_receive_at) 
                        VALUES (?, ?, 'เบิกหมวกเซฟตี้ (รอบ 2 ปี)', ?, ?, ?, 'Pending', ?, ?, ?, ?)
                    ''', (emp_id, item['product_id'], result_qty, item['qty'], result_pkg_qty, thai_now, result_note, receive_mode, requested_receive_at or None))
            else:
                result_note = withdrawal_result.get('note') or withdrawal_result.get('transaction_note', '')
                if has_medicine and is_medicine:
                    result_note = f"อาการ: {symptom}" + (f" | {result_note}" if result_note else "")
                conn.execute('''
                    INSERT INTO transaction_logs (emp_id, product_id, action, qty, qty_base_unit, qty_package_unit, status, timestamp, note, request_receive_mode, requested_receive_at) 
                    VALUES (?, ?, 'ขอเบิกอุปกรณ์', ?, ?, ?, 'Pending', ?, ?, ?, ?)
                ''', (emp_id, item['product_id'], result_qty, item['qty'], result_pkg_qty, thai_now, result_note, receive_mode, requested_receive_at or None))
            
            log_note = withdrawal_result.get('note') or withdrawal_result.get('transaction_note', '')
            display_qty = item['qty']
            display_unit = item['unit']
            if is_medicine:
                display_unit = item['base_unit'] if 'base_unit' in item.keys() and item['base_unit'] else 'เม็ด'
            msg_list.append(f"📦 {item_name}\n   🔹 จำนวน: {display_qty} {display_unit}\n   ℹ️ {log_note}")
            notification_items.append({
                'name': item_name,
                'qty': display_qty,
                'unit': display_unit,
                'note': log_note
            })
            
        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f'❌ ไม่สามารถยืนยันการเบิกได้: {str(e)}', 'danger')
            return redirect(url_for('menu', emp_id=emp_id, open_cart='true'))

    # ปลด reserved_stock เฉพาะยาที่ตัดสต็อกจริงแล้วตอน confirm
    conn.execute('''
        UPDATE products
        SET reserved_stock = MAX(0, reserved_stock - COALESCE((
            SELECT SUM(c.qty)
            FROM carts c
            JOIN products p2 ON p2.id = c.product_id
            WHERE c.emp_id = ?
              AND c.product_id = products.id
              AND p2.base_unit IS NOT NULL AND TRIM(p2.base_unit) != ''
              AND p2.package_unit IS NOT NULL AND TRIM(p2.package_unit) != ''
              AND COALESCE(p2.conversion_rate, 1) > 1
        ), 0))
        WHERE id IN (SELECT product_id FROM carts WHERE emp_id = ?)
    ''', (emp_id, emp_id))

    # ล้างตะกร้า
    conn.execute('DELETE FROM carts WHERE emp_id = ?', (emp_id,))
    conn.commit()
    conn.close()

    msg_list.append(f"📥 การรับของ: {receive_plan_text}")

    withdrawal_email_payload = {
        'requester_name': user['name'] if user and 'name' in user.keys() else '-',
        'emp_id': emp_id,
        'department': user['department'] if user and 'department' in user.keys() else '-',
        'location': user['location'] if user and 'location' in user.keys() else '-',
        'symptom': symptom,
        'receive_plan': receive_plan_text,
        'created_at': thai_now,
        'items': notification_items,
    }

    # 📧 ส่งแจ้งเตือนผ่าน EMAIL & LINE ตามการตั้งค่า (superadmin เป็นค่าเริ่มต้น)
    withdrawal_msg = "\n".join(msg_list)
    send_smart_notification(
        notification_type='withdrawal_confirmed',
        message=withdrawal_msg,
        location=(user['location'] if user and 'location' in user.keys() else ''),
        email_body=build_withdrawal_email_body(withdrawal_email_payload),
        html_body=build_withdrawal_email_html(withdrawal_email_payload),
        admin_id='superadmin'
    )
    
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
            available_stock = max(0, int(product['stock'] or 0) - int(product['reserved_stock'] or 0)) if product else 0
            can_update = (diff <= 0) or available_stock >= diff

        if can_update:
            if is_medicine:
                conn.execute('UPDATE carts SET qty = ? WHERE id = ? AND emp_id = ?', (new_qty, cart_id, emp_id))
                conn.execute('UPDATE products SET reserved_stock = MAX(0, reserved_stock + ?) WHERE id = ?',
                             (diff, item['product_id']))
            else:
                if diff > 0:
                    stock_update = conn.execute(
                        'UPDATE products SET reserved_stock = reserved_stock + ? WHERE id = ? AND (stock - reserved_stock) >= ?',
                        (diff, item['product_id'], diff)
                    )
                    if stock_update.rowcount == 0:
                        conn.rollback()
                        conn.close()
                        return jsonify({'success': False, 'message': 'ของในคลังไม่พอ'}), 409
                else:
                    conn.execute('UPDATE products SET reserved_stock = MAX(0, reserved_stock + ?) WHERE id = ?', 
                                 (diff, item['product_id']))
                conn.execute('UPDATE carts SET qty = ? WHERE id = ? AND emp_id = ?', (new_qty, cart_id, emp_id))
            conn.commit()
            res = {'success': True}
        else:
            res = {'success': False, 'message': 'ของในคลังไม่พอ'}
    else:
        res = {'success': False, 'message': 'ไม่พบรายการในตะกร้า'}
    
    conn.close()
    return jsonify(res)

@app.route('/api/get_cart')
def api_get_cart():
    emp_id = request.args.get('emp_id', '').strip()
    if not is_valid_user_session(emp_id):
        return jsonify({'success': False}), 401

    conn = get_db_connection()
    cart_items = conn.execute('''
        SELECT c.id, c.product_id, c.qty,
               p.name, p.code, p.category, p.unit, p.base_unit, p.package_unit, p.conversion_rate, p.base_unit_to_tablet_rate
        FROM carts c JOIN products p ON c.product_id = p.id
        WHERE c.emp_id = ?
    ''', (emp_id,)).fetchall()
    conn.close()

    items = [dict(row) for row in cart_items]
    for item in items:
        item['is_split_medicine'] = is_split_tablet_medicine(item)
        item['is_medicine'] = is_medicine_product(item)  # ยาทั่วไป (split หรือไม่)
        hint_text = get_split_unit_hint_text(item)
        item['split_unit_hint_label'] = hint_text
        item['split_unit_hint_text'] = f" ({hint_text})" if hint_text else ''
        item['name_with_unit_hint'] = item.get('name', '')
    return jsonify({'success': True, 'count': len(items), 'items': items})


@app.route('/api/get_history')
def api_get_history():
    emp_id = request.args.get('emp_id', '').strip()
    page = request.args.get('page', 1, type=int)
    if not is_valid_user_session(emp_id):
        return jsonify({'success': False}), 401

    per_page = 5
    offset = (page - 1) * per_page

    conn = get_db_connection()
    total = conn.execute(
        'SELECT COUNT(*) FROM transaction_logs WHERE emp_id = ?', (emp_id,)
    ).fetchone()[0]

    rows = conn.execute('''
         SELECT l.id, l.action, l.qty, l.qty_base_unit, l.status, l.note, l.timestamp,
             l.request_receive_mode, l.requested_receive_at,
               p.name as product_name, p.unit, p.category, p.base_unit,
               p.package_unit, p.conversion_rate
        FROM transaction_logs l
        JOIN products p ON l.product_id = p.id
        WHERE l.emp_id = ?
        ORDER BY l.timestamp DESC
        LIMIT ? OFFSET ?
    ''', (emp_id, per_page, offset)).fetchall()
    conn.close()

    items = []
    for row in rows:
        r = dict(row)
        # is_medicine_product / is_split_tablet_medicine ต้องการ key 'name' แต่ JOIN ใช้ alias 'product_name'
        r['name'] = r.get('product_name', '')
        is_med = is_split_tablet_medicine(r)
        note = r.get('note') or ''
        symptom = ''
        if 'อาการ: ' in note:
            parts = note.split(' | ', 1)
            symptom = parts[0].replace('อาการ: ', '', 1)
        r['is_split_medicine'] = is_med
        r['symptom'] = symptom
        r['receive_plan_text'] = build_receive_plan_text(r.get('request_receive_mode'), r.get('requested_receive_at'))
        r['display_qty'] = r['qty_base_unit'] if is_med and r.get('qty_base_unit') else r['qty']
        r['display_unit'] = (r['base_unit'] or 'เม็ด') if is_med and r.get('qty_base_unit') else (r['unit'] or '')
        r['timestamp'] = format_timestamp(r.get('timestamp', ''))
        items.append(r)

    total_pages = math.ceil(total / per_page) if total > 0 else 1
    return jsonify({
        'success': True,
        'items': items,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages
    })

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
    
    # Logic การกรอง Location ตามสิทธิ์ของ User
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
        is_limited, wait_minutes = is_auth_rate_limited('admin_login')
        if is_limited:
            flash(f'⚠️ มีการพยายามเข้าสู่ระบบผู้ดูแลถี่เกินไป กรุณารอ {wait_minutes} นาที', 'admin_error')
            return render_template('index.html')

        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''
        if not username or len(username) > 50 or not password or len(password) > 128:
            flash('❌ ข้อมูลเข้าสู่ระบบไม่ถูกต้อง', 'admin_error')
            return render_template('index.html')
        
        conn = get_db_connection()
        admin = conn.execute('SELECT * FROM admins WHERE username = ?', (username,)).fetchone()
        conn.close()
        
        # ตรวจสอบรหัสผ่าน
        if admin and check_password_hash(admin['password'], password):
            clear_failed_attempts('admin_login')
            session.clear()
            session['admin_logged_in'] = True
            session['admin_name'] = admin['name']
            session['admin_username'] = admin['username']
            session['admin_role'] = admin['role']
            session.permanent = True
            return redirect(url_for('admin_dashboard', module='stock'))
        
        register_failed_attempt('admin_login')
        flash('❌ ชื่อผู้ใช้หรือรหัสผ่านแอดมินไม่ถูกต้อง', 'admin_error')
        
    # สำคัญ: ต้อง render_template กลับไปหน้า index.html (หน้าที่มีทั้ง 2 ฟอร์ม)
    return render_template('index.html')

@app.route('/admin', defaults={'module': 'stock'})
@app.route('/admin/<module>')
def admin_dashboard(module):
    if not session.get('admin_logged_in'): return redirect(url_for('index'))
    
    role = session.get('admin_role', 'superadmin')
    admin_module = (module or 'stock').strip().lower()
    if admin_module not in ('stock', 'ga', 'vehicle'):
        admin_module = 'stock'
    
    # --- Filter ตาม Role และการเลือกสถานที่ (คงเดิม) ---
    role_log_filter = ""
    if role == 'admin_pc1':
        role_log_filter = " AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')"
    elif role == 'admin_cc':
        role_log_filter = " AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')"

    selected_loc = request.args.get('log_loc', '') 
    selected_pending_receive = request.args.get('pending_receive', '').strip().lower()
    if selected_pending_receive not in ('', 'immediate', 'scheduled'):
        selected_pending_receive = ''
    super_admin_filter = ""
    if role == 'superadmin':
        if selected_loc == 'PC1':
            super_admin_filter = " AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')"
        elif selected_loc == 'CC':
            super_admin_filter = " AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')"
    
    final_log_filter = role_log_filter + super_admin_filter
    pending_receive_filter = ""
    if selected_pending_receive == 'immediate':
        pending_receive_filter = " AND COALESCE(l.request_receive_mode, 'immediate') = 'immediate'"
    elif selected_pending_receive == 'scheduled':
        pending_receive_filter = " AND COALESCE(l.request_receive_mode, 'immediate') = 'scheduled'"

    ga_selected_loc = request.args.get('ga_loc', '')
    ga_role_filter = ""
    if role == 'admin_pc1':
        ga_role_filter = " AND (g.location LIKE '%PC1%')"
    elif role == 'admin_cc':
        ga_role_filter = " AND (g.location LIKE '%Coil Center%' OR g.location LIKE '%CC%')"

    ga_super_admin_filter = ""
    if role == 'superadmin':
        if ga_selected_loc == 'PC1':
            ga_super_admin_filter = " AND (g.location LIKE '%PC1%')"
        elif ga_selected_loc == 'CC':
            ga_super_admin_filter = " AND (g.location LIKE '%Coil Center%' OR g.location LIKE '%CC%')"

    final_ga_filter = ga_role_filter + ga_super_admin_filter

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
    # นับย้อนหลัง 30 วันจากเวลาปัจจุบันของเซิร์ฟเวอร์ โดยแปลง timestamp เดิมให้อยู่ในรูปที่ SQLite เทียบวันที่ได้ถูกต้อง
    chart_query = f'''
        SELECT u.department AS department,
               COALESCE(SUM(CASE
                   WHEN l.qty_base_unit IS NOT NULL AND l.qty_base_unit > 0 THEN l.qty_base_unit
                   ELSE l.qty
               END), 0) AS total_qty
        FROM transaction_logs l
        JOIN users u ON l.emp_id = u.emp_id
        WHERE l.status = 'Approved'
          AND l.emp_id NOT LIKE 'ADMIN:%'
          AND TRIM(COALESCE(u.department, '')) <> ''
          AND l.action <> 'withdraw'
          AND datetime({transaction_timestamp_expr('l')}) >= datetime('now', 'localtime', '-30 days')
        GROUP BY u.department
        HAVING total_qty > 0
        ORDER BY total_qty DESC
    '''
    chart_results = conn.execute(chart_query).fetchall()
    dept_labels = [row['department'] for row in chart_results]
    dept_values = [int(row['total_qty']) for row in chart_results]
    dept_summary = [{'name': row['department'], 'total': int(row['total_qty'])} for row in chart_results]

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
        WHERE l.status = 'Pending' {role_log_filter} {pending_receive_filter}
        ORDER BY l.timestamp ASC
    '''
    pending_logs = conn.execute(pending_query).fetchall()

    # --- รายการเบิกล่วงหน้าที่อนุมัติแล้ว รอรับของในอนาคต ---
    scheduled_approved_query = f'''
        SELECT l.*, u.name as emp_name, u.department, u.location,
               p.name as product_name, p.unit, p.category, p.base_unit, p.package_unit, p.conversion_rate
        FROM transaction_logs l
        LEFT JOIN users u ON l.emp_id = u.emp_id
        LEFT JOIN products p ON l.product_id = p.id
        WHERE l.request_receive_mode = 'scheduled'
        AND l.status = 'Approved'
        AND l.requested_receive_at IS NOT NULL
        AND trim(l.requested_receive_at) != ''
        AND l.requested_receive_at >= datetime('now', '+7 hours')
        {role_log_filter}
        ORDER BY l.requested_receive_at ASC
    '''
    scheduled_approved_logs = conn.execute(scheduled_approved_query).fetchall()

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

    ga_requests = conn.execute(f'''
        SELECT g.*,
               COALESCE(NULLIF(TRIM(g.requester_email_snapshot), ''), NULLIF(TRIM(u.email), ''), '-') AS requester_email
        FROM ga_requests g
        LEFT JOIN users u ON g.emp_id = u.emp_id
        WHERE 1=1 {final_ga_filter}
        ORDER BY
            CASE g.status
                WHEN 'Pending' THEN 0
                WHEN 'In Progress' THEN 1
                ELSE 2
            END,
            datetime(g.created_at) DESC,
            g.id DESC
        LIMIT 100
    ''').fetchall()
    ga_pending_count = conn.execute(
        f"SELECT COUNT(*) FROM ga_requests g WHERE g.status = 'Pending' {final_ga_filter}"
    ).fetchone()[0]

    stock_pending_count = len(pending_logs)

    conn.close()
    
    return render_template('admin_dashboard.html',
                           pending_logs=pending_logs,
                           stock_pending_count=stock_pending_count,
                           scheduled_approved_logs=scheduled_approved_logs,
                           items=all_stock,
                           categories=categories,
                           low_stock=low_stock,
                           logs=logs,
                           page=page, total_pages=total_pages,
                           dept_labels=dept_labels, dept_values=dept_values, dept_summary=dept_summary, # ข้อมูลสำหรับกราฟ
                           top_items=top_items, # ข้อมูลของเบิกสูงสุด
                           role=role,
                           admin_module=admin_module,
                           selected_loc=selected_loc,
                           selected_pending_receive=selected_pending_receive,
                           ga_requests=ga_requests,
                           ga_pending_count=ga_pending_count,
                           ga_selected_loc=ga_selected_loc,
                           ga_status_options=GA_REQUEST_STATUS_OPTIONS)

@app.route('/admin/stock_audit')
def stock_audit():
    """📊 Stock Audit Report"""
    if not session.get('admin_logged_in'):
        return redirect(url_for('index'))
    
    product_id = request.args.get('product_id', type=int)
    popup_mode = str(request.args.get('popup', '')).strip().lower() in ('1', 'true', 'yes', 'on')
    available_years = get_stock_audit_available_years()
    selected_year = request.args.get('year', type=int) or available_years[0]
    selected_month = request.args.get('month', type=int)

    if request.args.get('format') == 'xlsx':
        return export_stock_audit_monthly_excel(selected_year)

    monthly_payload = get_stock_audit_monthly_snapshot(selected_year, selected_month)
    audit_rows = monthly_payload['rows']
    active_day_count = calendar.monthrange(monthly_payload['selected_year'], monthly_payload['selected_month'])[1]
    if product_id:
        audit_rows = [item for item in audit_rows if item['id'] == product_id]
    
    if request.args.get('format') == 'json':
        return jsonify({
            'data': audit_rows,
            'month': monthly_payload['selected_month'],
            'year': monthly_payload['selected_year']
        })

    summary = monthly_payload['summary'].copy()
    if product_id:
        summary = {
            'total_products': len(audit_rows),
            'total_opening': sum(item['opening_balance'] for item in audit_rows),
            'total_received': sum(item['received'] for item in audit_rows),
            'total_withdrawn': sum(item['withdrawn'] for item in audit_rows),
            'total_closing': sum(item['closing_balance'] for item in audit_rows),
            'items_to_order': sum(1 for item in audit_rows if item['order_qty'] > 0),
            'total_order_qty': sum(item['order_qty'] for item in audit_rows),
        }
    
    return render_template('stock_audit.html',
                          audit_rows=audit_rows,
                          summary=summary,
                          product_id=product_id,
                          popup_mode=popup_mode,
                          available_years=available_years,
                          selected_year=monthly_payload['selected_year'],
                          available_months=monthly_payload['month_options'],
                          selected_month=monthly_payload['selected_month'],
                          selected_month_name=monthly_payload['month_name'],
                          active_day_count=active_day_count)

@app.route('/admin/update_ga_request/<int:request_id>', methods=['POST'])
def update_ga_request(request_id):
    if not session.get('admin_logged_in'):
        flash('❌ กรุณาเข้าสู่ระบบผู้ดูแลก่อน', 'danger')
        return redirect(url_for('index'))

    new_status = clean_input_text(request.form.get('status'), 20)
    admin_note = clean_multiline_text(request.form.get('admin_note'), 1000)
    ga_loc = clean_input_text(request.form.get('ga_loc'), 10)

    if new_status not in GA_REQUEST_STATUS_OPTIONS:
        flash('❌ สถานะคำร้องไม่ถูกต้อง', 'danger')
        return redirect(url_for('admin_dashboard', module='ga', ga_loc=ga_loc))

    role = session.get('admin_role', 'superadmin')
    conn = get_db_connection()
    ga_request = conn.execute('SELECT * FROM ga_requests WHERE id = ?', (request_id,)).fetchone()
    if not ga_request:
        conn.close()
        flash('❌ ไม่พบคำร้องที่เลือก', 'danger')
        return redirect(url_for('admin_dashboard', module='ga', ga_loc=ga_loc))

    if role == 'admin_pc1' and 'PC1' not in str(ga_request['location'] or ''):
        conn.close()
        flash('❌ คุณไม่มีสิทธิ์จัดการคำร้องนอก PC1', 'danger')
        return redirect(url_for('admin_dashboard', module='ga', ga_loc=ga_loc))
    if role == 'admin_cc' and not is_cc_location_value(ga_request['location']):
        conn.close()
        flash('❌ คุณไม่มีสิทธิ์จัดการคำร้องนอก CC', 'danger')
        return redirect(url_for('admin_dashboard', module='ga', ga_loc=ga_loc))

    handled_by = session.get('admin_name') or session.get('admin_username') or 'Admin'
    updated_at = get_thailand_time().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('''
        UPDATE ga_requests
        SET status = ?, admin_note = ?, handled_by = ?, updated_at = ?
        WHERE id = ?
    ''', (new_status, admin_note, handled_by, updated_at, request_id))
    conn.commit()

    requester_email = normalize_email_value(ga_request['requester_email_snapshot'])
    if not requester_email:
        user_row = conn.execute('SELECT email FROM users WHERE emp_id = ?', (ga_request['emp_id'],)).fetchone()
        requester_email = normalize_email_value(user_row['email']) if user_row and user_row['email'] else ''

    if requester_email and is_valid_email_address(requester_email):
        send_email_message(
            subject=f"[PCM] อัปเดตสถานะ GA Request - {ga_request['title']}",
            body=build_ga_status_email_body(
                {
                    'id': ga_request['id'],
                    'title': ga_request['title'],
                    'handled_by': handled_by,
                },
                new_status,
                admin_note
            ),
            html_body=build_ga_status_email_html(
                {
                    'id': ga_request['id'],
                    'title': ga_request['title'],
                    'handled_by': handled_by,
                    'emp_id': ga_request['emp_id'],
                },
                new_status,
                admin_note
            ),
            recipients=[requester_email]
        )

    conn.close()
    flash('✅ อัปเดตสถานะ GA Request เรียบร้อยแล้ว', 'success')
    return redirect(url_for('admin_dashboard', module='ga', ga_loc=ga_loc))

@app.route('/cron/daily_alert', methods=['POST'])
def daily_alert():
    if not session.get('admin_logged_in'):
        return "Unauthorized", 401

    conn = get_db_connection()

    # --- ส่วนที่ 1: เช็คของใกล้หมดอายุ (ภายใน 30 วัน) จาก product_lots ---
    expiry_query = '''
        SELECT p.name, p.category, p.location,
            CASE
                WHEN pl.expiry_date LIKE '%/%/%' THEN substr(pl.expiry_date,7,4)||'-'||substr(pl.expiry_date,4,2)||'-'||substr(pl.expiry_date,1,2)
                ELSE trim(pl.expiry_date)
            END AS formatted_expiry
        FROM product_lots pl
        JOIN products p ON pl.product_id = p.id
        WHERE pl.qty > 0
        AND pl.expiry_date IS NOT NULL AND trim(pl.expiry_date) != ''
        AND (p.category LIKE '%ยา%' OR p.name LIKE '%Helmet%' OR p.name LIKE '%Coffee%' OR p.name LIKE '%Tea%')
        AND (
            CASE
                WHEN pl.expiry_date LIKE '%/%/%' THEN substr(pl.expiry_date,7,4)||'-'||substr(pl.expiry_date,4,2)||'-'||substr(pl.expiry_date,1,2)
                ELSE trim(pl.expiry_date)
            END
        ) <= date('now', '+7 hours', '+30 days')
        ORDER BY formatted_expiry ASC
    '''
    expiring_items = conn.execute(expiry_query).fetchall()

    # --- ส่วนที่ 2: เช็คหมวกเซฟตี้ครบ 2 ปี (ย้อนหลัง 23 เดือนขึ้นไป) ---
    helmet_query = '''
        SELECT u.name as emp_name, u.department, u.location, p.name as product_name, l.timestamp
        FROM transaction_logs l
        JOIN users u ON l.emp_id = u.emp_id
        JOIN products p ON l.product_id = p.id
        WHERE (p.name LIKE '%หมวก%' OR p.name LIKE '%Helmet%' OR l.action LIKE '%หมวก%')
        AND l.status = 'Approved'
        AND l.timestamp <= datetime('now', '+7 hours', '-23 months')
    '''
    helmet_alerts = conn.execute(helmet_query).fetchall()
    conn.close()

    # --- ส่วนที่ 3: จัดกลุ่มแยก CC / PC1 แล้วส่งแยกกัน ---
    def is_cc_location(loc):
        loc = str(loc or '').lower()
        return 'coil center' in loc or loc == 'cc' or ' cc' in f' {loc}'

    def is_pc1_location(loc):
        return 'pc1' in str(loc or '').lower()

    sent_messages = []

    for location_label, location_check in [('CC', is_cc_location), ('PC1', is_pc1_location)]:
        location_key = 'cc' if location_label == 'CC' else 'pc1'
        msg = ""

        loc_expiry = [i for i in expiring_items if location_check(i['location'])]
        loc_helmets = [h for h in helmet_alerts if location_check(h['location'])]

        if loc_expiry:
            msg += f"⚠️ [{location_label}] แจ้งเตือนของใกล้หมดอายุ\n"
            for item in loc_expiry:
                date_parts = item['formatted_expiry'].split('-')
                show_date = f"{date_parts[2]}/{date_parts[1]}/{date_parts[0]}" if len(date_parts) == 3 else item['formatted_expiry']
                msg += f"📦 {item['name']}\n🗓️ หมดอายุ: {show_date}\n──────────────\n"

        if loc_helmets:
            if msg:msg += "\n"
            msg += f"👷 [{location_label}] ครบกำหนดเปลี่ยนหมวกเซฟตี้\n"
            for alert in loc_helmets:
                msg += f"👤 {alert['emp_name']} ({alert['department']})\n📦 {alert['product_name']}\n📅 เบิกเมื่อ: {alert['timestamp']}\n──────────────\n"

        if msg:
            exp_payload = []
            for item in loc_expiry:
                date_parts = item['formatted_expiry'].split('-')
                show_date = f"{date_parts[2]}/{date_parts[1]}/{date_parts[0]}" if len(date_parts) == 3 else item['formatted_expiry']
                exp_payload.append({
                    'name': item['name'],
                    'category': item['category'],
                    'show_date': show_date
                })

            helmet_payload = []
            for alert in loc_helmets:
                helmet_payload.append({
                    'emp_name': alert['emp_name'],
                    'department': alert['department'],
                    'product_name': alert['product_name'],
                    'show_date': alert['timestamp']
                })

            periodic_payload = {
                'location_label': location_label,
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'expiring_items': exp_payload,
                'helmet_alerts': helmet_payload,
            }

            send_smart_notification(
                notification_type='low_stock',
                message=msg.strip(),
                location=location_label,
                email_body=build_periodic_alert_email_body(periodic_payload),
                html_body=build_periodic_alert_email_html(periodic_payload),
                admin_id='superadmin'
            )
            sent_messages.append(f"[{location_label}] sent")

    if sent_messages:
        return f"Alert sent: {'|'.join(sent_messages)}", 200
    else:
        return "No alerts today", 200

@app.route('/api/admin/pending_requests')
def get_pending_requests():
    if not session.get('admin_logged_in'):
        return "Unauthorized", 401
    
    role = session.get('admin_role', 'superadmin')
    receive_mode = request.args.get('receive_mode', '').strip().lower()
    if receive_mode not in ('', 'immediate', 'scheduled'):
        receive_mode = ''
    conn = get_db_connection()
    
    # กรองตามสิทธิ์ Admin (PC1 / CC)
    role_log_filter = ""
    if role == 'admin_pc1':
        role_log_filter = " AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')"
    elif role == 'admin_cc':
        role_log_filter = " AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')"

    receive_filter = ""
    if receive_mode == 'immediate':
        receive_filter = " AND COALESCE(l.request_receive_mode, 'immediate') = 'immediate'"
    elif receive_mode == 'scheduled':
        receive_filter = " AND COALESCE(l.request_receive_mode, 'immediate') = 'scheduled'"

    query = f'''
        SELECT l.*, u.name as emp_name, u.department,
               p.name as product_name, p.unit, p.category, p.base_unit, p.package_unit, p.conversion_rate
        FROM transaction_logs l
        LEFT JOIN users u ON l.emp_id = u.emp_id
        LEFT JOIN products p ON l.product_id = p.id
        WHERE l.status = 'Pending' {role_log_filter} {receive_filter}
        ORDER BY l.timestamp ASC
    '''
    
    pending_logs = conn.execute(query).fetchall()
    conn.close()
    
    # ส่งกลับเป็น HTML เฉพาะส่วนของแถวตาราง (Partial)
    return render_template('pending_requests_partial.html', pending_logs=pending_logs)

@app.route('/admin/approve/<int:log_id>', methods=['POST']) # ฟังก์ชันนี้จะถูกเรียกเมื่อแอดมินกดอนุมัติการเบิก
def approve_request(log_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('index'))

    role = session.get('admin_role', 'superadmin')
    conn = get_db_connection()

    try:
        start_write_transaction(conn)

        if role == 'admin_pc1':
            permission_check = conn.execute('''
                SELECT l.id FROM transaction_logs l
                LEFT JOIN users u ON l.emp_id = u.emp_id
                WHERE l.id = ? AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')
            ''', (log_id,)).fetchone()
            if not permission_check:
                flash('❌ คุณไม่มีสิทธิ์อนุมัติรายการนี้', 'danger')
                return redirect(url_for('admin_dashboard', module='stock'))
        elif role == 'admin_cc':
            permission_check = conn.execute('''
                SELECT l.id FROM transaction_logs l
                LEFT JOIN users u ON l.emp_id = u.emp_id
                WHERE l.id = ? AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')
            ''', (log_id,)).fetchone()
            if not permission_check:
                flash('❌ คุณไม่มีสิทธิ์อนุมัติรายการนี้', 'danger')
                return redirect(url_for('admin_dashboard', module='stock'))

        log = conn.execute('SELECT * FROM transaction_logs WHERE id=? AND status = "Pending"', (log_id,)).fetchone()
        if not log:
            flash('⚠️ รายการนี้ถูกดำเนินการไปแล้วหรือไม่พบข้อมูล', 'warning')
            return redirect(url_for('admin_dashboard', module='stock'))

        product_id = log['product_id']
        qty_to_withdraw = int(log['qty'] or 0)
        
        # ตรวจสอบว่าเป็นยาแบบแยกหน่วยหรือไม่
        product_info_for_approve = conn.execute(
            'SELECT base_unit, package_unit, conversion_rate FROM products WHERE id = ?', (product_id,)
        ).fetchone()
        is_split_med = (
            product_info_for_approve and
            product_info_for_approve['base_unit'] and
            product_info_for_approve['package_unit'] and
            (product_info_for_approve['conversion_rate'] or 1) > 1
        )
        
        lot_withdraw_qty = qty_to_withdraw
        if is_split_med:
            lot_withdraw_qty = int(log['qty_base_unit'] or 0)
            if lot_withdraw_qty <= 0:
                conv = int(product_info_for_approve['conversion_rate'] or 1)
                lot_withdraw_qty = qty_to_withdraw * max(1, conv)

        lots = conn.execute('''
            SELECT * FROM product_lots 
            WHERE product_id = ? AND qty > 0 
            ORDER BY received_date ASC, id ASC
        ''', (product_id,)).fetchall()

        remaining = lot_withdraw_qty
        last_lot_id = None

        for lot in lots:
            if remaining <= 0:
                break

            take = min(lot['qty'], remaining)
            conn.execute('UPDATE product_lots SET qty = qty - ? WHERE id = ?', (take, lot['id']))
            remaining -= take
            last_lot_id = lot['id']

        if remaining > 0:
            conn.rollback()
            flash('❌ จำนวนของใน lot ไม่พอสำหรับการอนุมัติ', 'danger')
            return redirect(url_for('admin_dashboard', module='stock'))

        thai_now = get_thailand_time().strftime('%d/%m/%Y %H:%M:%S')
        update_result = conn.execute('''
            UPDATE transaction_logs 
            SET status = "Approved", lot_id = ?, timestamp = ? 
            WHERE id = ? AND status = "Pending"
        ''', (last_lot_id, thai_now, log_id))

        if update_result.rowcount == 0:
            conn.rollback()
            flash('⚠️ รายการนี้ถูกดำเนินการไปแล้ว', 'warning')
            return redirect(url_for('admin_dashboard', module='stock'))

        # สำหรับยาแบบแยกหน่วย: products.stock ถูกลดแล้วตอน confirm (apply_withdrawal)
        # สำหรับรายการอื่น: ต้องลด products.stock ตอน approve และปลด reservation พร้อมกัน
        if not is_split_med:
            stock_update = conn.execute(
                'UPDATE products SET stock = MAX(0, stock - ?), reserved_stock = MAX(0, reserved_stock - ?) WHERE id = ? AND stock >= ?',
                (qty_to_withdraw, qty_to_withdraw, product_id, qty_to_withdraw)
            )
            if stock_update.rowcount == 0:
                conn.rollback()
                flash('❌ สต็อกปัจจุบันไม่พอสำหรับการอนุมัติ', 'danger')
                return redirect(url_for('admin_dashboard', module='stock'))
        conn.execute('UPDATE products SET withdraw = withdraw + ? WHERE id = ?', (qty_to_withdraw, product_id))
        conn.commit()

        check_safety_alert(product_id)

        user_info = conn.execute('SELECT name, department, location FROM users WHERE emp_id = ?', (log['emp_id'],)).fetchone()
        product_info = conn.execute('SELECT name, unit, base_unit, package_unit, conversion_rate, category FROM products WHERE id = ?', (product_id,)).fetchone()
        admin_label = 'Admin CC' if role == 'admin_cc' else ('Admin PC1' if role == 'admin_pc1' else 'Super Admin')
        is_split_medicine_log = is_split_tablet_medicine(product_info) and log['qty_base_unit']
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
        # 📧 ส่งแจ้งเตือนผ่าน EMAIL & LINE ตามการตั้งค่า
        send_smart_notification(
            notification_type='approval',
            message=approval_message,
            location=(user_info['location'] if user_info else ''),
            role=role,
            email_body=build_approval_email_body({
                'requester_name': user_info['name'] if user_info else log['emp_id'],
                'department': user_info['department'] if user_info else '-',
                'location': user_info['location'] if user_info else '-',
                'product_name': product_info['name'] if product_info else product_id,
                'qty': approved_qty,
                'unit': approved_unit,
                'approver': admin_label,
                'approved_at': thai_now,
            }),
            html_body=build_approval_email_html({
                'requester_name': user_info['name'] if user_info else log['emp_id'],
                'department': user_info['department'] if user_info else '-',
                'location': user_info['location'] if user_info else '-',
                'product_name': product_info['name'] if product_info else product_id,
                'qty': approved_qty,
                'unit': approved_unit,
                'approver': admin_label,
                'approved_at': thai_now,
            }),
            admin_id='superadmin'
        )

        flash('✅ อนุมัติและตัดสต็อกแบบ FIFO เรียบร้อยแล้ว', 'success')
        return redirect(url_for('admin_dashboard', module='stock'))

    except Exception as e:
        conn.rollback()
        print(f'Approve request error: {e}')
        flash('❌ ไม่สามารถอนุมัติรายการได้ กรุณาลองใหม่', 'danger')
        return redirect(url_for('admin_dashboard', module='stock'))
    finally:
        conn.close()

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
        # 📧 ส่งแจ้งเตือนผ่าน EMAIL & LINE ตามการตั้งค่า
        send_smart_notification(
            notification_type='low_stock',
            message=alert_msg,
            location=(product['location'] if product and 'location' in product.keys() else ''),
            email_body=build_low_stock_email_body({
                'product_name': product['name'],
                'stock': product['stock'],
                'safety_stock': product['safety_stock'],
                'unit': product['unit'],
                'location': product['location'] if 'location' in product.keys() else '-',
            }),
            html_body=build_low_stock_email_html({
                'product_name': product['name'],
                'stock': product['stock'],
                'safety_stock': product['safety_stock'],
                'unit': product['unit'],
                'location': product['location'] if 'location' in product.keys() else '-',
            }),
            admin_id='superadmin'
        )

@app.route('/admin/reject/<int:log_id>', methods=['POST'])
def reject_request(log_id):
    if not session.get('admin_logged_in'):
        return redirect(url_for('index'))

    role = session.get('admin_role', 'superadmin')
    conn = get_db_connection()

    try:
        start_write_transaction(conn)

        if role == 'admin_pc1':
            permission_check = conn.execute('''
                SELECT l.id FROM transaction_logs l
                LEFT JOIN users u ON l.emp_id = u.emp_id
                WHERE l.id = ? AND (u.location LIKE '%PC1%' OR u.department LIKE '%PC1%')
            ''', (log_id,)).fetchone()
            if not permission_check:
                flash('❌ คุณไม่มีสิทธิ์ปฏิเสธรายการนี้', 'danger')
                return redirect(url_for('admin_dashboard', module='stock'))
        elif role == 'admin_cc':
            permission_check = conn.execute('''
                SELECT l.id FROM transaction_logs l
                LEFT JOIN users u ON l.emp_id = u.emp_id
                WHERE l.id = ? AND (u.location LIKE '%Coil Center%' OR u.location LIKE '%CC%' OR u.department LIKE '%CC%')
            ''', (log_id,)).fetchone()
            if not permission_check:
                flash('❌ คุณไม่มีสิทธิ์ปฏิเสธรายการนี้', 'danger')
                return redirect(url_for('admin_dashboard', module='stock'))

        log = conn.execute('SELECT * FROM transaction_logs WHERE id=? AND status = "Pending"', (log_id,)).fetchone()
        if not log:
            flash('⚠️ รายการนี้ถูกดำเนินการไปแล้วหรือไม่พบข้อมูล', 'warning')
            return redirect(url_for('admin_dashboard', module='stock'))

        product = conn.execute('SELECT * FROM products WHERE id = ?', (log['product_id'],)).fetchone()
        is_medicine = is_split_tablet_medicine(product) if product else False

        if is_medicine:
            conn.execute('UPDATE products SET reserved_stock = MAX(0, reserved_stock - ?) WHERE id = ?', (log['qty'], log['product_id']))
        else:
            conn.execute('UPDATE products SET reserved_stock = MAX(0, reserved_stock - ?) WHERE id = ?', (log['qty'], log['product_id']))

        update_result = conn.execute('UPDATE transaction_logs SET status = "Rejected" WHERE id = ? AND status = "Pending"', (log_id,))
        if update_result.rowcount == 0:
            conn.rollback()
            flash('⚠️ รายการนี้ถูกดำเนินการไปแล้ว', 'warning')
            return redirect(url_for('admin_dashboard', module='stock'))

        conn.commit()
        flash('❌ ปฏิเสธรายการเรียบร้อยแล้ว', 'warning')
        return redirect(url_for('admin_dashboard', module='stock'))

    except Exception as e:
        conn.rollback()
        print(f'Reject request error: {e}')
        flash('❌ ไม่สามารถปฏิเสธรายการได้ กรุณาลองใหม่', 'danger')
        return redirect(url_for('admin_dashboard', module='stock'))
    finally:
        conn.close()


@app.route('/admin/cancel_scheduled_withdrawal/<int:log_id>', methods=['POST'])
def cancel_scheduled_withdrawal(log_id):
    """ยกเลิกการเบิกล่วงหน้าที่อนุมัติแล้ว (คืนสต็อก)"""
    if not session.get('admin_logged_in'):
        return redirect(url_for('index'))

    if not validate_csrf_token():
        flash('❌ CSRF token ไม่ถูกต้อง', 'danger')
        return redirect(url_for('admin_dashboard', module='stock'))

    conn = get_db_connection()
    try:
        start_write_transaction(conn)

        log = conn.execute(
            '''SELECT l.*, p.base_unit, p.package_unit, p.conversion_rate
               FROM transaction_logs l
               LEFT JOIN products p ON l.product_id = p.id
               WHERE l.id = ? AND l.status = 'Approved' AND l.request_receive_mode = 'scheduled'
               AND l.requested_receive_at >= datetime('now', '+7 hours')''',
            (log_id,)
        ).fetchone()

        if not log:
            flash('⚠️ ไม่พบรายการ หรือรายการนี้ไม่สามารถยกเลิกได้', 'warning')
            return redirect(url_for('admin_dashboard', module='stock'))

        product_id = log['product_id']
        qty_to_restore = int(log['qty'] or 0)

        product = conn.execute('SELECT * FROM products WHERE id = ?', (product_id,)).fetchone()
        is_split_med = is_split_tablet_medicine(product) if product else False

        lot_restore_qty = qty_to_restore
        if is_split_med:
            lot_restore_qty = int(log['qty_base_unit'] or 0) or qty_to_restore

        # คืนสต็อกสินค้า
        conn.execute(
            'UPDATE products SET stock = stock + ?, withdraw = MAX(0, withdraw - ?) WHERE id = ?',
            (qty_to_restore, qty_to_restore, product_id)
        )

        # คืนสต็อกใน lot (ถ้ามี lot_id)
        if log['lot_id']:
            conn.execute(
                'UPDATE product_lots SET qty = qty + ? WHERE id = ?',
                (lot_restore_qty, log['lot_id'])
            )

        # อัปเดตสถานะเป็น Cancelled
        update_result = conn.execute(
            "UPDATE transaction_logs SET status = 'Cancelled' WHERE id = ? AND status = 'Approved'",
            (log_id,)
        )
        if update_result.rowcount == 0:
            conn.rollback()
            flash('⚠️ ไม่สามารถยกเลิกได้ กรุณาลองใหม่', 'warning')
            return redirect(url_for('admin_dashboard', module='stock'))

        conn.commit()
        flash('✅ ยกเลิกการเบิกล่วงหน้าเรียบร้อย สต็อกได้รับการคืนแล้ว', 'success')
        return redirect(url_for('admin_dashboard', module='stock'))

    except Exception as e:
        conn.rollback()
        print(f'Cancel scheduled withdrawal error: {e}')
        flash('❌ เกิดข้อผิดพลาด กรุณาลองใหม่', 'danger')
        return redirect(url_for('admin_dashboard', module='stock'))
    finally:
        conn.close()


@app.route('/admin/reschedule_withdrawal/<int:log_id>', methods=['POST'])
def reschedule_withdrawal(log_id):
    """เปลี่ยนวันรับของของการเบิกล่วงหน้าที่อนุมัติแล้ว"""
    if not session.get('admin_logged_in'):
        return redirect(url_for('index'))

    if not validate_csrf_token():
        flash('❌ CSRF token ไม่ถูกต้อง', 'danger')
        return redirect(url_for('admin_dashboard', module='stock'))

    new_date_str = request.form.get('new_receive_at', '').strip()
    if not new_date_str:
        flash('⚠️ กรุณาระบุวันที่และเวลาใหม่', 'warning')
        return redirect(url_for('admin_dashboard', module='stock'))

    try:
        new_dt = datetime.strptime(new_date_str, '%Y-%m-%dT%H:%M')
        if new_dt <= get_thailand_time():
            flash('⚠️ วันที่ใหม่ต้องเป็นเวลาในอนาคต', 'warning')
            return redirect(url_for('admin_dashboard', module='stock'))
        new_receive_at = new_dt.strftime('%Y-%m-%d %H:%M:%S')
    except ValueError:
        flash('⚠️ รูปแบบวันที่ไม่ถูกต้อง', 'warning')
        return redirect(url_for('admin_dashboard', module='stock'))

    conn = get_db_connection()
    try:
        start_write_transaction(conn)

        log = conn.execute(
            "SELECT id FROM transaction_logs WHERE id = ? AND status = 'Approved' AND request_receive_mode = 'scheduled'",
            (log_id,)
        ).fetchone()

        if not log:
            flash('⚠️ ไม่พบรายการ หรือรายการนี้ไม่สามารถแก้ไขได้', 'warning')
            return redirect(url_for('admin_dashboard', module='stock'))

        conn.execute(
            'UPDATE transaction_logs SET requested_receive_at = ? WHERE id = ?',
            (new_receive_at, log_id)
        )
        conn.commit()
        flash(f'✅ เปลี่ยนวันรับของเป็น {new_dt.strftime("%d/%m/%Y %H:%M")} น. เรียบร้อย', 'success')
        return redirect(url_for('admin_dashboard', module='stock'))

    except Exception as e:
        conn.rollback()
        print(f'Reschedule withdrawal error: {e}')
        flash('❌ เกิดข้อผิดพลาด กรุณาลองใหม่', 'danger')
        return redirect(url_for('admin_dashboard', module='stock'))
    finally:
        conn.close()


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
        'IT อุปกรณ์': 'IT',
        'อื่นๆ': 'ITEM'
    }
    cat_prefix = prefix_map.get(category, 'ITEM') # ถ้าหาไม่เจอให้ใช้ ITEM
    loc_prefix = 'PC1' if 'PC1' in location else 'CC'

    conn = get_db_connection()
    # 2. ค้นหารหัสล่าสุดจาก prefix ของรหัส (ไม่พึ่ง category text ใน DB)
    code_pattern = f"{cat_prefix}-{loc_prefix}-%"
    row = conn.execute(
        "SELECT code FROM products WHERE code LIKE ? ORDER BY LENGTH(code) DESC, code DESC LIMIT 1",
        (code_pattern,)
    ).fetchone()
    conn.close()

    # 3. คำนวณเลขถัดไป
    next_number = 1
    if row and row['code']:
        last_code = row['code']
        # รหัสเก่าคือ MAID-PC1-001 หรือ IT-PC1-010 — แยกเอาส่วนตัวเลขท้ายสุด
        parts = last_code.split('-')
        if parts and parts[-1].isdigit():
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
        conn.execute(
            """
            UPDATE products
            SET is_active = ?,
                status = CASE WHEN ? = 1 THEN 'Active' ELSE 'Inactive' END
            WHERE id = ?
            """,
            (new_status, new_status, product_id)
        )
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'new_status': new_status})
    conn.close()
    return jsonify({'success': False, 'message': 'ไม่พบของ'})

@app.route('/admin/add_product', methods=['POST'])
def add_product():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    admin_name = 'ADMIN:' + session.get('admin_name', 'Unknown')
    
    code = clean_input_text(request.form.get('code'), 40).upper()
    name = clean_input_text(request.form.get('name'), 150)
    category = clean_input_text(request.form.get('category'), 60)
    unit = clean_input_text(request.form.get('unit'), 30)
    location = normalize_location_value(request.form.get('location'))
    safety_stock = max(0, request.form.get('safety_stock', 0, type=int) or 0)
    stock = max(0, request.form.get('stock', 0, type=int) or 0)
    expiry_date = standardize_date(request.form.get('expiry_date', ''))
    package_unit = clean_input_text(request.form.get('package_unit', ''), 30) or None
    base_unit = clean_input_text(request.form.get('base_unit', ''), 30) or None
    conversion_rate = max(1, request.form.get('conversion_rate', 1, type=int) or 1)
    base_unit_to_tablet_rate = max(0, request.form.get('base_unit_to_tablet_rate', 0, type=int) or 0)
    split_mode = clean_input_text(request.form.get('split_mode', 'single'), 20).lower() or 'single'
    split_enabled = str(request.form.get('split_enabled', '0')).strip().lower() in ('1', 'true', 'on', 'yes')
    package_tablet_total = max(0, request.form.get('package_tablet_total', 0, type=int) or 0)
    open_base_qty = max(0, request.form.get('open_base_qty', 0, type=int) or 0)
    open_extra_tablet_qty = max(0, request.form.get('open_extra_tablet_qty', 0, type=int) or 0)

    if split_mode not in ('single', 'multi'):
        split_mode = 'single'

    if category == 'ยา' and split_enabled:
        package_unit = package_unit or unit
        if split_mode == 'multi':
            if not package_unit:
                return jsonify({'success': False, 'message': 'กรุณาระบุหน่วยนำเข้า เช่น กระปุก/ขวด/แผง'}), 400
            if not base_unit:
                return jsonify({'success': False, 'message': 'กรุณาระบุหน่วยแยก เช่น ซอง/ห่อ'}), 400
            if package_tablet_total <= 0:
                return jsonify({'success': False, 'message': f'กรุณาระบุ 1 {package_unit} มีกี่เม็ด'}), 400
            if base_unit_to_tablet_rate <= 0:
                return jsonify({'success': False, 'message': f'กรุณาระบุ 1 {base_unit} มีกี่เม็ด'}), 400
            if package_tablet_total < base_unit_to_tablet_rate:
                return jsonify({'success': False, 'message': 'จำนวนเม็ดต่อหน่วยแยกมากกว่าจำนวนเม็ดต่อหน่วยนำเข้า'}), 400
            conversion_rate = max(1, package_tablet_total // base_unit_to_tablet_rate)
        else:
            # single layer: ถ้าระบุเม็ดต่อหน่วยนำเข้า ให้ตั้งเป็นหน่วยเม็ดโดยตรง
            if package_tablet_total > 0:
                base_unit = 'เม็ด'
                conversion_rate = max(1, package_tablet_total)
                base_unit_to_tablet_rate = 0
        if package_tablet_total <= 0:
            if base_unit_to_tablet_rate > 0:
                package_tablet_total = conversion_rate * base_unit_to_tablet_rate
            elif str(base_unit or '').strip().lower() in {'เม็ด', 'tablet', 'tablets', 'pill', 'pills', 'capsule', 'capsules'}:
                package_tablet_total = conversion_rate

    # Only store split-unit info when both package_unit and base_unit are provided
    if not split_enabled or not package_unit or not base_unit:
        package_unit = None
        base_unit = None
        conversion_rate = 1
        base_unit_to_tablet_rate = 0
        package_tablet_total = 0
        open_base_qty = 0
        open_extra_tablet_qty = 0

    if category == 'ยา' and split_enabled and split_mode == 'multi' and package_unit and base_unit and base_unit != 'เม็ด' and base_unit_to_tablet_rate <= 0:
        return jsonify({'success': False, 'message': f'กรุณาระบุ 1 {base_unit} มีกี่เม็ด'}), 400

    if not re.fullmatch(r'[A-Z0-9_-]{2,40}', code):
        return jsonify({'success': False, 'message': 'รหัสของไม่ถูกต้อง'}), 400
    if not name or not category or not unit:
        return jsonify({'success': False, 'message': 'กรุณากรอกข้อมูลของให้ครบ'}), 400
    if location not in ('PC1', 'Coil Center', 'General'):
        return jsonify({'success': False, 'message': 'สถานที่เก็บไม่ถูกต้อง'}), 400

    conn = get_db_connection()

    try:
        # 2. เพิ่มของลงตารางหลัก พร้อมบันทึกข้อมูล Lot ถ้ามี
        if stock > 0:
            from datetime import datetime
            lot_number = datetime.now().strftime('%d%m%Y') + "-NEW"
            receive_date = datetime.now().strftime('%Y-%m-%d')
            cursor = conn.execute('''
                INSERT INTO products (code, name, category, unit, location, safety_stock, stock, expiry_date, lot_no, received_date, package_unit, base_unit, conversion_rate, base_unit_to_tablet_rate, package_tablet_total)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (code, name, category, unit, location, safety_stock, stock, expiry_date, lot_number, receive_date, package_unit, base_unit, conversion_rate, base_unit_to_tablet_rate, package_tablet_total))
        else:
            cursor = conn.execute('''
                INSERT INTO products (code, name, category, unit, location, safety_stock, stock, expiry_date, package_unit, base_unit, conversion_rate, base_unit_to_tablet_rate, package_tablet_total)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (code, name, category, unit, location, safety_stock, stock, '', package_unit, base_unit, conversion_rate, base_unit_to_tablet_rate, package_tablet_total))
        
        product_id = cursor.lastrowid

        if package_unit and base_unit and conversion_rate > 1 and (open_base_qty > 0 or open_extra_tablet_qty > 0):
            conn.execute(
                '''
                INSERT INTO open_packages (product_id, base_unit_qty, extra_tablet_qty, status)
                VALUES (?, ?, ?, ?)
                ''',
                (product_id, open_base_qty, open_extra_tablet_qty, 'active')
            )

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
        return jsonify({'success': False, 'message': 'รหัสของชิ้นนี้มีซ้ำในระบบแล้ว'}), 400
    except Exception as e:
        conn.rollback()
        print(f'Add product error: {e}')
        return jsonify({'success': False, 'message': 'ไม่สามารถบันทึกข้อมูลของได้'}), 500
    finally:
        conn.close()

@app.route('/admin/reset_lock', methods=['POST'])
def reset_lock():
    if not session.get('admin_logged_in'): return redirect(url_for('index'))
    conn = get_db_connection()
    conn.execute('UPDATE users SET is_locked = 0')
    conn.commit()
    conn.close()
    flash('✅ ปลดล็อกพนักงานทุกคนเรียบร้อยแล้ว', 'success')
    return redirect(url_for('admin_dashboard', module='stock'))

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
        
    # 2. Query ดึงข้อมูลของและ Lot ที่เกี่ยวข้อง (GROUP BY เพื่อไม่ให้แถวซ้ำกรณีมีหลาย Lot)
    query = f'''
        SELECT 
            p.code as 'รหัสของ',
            p.name as 'ชื่อของ',
            p.category as 'หมวดหมู่',
            p.unit as 'หน่วยนับ',
            p.location as 'สถานที่เก็บ (Location)',
            p.safety_stock as 'จุดสั่งซื้อ (Safety Stock)',
            p.stock as 'จำนวนคงเหลือ',
            COALESCE(p.package_unit, '') as 'หน่วยหลัก',
            COALESCE(p.base_unit, '') as 'หน่วยย่อย',
            COALESCE(p.conversion_rate, 1) as 'อัตราแบ่ง',
            COALESCE(p.package_tablet_total, 0) as '1 หน่วยนำเข้า = กี่เม็ด',
            COALESCE(p.base_unit_to_tablet_rate, 0) as '1 หน่วยย่อย = กี่เม็ด',
            COALESCE(op.base_unit_qty, 0) as 'หน่วยย่อยที่เปิดแล้ว',
            COALESCE(op.extra_tablet_qty, 0) as 'เศษเม็ดที่เปิดแล้ว',
            CASE WHEN p.is_active = 1 THEN 'เปิดใช้งาน' ELSE 'ปิดใช้งาน' END as 'สถานะการใช้งาน',
            CASE
                WHEN COUNT(pl.id) > 1 THEN 'หลาย Lot'
                ELSE COALESCE(MAX(pl.lot_number), p.lot_no, '')
            END as 'Lot No.',
            COALESCE(MIN(pl.received_date), p.received_date, '') as 'วันที่รับเข้า',
            CASE
                WHEN COUNT(pl.id) > 1 THEN MIN(pl.expiry_date)
                ELSE COALESCE(MAX(pl.expiry_date), p.expiry_date, '')
            END as 'วันหมดอายุ',
            COALESCE(SUM(pl.qty), 0) as 'จำนวนใน Lot'
        FROM products p
        LEFT JOIN product_lots pl ON pl.product_id = p.id AND pl.qty > 0
        LEFT JOIN (
            SELECT product_id,
                   COALESCE(SUM(base_unit_qty), 0) as base_unit_qty,
                   COALESCE(SUM(extra_tablet_qty), 0) as extra_tablet_qty
            FROM open_packages WHERE status = 'active'
            GROUP BY product_id
        ) op ON op.product_id = p.id
        {location_filter}
        GROUP BY p.id
        ORDER BY p.location ASC, p.code ASC
    '''
    
    # อ่านข้อมูลเข้า Pandas
    df = pd.read_sql_query(query, conn)
    conn.close()

    def _auto_col_widths(writer, sheet_name, df_sheet):
        """ปรับความกว้างคอลัมน์อัตโนมัติ"""
        worksheet = writer.sheets[sheet_name]
        for i, col in enumerate(df_sheet.columns):
            header_len = len(str(col))
            data_len = df_sheet[col].astype(str).str.len().max() if len(df_sheet) > 0 else 0
            data_len = 0 if pd.isna(data_len) else data_len
            worksheet.set_column(i, i, int(max(header_len, data_len) + 2))

    # 3. สร้างไฟล์ Excel ใน Memory แยก Sheet ตามหมวดหมู่ (ไม่มี Sheet "ทั้งหมด")
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        medicine_columns = [
            'หน่วยหลัก',
            'หน่วยย่อย',
            'อัตราแบ่ง',
            '1 หน่วยนำเข้า = กี่เม็ด',
            '1 หน่วยย่อย = กี่เม็ด',
            'หน่วยย่อยที่เปิดแล้ว',
            'เศษเม็ดที่เปิดแล้ว',
        ]

        first_data_row = 1
        last_data_row = 5000

        list_columns = {
            'หมวดหมู่': ['ยา', 'แม่บ้าน', 'Safety', 'ของใช้สำนักงาน', 'IT อุปกรณ์', 'อื่นๆ'],
            'สถานที่เก็บ (Location)': ['PC1', 'Coil Center', 'General', 'CC', 'ห้องยา'],
            'สถานะการใช้งาน': ['เปิดใช้งาน', 'ปิดใช้งาน'],
        }

        integer_columns = [
            'จุดสั่งซื้อ (Safety Stock)',
            'จำนวนคงเหลือ',
            'อัตราแบ่ง',
            '1 หน่วยนำเข้า = กี่เม็ด',
            '1 หน่วยย่อย = กี่เม็ด',
            'หน่วยย่อยที่เปิดแล้ว',
            'เศษเม็ดที่เปิดแล้ว',
            'จำนวนใน Lot',
        ]

        workbook = writer.book
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#DCEBFF',
            'font_color': '#123A66',
            'border': 1,
            'align': 'center',
            'valign': 'vcenter'
        })
        medicine_header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#E8F8EE',
            'font_color': '#0F5F3A',
            'border': 1,
            'align': 'center',
            'valign': 'vcenter'
        })
        normal_cell_format = workbook.add_format({'border': 1})
        medicine_cell_format = workbook.add_format({'bg_color': '#F4FCF7', 'border': 1})

        def _decorate_sheet(sheet_name, df_sheet, is_medicine=False):
            ws = writer.sheets[sheet_name]
            headers = list(df_sheet.columns)
            col_map = {name: idx for idx, name in enumerate(headers)}

            # สีหัวตารางและสีคอลัมน์หน่วยยา
            for idx, col_name in enumerate(headers):
                head_fmt = medicine_header_format if col_name in medicine_columns else header_format
                ws.write(0, idx, col_name, head_fmt)
                if col_name in medicine_columns:
                    ws.set_column(idx, idx, None, medicine_cell_format)
                else:
                    ws.set_column(idx, idx, None, normal_cell_format)

            if headers:
                ws.autofilter(0, 0, 0, len(headers) - 1)
            ws.freeze_panes(1, 0)

            # validation dropdown/list และเลขจำนวนเต็ม
            for col_name, options in list_columns.items():
                if col_name not in col_map:
                    continue
                col_idx = col_map[col_name]
                ws.data_validation(first_data_row, col_idx, last_data_row, col_idx, {
                    'validate': 'list',
                    'source': options,
                    'error_title': 'ข้อมูลไม่ถูกต้อง',
                    'error_message': f'กรุณาเลือกค่าในรายการที่กำหนดสำหรับคอลัมน์ {col_name}',
                })

            for col_name in integer_columns:
                if col_name not in col_map:
                    continue
                if (not is_medicine) and col_name in medicine_columns:
                    continue
                col_idx = col_map[col_name]
                ws.data_validation(first_data_row, col_idx, last_data_row, col_idx, {
                    'validate': 'integer',
                    'criteria': '>=',
                    'value': 0,
                    'error_title': 'รูปแบบตัวเลขไม่ถูกต้อง',
                    'error_message': f'{col_name} ต้องเป็นจำนวนเต็มตั้งแต่ 0 ขึ้นไป',
                })

        # ใส่ชีตคำอธิบายคอลัมน์เพื่อให้กรอกไฟล์ได้ถูกต้อง
        guide_sheet_name = 'README_IMPORT'
        guide_rows = [
            {'คอลัมน์': 'Legend: แถวพื้นเขียว', 'คำอธิบาย': 'คือคอลัมน์เฉพาะชีทยาเท่านั้น'},
            {'คอลัมน์': 'Legend: แถวพื้นขาว', 'คำอธิบาย': 'คือคอลัมน์ที่ใช้ได้ทุกชีท'},
            {'คอลัมน์': 'รหัสของ', 'คำอธิบาย': 'รหัสสินค้าต้องไม่ซ้ำ ใช้เป็น key หลักตอน import'},
            {'คอลัมน์': 'การกรอกข้อมูล', 'คำอธิบาย': 'แนะนำให้แก้ไขในชีทหมวดจริงโดยตรง (ระบบมี dropdown + validation แล้ว)'},
            {'คอลัมน์': 'จำนวนคงเหลือ', 'คำอธิบาย': 'จำนวนหน่วยแพ็ค/หน่วยหลักที่เก็บใน stock หลัก'},
            {'คอลัมน์': 'หน่วยหลัก', 'คำอธิบาย': 'หน่วยนำเข้า เช่น กระปุก/ขวด/แผง'},
            {'คอลัมน์': 'หน่วยย่อย', 'คำอธิบาย': 'หน่วยเบิกย่อย เช่น ซอง/ห่อ/เม็ด'},
            {'คอลัมน์': 'อัตราแบ่ง', 'คำอธิบาย': '1 หน่วยหลัก = กี่หน่วยย่อย'},
            {'คอลัมน์': '1 หน่วยนำเข้า = กี่เม็ด', 'คำอธิบาย': 'จำนวนเม็ดจริงต่อหน่วยหลัก (รองรับหารไม่ลงตัว)'},
            {'คอลัมน์': '1 หน่วยย่อย = กี่เม็ด', 'คำอธิบาย': 'จำนวนเม็ดต่อหน่วยย่อย เช่น ซองละ 10 เม็ด'},
            {'คอลัมน์': 'หน่วยย่อยที่เปิดแล้ว', 'คำอธิบาย': 'จำนวนหน่วยย่อยที่ค้างจากแพ็คที่เปิดแล้ว'},
            {'คอลัมน์': 'เศษเม็ดที่เปิดแล้ว', 'คำอธิบาย': 'เศษเม็ดคงเหลือจากการแตกหน่วยที่หารไม่ลงตัว'},
            {'คอลัมน์': 'สถานะการใช้งาน', 'คำอธิบาย': 'ใช้ค่า เปิดใช้งาน หรือ ปิดใช้งาน'},
            {'คอลัมน์': 'Lot No.', 'คำอธิบาย': 'ถ้าระบุจะใช้เพื่อ upsert lot เดิม'},
            {'คอลัมน์': 'วันที่รับเข้า', 'คำอธิบาย': 'รองรับรูปแบบวันที่ที่ระบบเดิมใช้อยู่'},
            {'คอลัมน์': 'วันหมดอายุ', 'คำอธิบาย': 'ถ้าไม่ระบุให้เว้นว่างได้'},
            {'คอลัมน์': 'จำนวนใน Lot', 'คำอธิบาย': 'จำนวนคงเหลือใน lot นั้นๆ'},
        ]
        df_guide = pd.DataFrame(guide_rows)
        df_guide.to_excel(writer, index=False, sheet_name=guide_sheet_name)
        _auto_col_widths(writer, guide_sheet_name, df_guide)
        _decorate_sheet(guide_sheet_name, df_guide, is_medicine=False)
        guide_ws = writer.sheets[guide_sheet_name]
        for row_idx, col_name in enumerate(df_guide['คอลัมน์'].tolist(), start=1):
            if str(col_name).strip() in medicine_columns or str(col_name).strip() == 'Legend: แถวพื้นเขียว':
                guide_ws.write(row_idx, 0, col_name, medicine_cell_format)
                guide_ws.write(row_idx, 1, df_guide.iloc[row_idx - 1]['คำอธิบาย'], medicine_cell_format)
            elif str(col_name).strip() == 'Legend: แถวพื้นขาว':
                guide_ws.write(row_idx, 0, col_name, normal_cell_format)
                guide_ws.write(row_idx, 1, df_guide.iloc[row_idx - 1]['คำอธิบาย'], normal_cell_format)

        categories = df['หมวดหมู่'].dropna().unique()
        for cat in sorted(categories):
            df_cat = df[df['หมวดหมู่'] == cat].copy()
            if df_cat.empty:
                continue
            is_medicine_cat = str(cat).strip() == 'ยา'
            if not is_medicine_cat:
                df_cat = df_cat.drop(columns=[col for col in medicine_columns if col in df_cat.columns])
            # ตัดชื่อ Sheet ให้ไม่เกิน 31 ตัวอักษร (ข้อจำกัดของ Excel)
            sheet_name = str(cat)[:31]
            df_cat.to_excel(writer, index=False, sheet_name=sheet_name)
            _auto_col_widths(writer, sheet_name, df_cat)
            _decorate_sheet(sheet_name, df_cat, is_medicine=is_medicine_cat)

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
        return redirect(url_for('admin_dashboard', module='stock'))

    filename = secure_filename(file.filename or '')
    file_ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
    if file_ext not in ALLOWED_IMPORT_EXTENSIONS:
        flash('❌ รองรับเฉพาะไฟล์ Excel .xlsx, .xlsm, .xls', 'danger')
        return redirect(url_for('admin_dashboard', module='stock'))

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

        # Business overrides: configure by real dispensing workflow (withdraw by sachet/pack)
        explicit_setups = [
            (('paracetamol', 'ไทลินอล'), ('ซอง', 'กระปุก', 25)),
            (('tablet antacid', 'แอนตาซิล'), ('ซอง', 'แผง', 3)),
            (('มะแว้ง',), ('ห่อ', 'ห่อ', 1)),
            (('มายบาซิน',), ('ซอง', 'ซอง', 1)),
            (('decolgen', 'ดีคอลเจน'), ('ซอง', 'ซอง', 1)),
            (('counterpain', 'เคาน์เตอร์เพน', 'คเตอร์เพน'), ('ตลับ', 'หลอด', 10)),
            (('anti-allergy', 'ยาแก้แพ้'), ('ซอง', 'แผง', 2)),
            (('ทางเดินปัสสาวะอักเสบ',), ('ซอง', 'แผง', 2)),
        ]
        for keywords, setup in explicit_setups:
            if any(keyword in lower_name for keyword in keywords):
                return setup

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
        open_base_col = next((col for col in df.columns if 'หน่วยย่อยที่เปิดแล้ว' in str(col) or 'เปิดแล้ว' in str(col)), None)
        open_extra_col = next((col for col in df.columns if 'เศษเม็ดที่เปิดแล้ว' in str(col) or 'extra tablet' in str(col).lower()), None)

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
            open_base = safe_int(row[open_base_col]) if open_base_col and open_base_col in row.index and pd.notna(row[open_base_col]) else None
            open_extra = safe_int(row[open_extra_col]) if open_extra_col and open_extra_col in row.index and pd.notna(row[open_extra_col]) else None
            rows_to_import.append({
                'raw_name': raw_name,
                'name': name,
                'stock': stock,
                'unit': unit,
                'open_base': open_base,
                'open_extra': open_extra
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

                if item['open_base'] is not None or item['open_extra'] is not None:
                    effective_open_base = max(0, int(item['open_base'] or 0))
                    effective_open_extra = max(0, int(item['open_extra'] or 0))
                    existing_open = conn.execute(
                        'SELECT id FROM open_packages WHERE product_id = ?',
                        (existing['id'],)
                    ).fetchone()
                    if existing_open:
                        conn.execute(
                            '''
                            UPDATE open_packages
                            SET base_unit_qty = ?,
                                extra_tablet_qty = ?,
                                status = CASE WHEN ? > 0 OR ? > 0 THEN 'active' ELSE 'closed' END
                            WHERE product_id = ?
                            ''',
                            (effective_open_base, effective_open_extra, effective_open_base, effective_open_extra, existing['id'])
                        )
                    elif effective_open_base > 0 or effective_open_extra > 0:
                        conn.execute(
                            '''
                            INSERT INTO open_packages (product_id, base_unit_qty, extra_tablet_qty, status)
                            VALUES (?, ?, ?, 'active')
                            ''',
                            (existing['id'], effective_open_base, effective_open_extra)
                        )
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

                inserted_product = conn.execute('SELECT id FROM products WHERE code = ?', (generated_code,)).fetchone()
                if inserted_product and (item['open_base'] is not None or item['open_extra'] is not None):
                    effective_open_base = max(0, int(item['open_base'] or 0))
                    effective_open_extra = max(0, int(item['open_extra'] or 0))
                    if effective_open_base > 0 or effective_open_extra > 0:
                        conn.execute(
                            '''
                            INSERT INTO open_packages (product_id, base_unit_qty, extra_tablet_qty, status)
                            VALUES (?, ?, ?, 'active')
                            ''',
                            (inserted_product['id'], effective_open_base, effective_open_extra)
                        )
                inserted_count += 1

        return updated_count, inserted_count

    conn = None
    try:
        # อ่านทุก Sheet (รองรับทั้งไฟล์ single-sheet เก่า และไฟล์ multi-sheet ใหม่)
        all_sheets = pd.read_excel(file, sheet_name=None)

        # ประมวลผลทุกชีตเพื่อรองรับทั้งกรณีแก้เฉพาะชีต "ทั้งหมด" และแก้เฉพาะชีตย่อย
        # โดยเรียงให้ชีต "ทั้งหมด" อยู่ท้ายสุด เผื่อกรณีมีการแก้ทั้งสองฝั่งพร้อมกัน
        master_sheets = []
        other_sheets = []
        for raw_sheet_name in all_sheets.keys():
            normalized_sheet_name = str(raw_sheet_name or '').strip().lower()
            if normalized_sheet_name in ('ทั้งหมด', 'all'):
                master_sheets.append(raw_sheet_name)
            else:
                other_sheets.append(raw_sheet_name)
        selected_sheets = other_sheets + master_sheets

        conn = get_db_connection()
        updated_count = 0
        inserted_count = 0
        medicine_done = False

        # Snapshot ก่อน import ใช้ตรวจว่าแถวไหนเปลี่ยนจริง เพื่อไม่ให้ชีตที่ไม่ได้แก้ทับข้อมูลที่แก้แล้ว
        baseline_products = {}
        for row in conn.execute('''
            SELECT code, name, category, unit, location, safety_stock, stock, is_active,
                   COALESCE(lot_no, '') AS lot_no,
                   COALESCE(received_date, '') AS received_date,
                   COALESCE(expiry_date, '') AS expiry_date
            FROM products
        ''').fetchall():
            baseline_products[str(row['code']).strip().upper()] = {
                'name': str(row['name'] or '').strip(),
                'category': str(row['category'] or '').strip(),
                'unit': str(row['unit'] or '').strip(),
                'location': str(row['location'] or '').strip(),
                'safety_stock': int(row['safety_stock'] or 0),
                'stock': int(row['stock'] or 0),
                'is_active': int(row['is_active'] or 0),
                'lot_no': normalize_lot_number(row['lot_no']),
                'received_date': standardize_date(row['received_date']),
                'expiry_date': standardize_date(row['expiry_date']),
            }

        baseline_lots = {}
        for row in conn.execute('''
            SELECT p.code,
                   COALESCE(pl.lot_number, '') AS lot_number,
                   COALESCE(pl.received_date, '') AS received_date,
                   COALESCE(pl.expiry_date, '') AS expiry_date,
                   COALESCE(pl.qty, 0) AS qty
            FROM product_lots pl
            JOIN products p ON p.id = pl.product_id
        ''').fetchall():
            key = (
                str(row['code'] or '').strip().upper(),
                normalize_lot_number(row['lot_number']),
                standardize_date(row['received_date'])
            )
            baseline_lots[key] = {
                'qty': int(row['qty'] or 0),
                'expiry_date': standardize_date(row['expiry_date'])
            }

        for sheet_name in selected_sheets:
            df = all_sheets[sheet_name]
            if sheet_name not in all_sheets:
                continue

            df.columns = df.columns.astype(str).str.strip()

            code_col = next((col for col in df.columns if 'รหัสของ' in col or 'code' in col.lower()), None)
            preview_text = ' '.join(
                str(v).strip()
                for v in df.head(5).fillna('').astype(str).values.flatten().tolist()
                if str(v).strip()
            ).lower()
            is_medicine_file = 'รายการ/list' in preview_text or 'medicine' in str(df.columns[0]).lower()

            if not code_col and is_medicine_file and not medicine_done:
                u, i = import_medicine_file(conn, df)
                updated_count += u
                inserted_count += i
                medicine_done = True
                continue

            if not code_col:
                # Sheet ที่ไม่มี code column → ข้ามไป
                continue

            is_medicine_sheet = str(sheet_name or '').strip() == 'ยา'

            name_col = next((col for col in df.columns if 'ชื่อของ' in col), None)
            cat_col = next((col for col in df.columns if 'หมวดหมู่' in col), None)
            unit_col = next((col for col in df.columns if 'หน่วยนับ' in col), None)
            loc_col = next((col for col in df.columns if 'สถานที่เก็บ' in col or 'location' in col.lower()), None)
            safe_col = next((col for col in df.columns if 'จุดสั่งซื้อ' in col or 'safety stock' in col.lower()), None)
            stock_col = next((col for col in df.columns if 'จำนวนคงเหลือ' in col), None)
            lot_col = next((col for col in df.columns if 'lot' in col.lower() and 'จำนวน' not in col), None)
            received_col = next((col for col in df.columns if 'วันที่รับเข้า' in col or 'received_date' in col.lower() or 'received date' in col.lower()), None)
            expiry_col = next((col for col in df.columns if 'วันหมดอายุ' in col or 'expiry_date' in col.lower() or 'expiry date' in col.lower()), None)
            lot_qty_col = next((col for col in df.columns if 'จำนวนใน lot' in col.lower() or 'lot qty' in col.lower() or 'lot quantity' in col.lower()), None)
            active_col = next((col for col in df.columns if 'สถานะ' in col), None)
            # คอลัมน์สำหรับสินค้าแยกหน่วยย่อย (ยา)
            pkg_unit_col = next((col for col in df.columns if 'หน่วยหลัก' in col), None) if is_medicine_sheet else None
            base_unit_col = next((col for col in df.columns if 'หน่วยย่อย' in col and 'เปิด' not in col), None) if is_medicine_sheet else None
            conv_rate_col = next((col for col in df.columns if 'อัตราแบ่ง' in col), None) if is_medicine_sheet else None
            package_tablet_col = next((col for col in df.columns if '1 หน่วยนำเข้า = กี่เม็ด' in col or 'เม็ดต่อหน่วยนำเข้า' in col), None) if is_medicine_sheet else None
            base_to_tablet_col = next((col for col in df.columns if '1 หน่วยย่อย = กี่เม็ด' in col or 'เม็ดต่อหน่วยย่อย' in col), None) if is_medicine_sheet else None
            open_base_col = next((col for col in df.columns if 'หน่วยย่อยที่เปิดแล้ว' in col or 'เปิดแล้ว' in col), None) if is_medicine_sheet else None
            open_extra_col = next((col for col in df.columns if 'เศษเม็ดที่เปิดแล้ว' in col or 'extra tablet' in col.lower()), None) if is_medicine_sheet else None

            for _, row in df.iterrows():
                code = str(row[code_col]).strip()
                if not code or code.lower() == 'nan':
                    continue

                name = str(row[name_col]).strip() if name_col and pd.notna(row[name_col]) else 'No Name'
                category = str(row[cat_col]).strip() if cat_col and pd.notna(row[cat_col]) else 'General'
                unit = str(row[unit_col]).strip() if unit_col and pd.notna(row[unit_col]) else 'PCS'
                location = str(row[loc_col]).strip() if loc_col and pd.notna(row[loc_col]) else '-'
                safety_stock = safe_int(row[safe_col]) if safe_col else 0
                stock = safe_int(row[stock_col]) if stock_col else 0

                lot_no = normalize_lot_number(row[lot_col]) if lot_col and pd.notna(row[lot_col]) else ''
                received_date = standardize_date(row[received_col]) if received_col and pd.notna(row[received_col]) else ''
                expiry_date = standardize_date(row[expiry_col]) if expiry_col and pd.notna(row[expiry_col]) else ''
                lot_qty = safe_int(row[lot_qty_col]) if lot_qty_col and pd.notna(row[lot_qty_col]) else None
                effective_lot_qty = stock if lot_qty is None else lot_qty

                # อ่านข้อมูลหน่วยย่อย (ถ้ามีในไฟล์)
                import_pkg_unit = str(row[pkg_unit_col]).strip() if pkg_unit_col and pd.notna(row[pkg_unit_col]) else None
                import_base_unit = str(row[base_unit_col]).strip() if base_unit_col and pd.notna(row[base_unit_col]) else None
                import_conv_rate = safe_int(row[conv_rate_col]) if conv_rate_col and pd.notna(row[conv_rate_col]) else None
                import_package_tablet = safe_int(row[package_tablet_col]) if package_tablet_col and pd.notna(row[package_tablet_col]) else None
                import_base_to_tablet = safe_int(row[base_to_tablet_col]) if base_to_tablet_col and pd.notna(row[base_to_tablet_col]) else None
                import_open_base = safe_int(row[open_base_col]) if open_base_col and pd.notna(row[open_base_col]) else None
                import_open_extra = safe_int(row[open_extra_col]) if open_extra_col and pd.notna(row[open_extra_col]) else None
                # ล้าง empty string
                if import_pkg_unit == '' or import_pkg_unit == 'nan': import_pkg_unit = None
                if import_base_unit == '' or import_base_unit == 'nan': import_base_unit = None

                is_active = 1
                if active_col and pd.notna(row[active_col]):
                    status_text = str(row[active_col]).strip()
                    is_active = 1 if status_text == 'เปิดใช้งาน' else 0

                is_medicine_row = str(category or '').strip() == 'ยา'
                split_payload_present = bool(
                    import_pkg_unit or import_base_unit or import_conv_rate is not None or
                    import_base_to_tablet is not None or import_package_tablet is not None
                )

                baseline_product = baseline_products.get(code)
                product_changed = baseline_product is None or any([
                    baseline_product['name'] != name,
                    baseline_product['category'] != category,
                    baseline_product['unit'] != unit,
                    baseline_product['location'] != location,
                    baseline_product['safety_stock'] != safety_stock,
                    baseline_product['stock'] != stock,
                    baseline_product['is_active'] != is_active,
                    baseline_product['lot_no'] != lot_no,
                    baseline_product['received_date'] != received_date,
                    baseline_product['expiry_date'] != expiry_date,
                ])

                lot_changed = False
                if lot_no or received_date:
                    lot_key = (code, lot_no, received_date)
                    baseline_lot = baseline_lots.get(lot_key)
                    if baseline_lot is None:
                        lot_changed = effective_lot_qty > 0
                    else:
                        lot_changed = (
                            baseline_lot['qty'] != effective_lot_qty or
                            baseline_lot['expiry_date'] != expiry_date
                        )

                if not product_changed and not lot_changed:
                    # ยังต้องตรวจ open_base และ split-unit fields แม้ product ไม่เปลี่ยน
                    pass

                existing = conn.execute('SELECT id FROM products WHERE code = ?', (code,)).fetchone()
                if existing:
                    # อัปเดต split-unit fields ถ้ามีในไฟล์
                    if is_medicine_row and split_payload_present:
                        effective_pkg_unit = import_pkg_unit
                        effective_base_unit = import_base_unit
                        effective_conv_rate = max(1, import_conv_rate or 1)
                        effective_base_to_tablet = max(0, import_base_to_tablet or 0)
                        effective_package_tablet = max(0, import_package_tablet or 0)
                        conn.execute('''
                            UPDATE products
                            SET name=?, stock=?, safety_stock=?, category=?, unit=?, location=?, is_active=?, lot_no=?, received_date=?, expiry_date=?,
                                package_unit=?, base_unit=?, conversion_rate=?, base_unit_to_tablet_rate=?, package_tablet_total=?
                            WHERE id=?
                        ''', (name, stock, safety_stock, category, unit, location, is_active, lot_no, received_date, expiry_date,
                              effective_pkg_unit, effective_base_unit, effective_conv_rate, effective_base_to_tablet, effective_package_tablet, existing['id']))
                    elif product_changed:
                        conn.execute('''
                            UPDATE products
                            SET name=?, stock=?, safety_stock=?, category=?, unit=?, location=?, is_active=?, lot_no=?, received_date=?, expiry_date=?
                            WHERE id=?
                        ''', (name, stock, safety_stock, category, unit, location, is_active, lot_no, received_date, expiry_date, existing['id']))
                    product_id = existing['id']
                    updated_count += 1

                    # อัปเดต open_packages ถ้ามีคอลัมน์นี้ในไฟล์
                    if is_medicine_row and open_base_col and import_open_base is not None:
                        existing_open = conn.execute(
                            'SELECT id FROM open_packages WHERE product_id=?', (product_id,)
                        ).fetchone()
                        effective_open_extra = max(0, import_open_extra or 0)
                        if existing_open:
                            conn.execute(
                                'UPDATE open_packages SET base_unit_qty=?, extra_tablet_qty=?, status=CASE WHEN ? > 0 OR ? > 0 THEN "active" ELSE "closed" END WHERE product_id=?',
                                (import_open_base, effective_open_extra, import_open_base, effective_open_extra, product_id)
                            )
                        elif import_open_base > 0 or effective_open_extra > 0:
                            conn.execute(
                                'INSERT INTO open_packages (product_id, base_unit_qty, extra_tablet_qty, status) VALUES (?, ?, ?, ?)',
                                (product_id, import_open_base, effective_open_extra, 'active')
                            )
                else:
                    cursor = conn.execute('''
                        INSERT INTO products (code, name, stock, safety_stock, category, unit, location, withdraw, reserved_stock, is_active, lot_no, received_date, expiry_date,
                                                                                         package_unit, base_unit, conversion_rate, base_unit_to_tablet_rate, package_tablet_total)
                                                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (code, name, stock, safety_stock, category, unit, location, is_active, lot_no, received_date, expiry_date,
                                                    import_pkg_unit, import_base_unit, max(1, import_conv_rate or 1), max(0, import_base_to_tablet or 0), max(0, import_package_tablet or 0)))
                    product_id = cursor.lastrowid
                    inserted_count += 1

                    if (import_open_base and import_open_base > 0) or (import_open_extra and import_open_extra > 0):
                        conn.execute(
                            'INSERT INTO open_packages (product_id, base_unit_qty, extra_tablet_qty, status) VALUES (?, ?, ?, ?)',
                            (product_id, max(0, import_open_base or 0), max(0, import_open_extra or 0), 'active')
                        )

                if not (product_changed or lot_changed or
                        (is_medicine_row and open_base_col and import_open_base is not None) or
                        (is_medicine_row and open_extra_col and import_open_extra is not None) or
                    (is_medicine_row and split_payload_present)):
                    continue

                if lot_no:
                    # มี Lot No. → upsert ด้วย lot_number
                    lot_rows = conn.execute(
                        'SELECT id, lot_number FROM product_lots WHERE product_id = ?',
                        (product_id,)
                    ).fetchall()
                    existing_lot = next((
                        lot for lot in lot_rows
                        if normalize_lot_number(lot['lot_number']) == lot_no
                    ), None)
                    if existing_lot:
                        conn.execute('''
                            UPDATE product_lots
                            SET lot_number = ?, qty = ?, received_date = ?, expiry_date = ?
                            WHERE id = ?
                        ''', (lot_no, effective_lot_qty, received_date, expiry_date, existing_lot['id']))
                    else:
                        conn.execute('''
                            INSERT INTO product_lots (product_id, lot_number, qty, received_date, expiry_date)
                            VALUES (?, ?, ?, ?, ?)
                        ''', (product_id, lot_no, effective_lot_qty, received_date, expiry_date))
                elif effective_lot_qty > 0:
                    # ไม่มี Lot No. แต่มีจำนวน → upsert ด้วย received_date (เช่น อุปกรณ์ IT)
                    existing_lot = conn.execute(
                        'SELECT id FROM product_lots WHERE product_id = ? AND (lot_number IS NULL OR lot_number = "") AND received_date = ?',
                        (product_id, received_date)
                    ).fetchone()
                    if existing_lot:
                        conn.execute(
                            'UPDATE product_lots SET qty = ?, expiry_date = ? WHERE id = ?',
                            (effective_lot_qty, expiry_date, existing_lot['id'])
                        )
                    else:
                        conn.execute('''
                            INSERT INTO product_lots (product_id, lot_number, qty, received_date, expiry_date)
                            VALUES (?, NULL, ?, ?, ?)
                        ''', (product_id, effective_lot_qty, received_date, expiry_date))

        conn.commit()
        flash(f'✅ นำเข้าสำเร็จ: อัปเดต {updated_count}, เพิ่มใหม่ {inserted_count}', 'success')
    except Exception as e:
        flash(f'❌ ผิดพลาด: {str(e)}', 'danger')
    finally:
        if conn:
            conn.close()

    return redirect(url_for('admin_dashboard', module='stock'))

# 1. ดึงข้อมูลของเดิมมาแสดงในหน้าต่างแก้ไข
@app.route('/admin/get_product/<code>')
def get_product(code):
    if not session.get('admin_logged_in'): return jsonify({'error': 'Unauthorized'}), 401
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM products WHERE code = ?', (code,)).fetchone()
    split_summary = None
    if product:
        conversion_rate = int(product['conversion_rate'] or 1)
        has_split_units = bool(product['base_unit'] and product['package_unit'] and conversion_rate > 1)
        if has_split_units:
            open_base_qty = conn.execute('''
                SELECT COALESCE(SUM(base_unit_qty), 0)
                FROM open_packages
                WHERE product_id = ? AND status = 'active'
            ''', (product['id'],)).fetchone()[0]
            open_extra_tablet_qty = conn.execute('''
                SELECT COALESCE(SUM(extra_tablet_qty), 0)
                FROM open_packages
                WHERE product_id = ? AND status = 'active'
            ''', (product['id'],)).fetchone()[0]
            lot_total_qty = conn.execute('''
                SELECT COALESCE(SUM(qty), 0)
                FROM product_lots
                WHERE product_id = ? AND qty > 0
            ''', (product['id'],)).fetchone()[0]

            stock_package_qty = int(product['stock'] or 0)
            package_tablet_total = int(product['package_tablet_total'] or 0)
            base_to_tablet_rate = int(product['base_unit_to_tablet_rate'] or 0)
            extra_base_from_open_remainder = 0
            package_remainder_tablets = 0
            total_remainder_tablets = int(open_extra_tablet_qty or 0)
            display_open_extra_tablet_qty = int(open_extra_tablet_qty or 0)
            if base_to_tablet_rate > 0:
                if package_tablet_total > 0:
                    package_remainder_tablets = stock_package_qty * (package_tablet_total % base_to_tablet_rate)
                    total_remainder_tablets = package_remainder_tablets + int(open_extra_tablet_qty or 0)
                extra_base_from_open_remainder = total_remainder_tablets // base_to_tablet_rate
                display_open_extra_tablet_qty = total_remainder_tablets % base_to_tablet_rate

            total_base_qty = (stock_package_qty * conversion_rate) + int(open_base_qty or 0) + extra_base_from_open_remainder
            if package_tablet_total <= 0 and base_to_tablet_rate > 0:
                package_tablet_total = conversion_rate * base_to_tablet_rate
            total_tablet_qty = 0
            if package_tablet_total > 0:
                total_tablet_qty = (stock_package_qty * package_tablet_total) + (int(open_base_qty or 0) * base_to_tablet_rate) + int(open_extra_tablet_qty or 0)
                if base_to_tablet_rate > 0:
                    total_base_qty = total_tablet_qty // base_to_tablet_rate
                    display_open_extra_tablet_qty = total_tablet_qty % base_to_tablet_rate
            split_summary = {
                'stock_package_qty': stock_package_qty,
                'open_base_qty': int(open_base_qty or 0),
                'open_extra_tablet_qty': int(display_open_extra_tablet_qty),
                'open_extra_tablet_qty_raw': int(open_extra_tablet_qty or 0),
                'package_remainder_tablets': int(package_remainder_tablets),
                'total_remainder_tablets': int(total_remainder_tablets),
                'extra_base_from_open_remainder': int(extra_base_from_open_remainder),
                'total_base_qty': int(total_base_qty),
                'total_tablet_qty': int(total_tablet_qty or 0),
                'lot_total_qty': int(lot_total_qty or 0)
            }
    conn.close()
    if product:
        payload = dict(product)
        conversion_rate = int(payload.get('conversion_rate') or 1)
        base_unit = str(payload.get('base_unit') or '').strip().lower()
        base_to_tablet_rate = int(payload.get('base_unit_to_tablet_rate') or 0)
        tablet_like_units = {'เม็ด', 'tablet', 'tablets', 'pill', 'pills', 'capsule', 'capsules'}
        if conversion_rate > 1 and base_to_tablet_rate > 0 and base_unit not in tablet_like_units:
            payload['split_mode'] = 'multi'
            payload['package_tablet_total'] = int(payload.get('package_tablet_total') or (conversion_rate * base_to_tablet_rate))
        elif conversion_rate > 1 and base_unit:
            payload['split_mode'] = 'single'
            payload['package_tablet_total'] = int(payload.get('package_tablet_total') or (conversion_rate if base_unit in tablet_like_units else 0))
        else:
            payload['split_mode'] = 'single'
            payload['package_tablet_total'] = int(payload.get('package_tablet_total') or 0)
        payload['split_summary'] = split_summary
        return jsonify(payload)
    return jsonify({'error': 'Product not found'}), 404

@app.route('/admin/edit_product', methods=['POST'])
def edit_product():
    if not session.get('admin_logged_in'): 
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    code = clean_input_text(request.form.get('code'), 40).upper()
    name = clean_input_text(request.form.get('name'), 150)
    category = clean_input_text(request.form.get('category', ''), 60)
    unit = clean_input_text(request.form.get('unit'), 30)
    package_unit = clean_input_text(request.form.get('package_unit', ''), 30) or None
    base_unit = clean_input_text(request.form.get('base_unit', ''), 30) or None
    conversion_rate = max(1, request.form.get('conversion_rate', 1, type=int) or 1)
    base_unit_to_tablet_rate = max(0, request.form.get('base_unit_to_tablet_rate', 0, type=int) or 0)
    split_mode = clean_input_text(request.form.get('split_mode', 'single'), 20).lower() or 'single'
    split_enabled = str(request.form.get('split_enabled', '0')).strip().lower() in ('1', 'true', 'on', 'yes')
    package_tablet_total = max(0, request.form.get('package_tablet_total', 0, type=int) or 0)
    open_base_qty = max(0, request.form.get('open_base_qty', 0, type=int) or 0)
    open_extra_tablet_qty = max(0, request.form.get('open_extra_tablet_qty', 0, type=int) or 0)
    if split_mode not in ('single', 'multi'):
        split_mode = 'single'

    if category == 'ยา' and split_enabled:
        package_unit = package_unit or unit
        if split_mode == 'multi':
            if not package_unit:
                return jsonify({'success': False, 'message': 'กรุณาระบุหน่วยนำเข้า เช่น กระปุก/ขวด/แผง'}), 400
            if not base_unit:
                return jsonify({'success': False, 'message': 'กรุณาระบุหน่วยแยก เช่น ซอง/ห่อ'}), 400
            if package_tablet_total <= 0:
                return jsonify({'success': False, 'message': f'กรุณาระบุ 1 {package_unit} มีกี่เม็ด'}), 400
            if base_unit_to_tablet_rate <= 0:
                return jsonify({'success': False, 'message': f'กรุณาระบุ 1 {base_unit} มีกี่เม็ด'}), 400
            if package_tablet_total < base_unit_to_tablet_rate:
                return jsonify({'success': False, 'message': 'จำนวนเม็ดต่อหน่วยแยกมากกว่าจำนวนเม็ดต่อหน่วยนำเข้า'}), 400
            conversion_rate = max(1, package_tablet_total // base_unit_to_tablet_rate)
        else:
            if package_tablet_total > 0:
                conversion_rate = max(1, package_tablet_total)
                base_unit_to_tablet_rate = 0
        if package_tablet_total <= 0:
            if base_unit_to_tablet_rate > 0:
                package_tablet_total = conversion_rate * base_unit_to_tablet_rate
            elif str(base_unit or '').strip().lower() in {'เม็ด', 'tablet', 'tablets', 'pill', 'pills', 'capsule', 'capsules'}:
                package_tablet_total = conversion_rate

    # เก็บข้อมูลแยกหน่วยย่อยเฉพาะกรณีที่กรอกครบทั้งสองหน่วยเท่านั้น
    if not split_enabled or not package_unit or not base_unit:
        package_unit = None
        base_unit = None
        conversion_rate = 1
        base_unit_to_tablet_rate = 0
        package_tablet_total = 0
        open_base_qty = 0
        open_extra_tablet_qty = 0
    safety_stock = max(0, int(request.form.get('safety_stock', 0) or 0))
    stock = max(0, int(request.form.get('stock', 0) or 0))
    expiry_date = standardize_date(request.form.get('expiry_date', ''))

    if category == 'ยา' and split_enabled and split_mode == 'multi' and package_unit and base_unit and base_unit != 'เม็ด' and base_unit_to_tablet_rate <= 0:
        return jsonify({'success': False, 'message': f'กรุณาระบุ 1 {base_unit} มีกี่เม็ด'}), 400

    if not code or not name or not unit:
        return jsonify({'success': False, 'message': 'ข้อมูลของไม่ครบถ้วน'}), 400

    conn = get_db_connection()
    try:
        conn.execute('''
            UPDATE products 
            SET name=?, unit=?, base_unit=?, package_unit=?, conversion_rate=?, base_unit_to_tablet_rate=?, package_tablet_total=?, safety_stock=?, stock=?, expiry_date=?
            WHERE code=?
        ''', (name, unit, base_unit, package_unit, conversion_rate, base_unit_to_tablet_rate, package_tablet_total, safety_stock, stock, expiry_date, code))

        # อัปเดต open_packages ถ้าเป็นสินค้าแยกหน่วยย่อย
        if package_unit and base_unit and conversion_rate > 1:
            product_row = conn.execute('SELECT id FROM products WHERE code=?', (code,)).fetchone()
            if product_row:
                pid = product_row['id']
                existing_open = conn.execute(
                    'SELECT id FROM open_packages WHERE product_id=?', (pid,)
                ).fetchone()
                if existing_open:
                    conn.execute(
                        'UPDATE open_packages SET base_unit_qty=?, extra_tablet_qty=?, status=CASE WHEN ? > 0 OR ? > 0 THEN "active" ELSE "closed" END WHERE product_id=?',
                        (open_base_qty, open_extra_tablet_qty, open_base_qty, open_extra_tablet_qty, pid)
                    )
                elif open_base_qty > 0 or open_extra_tablet_qty > 0:
                    conn.execute(
                        'INSERT INTO open_packages (product_id, base_unit_qty, extra_tablet_qty, status) VALUES (?, ?, ?, ?)',
                        (pid, open_base_qty, open_extra_tablet_qty, 'active')
                    )

        conn.commit()
        return jsonify({'success': True, 'message': 'แก้ไขข้อมูลของเรียบร้อย'})
    except Exception as e:
        conn.rollback()
        print(f'Edit product error: {e}')
        return jsonify({'success': False, 'message': 'ไม่สามารถแก้ไขข้อมูลของได้'}), 500
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
    
    # 2. สร้าง Filter สำหรับ Log ตาม Role และ Location
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

    # 3. Query ข้อมูล Log พร้อม Join กับ Users และ Products เพื่อดึงชื่อพนักงานและชื่อของ
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

# ==================== 📬 Email Notification Settings ====================

@app.route('/admin/email_settings', methods=['GET', 'POST'])
def email_settings():
    """⚙️ Email Notification Settings Page"""
    if not session.get('admin_logged_in'):
        return redirect(url_for('index'))

    role = session.get('admin_role', 'superadmin')
    if role != 'superadmin':
        flash('❌ เฉพาะบัญชี superadmin เท่านั้นที่ตั้งค่า Email Notifications ได้', 'danger')
        return redirect(url_for('admin_dashboard', module='stock'))

    admin_id = 'superadmin'
    popup_mode = str(request.args.get('popup', '')).strip().lower() in ('1', 'true', 'yes', 'on')
    
    if request.method == 'POST':
        # Save settings
        conn = get_db_connection()
        try:
            ensure_notification_settings_columns(conn)
            existing_row = conn.execute(
                'SELECT * FROM notification_settings WHERE admin_id = ?',
                (admin_id,)
            ).fetchone()
            existing = dict(existing_row) if existing_row else {}

            email_recipients = (request.form.get('email_recipients', '') if 'email_recipients' in request.form else existing.get('email_recipients', '')).strip()
            email_recipients_pc1 = (request.form.get('email_recipients_pc1', '') if 'email_recipients_pc1' in request.form else existing.get('email_recipients_pc1', '')).strip()
            email_recipients_cc = (request.form.get('email_recipients_cc', '') if 'email_recipients_cc' in request.form else existing.get('email_recipients_cc', '')).strip()

            # Parse checkboxes
            settings = {
                'approval_email': (request.form.get('approval_email') == 'on') if 'approval_email' in request.form else bool(existing.get('approval_email', True)),
                'approval_line': False,
                'rejection_email': (request.form.get('rejection_email') == 'on') if 'rejection_email' in request.form else bool(existing.get('rejection_email', True)),
                'rejection_line': False,
                'low_stock_email': (request.form.get('low_stock_email') == 'on') if 'low_stock_email' in request.form else bool(existing.get('low_stock_email', True)),
                'low_stock_line': False,
                'email_recipients': email_recipients,
                'email_recipients_pc1': email_recipients_pc1,
                'email_recipients_cc': email_recipients_cc
            }
            
            # Upsert ป้องกันชน UNIQUE และรองรับ concurrent requests
            cols = ', '.join(settings.keys())
            placeholders = ', '.join(['?'] * (len(settings) + 1))
            update_fields = ', '.join(f'{k} = excluded.{k}' for k in settings.keys())
            values = [admin_id] + list(settings.values())
            conn.execute(
                f'''
                INSERT INTO notification_settings (admin_id, {cols})
                VALUES ({placeholders})
                ON CONFLICT(admin_id) DO UPDATE SET
                    {update_fields},
                    updated_at = CURRENT_TIMESTAMP
                ''',
                values
            )
            conn.execute("DELETE FROM notification_settings WHERE admin_id != 'superadmin'")
            
            conn.commit()
            flash('✅ บันทึกการตั้งค่าแจ้งเตือนเรียบร้อย', 'success')
            if popup_mode:
                return redirect(url_for('email_settings', popup='1'))
            return redirect(url_for('admin_dashboard', module='stock'))
        finally:
            conn.close()
    
    # Get current settings
    conn = get_db_connection()
    try:
        ensure_notification_settings_columns(conn)
        settings = conn.execute(
            'SELECT * FROM notification_settings WHERE admin_id = ?',
            (admin_id,)
        ).fetchone()
        if not settings and admin_id != 'superadmin':
            settings = conn.execute(
                'SELECT * FROM notification_settings WHERE admin_id = ?',
                ('superadmin',)
            ).fetchone()

        recent_test_logs = conn.execute(
            '''
            SELECT admin_id, recipients, subject, status, error_message, created_at
            FROM email_test_logs
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 10
            '''
        ).fetchall()

        recent_delivery_logs = conn.execute(
            '''
            SELECT admin_id, notification_type, scope, channel, recipients, status, error_message, location, role, created_at
            FROM notification_delivery_logs
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 30
            '''
        ).fetchall()
        
        if settings:
            settings = dict(settings)
        else:
            settings = {
                'approval_email': True,
                'approval_line': False,
                'rejection_email': True,
                'rejection_line': False,
                'low_stock_email': True,
                'low_stock_line': False,
                'email_recipients': '',
                'email_recipients_pc1': '',
                'email_recipients_cc': ''
            }
            settings['_effective_owner'] = 'superadmin'
            settings['_viewer_role'] = role
    finally:
        conn.close()
    
    return render_template(
        'email_settings.html',
        settings=settings,
        recent_test_logs=recent_test_logs,
        recent_delivery_logs=recent_delivery_logs,
        popup_mode=popup_mode
    )

@app.route('/admin/email_settings/test', methods=['POST'])
def email_settings_test():
    """ส่งอีเมลทดสอบจากหน้าตั้งค่า และบันทึก log ผลการทดสอบ"""
    if not session.get('admin_logged_in'):
        return redirect(url_for('index'))

    role = session.get('admin_role', 'superadmin')
    if role != 'superadmin':
        flash('❌ เฉพาะบัญชี superadmin เท่านั้นที่ทดสอบ Email Notifications ได้', 'danger')
        return redirect(url_for('admin_dashboard', module='stock'))

    admin_id = 'superadmin'
    popup_mode = str(request.form.get('popup', '')).strip().lower() in ('1', 'true', 'yes', 'on')
    test_scope = clean_input_text(request.form.get('test_scope', 'all'), 20).lower() or 'all'

    form_recipients_default = parse_email_recipients(request.form.get('email_recipients', '').strip())
    form_recipients_pc1 = parse_email_recipients(request.form.get('email_recipients_pc1', '').strip())
    form_recipients_cc = parse_email_recipients(request.form.get('email_recipients_cc', '').strip())

    # บันทึกค่าผู้รับจากฟอร์มก่อนทดสอบ เพื่อไม่ให้ค่าหายหลัง redirect
    raw_default = request.form.get('email_recipients', '').strip()
    raw_pc1 = request.form.get('email_recipients_pc1', '').strip()
    raw_cc = request.form.get('email_recipients_cc', '').strip()
    try:
        conn = get_db_connection()
        ensure_notification_settings_columns(conn)
        existing = conn.execute(
            'SELECT email_recipients, email_recipients_pc1, email_recipients_cc FROM notification_settings WHERE admin_id = ?',
            (admin_id,)
        ).fetchone()
        if existing:
            if 'email_recipients' not in request.form:
                raw_default = existing['email_recipients'] or ''
            if 'email_recipients_pc1' not in request.form:
                raw_pc1 = existing['email_recipients_pc1'] or ''
            if 'email_recipients_cc' not in request.form:
                raw_cc = existing['email_recipients_cc'] or ''

        conn.execute(
            '''
            INSERT INTO notification_settings (admin_id, email_recipients, email_recipients_pc1, email_recipients_cc)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(admin_id) DO UPDATE SET
                email_recipients = excluded.email_recipients,
                email_recipients_pc1 = excluded.email_recipients_pc1,
                email_recipients_cc = excluded.email_recipients_cc,
                updated_at = CURRENT_TIMESTAMP
            ''',
            (admin_id, raw_default, raw_pc1, raw_cc)
        )
        conn.execute("DELETE FROM notification_settings WHERE admin_id != 'superadmin'")
        conn.commit()
    except Exception as e:
        print(f'Warning: cannot persist recipients before test: {e}')
    finally:
        try:
            conn.close()
        except Exception:
            pass

    def collect_test_recipients(scope_name, default_list, pc1_list, cc_list):
        if scope_name == 'default':
            return list(dict.fromkeys(default_list))
        if scope_name == 'pc1':
            return list(dict.fromkeys(pc1_list or default_list))
        if scope_name == 'cc':
            return list(dict.fromkeys(cc_list or default_list))
        merged = []
        merged.extend(default_list)
        merged.extend(pc1_list)
        merged.extend(cc_list)
        return list(dict.fromkeys(merged))

    recipients = collect_test_recipients(test_scope, form_recipients_default, form_recipients_pc1, form_recipients_cc)

    if not recipients:
        conn = get_db_connection()
        try:
            ensure_notification_settings_columns(conn)
            row = conn.execute(
                'SELECT email_recipients, email_recipients_pc1, email_recipients_cc FROM notification_settings WHERE admin_id = ?',
                (admin_id,)
            ).fetchone()
        finally:
            conn.close()
        if row:
            db_default = parse_email_recipients(row['email_recipients'])
            db_pc1 = parse_email_recipients(row['email_recipients_pc1'])
            db_cc = parse_email_recipients(row['email_recipients_cc'])
            recipients = collect_test_recipients(test_scope, db_default, db_pc1, db_cc)

    scope_label = 'ALL' if test_scope == 'all' else test_scope.upper()
    subject = f"[PCM TEST:{scope_label}] Email Notification Test {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    body = (
        "This is a test email from PCM Email Notification Settings.\n\n"
        f"Admin: {admin_id}\n"
        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    html_body = f"""
    <div style=\"font-family:Segoe UI,Arial,sans-serif;line-height:1.5\">
      <h3 style=\"margin-bottom:8px\">PCM Email Notification Test</h3>
      <p style=\"margin:0 0 8px 0\">This is a test email from PCM system.</p>
      <ul style=\"margin:0;padding-left:18px\">
        <li><strong>Admin:</strong> {admin_id}</li>
        <li><strong>Time:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</li>
      </ul>
    </div>
    """

    if not recipients:
        log_email_test_result(admin_id, [], subject, 'failed', 'no-recipients')
        flash('❌ ไม่พบผู้รับอีเมลทดสอบ กรุณากรอก Email Recipients ก่อน', 'danger')
        if popup_mode:
            return redirect(url_for('email_settings', popup='1'))
        return redirect(url_for('email_settings'))

    sent, error = send_email_message(
        subject=subject,
        body=body,
        recipients=recipients,
        html_body=html_body
    )

    if sent:
        log_email_test_result(admin_id, recipients, subject, 'sent', '')
        flash(f'✅ ส่งอีเมลทดสอบสำเร็จ ({len(recipients)} ผู้รับ)', 'success')
    else:
        log_email_test_result(admin_id, recipients, subject, 'failed', error)
        flash(f'❌ ส่งอีเมลทดสอบไม่สำเร็จ: {error}', 'danger')

    if popup_mode:
        return redirect(url_for('email_settings', popup='1'))
    return redirect(url_for('email_settings'))

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/admin/change_password', methods=['POST'])
def change_password():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    data = request.get_json(silent=True) or {}
    old_password = data.get('old_password', '')
    new_password = data.get('new_password', '')

    if not old_password or not new_password or len(new_password) < 8 or len(new_password) > 128:
        return jsonify({'success': False, 'message': 'ข้อมูลไม่ถูกต้อง'}), 400

    username = session.get('admin_username', '')
    if not username:
        return jsonify({'success': False, 'message': 'Session หมดอายุ'}), 401
    conn = get_db_connection()
    try:
        admin = conn.execute('SELECT * FROM admins WHERE username = ?', (username,)).fetchone()
        if not admin or not check_password_hash(admin['password'], old_password):
            return jsonify({'success': False, 'message': 'รหัสผ่านเดิมไม่ถูกต้อง'}), 400

        new_hash = generate_password_hash(new_password)
        conn.execute('UPDATE admins SET password = ? WHERE username = ?', (new_hash, username))
        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        print(f'Change password error: {e}')
        return jsonify({'success': False, 'message': 'ไม่สามารถเปลี่ยนรหัสผ่านได้'}), 500
    finally:
        conn.close()

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

    product_id = request.form.get('product_id', type=int)
    lot_number = normalize_lot_number(request.form.get('lot_number'))
    qty = int(request.form.get('add_qty', 0) or 0)
    qty_unit = (request.form.get('qty_unit', 'package') or 'package').strip().lower()
    receive_date = standardize_date(request.form.get('receive_date'))
    expire_date = standardize_date(request.form.get('expire_date', ''))

    if not product_id or qty <= 0:
        return jsonify({'success': False, 'message': 'ข้อมูล Lot ไม่ถูกต้อง'}), 400

    conn = get_db_connection()
    try:
        product = conn.execute('''
            SELECT id, code, name, category, unit, base_unit, package_unit, conversion_rate
            FROM products WHERE id = ?
        ''', (product_id,)).fetchone()
        if not product:
            return jsonify({'success': False, 'message': 'ไม่พบรายการสินค้า'}), 404

        is_split_med = is_split_tablet_medicine(product)
        if not is_split_med:
            qty_unit = 'package'
        if qty_unit not in ('package', 'base'):
            return jsonify({'success': False, 'message': 'หน่วยเพิ่มสต็อกไม่ถูกต้อง'}), 400

        conversion_rate = max(1, int(product['conversion_rate'] or 1))
        lot_qty_to_add = qty
        stock_qty_to_add = qty
        open_base_to_add = 0
        if is_split_med and qty_unit == 'base':
            stock_qty_to_add = qty // conversion_rate
            open_base_to_add = qty % conversion_rate
            lot_qty_to_add = stock_qty_to_add

        # 1. Upsert Lot แบบ normalize เพื่อลดความเสี่ยงข้อมูลซ้ำจากรูปแบบเลข Lot ต่างกัน
        if lot_qty_to_add > 0:
            lot_rows = conn.execute(
                'SELECT id, lot_number, received_date FROM product_lots WHERE product_id = ?',
                (product_id,)
            ).fetchall()
            existing_lot = next((
                row for row in lot_rows
                if normalize_lot_number(row['lot_number']) == lot_number and standardize_date(row['received_date']) == receive_date
            ), None)

            if existing_lot:
                conn.execute('''
                    UPDATE product_lots
                    SET lot_number = ?, qty = qty + ?, expiry_date = CASE WHEN ? = '' THEN expiry_date ELSE ? END
                    WHERE id = ?
                ''', (lot_number, lot_qty_to_add, expire_date, expire_date, existing_lot['id']))
            else:
                conn.execute('''
                    INSERT INTO product_lots (product_id, lot_number, qty, received_date, expiry_date)
                    VALUES (?, ?, ?, ?, ?)
                ''', (product_id, lot_number, lot_qty_to_add, receive_date, expire_date))

        # 2. อัปเดตยอดรวม Stock ใน Table products
        if stock_qty_to_add > 0:
            conn.execute('UPDATE products SET stock = stock + ? WHERE id = ?', (stock_qty_to_add, product_id))

        if open_base_to_add > 0:
            existing_open = conn.execute('''
                SELECT id
                FROM open_packages
                WHERE product_id = ? AND status = 'active'
                ORDER BY opened_date ASC, id ASC
                LIMIT 1
            ''', (product_id,)).fetchone()

            if existing_open:
                conn.execute('''
                    UPDATE open_packages
                    SET base_unit_qty = base_unit_qty + ?, status = 'active'
                    WHERE id = ?
                ''', (open_base_to_add, existing_open['id']))
            else:
                conn.execute('''
                    INSERT INTO open_packages (product_id, lot_id, opened_date, base_unit_qty, extra_tablet_qty, package_unit_qty_before, status)
                    VALUES (?, NULL, datetime('now'), ?, 0, 1, 'active')
                ''', (product_id, open_base_to_add))

        # 3. บันทึก Log การนำเข้า
        admin_name = session.get('admin_name')
        action_text = f"รับเข้า Lot: {lot_number}"
        log_qty = qty
        if is_split_med:
            package_unit = product['package_unit'] or product['unit'] or 'แพ็ค'
            base_unit = product['base_unit'] or 'หน่วยย่อย'
            if qty_unit == 'base':
                action_text = f"รับเข้า Lot: {lot_number} ({qty} {base_unit})"
            else:
                action_text = f"รับเข้า Lot: {lot_number} ({qty} {package_unit})"
        conn.execute('''
            INSERT INTO transaction_logs (emp_id, product_id, action, qty, status) 
            VALUES (?, ?, ?, ?, 'Completed')
        ''', (f"ADMIN:{admin_name}", product_id, action_text, log_qty))

        conn.commit()
        return jsonify({'success': True, 'message': 'เพิ่ม Lot ของสำเร็จ!'})
    except Exception as e:
        conn.rollback()
        print(f'Add lot error: {e}')
        return jsonify({'success': False, 'message': 'ไม่สามารถเพิ่ม Lot ได้'}), 500
    finally:
        conn.close()

@app.route('/admin/write_off_ajax', methods=['POST'])
def write_off_ajax():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    admin_name = 'ADMIN:' + session.get('admin_name', 'Unknown')
    product_id = request.form.get('product_id', type=int)
    qty = request.form.get('qty', type=int)
    qty_unit = (request.form.get('qty_unit', 'package') or 'package').strip().lower()
    reason = clean_input_text(request.form.get('reason', 'หมดอายุ'), 120)

    if not qty or qty <= 0:
        return jsonify({'success': False, 'message': 'จำนวนต้องมากกว่า 0'})

    conn = get_db_connection()
    try:
        start_write_transaction(conn)
        product = conn.execute('''
            SELECT id, stock, unit, category, base_unit, package_unit, conversion_rate, base_unit_to_tablet_rate, package_tablet_total
            FROM products WHERE id = ?
        ''', (product_id,)).fetchone()

        if not product:
            conn.rollback()
            return jsonify({'success': False, 'message': 'จำนวนสต็อกไม่เพียงพอให้ตัดจำหน่าย'})

        is_split_med = is_split_tablet_medicine(product)
        if not is_split_med:
            qty_unit = 'package'

        if is_split_med and qty_unit not in ('package', 'base'):
            conn.rollback()
            return jsonify({'success': False, 'message': 'หน่วยตัดจำหน่ายไม่ถูกต้อง'})

        conv = max(1, int(product['conversion_rate'] or 1))
        base_to_tablet_rate = max(0, int(product['base_unit_to_tablet_rate'] or 0))
        package_tablet_total = max(0, int(product['package_tablet_total'] or 0))
        if package_tablet_total <= 0 and base_to_tablet_rate > 0:
            package_tablet_total = conv * base_to_tablet_rate
        per_package_extra_tablet = max(0, package_tablet_total - (conv * base_to_tablet_rate)) if base_to_tablet_rate > 0 else 0
        stock_pkg_qty = int(product['stock'] or 0)
        open_base_qty = 0

        if is_split_med:
            open_base_qty = int(conn.execute('''
                SELECT COALESCE(SUM(base_unit_qty), 0)
                FROM open_packages
                WHERE product_id = ? AND status = 'active'
            ''', (product_id,)).fetchone()[0] or 0)

        if is_split_med and qty_unit == 'base':
            total_base_available = (stock_pkg_qty * conv) + open_base_qty
            if qty > total_base_available:
                conn.rollback()
                return jsonify({'success': False, 'message': 'จำนวนสต็อกไม่เพียงพอให้ตัดจำหน่าย'})

            used_from_open = min(open_base_qty, qty)
            remaining_after_open = qty - used_from_open
            qty_package_to_cut = (remaining_after_open + conv - 1) // conv if remaining_after_open > 0 else 0
            lot_qty_to_cut = qty
            new_open_base_qty = (qty_package_to_cut * conv) - remaining_after_open if qty_package_to_cut > 0 else 0
        else:
            if stock_pkg_qty < qty:
                conn.rollback()
                return jsonify({'success': False, 'message': 'จำนวนสต็อกไม่เพียงพอให้ตัดจำหน่าย'})
            qty_package_to_cut = qty
            lot_qty_to_cut = qty * conv if is_split_med else qty
            used_from_open = 0
            new_open_base_qty = 0

        if is_split_med:
            lot_qty_to_cut = int(lot_qty_to_cut)

        remaining_qty = lot_qty_to_cut

        # --- 1. ไล่ตัดสต็อกจากตาราง Lot แบบ FIFO ---
        lots = conn.execute('''
            SELECT id, qty, lot_number
            FROM product_lots
            WHERE product_id = ? AND qty > 0
            ORDER BY
                CASE
                    WHEN expiry_date LIKE '%/%/%' THEN substr(expiry_date, 7, 4) || '-' || substr(expiry_date, 4, 2) || '-' || substr(expiry_date, 1, 2)
                    ELSE expiry_date
                END ASC,
                id ASC
        ''', (product_id,)).fetchall()

        for lot in lots:
            if remaining_qty <= 0:
                break

            lot_available = int(lot['qty'] or 0)
            cut_qty = min(lot_available, remaining_qty)

            # 1.1 อัปเดตจำนวนในตาราง product_lots
            conn.execute("UPDATE product_lots SET qty = qty - ? WHERE id = ?", (cut_qty, lot['id']))

            # 1.2 บันทึกประวัติลง transaction_logs (แยกตาม Lot ที่ถูกตัด)
            action_text = f"ตัดจำหน่าย (Scrap) - {reason} [Lot: {lot['lot_number']}]"
            if is_split_med:
                qty_package = cut_qty / conv
                conn.execute('''
                    INSERT INTO transaction_logs (emp_id, product_id, lot_id, action, qty, qty_base_unit, qty_package_unit, status, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'Approved', datetime('now', '+7 hours'))
                ''', (admin_name, product_id, lot['id'], action_text, qty_package, cut_qty, qty_package))
            else:
                conn.execute('''
                    INSERT INTO transaction_logs (emp_id, product_id, lot_id, action, qty, status, timestamp)
                    VALUES (?, ?, ?, ?, ?, 'Approved', datetime('now', '+7 hours'))
                ''', (admin_name, product_id, lot['id'], action_text, cut_qty))

            remaining_qty -= cut_qty

        if remaining_qty > 0:
            conn.rollback()
            unit_label = product['base_unit'] if is_split_med and product['base_unit'] else product['unit']
            return jsonify({'success': False, 'message': f'จำนวนใน Lot ({unit_label}) ไม่พอสำหรับการตัดจำหน่าย'})

        if is_split_med and qty_unit == 'base':
            # ใช้ของคงเหลือใน open_packages ก่อน
            open_rows = conn.execute('''
                SELECT id, base_unit_qty, COALESCE(extra_tablet_qty, 0) as extra_tablet_qty
                FROM open_packages
                WHERE product_id = ? AND status = 'active' AND base_unit_qty > 0
                ORDER BY opened_date ASC, id ASC
            ''', (product_id,)).fetchall()

            open_remaining = used_from_open
            for row in open_rows:
                if open_remaining <= 0:
                    break
                available = int(row['base_unit_qty'] or 0)
                take = min(available, open_remaining)
                conn.execute('''
                    UPDATE open_packages
                    SET base_unit_qty = MAX(0, base_unit_qty - ?),
                        status = CASE WHEN base_unit_qty - ? <= 0 AND COALESCE(extra_tablet_qty, 0) <= 0 THEN 'used' ELSE 'active' END
                    WHERE id = ?
                ''', (take, take, row['id']))
                open_remaining -= take

            if new_open_base_qty > 0:
                conn.execute('''
                    INSERT INTO open_packages (product_id, lot_id, opened_date, base_unit_qty, extra_tablet_qty, package_unit_qty_before, status)
                    VALUES (?, NULL, datetime('now'), ?, ?, 1, 'active')
                ''', (product_id, int(new_open_base_qty), int(per_package_extra_tablet)))

        # --- 2. อัปเดตสต็อกรวมในตารางหลัก (products) ---
        if qty_package_to_cut > 0:
            stock_update = conn.execute(
                "UPDATE products SET stock = stock - ? WHERE id = ? AND stock >= ?",
                (qty_package_to_cut, product_id, qty_package_to_cut)
            )
            if stock_update.rowcount == 0:
                conn.rollback()
                return jsonify({'success': False, 'message': 'ไม่สามารถอัปเดตสต็อกได้ (จำนวนอาจเปลี่ยนระหว่างทำรายการ)'})

        conn.commit()
        return jsonify({'success': True})
    except Exception as e:
        conn.rollback()
        print(f'Write off error: {e}')
        return jsonify({'success': False, 'message': 'ไม่สามารถตัดจำหน่ายได้'}), 500
    finally:
        conn.close()

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
            # 2. รีเซ็ตจำนวนของและวันหมดอายุในตารางหลักให้กลับเป็นศูนย์
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
    query = f'''
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
          AND strftime('%Y-%m', datetime({transaction_timestamp_expr('l')})) = strftime('%Y-%m', 'now', 'localtime')
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
    stats = conn.execute(f'''
        SELECT 
            p.id, 
            p.name, 
            p.stock,
            COALESCE(SUM(l.qty), 0) / 30.0 as daily_avg
        FROM products p
        LEFT JOIN transaction_logs l ON p.id = l.product_id 
            AND l.status = 'Approved'
            AND datetime({transaction_timestamp_expr('l')}) >= datetime('now', 'localtime', '-30 days')
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
@app.route('/admin/unlock_user/<emp_id>', methods=['POST'])
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
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
        
    role = session.get('admin_role')
    search = clean_input_text(request.args.get('search', ''), 100)
    conn = get_db_connection()

    query = "SELECT emp_id, name, COALESCE(name_eng, '') AS name_eng, department, location, COALESCE(email, '') AS email FROM users WHERE 1=1"
    params = []

    if role == 'admin_pc1':
        query += " AND location = 'PC1'"
    elif role == 'admin_cc':
        query += " AND (location = 'CC' OR location = 'Coil Center' OR location LIKE '%CC%' OR location LIKE '%Coil Center%')"

    if search:
        query += " AND (emp_id LIKE ? OR name LIKE ? OR COALESCE(name_eng, '') LIKE ? OR department LIKE ? OR location LIKE ? OR COALESCE(email, '') LIKE ?)"
        like_term = f"%{search}%"
        params.extend([like_term, like_term, like_term, like_term, like_term, like_term])

    query += " ORDER BY emp_id ASC"

    users = conn.execute(query, params).fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

@app.route('/admin/add_user_ajax', methods=['POST'])
def add_user_ajax():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    emp_id = clean_input_text(request.form.get('emp_id'), 20)
    name = clean_input_text(request.form.get('name'), 100)
    name_eng = clean_input_text(request.form.get('name_eng'), 100)
    dept = clean_input_text(request.form.get('department'), 100)
    loc = normalize_location_value(request.form.get('location'))
    email = normalize_email_value(request.form.get('email'))

    if not is_valid_emp_id(emp_id):
        return jsonify({'success': False, 'message': 'รหัสพนักงานไม่ถูกต้อง'}), 400
    if not name:
        return jsonify({'success': False, 'message': 'กรุณาระบุชื่อพนักงาน'}), 400
    if loc not in ('PC1', 'Coil Center', 'General'):
        return jsonify({'success': False, 'message': 'สถานที่ไม่ถูกต้อง'}), 400
    if email and not is_valid_email_address(email):
        return jsonify({'success': False, 'message': 'รูปแบบอีเมลไม่ถูกต้อง'}), 400
    
    conn = get_db_connection()
    try:
        conn.execute('INSERT INTO users (emp_id, name, name_eng, department, location, email, is_locked) VALUES (?, ?, ?, ?, ?, ?, 0)',
                     (emp_id, name, name_eng or '', dept or '-', loc, email))
        conn.commit()
        return jsonify({'success': True})
    except Exception:
        return jsonify({'success': False, 'message': 'รหัสพนักงานซ้ำหรือข้อมูลผิดพลาด'}), 400
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
        conn.close()
        return jsonify({'success': False, 'message': 'ไม่พบพนักงาน'}), 404
        
    # ถ้าไม่ใช่ Super Admin และพนักงานไม่ได้อยู่โรงงานตัวเอง จะลบไม่ได้
    if role == 'admin_pc1' and user['location'] != 'PC1':
        conn.close()
        return jsonify({'success': False, 'message': 'ไม่มีสิทธิ์จัดการพนักงานนอก PC1'}), 403
    if role == 'admin_cc' and not is_cc_location_value(user['location']):
        conn.close()
        return jsonify({'success': False, 'message': 'ไม่มีสิทธิ์จัดการพนักงานนอก CC'}), 403

    conn.execute('DELETE FROM users WHERE emp_id = ?', (emp_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/admin/update_user_ajax', methods=['POST'])
def update_user_ajax():
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401

    role = session.get('admin_role')
    
    emp_id = clean_input_text(request.form.get('emp_id'), 20)
    name = clean_input_text(request.form.get('name'), 100)
    name_eng = clean_input_text(request.form.get('name_eng'), 100)
    dept = clean_input_text(request.form.get('department'), 100)
    loc = normalize_location_value(request.form.get('location'))
    email = normalize_email_value(request.form.get('email'))

    if not is_valid_emp_id(emp_id):
        return jsonify({'success': False, 'message': 'รหัสพนักงานไม่ถูกต้อง'}), 400
    if not name:
        return jsonify({'success': False, 'message': 'กรุณาระบุชื่อพนักงาน'}), 400
    if loc not in ('PC1', 'Coil Center', 'General'):
        return jsonify({'success': False, 'message': 'สถานที่ไม่ถูกต้อง'}), 400
    if email and not is_valid_email_address(email):
        return jsonify({'success': False, 'message': 'รูปแบบอีเมลไม่ถูกต้อง'}), 400
    
    conn = get_db_connection()
    existing_user = conn.execute('SELECT location FROM users WHERE emp_id = ?', (emp_id,)).fetchone()
    if not existing_user:
        conn.close()
        return jsonify({'success': False, 'message': 'ไม่พบพนักงาน'}), 404

    # จำกัดสิทธิ์ให้แก้ไขเฉพาะพนักงานในโรงงานของตนเอง
    if role == 'admin_pc1' and existing_user['location'] != 'PC1':
        conn.close()
        return jsonify({'success': False, 'message': 'ไม่มีสิทธิ์แก้ไขพนักงานนอก PC1'}), 403
    if role == 'admin_cc' and not is_cc_location_value(existing_user['location']):
        conn.close()
        return jsonify({'success': False, 'message': 'ไม่มีสิทธิ์แก้ไขพนักงานนอก CC'}), 403

    # จำกัดสิทธิ์ไม่ให้ย้ายพนักงานข้ามโรงงานโดย role ปกติ
    if role == 'admin_pc1' and loc != 'PC1':
        conn.close()
        return jsonify({'success': False, 'message': 'ไม่มีสิทธิ์ย้ายพนักงานออกนอก PC1'}), 403
    if role == 'admin_cc' and not is_cc_location_value(loc):
        conn.close()
        return jsonify({'success': False, 'message': 'ไม่มีสิทธิ์ย้ายพนักงานออกนอก CC'}), 403

    conn.execute('UPDATE users SET name=?, name_eng=?, department=?, location=?, email=? WHERE emp_id=?', 
                 (name, name_eng or '', dept or '-', loc, email, emp_id))
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
    if not session.get('admin_logged_in'):
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
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
        # 1. แจ้งเตือนของใกล้หมดอายุ (จาก product_lots รองรับทั้งของเก่า + Lot ใหม่)
        # ==========================================
        expiry_query = '''
            SELECT p.name, p.category, p.location,
                CASE
                    WHEN pl.expiry_date LIKE '%/%/%' THEN substr(pl.expiry_date,7,4)||'-'||substr(pl.expiry_date,4,2)||'-'||substr(pl.expiry_date,1,2)
                    ELSE trim(pl.expiry_date)
                END AS formatted_expiry
            FROM product_lots pl
            JOIN products p ON pl.product_id = p.id
            WHERE pl.qty > 0
            AND pl.expiry_date IS NOT NULL AND trim(pl.expiry_date) != ''
            AND (p.category LIKE '%ยา%' OR p.name LIKE '%Helmet%' OR p.name LIKE '%Coffee%')
            AND (
                CASE
                    WHEN pl.expiry_date LIKE '%/%/%' THEN substr(pl.expiry_date,7,4)||'-'||substr(pl.expiry_date,4,2)||'-'||substr(pl.expiry_date,1,2)
                    ELSE trim(pl.expiry_date)
                END
            ) <= date('now', '+7 hours', '+30 days')
            ORDER BY formatted_expiry ASC
        '''
        expiring_items = conn.execute(expiry_query).fetchall()

        # ==========================================
        # 2. เช็คหมวกเซฟตี้ (แยกตามพนักงาน, ของ และ Lot)
        # ==========================================
        helmet_query = '''
            SELECT 
                u.name AS emp_name, 
                u.department, 
                u.location, 
                p.name AS product_name,
                l.lot_id,
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
            GROUP BY u.emp_id, p.id, l.lot_id
            HAVING last_timestamp <= datetime('now', '+7 hours', '-23 months')
        '''
        helmet_alerts = conn.execute(helmet_query).fetchall()

        # ==========================================
        # 3. แจ้งเตือนการขอเบิกล่วงหน้าที่ใกล้ถึงกำหนด (ภายใน 24 ชั่วโมง)
        # ==========================================
        scheduled_query = '''
            SELECT l.id, l.qty, l.note, l.requested_receive_at,
                   u.name AS emp_name, u.department, u.location,
                   p.name AS product_name, p.unit
            FROM transaction_logs l
            LEFT JOIN users u ON l.emp_id = u.emp_id
            LEFT JOIN products p ON l.product_id = p.id
            WHERE l.request_receive_mode = 'scheduled'
            AND l.status = 'Pending'
            AND l.requested_receive_at IS NOT NULL
            AND trim(l.requested_receive_at) != ''
            AND l.requested_receive_at >= datetime('now', '+7 hours')
            AND l.requested_receive_at <= datetime('now', '+7 hours', '+24 hours')
            ORDER BY l.requested_receive_at ASC
        '''
        scheduled_withdrawals = conn.execute(scheduled_query).fetchall()
        conn.close()
        
        # ==========================================
        # 4. จัดกลุ่มแยก CC / PC1 แล้วส่งแยกกัน
        # ==========================================
        def is_cc_location(loc):
            loc = str(loc or '').lower()
            return 'coil center' in loc or loc == 'cc' or ' cc' in f' {loc}'

        def is_pc1_location(loc):
            return 'pc1' in str(loc or '').lower()

        for location_label, location_check, location_key in [
            ('CC', is_cc_location, 'cc'),
            ('PC1', is_pc1_location, 'pc1')
        ]:
            loc_expiry = [i for i in expiring_items if location_check(i['location'])]
            loc_helmets = [h for h in helmet_alerts if location_check(h['location'])]
            loc_scheduled = [s for s in scheduled_withdrawals if location_check(s['location'])]

            exp_payload = []
            for item in loc_expiry:
                date_parts = item['formatted_expiry'].split('-')
                show_date = f"{date_parts[2]}/{date_parts[1]}/{date_parts[0]}" if len(date_parts) == 3 else item['formatted_expiry']
                exp_payload.append({
                    'name': item['name'],
                    'category': item['category'],
                    'show_date': show_date
                })

            helmet_payload = []
            for alert in loc_helmets:
                lot_text = f" [Lot: {alert['lot_id']}]" if alert['lot_id'] else ""
                last_date = alert['last_timestamp'][:10]
                date_parts = last_date.split('-')
                show_date = f"{date_parts[2]}/{date_parts[1]}/{date_parts[0]}" if len(date_parts) == 3 else last_date
                helmet_payload.append({
                    'emp_name': alert['emp_name'],
                    'department': alert['department'],
                    'product_name': f"{alert['product_name']}{lot_text}",
                    'show_date': show_date
                })

            scheduled_payload = []
            for sw in loc_scheduled:
                raw_dt = sw['requested_receive_at'] or ''
                try:
                    dt_obj = datetime.strptime(raw_dt[:16], '%Y-%m-%d %H:%M')
                    show_dt = f"{dt_obj.day:02d}/{dt_obj.month:02d}/{dt_obj.year}  {dt_obj.hour:02d}:{dt_obj.minute:02d} น."
                except Exception:
                    show_dt = raw_dt
                scheduled_payload.append({
                    'emp_name': sw['emp_name'] or '-',
                    'department': sw['department'] or '-',
                    'product_name': sw['product_name'] or '-',
                    'qty': sw['qty'],
                    'unit': sw['unit'] or '',
                    'show_dt': show_dt,
                    'note': sw['note'] or ''
                })

            periodic_payload = {
                'location_label': location_label,
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'expiring_items': exp_payload,
                'helmet_alerts': helmet_payload,
                'scheduled_withdrawals': scheduled_payload,
            }

            send_smart_notification(
                notification_type='low_stock',
                message=build_periodic_alert_email_body(periodic_payload),
                location=location_label,
                email_body=build_periodic_alert_email_body(periodic_payload),
                html_body=build_periodic_alert_email_html(periodic_payload),
                admin_id='superadmin'
            )

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
        return jsonify({'success': False, 'message': 'Unauthorized'}), 401
    
    conn = get_db_connection()
    # ดึงค่าจากตาราง settings
    row = conn.execute("SELECT value FROM settings WHERE key = 'daily_alert_time'").fetchone()
    conn.close()
    
    current_time = row['value'] if row else "08:30"
    return jsonify({'time': current_time}) #

# 2. API บันทึกเวลาใหม่
@app.route('/admin/save_alert_time', methods=['POST'])
def save_alert_time():
    if session.get('admin_role') != 'superadmin':
        return jsonify({'success': False, 'message': 'No Permission'}), 403

    new_time = clean_input_text(request.form.get('alert_time'), 5)
    if not is_valid_alert_time(new_time):
        return jsonify({'success': False, 'message': 'รูปแบบเวลาไม่ถูกต้อง'}), 400
    
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('daily_alert_time', ?)", (new_time,))
    conn.commit()
    conn.close()

    update_scheduler_time(new_time)
    
    return jsonify({'success': True, 'message': f'เปลี่ยนเวลาแจ้งเตือนอีเมลเป็น {new_time} น. เรียบร้อย'})

@app.route('/admin/get_ga_email_settings')
def get_ga_email_settings():
    if session.get('admin_role') != 'superadmin':
        return jsonify({'success': False, 'message': 'No Permission'}), 403

    init_settings_db()
    keys = [
        'ga_recipients_ga_pc1', 'ga_recipients_ga_cc',
        'ga_recipients_it_pc1', 'ga_recipients_it_cc',
        'ga_recipients_default',
        'smtp_host', 'smtp_port', 'smtp_username', 'smtp_password', 'smtp_from', 'smtp_use_tls'
    ]
    settings = get_settings_values(keys)
    return jsonify({'success': True, 'settings': settings})

@app.route('/admin/save_ga_email_settings', methods=['POST'])
def save_ga_email_settings():
    if session.get('admin_role') != 'superadmin':
        return jsonify({'success': False, 'message': 'No Permission'}), 403

    payload = {
        'ga_recipients_ga_pc1': clean_input_text(request.form.get('ga_recipients_ga_pc1'), 500),
        'ga_recipients_ga_cc': clean_input_text(request.form.get('ga_recipients_ga_cc'), 500),
        'ga_recipients_it_pc1': clean_input_text(request.form.get('ga_recipients_it_pc1'), 500),
        'ga_recipients_it_cc': clean_input_text(request.form.get('ga_recipients_it_cc'), 500),
        'ga_recipients_default': clean_input_text(request.form.get('ga_recipients_default'), 500),
        'smtp_host': clean_input_text(request.form.get('smtp_host'), 200),
        'smtp_port': clean_input_text(request.form.get('smtp_port'), 10),
        'smtp_username': clean_input_text(request.form.get('smtp_username'), 200),
        'smtp_password': clean_input_text(request.form.get('smtp_password'), 300),
        'smtp_from': normalize_email_value(request.form.get('smtp_from')),
        'smtp_use_tls': '1' if request.form.get('smtp_use_tls') in ('1', 'true', 'on', 'yes') else '0',
    }

    recipient_keys = [
        'ga_recipients_ga_pc1', 'ga_recipients_ga_cc',
        'ga_recipients_it_pc1', 'ga_recipients_it_cc', 'ga_recipients_default'
    ]
    invalid_tokens = []
    for key in recipient_keys:
        invalid_tokens.extend(list_invalid_email_entries(payload[key]))

    if invalid_tokens:
        invalid_preview = ', '.join(sorted(set(invalid_tokens))[:5])
        return jsonify({'success': False, 'message': f'พบอีเมลไม่ถูกต้อง: {invalid_preview}'}), 400

    if payload['smtp_from'] and not is_valid_email_address(payload['smtp_from']):
        return jsonify({'success': False, 'message': 'รูปแบบ SMTP From ไม่ถูกต้อง'}), 400

    if payload['smtp_port']:
        try:
            port_value = int(payload['smtp_port'])
            if port_value <= 0 or port_value > 65535:
                raise ValueError()
        except ValueError:
            return jsonify({'success': False, 'message': 'SMTP Port ไม่ถูกต้อง'}), 400

    init_settings_db()
    conn = get_db_connection()
    try:
        for key, value in payload.items():
            conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()

    return jsonify({'success': True, 'message': 'บันทึกการตั้งค่าอีเมล GA Request เรียบร้อย'})

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
    setting_defaults = {
        'ga_recipients_ga_pc1': '',
        'ga_recipients_ga_cc': '',
        'ga_recipients_it_pc1': '',
        'ga_recipients_it_cc': '',
        'ga_recipients_default': '',
        'smtp_host': 'smtp.gmail.com',
        'smtp_port': '587',
        'smtp_username': '',
        'smtp_password': '',
        'smtp_from': '',
        'smtp_use_tls': '1',
    }
    for key, value in setting_defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
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


@app.route('/admin/backup_database')
def backup_database():
    if not session.get('admin_logged_in'):
        return redirect(url_for('index'))
    if session.get('admin_role') != 'superadmin':
        flash('❌ เฉพาะบัญชี superadmin เท่านั้นที่สำรองข้อมูลได้', 'danger')
        return redirect(url_for('admin_dashboard', module='stock'))

    backup_timestamp = get_thailand_time().strftime('%Y%m%d_%H%M%S')
    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup_path = os.path.join(BACKUP_DIR, f'factory_stock_backup_{backup_timestamp}.db')

    try:
        source_conn = sqlite3.connect(DB_NAME)
        backup_conn = sqlite3.connect(backup_path)
        try:
            source_conn.backup(backup_conn)
        finally:
            backup_conn.close()
            source_conn.close()
    except Exception:
        if os.path.exists(backup_path):
            os.remove(backup_path)
        raise

    return send_file(
        backup_path,
        as_attachment=True,
        download_name=f'factory_stock_backup_{backup_timestamp}.db',
        mimetype='application/octet-stream'
    )

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
    return "🚀 สั่งรันระบบแจ้งเตือนเรียบร้อย! เช็ค Email และ Terminal"

@app.route('/admin/get_monthly_report_data')
def get_monthly_report_data():
    if not session.get('admin_logged_in'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    month = request.args.get('month')
    year = request.args.get('year')
    
    # ดึงข้อมูลวันที่ทั้ง 2 รูปแบบที่พบใน DB มาใช้ในการกรองข้อมูล
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
            p.code as "รหัสของ",
            p.name as "รายการของ",
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
        try:
            saved_time = conn.execute("SELECT value FROM settings WHERE key = 'daily_alert_time'").fetchone()
        except Exception:
            saved_time = None
        conn.close()
        
        alert_time = saved_time['value'] if saved_time else "08:30"
        h, m = alert_time.split(':')

        # 3. เพิ่ม Job เข้าไปในระบบ (ถ้ามีอยู่แล้วให้ทับของเก่า)
        scheduler.add_job(
            id='Daily_Alert_Job',
            func=scheduled_daily_alert, # ชื่อฟังก์ชันส่ง Email
            trigger='cron',
            hour=int(h),
            minute=int(m),
            timezone='Asia/Bangkok',
            replace_existing=True
        )
        
        scheduler.start()

    debug_mode = os.environ.get('FLASK_DEBUG', '0') == '1'
    app.run(host='0.0.0.0', port=5000, debug=debug_mode, use_reloader=debug_mode) # ควบคุมผ่าน environment