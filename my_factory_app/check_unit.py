import sqlite3
conn = sqlite3.connect('factory_stock.db')
conn.row_factory = sqlite3.Row

# ดู product 84 (CC) และ 1 (PC1)
for pid in [84, 1]:
    p = conn.execute('SELECT id, name, unit, base_unit, package_unit, conversion_rate FROM products WHERE id=?', (pid,)).fetchone()
    print(f"Product {pid}: name={p['name']}, unit={p['unit']}, base_unit={p['base_unit']}, package_unit={p['package_unit']}, conversion_rate={p['conversion_rate']}")

print()

# ดู transaction_logs 2 รายการล่าสุด
logs = conn.execute('SELECT id, emp_id, product_id, action, qty, qty_base_unit, qty_package_unit, status FROM transaction_logs ORDER BY id DESC LIMIT 4').fetchall()
for l in logs:
    print(f"Log {l['id']}: emp={l['emp_id']}, product={l['product_id']}, qty={l['qty']}, qty_base_unit={l['qty_base_unit']}, qty_package_unit={l['qty_package_unit']}, status={l['status']}")

conn.close()
