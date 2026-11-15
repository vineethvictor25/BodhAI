import sqlite3
import tempfile
import unittest
from pathlib import Path

from langgraph_impl.demo import create_demo, expand_demo, validate_dataset
from langgraph_impl.storage import Database


class ExpandedDatasetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "sales.db"

    def connect(self):
        conn = sqlite3.connect(self.path)
        self.addCleanup(conn.close)
        return conn

    def test_upgrade_preserves_rows_and_is_repeatable(self):
        create_demo(self.path, expanded=False)
        conn = self.connect()
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")]
        original = {t: ([r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')],conn.execute(f'SELECT * FROM "{t}"').fetchall()) for t in tables}
        self.assertTrue(expand_demo(self.path))
        for table, (columns,rows) in original.items():
            selection=','.join('"'+column+'"' for column in columns)
            current = conn.execute(f'SELECT {selection} FROM "{table}"').fetchall()
            self.assertTrue(set(rows) <= set(current), table)
        before = list(conn.iterdump())
        self.assertFalse(expand_demo(self.path))
        self.assertEqual(before, list(conn.iterdump()))
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 2)

    def test_upgrade_failure_rolls_back_new_tables_and_data(self):
        create_demo(self.path, expanded=False)
        conn = self.connect()
        conn.execute("INSERT INTO customers VALUES (5,'Existing customer','South')")
        conn.commit()
        before = list(conn.iterdump())
        with self.assertRaises(sqlite3.IntegrityError):
            expand_demo(self.path)
        self.assertEqual(before, list(conn.iterdump()))
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 0)

    def test_fresh_seed_matches_existing_database_upgrade(self):
        create_demo(self.path)
        upgraded = Path(self.temp.name) / "upgraded.db"
        create_demo(upgraded, expanded=False)
        expand_demo(upgraded)
        other = sqlite3.connect(upgraded)
        self.addCleanup(other.close)
        self.assertEqual(list(self.connect().iterdump()), list(other.iterdump()))

    def test_counts_dates_and_edge_cases(self):
        create_demo(self.path)
        conn = self.connect()
        expected = {"customers":12,"products":10,"orders":25,"order_items":36,
                    "suppliers":4,"product_suppliers":11,"warehouses":3,"inventory":30,
                    "payments":27,"shipments":17,"customer_segments":4,"customer_profiles":12,
                    "promotions":5,"order_discounts":13,"returns":5,"refunds":5,
                    "purchase_orders":5,"purchase_order_items":8}
        for table, count in expected.items():
            self.assertEqual(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0], count, table)
        self.assertEqual(len(Database(self.path).allowed), 19)
        self.assertEqual(conn.execute("SELECT substr(ordered_at,1,7),COUNT(*) FROM orders GROUP BY 1").fetchall(),
                         [("2026-09",4),("2026-10",7),("2026-11",14)])
        self.assertEqual(conn.execute("SELECT name FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.customer_id)").fetchall(), [("Noor",)])
        self.assertEqual(conn.execute("SELECT name FROM products p WHERE NOT EXISTS (SELECT 1 FROM order_items i WHERE i.product_id=p.product_id)").fetchall(), [("Portable SSD",)])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM payments WHERE status='failed'").fetchone()[0], 3)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory WHERE quantity=0").fetchone()[0], 1)
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_synthetic_contacts_are_fictional_complete_and_preserve_existing_values(self):
        create_demo(self.path)
        conn=self.connect()
        rows=conn.execute('SELECT customer_id,phone,mobile,email FROM customers ORDER BY customer_id').fetchall()
        self.assertEqual(len(rows),12)
        self.assertTrue(all(phone.startswith('+1-202-555-01') and mobile.startswith('+1-202-555-01') and email.endswith('@customers.example.test') for _,phone,mobile,email in rows))
        conn.execute("UPDATE customers SET email='preserved@example.test' WHERE customer_id=1")
        conn.commit()
        from langgraph_impl.demo import add_synthetic_contacts
        self.assertFalse(add_synthetic_contacts(self.path))
        self.assertEqual(conn.execute('SELECT email FROM customers WHERE customer_id=1').fetchone()[0],'preserved@example.test')

    def test_hand_checked_cash_refunds_discounts_and_outstanding(self):
        create_demo(self.path)
        conn = self.connect()
        captured = conn.execute("SELECT SUM(amount_cents) FROM payments WHERE status='succeeded'").fetchone()[0]
        refunded = conn.execute("SELECT SUM(amount_cents) FROM refunds WHERE status='succeeded'").fetchone()[0]
        self.assertEqual((captured,refunded,captured-refunded), (912500,163000,749500))
        self.assertEqual(conn.execute("SELECT SUM(amount_cents) FROM order_discounts").fetchone()[0], 104500)
        self.assertEqual(conn.execute("SELECT SUM(amount_cents) FROM refunds WHERE status='pending'").fetchone()[0], 22500)
        outstanding = conn.execute("""WITH lines AS (
            SELECT order_id,SUM(quantity*unit_price_cents) gross FROM order_items GROUP BY order_id
        ), discounts AS (SELECT order_id,SUM(amount_cents) amount FROM order_discounts GROUP BY order_id),
        paid AS (SELECT order_id,SUM(amount_cents) amount FROM payments WHERE status='succeeded' GROUP BY order_id)
        SELECT SUM(l.gross-COALESCE(d.amount,0)-COALESCE(p.amount,0)) FROM orders o
        JOIN lines l USING(order_id) LEFT JOIN discounts d USING(order_id) LEFT JOIN paid p USING(order_id)
        WHERE o.status <> 'cancelled'""").fetchone()[0]
        self.assertEqual(outstanding, 188000)
        # Correctly avoid item/payment/refund fan-out by aggregating cash streams separately.
        monthly = conn.execute("""SELECT month,SUM(amount)/100.0 FROM (
            SELECT substr(paid_at,1,7) month,amount_cents amount FROM payments WHERE status='succeeded'
            UNION ALL SELECT substr(refunded_at,1,7),-amount_cents FROM refunds WHERE status='succeeded'
        ) GROUP BY month ORDER BY month""").fetchall()
        self.assertEqual(monthly, [("2026-09",1890.0),("2026-10",1940.0),("2026-11",3665.0)])

    def test_ledger_receipts_and_refunds_reconcile(self):
        create_demo(self.path)
        conn = self.connect()
        validate_dataset(conn)
        receipts = conn.execute("""SELECT i.product_id,i.ordered_quantity-SUM(m.quantity_change)
            FROM purchase_order_items i JOIN inventory_movements m USING(purchase_order_id,product_id)
            WHERE i.purchase_order_id=2 AND m.movement_type='purchase_receipt'
            GROUP BY i.product_id ORDER BY i.product_id""").fetchall()
        self.assertEqual(receipts, [(6,3),(7,5)])
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM inventory_movements WHERE movement_type='return'").fetchone()[0], 3)
        conn.execute("UPDATE inventory SET quantity=quantity+1 WHERE warehouse_id=1 AND product_id=1")
        with self.assertRaisesRegex(ValueError, "reconcile"):
            validate_dataset(conn)
        conn.rollback()
        validate_dataset(conn)

    def test_demo_does_not_overwrite_existing_file(self):
        create_demo(self.path)
        with self.assertRaises(FileExistsError):
            create_demo(self.path)


if __name__ == "__main__":
    unittest.main()
