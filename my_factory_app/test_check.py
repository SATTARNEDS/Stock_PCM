import sqlite3
conn = sqlite3.connect('factory_stock.db')
conn.row_factory = sqlite3.Row

print('=== PRODUCTS - CC ===')
rows = conn.execute("SELECT id, name, stock, safety_stock, unit, location FROM products WHERE is_active=1 AND stock > 0 AND location LIKE '%CC%' LIMIT 5").fetchall()
for r in rows: print(dict(r))

print('\n=== PRODUCTS - PC1 ===')
rows = conn.execute("SELECT id, name, stock, safety_stock, unit, location FROM products WHERE is_active=1 AND stock > 0 AND location='PC1' LIMIT 5").fetchall()
for r in rows: print(dict(r))

print('\n=== RECENT LOGS ===')
rows = conn.execute("SELECT id, emp_id, product_id, action, qty, status, timestamp FROM transaction_logs ORDER BY id DESC LIMIT 5").fetchall()
for r in rows: print(dict(r))

print('\n=== ADMINS ===')
rows = conn.execute("SELECT username, role FROM admins LIMIT 10").fetchall()
for r in rows: print(dict(r))

print('\n=== USERS CC ===')
rows = conn.execute("SELECT emp_id, name, location FROM users WHERE location LIKE '%CC%' LIMIT 3").fetchall()
for r in rows: print(dict(r))

print('\n=== PRODUCT_LOTS (sample) ===')
rows = conn.execute("SELECT * FROM product_lots ORDER BY id DESC LIMIT 5").fetchall()
for r in rows: print(dict(r))

conn.close()
