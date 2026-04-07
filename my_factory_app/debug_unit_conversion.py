#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Debug unit conversion"""

import sqlite3
from unit_conversion import UnitConversionManager

conn = sqlite3.connect('factory_stock.db')

# Test basic connection
print("✓ Connected to database")

# Test UnitConversionManager init
manager = UnitConversionManager(conn)
print("✓ UnitConversionManager created")

# Test basic query
cursor = conn.cursor()
cursor.execute('SELECT * FROM products WHERE id = 1')
cols = [col[0] for col in cursor.description]
row = cursor.fetchone()
print(f"✓ Direct query: Got {len(cols)} columns, {len(row)} values")
print(f"  Columns: {cols}")
print(f"  First 5: id={row[0]}, code={row[1]}, name={row[2]}, category={row[3]}, unit={row[4]}")

# Test manager method
try:
    print("\n→ Calling manager.get_product_unit_info(1)...")
    info = manager.get_product_unit_info(1)
    print("✅ SUCCESS!")
    print(f"  Product: {info}")
except Exception as e:
    print(f"❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

conn.close()
