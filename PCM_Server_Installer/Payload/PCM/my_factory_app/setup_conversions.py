#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ตั้งค่า Unit Conversions สำหรับสินค้าตัวอย่าง
"""

import sqlite3
from datetime import datetime

def setup_pharmacy_conversions():
    """ตั้งค่าการแปลงหน่วยสำหรับยา"""
    
    conn = sqlite3.connect('factory_stock.db')
    cursor = conn.cursor()
    
    print('=' * 60)
    print('⚙️  ตั้งค่าการแปลงหน่วยสำหรับยา')
    print('=' * 60)
    
    try:
        # ดึง products ที่มี
        cursor.execute('SELECT id, code, name FROM products LIMIT 5')
        products = cursor.fetchall()
        
        if not products:
            print('❌ ไม่มีสินค้าในระบบ')
            return False
        
        print(f'\n✓ พบสินค้า {len(products)} ชิ้น:')
        for pid, code, name in products:
            print(f'  ID={pid}, Code={code}, Name={name}')
        
        # ตั้งค่าข้อมูลตัวอย่างสำหรับ 3 สินค้าแรก
        conversions = [
            (1, 'tablet', 'bottle', 20, 'Aspirin'),           # 1 bottle = 20 tablets
            (2, 'tablet', 'sheet', 10, 'Vitamin C'),          # 1 sheet = 10 tablets
            (3, 'ml', 'bottle', 100, 'Cough Syrup'),         # 1 bottle = 100 ml
        ]
        
        print('\n✓ ตั้งค่าการแปลงหน่วย:')
        for product_id, base_unit, package_unit, rate, med_name in conversions:
            # อัปเดต products table
            cursor.execute('''
                UPDATE products 
                SET base_unit = ?, package_unit = ?, conversion_rate = ?, stock = 5
                WHERE id = ?
            ''', (base_unit, package_unit, rate, product_id))
            
            # เพิ่ม/อัปเดต unit_conversions
            cursor.execute('''
                INSERT OR REPLACE INTO unit_conversions 
                (product_id, base_unit, package_unit, conversion_rate, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (product_id, base_unit, package_unit, rate, datetime.now(), datetime.now()))
            
            print(f'  ✓ {med_name}: 1 {package_unit} = {rate} {base_unit}')
        
        conn.commit()
        
        print('\n' + '=' * 60)
        print('✅ ตั้งค่าสำเร็จ!')
        print('=' * 60)
        
        return True
        
    except Exception as e:
        print(f'❌ Error: {e}')
        conn.rollback()
        return False
    finally:
        conn.close()


if __name__ == '__main__':
    setup_pharmacy_conversions()
