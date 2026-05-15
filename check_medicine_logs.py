import sqlite3

conn = sqlite3.connect('my_factory_app/factory_stock.db')
conn.row_factory = sqlite3.Row

print("=== ขอเบิกยา logs ===")
rows = conn.execute(
    "SELECT l.id, l.action, l.qty, l.qty_base_unit, l.qty_package_unit, "
    "l.status, l.timestamp, p.name as product_name, p.base_unit, p.unit "
    "FROM transaction_logs l "
    "LEFT JOIN products p ON l.product_id = p.id "
    "WHERE l.action = 'ขอเบิกยา' "
    "ORDER BY l.id DESC LIMIT 15"
).fetchall()
for r in rows:
    print(f"id={r['id']} | qty={r['qty']} | qty_base={r['qty_base_unit']} | status={r['status']} | product={r['product_name']} | base_unit={r['base_unit']} | ts={r['timestamp']}")

print("\n=== Anti-allergy products ===")
rows_p = conn.execute(
    "SELECT id, name, unit, base_unit, package_unit, conversion_rate, category "
    "FROM products "
    "WHERE name LIKE '%allergy%' OR name LIKE '%แก้แพ้%'"
).fetchall()
for r in rows_p:
    print(dict(r))

print("\n=== Anti-allergy / medicine logs (any action, recent) ===")
rows3 = conn.execute(
    "SELECT l.id, l.action, l.qty, l.qty_base_unit, l.status, l.timestamp, p.name "
    "FROM transaction_logs l "
    "LEFT JOIN products p ON l.product_id = p.id "
    "WHERE (p.name LIKE '%allergy%' OR p.name LIKE '%แก้แพ้%') "
    "ORDER BY l.id DESC LIMIT 10"
).fetchall()
for r in rows3:
    print(dict(r))

print("\n=== ขอเบิกอุปกรณ์ Approved (Medicine category) ===")
rows2 = conn.execute(
    "SELECT l.id, l.action, l.qty, l.qty_base_unit, l.status, l.timestamp, "
    "p.name as product_name, p.base_unit, p.category "
    "FROM transaction_logs l "
    "LEFT JOIN products p ON l.product_id = p.id "
    "WHERE l.action = 'ขอเบิกอุปกรณ์' AND l.status = 'Approved' "
    "AND p.category = 'ยา' "
    "ORDER BY l.id DESC LIMIT 10"
).fetchall()
for r in rows2:
    print(f"id={r['id']} | qty={r['qty']} | qty_base={r['qty_base_unit']} | product={r['product_name']} | base_unit={r['base_unit']} | ts={r['timestamp']}")

conn.close()
