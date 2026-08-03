#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ตรวจสอบว่า schema ใหม่ถูกสร้างสำเร็จ"""

import sqlite3

conn = sqlite3.connect('factory_stock.db')
cursor = conn.cursor()

print('=' * 60)
print('📊 ตรวจสอบ Schema ที่สร้างขึ้นมา')
print('=' * 60)

# ตรวจสอบตาราใหม่
print('\n✓ ตารางใหม่:')
cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('open_packages', 'unit_conversions')")
tables = cursor.fetchall()
for table in tables:
    print(f'  - {table[0]}')

# ตรวจสอบสดมภ์ใหม่ใน products
print('\n✓ สดมภ์ใหม่ใน products:')
cursor.execute('PRAGMA table_info(products)')
columns = cursor.fetchall()
new_cols = ['base_unit', 'package_unit', 'conversion_rate']
for col in columns:
    if col[1] in new_cols:
        print(f'  - {col[1]} ({col[2]})')

# ตรวจสอบสดมภ์ใหม่ใน transaction_logs
print('\n✓ สดมภ์ใหม่ใน transaction_logs:')
cursor.execute('PRAGMA table_info(transaction_logs)')
columns = cursor.fetchall()
new_cols = ['qty_base_unit', 'qty_package_unit', 'note']
for col in columns:
    if col[1] in new_cols:
        print(f'  - {col[1]} ({col[2]})')

print('\n' + '=' * 60)
print('✅ สำเร็จทั้งหมด! ฐานข้อมูลพร้อมแล้ว')
print('=' * 60)

conn.close()
