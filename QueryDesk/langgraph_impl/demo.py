"""Create a fresh local dataset from this application's own seed file."""
import sqlite3
from contextlib import closing
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE = APP_DIR / "data" / "sales.db"


def _synthetic_contacts(connection):
    """Add fictional contacts without replacing any existing contact values."""
    existing={row[1] for row in connection.execute('PRAGMA table_info(customers)')}
    changed=False
    for column in ('phone','mobile','email'):
        if column not in existing:
            connection.execute('ALTER TABLE customers ADD COLUMN '+column+' TEXT')
            changed=True
    for identifier, in connection.execute('SELECT customer_id FROM customers WHERE customer_id BETWEEN 1 AND 12').fetchall():
        values=(f'+1-202-555-{100+identifier:04d}',f'+1-202-555-{150+identifier:04d}',f'customer{identifier:02d}@customers.example.test',identifier)
        if connection.execute('SELECT 1 FROM customers WHERE customer_id=? AND (phone IS NULL OR mobile IS NULL OR email IS NULL)',(identifier,)).fetchone():
            connection.execute('UPDATE customers SET phone=COALESCE(phone,?),mobile=COALESCE(mobile,?),email=COALESCE(email,?) WHERE customer_id=?',values)
            changed=True
    return changed


def add_synthetic_contacts(filename=DEFAULT_DATABASE):
    target=Path(filename).resolve()
    if not target.is_file():
        raise ValueError('Sample database missing')
    with closing(sqlite3.connect(target)) as connection:
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            return _synthetic_contacts(connection)


def validate_dataset(connection):
    """Reject inconsistent relationships, cash totals or inventory before committing."""
    if connection.execute("PRAGMA foreign_key_check").fetchall():
        raise ValueError("Sample data contains invalid relationships")
    checks = {
        "Inventory snapshot does not reconcile to movements": """
            SELECT 1 FROM inventory i LEFT JOIN (
                SELECT warehouse_id,product_id,SUM(quantity_change) total
                FROM inventory_movements GROUP BY warehouse_id,product_id
            ) m USING(warehouse_id,product_id) WHERE i.quantity <> COALESCE(m.total,0) LIMIT 1""",
        "Refund exceeds a captured payment": """
            SELECT 1 FROM refunds r JOIN payments p USING(payment_id)
            WHERE r.status IN ('succeeded','pending') GROUP BY r.payment_id
            HAVING p.status <> 'succeeded' OR SUM(r.amount_cents) > p.amount_cents LIMIT 1""",
        "Return and refund refer to different orders": """
            SELECT 1 FROM refunds r JOIN returns t USING(return_id) JOIN payments p USING(payment_id)
            WHERE t.order_id <> p.order_id LIMIT 1""",
        "Discount exceeds order line value": """
            SELECT 1 FROM (SELECT order_id,SUM(amount_cents) discount FROM order_discounts GROUP BY order_id) d
            JOIN (SELECT order_id,SUM(quantity*unit_price_cents) gross FROM order_items GROUP BY order_id) i
            USING(order_id) WHERE d.discount > i.gross LIMIT 1""",
        "Supplier receipt exceeds ordered quantity": """
            SELECT 1 FROM purchase_order_items i JOIN inventory_movements m USING(purchase_order_id,product_id)
            WHERE m.movement_type='purchase_receipt' GROUP BY i.purchase_order_id,i.product_id
            HAVING SUM(m.quantity_change) > i.ordered_quantity LIMIT 1""",
        "Return quantity exceeds quantity ordered": """
            SELECT 1 FROM returns r JOIN order_items i USING(order_id,product_id)
            WHERE r.status <> 'rejected' GROUP BY r.order_id,r.product_id
            HAVING SUM(r.quantity) > i.quantity LIMIT 1""",
    }
    for message, sql in checks.items():
        if connection.execute(sql).fetchone():
            raise ValueError(message)


def create_demo(filename=DEFAULT_DATABASE, *, expanded=True):
    target = Path(filename).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("xb"):
        pass
    connection = sqlite3.connect(target)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        sql = (APP_DIR / "seed.sql").read_text(encoding="utf-8")
        if expanded:
            sql += "\n" + (APP_DIR / "dataset_extension.sql").read_text(encoding="utf-8")
        connection.executescript(sql)
        if expanded:
            validate_dataset(connection)
            _synthetic_contacts(connection)
        elif connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("Demo seed contains invalid relationships")
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def expand_demo(filename=DEFAULT_DATABASE):
    """Upgrade an existing sample atomically, without replacing existing records."""
    target = Path(filename).resolve()
    if not target.is_file():
        raise ValueError("Create a sample database first with --init-demo")
    connection = sqlite3.connect(target.as_uri() + "?mode=rw", uri=True)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version == 2:
            validate_dataset(connection)
            changed=_synthetic_contacts(connection)
            connection.commit()
            return changed
        if version != 0:
            raise ValueError("Unsupported sample dataset version")
        required = {"customers", "products", "orders", "order_items", "payments", "shipments",
                    "inventory", "warehouses", "suppliers", "product_suppliers"}
        existing = {r[0] for r in connection.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if not required <= existing:
            raise ValueError("This upgrade requires the original sample sales schema")
        connection.executescript("BEGIN IMMEDIATE;\n" + (APP_DIR / "dataset_extension.sql").read_text(encoding="utf-8"))
        validate_dataset(connection)
        _synthetic_contacts(connection)
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
