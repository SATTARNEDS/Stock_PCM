"""
Import IT equipment from data_imports/Stock IT.xlsx into factory_stock.db
"""
import sqlite3
import pandas as pd
import os
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'factory_stock.db')
EXCEL_PATH = os.path.join(os.path.dirname(__file__), '..', 'data_imports', 'Stock IT.xlsx')


def safe_int(v):
    try:
        val = pd.to_numeric(v, errors='coerce')
        return int(val) if not pd.isna(val) else 0
    except Exception:
        return 0


def safe_str(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    return str(v).strip()


def safe_date(v):
    """Return a clean YYYY-MM-DD string or None."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        # pandas Timestamp or datetime -> isoformat date only
        return pd.Timestamp(v).strftime('%Y-%m-%d')
    except Exception:
        s = str(v).strip()
        # strip time portion if present (e.g. '2026-04-21 00:00:00')
        return s[:10] if len(s) >= 10 else s


def main():
    if not os.path.exists(EXCEL_PATH):
        print(f"Cannot find Excel file: {EXCEL_PATH}")
        sys.exit(1)

    df = pd.read_excel(EXCEL_PATH, sheet_name='IT')
    df.columns = [str(c).strip() for c in df.columns]

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # Ensure products table has needed columns (add missing ones if any)
    c.execute("PRAGMA table_info(products)")
    existing_cols = {row[1] for row in c.fetchall()}

    added = 0
    updated = 0
    lot_added = 0
    errors = []

    for idx, row in df.iterrows():
        code = safe_str(row.get('รหัสของ'))
        if not code or code.lower() == 'nan':
            continue

        name = safe_str(row.get('ชื่อของ')) or 'No Name'
        category = safe_str(row.get('หมวดหมู่')) or 'IT'
        unit = safe_str(row.get('หน่วยนับ')) or 'pcs.'
        location = safe_str(row.get('สถานที่เก็บ (Location)')) or '-'
        safety_stock = safe_int(row.get('จุดสั่งซื้อ (Safety Stock)', 0))
        stock = safe_int(row.get('จำนวนคงเหลือ', 0))
        lot_number = safe_str(row.get('Lot No.'))
        received_date = safe_date(row.get('วันที่รับเข้า'))
        expiry_date = safe_date(row.get('วันหมดอายุ'))
        lot_qty = safe_int(row.get('จำนวนใน Lot', 0))

        try:
            # Check if product already exists
            c.execute("SELECT id, stock FROM products WHERE code = ?", (code,))
            existing = c.fetchone()

            if existing:
                product_id = existing[0]
                c.execute(
                    "UPDATE products SET name=?, category=?, unit=?, location=?, safety_stock=?, stock=? WHERE id=?",
                    (name, category, unit, location, safety_stock, stock, product_id)
                )
                updated += 1
            else:
                c.execute(
                    "INSERT INTO products (code, name, category, unit, location, safety_stock, stock) VALUES (?,?,?,?,?,?,?)",
                    (code, name, category, unit, location, safety_stock, stock)
                )
                product_id = c.lastrowid
                added += 1

            # Insert a lot if lot_qty > 0 (or stock > 0 even if no explicit lot)
            qty_for_lot = lot_qty if lot_qty > 0 else stock
            if qty_for_lot > 0:
                # Check if a lot already exists for this product on this received_date
                c.execute(
                    "SELECT id FROM product_lots WHERE product_id=? AND received_date=?",
                    (product_id, received_date)
                )
                existing_lot = c.fetchone()
                if not existing_lot:
                    c.execute(
                        "INSERT INTO product_lots (product_id, lot_number, qty, received_date, expiry_date) VALUES (?,?,?,?,?)",
                        (product_id, lot_number, qty_for_lot, received_date, expiry_date)
                    )
                    lot_added += 1

        except Exception as e:
            errors.append(f"Row {idx} ({code}): {e}")

    conn.commit()
    conn.close()

    print(f"Import complete.")
    print(f"  Products added   : {added}")
    print(f"  Products updated : {updated}")
    print(f"  Lots added       : {lot_added}")
    if errors:
        print(f"  Errors ({len(errors)}):")
        for err in errors:
            print(f"    - {err}")


if __name__ == '__main__':
    main()
