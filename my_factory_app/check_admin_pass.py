import sqlite3
from werkzeug.security import check_password_hash

conn = sqlite3.connect('factory_stock.db')
conn.row_factory = sqlite3.Row
admins = conn.execute('SELECT username, password, role FROM admins').fetchall()
candidates = ['admin', 'cc', 'pc1', '1234', 'password', 'Admin@1234', 'pcm_admin', 'PCM@1234', '123456', 'admin123']
for a in admins:
    found = False
    for pw in candidates:
        if check_password_hash(a['password'], pw):
            print(f"{a['username']} (role={a['role']}) -> password: {pw}")
            found = True
            break
    if not found:
        print(f"{a['username']} (role={a['role']}) -> UNKNOWN (hash prefix: {a['password'][:30]}...)")
conn.close()
