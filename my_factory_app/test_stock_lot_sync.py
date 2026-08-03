import os
import sqlite3
import tempfile
import unittest

import app as stock_app


class StockLotSyncTestCase(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        self.db_path = handle.name
        self.original_db_name = stock_app.DB_NAME
        stock_app.DB_NAME = self.db_path

        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                code TEXT NOT NULL,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                unit TEXT NOT NULL,
                package_unit TEXT,
                base_unit TEXT,
                conversion_rate INTEGER DEFAULT 1,
                base_unit_to_tablet_rate INTEGER DEFAULT 0,
                package_tablet_total INTEGER DEFAULT 0,
                safety_stock INTEGER DEFAULT 0,
                expiry_date TEXT,
                stock INTEGER DEFAULT 0
            );

            CREATE TABLE product_lots (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL,
                lot_number TEXT,
                qty INTEGER DEFAULT 0,
                received_date TEXT,
                expiry_date TEXT
            );

            CREATE TABLE open_packages (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL,
                lot_id INTEGER,
                opened_date TEXT,
                base_unit_qty INTEGER DEFAULT 0,
                extra_tablet_qty INTEGER DEFAULT 0,
                package_unit_qty_before REAL DEFAULT 1,
                status TEXT DEFAULT 'active'
            );

            CREATE TABLE transaction_logs (
                id INTEGER PRIMARY KEY,
                emp_id TEXT,
                product_id INTEGER,
                action TEXT,
                qty INTEGER,
                status TEXT,
                timestamp TEXT
            );

            CREATE TABLE carts (
                id INTEGER PRIMARY KEY,
                product_id INTEGER NOT NULL,
                qty INTEGER DEFAULT 0
            );
            """
        )
        conn.execute(
            """
            INSERT INTO products
                (id, code, name, category, unit, package_unit, base_unit, conversion_rate,
                 base_unit_to_tablet_rate, package_tablet_total, stock)
            VALUES (1, 'MEDIC-TEST-001', 'ยาทดสอบ', 'ยา', 'หลอด', 'หลอด', 'ตลับ', 10, 2, 20, 4)
            """
        )
        conn.executemany(
            """
            INSERT INTO product_lots
                (id, product_id, lot_number, qty, received_date, expiry_date)
            VALUES (?, 1, ?, ?, ?, '')
            """,
            [
                (1, 'LOT-A', 3, '2026-03-10'),
                (2, 'LOT-B', 3, '2026-03-25'),
                (3, 'LOT-C', 2, '2026-07-02'),
            ],
        )
        conn.execute(
            "INSERT INTO open_packages (product_id, base_unit_qty, extra_tablet_qty, status) VALUES (1, 6, 0, 'active')"
        )
        conn.commit()
        conn.close()

        self.client = stock_app.app.test_client()
        with self.client.session_transaction() as session:
            session["admin_logged_in"] = True
            session["admin_role"] = "superadmin"
            session["admin_name"] = "Lot Test"
            session["_csrf_token"] = "test-csrf"

    def tearDown(self):
        stock_app.DB_NAME = self.original_db_name
        try:
            os.unlink(self.db_path)
        except FileNotFoundError:
            pass

    def test_edit_split_medicine_lot_updates_displayed_package_stock(self):
        response = self.client.post(
            "/admin/update_product_lot",
            data={
                "lot_id": 3,
                "lot_number": "LOT-C",
                "qty": 4,
                "received_date": "02/07/2026",
                "expiry_date": "",
                "csrf_token": "test-csrf",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

        conn = sqlite3.connect(self.db_path)
        stock_after = conn.execute("SELECT stock FROM products WHERE id = 1").fetchone()[0]
        open_after = conn.execute(
            "SELECT base_unit_qty FROM open_packages WHERE product_id = 1 AND status = 'active'"
        ).fetchone()[0]
        conn.close()

        self.assertEqual(stock_after, 10)
        self.assertEqual(open_after, 6)

        payload = self.client.get("/admin/get_product_lots/1").get_json()
        self.assertEqual(payload["product_stock"], 10)
        self.assertEqual(payload["lot_total_qty"], 10)

    def test_split_medicine_dashboard_uses_lot_total_as_visible_stock(self):
        conn = stock_app.get_db_connection()
        product = conn.execute("SELECT * FROM products WHERE id = 1").fetchone()
        enriched = stock_app.enrich_products_for_display(conn, [product])[0]
        conn.close()

        self.assertEqual(enriched["display_stock"], 8)
        self.assertEqual(enriched["stock_source"], "lot")
        self.assertEqual(enriched["backend_stock_text"], "8 หลอด + 6 ตลับ")
        self.assertEqual(enriched["frontend_stock_text"], "86 ตลับ")

    def test_reduce_split_medicine_lot_updates_stock_but_keeps_open_units(self):
        response = self.client.post(
            "/admin/update_product_lot",
            data={
                "lot_id": 3,
                "lot_number": "LOT-C",
                "qty": 0,
                "received_date": "02/07/2026",
                "expiry_date": "",
                "csrf_token": "test-csrf",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["product_stock"], 6)

        conn = sqlite3.connect(self.db_path)
        stock_after = conn.execute("SELECT stock FROM products WHERE id = 1").fetchone()[0]
        open_after = conn.execute(
            "SELECT base_unit_qty FROM open_packages WHERE product_id = 1 AND status = 'active'"
        ).fetchone()[0]
        conn.close()

        self.assertEqual(stock_after, 6)
        self.assertEqual(open_after, 6)

    def _edit_split_product(self, *, stock, open_base_qty, open_extra_tablet_qty, open_stock_changed):
        return self.client.post(
            "/admin/edit_product",
            data={
                "code": "MEDIC-TEST-001",
                "name": "ยาทดสอบ",
                "category": "ยา",
                "unit": "หลอด",
                "package_unit": "หลอด",
                "base_unit": "ตลับ",
                "conversion_rate": 10,
                "base_unit_to_tablet_rate": 2,
                "package_tablet_total": 20,
                "split_mode": "multi",
                "split_enabled": "1",
                "stock": stock,
                "safety_stock": 0,
                "expiry_date": "",
                "open_base_qty": open_base_qty,
                "open_extra_tablet_qty": open_extra_tablet_qty,
                "open_stock_changed": "1" if open_stock_changed else "0",
                "csrf_token": "test-csrf",
            },
        )

    def test_edit_split_full_packages_adjusts_fifo_lots_without_touching_open_stock(self):
        self.assertEqual(self._edit_split_product(
            stock=12,
            open_base_qty=999,
            open_extra_tablet_qty=999,
            open_stock_changed=False,
        ).status_code, 200)

        conn = sqlite3.connect(self.db_path)
        lot_rows = conn.execute("SELECT id, qty FROM product_lots ORDER BY id").fetchall()
        stock_after = conn.execute("SELECT stock FROM products WHERE id=1").fetchone()[0]
        open_rows = conn.execute(
            "SELECT base_unit_qty, extra_tablet_qty, status FROM open_packages WHERE product_id=1 ORDER BY id"
        ).fetchall()
        conn.close()

        self.assertEqual(lot_rows, [(1, 7), (2, 3), (3, 2)])
        self.assertEqual(stock_after, 12)
        self.assertEqual(open_rows, [(6, 0, "active")])

    def test_edit_open_stock_replaces_aggregate_once_when_multiple_rows_exist(self):
        conn = sqlite3.connect(self.db_path)
        conn.executemany(
            "INSERT INTO open_packages (product_id, base_unit_qty, extra_tablet_qty, status) VALUES (1, ?, ?, 'active')",
            [(2, 1), (3, 2)],
        )
        conn.commit()
        conn.close()

        response = self._edit_split_product(
            stock=8,
            open_base_qty=7,
            open_extra_tablet_qty=3,
            open_stock_changed=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["success"])

        conn = sqlite3.connect(self.db_path)
        active_total = conn.execute('''
            SELECT COUNT(*), COALESCE(SUM(base_unit_qty), 0), COALESCE(SUM(extra_tablet_qty), 0)
            FROM open_packages WHERE product_id=1 AND status='active'
        ''').fetchone()
        closed_count = conn.execute(
            "SELECT COUNT(*) FROM open_packages WHERE product_id=1 AND status='closed'"
        ).fetchone()[0]
        lot_total = conn.execute("SELECT COALESCE(SUM(qty), 0) FROM product_lots WHERE product_id=1").fetchone()[0]
        conn.close()

        self.assertEqual(active_total, (1, 7, 3))
        self.assertEqual(closed_count, 3)
        self.assertEqual(lot_total, 8)


if __name__ == "__main__":
    unittest.main()
