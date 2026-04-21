import sqlite3
conn = sqlite3.connect('factory_stock.db')
conn.row_factory = sqlite3.Row

cols = conn.execute('PRAGMA table_info(products)').fetchall()
print('products columns:', [c['name'] for c in cols])

p = conn.execute('SELECT id, name, stock, withdraw FROM products WHERE id=84').fetchone()
lots = conn.execute('SELECT id, qty, received_date FROM product_lots WHERE product_id=84').fetchall()
total_lots = sum(l['qty'] for l in lots)
print(f'\nProduct 84: stock={p["stock"]}, withdraw={p["withdraw"]}')
print(f'product_lots total qty={total_lots}')
for l in lots:
    print(f'  lot id={l["id"]}, qty={l["qty"]}, received={l["received_date"]}')

# ดู trigger ถ้ามี
triggers = conn.execute("SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name='products'").fetchall()
print('\nTriggers on products:', [(t['name'],) for t in triggers])

conn.close()
