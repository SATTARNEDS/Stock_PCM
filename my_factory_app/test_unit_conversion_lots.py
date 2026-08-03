import sqlite3
import unittest

from unit_conversion import UnitConversionManager


class UnitConversionLotTestCase(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE products (
                id INTEGER PRIMARY KEY,
                code TEXT,
                name TEXT,
                unit TEXT,
                stock INTEGER,
                reserved_stock INTEGER DEFAULT 0,
                base_unit TEXT,
                package_unit TEXT,
                conversion_rate REAL,
                base_unit_to_tablet_rate INTEGER,
                package_tablet_total INTEGER
            );
            CREATE TABLE carts (product_id INTEGER, qty INTEGER);
            CREATE TABLE product_lots (
                id INTEGER PRIMARY KEY,
                product_id INTEGER,
                lot_number TEXT,
                qty INTEGER,
                received_date TEXT,
                expiry_date TEXT
            );
            CREATE TABLE open_packages (
                id INTEGER PRIMARY KEY,
                product_id INTEGER,
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
                lot_id INTEGER,
                action TEXT,
                qty INTEGER,
                qty_base_unit INTEGER,
                qty_package_unit REAL,
                note TEXT,
                status TEXT,
                timestamp TEXT
            );
            INSERT INTO products
                (id,code,name,unit,stock,base_unit,package_unit,conversion_rate,base_unit_to_tablet_rate,package_tablet_total)
            VALUES (1,'MED-1','Test medicine','แผง',2,'ห่อ','แผง',2,4,10);
            INSERT INTO product_lots(id,product_id,lot_number,qty,received_date)
            VALUES (1,1,'OLD',1,'2026-01-01'),(2,1,'NEW',1,'2026-02-01');
            """
        )
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_opening_package_consumes_oldest_lot_and_records_trace(self):
        result = UnitConversionManager(self.conn).apply_withdrawal(1, 1, "E001")

        self.assertTrue(result["success"], result)
        self.assertEqual(result["lot_id"], 1)
        self.assertEqual(result["lot_detail"], "OLD x 1")
        self.assertEqual(
            [tuple(row) for row in self.conn.execute("SELECT id,qty FROM product_lots ORDER BY id")],
            [(1, 0), (2, 1)],
        )
        self.assertEqual(self.conn.execute("SELECT stock FROM products WHERE id=1").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT lot_id FROM open_packages").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT lot_id FROM transaction_logs").fetchone()[0], 1)

    def test_using_open_stock_does_not_consume_another_lot(self):
        self.conn.execute(
            "INSERT INTO open_packages(product_id,lot_id,base_unit_qty,status) VALUES(1,1,1,'active')"
        )
        result = UnitConversionManager(self.conn).apply_withdrawal(1, 1, "E001")

        self.assertTrue(result["success"], result)
        self.assertEqual(result["full_packages_needed"], 0)
        self.assertEqual(
            [tuple(row) for row in self.conn.execute("SELECT qty FROM product_lots ORDER BY id")],
            [(1,), (1,)],
        )
        self.assertEqual(self.conn.execute("SELECT stock FROM products WHERE id=1").fetchone()[0], 2)

    def test_insufficient_lot_rolls_back_product_and_lot_changes(self):
        self.conn.execute("UPDATE product_lots SET qty=0")
        self.conn.commit()
        result = UnitConversionManager(self.conn).apply_withdrawal(1, 1, "E001")

        self.assertFalse(result["success"])
        self.assertIn("ของไม่พอ", result["message"])
        self.assertEqual(self.conn.execute("SELECT stock FROM products WHERE id=1").fetchone()[0], 2)
        self.assertEqual(self.conn.execute("SELECT COALESCE(SUM(qty),0) FROM product_lots").fetchone()[0], 0)

    def test_withdrawal_uses_lot_total_when_legacy_product_stock_is_stale(self):
        self.conn.execute("UPDATE products SET stock=32 WHERE id=1")
        self.conn.commit()

        manager = UnitConversionManager(self.conn)
        self.assertEqual(manager.get_product_unit_info(1)["stock_package_unit"], 2)

        result = manager.apply_withdrawal(1, 1, "E001")

        self.assertTrue(result["success"], result)
        self.assertEqual(self.conn.execute("SELECT COALESCE(SUM(qty),0) FROM product_lots").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT stock FROM products WHERE id=1").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
