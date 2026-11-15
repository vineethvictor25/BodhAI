BEGIN;
CREATE TABLE customers (
    customer_id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
CREATE TABLE products (
    product_id INTEGER PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL);
CREATE TABLE orders (
    order_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL REFERENCES customers,
    ordered_at TEXT NOT NULL, status TEXT NOT NULL, currency TEXT NOT NULL DEFAULT 'INR');
CREATE TABLE order_items (
    order_id INTEGER NOT NULL REFERENCES orders, product_id INTEGER NOT NULL REFERENCES products,
    quantity INTEGER NOT NULL CHECK(quantity>0), unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents>=0),
    PRIMARY KEY(order_id, product_id));
CREATE TABLE suppliers (
    supplier_id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
CREATE TABLE product_suppliers (
    product_id INTEGER NOT NULL REFERENCES products, supplier_id INTEGER NOT NULL REFERENCES suppliers,
    cost_cents INTEGER NOT NULL CHECK(cost_cents>=0), PRIMARY KEY(product_id,supplier_id));
CREATE TABLE warehouses (
    warehouse_id INTEGER PRIMARY KEY, name TEXT NOT NULL, region TEXT NOT NULL);
CREATE TABLE inventory (
    warehouse_id INTEGER NOT NULL REFERENCES warehouses, product_id INTEGER NOT NULL REFERENCES products,
    quantity INTEGER NOT NULL CHECK(quantity>=0), PRIMARY KEY(warehouse_id,product_id));
CREATE TABLE payments (
    payment_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders,
    amount_cents INTEGER NOT NULL CHECK(amount_cents>=0), paid_at TEXT NOT NULL,
    status TEXT NOT NULL, method TEXT NOT NULL);
CREATE TABLE shipments (
    shipment_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses, shipped_at TEXT NOT NULL, status TEXT NOT NULL);

INSERT INTO customers VALUES
    (1,'Mira','South'),(2,'Arjun','North'),(3,'Leena','West'),(4,'Kabir','East');
INSERT INTO products VALUES
    (1,'Compact Keyboard','Accessories'),(2,'Office Monitor','Displays'),
    (3,'Wireless Mouse','Accessories'),(4,'USB Headset','Audio'),(5,'Webcam','Video');
INSERT INTO orders VALUES
    (101,1,'2026-11-01','completed','INR'),(102,2,'2026-11-02','completed','INR'),
    (103,1,'2026-11-03','cancelled','INR'),(104,3,'2026-11-04','completed','INR'),
    (105,4,'2026-11-05','pending','INR');
INSERT INTO order_items VALUES
    (101,1,1,10000),(101,3,1,5000),(102,2,1,15000),
    (103,1,1,10000),(104,4,2,10000),(105,5,1,25000);
INSERT INTO suppliers VALUES
    (1,'South Components','South'),(2,'North Displays','North'),(3,'West Electronics','West');
INSERT INTO product_suppliers VALUES
    (1,1,7000),(2,2,11000),(3,1,3000),(3,3,3200),(4,3,6000),(5,3,18000);
INSERT INTO warehouses VALUES (1,'Chennai Depot','South'),(2,'Jaipur Depot','North');
INSERT INTO inventory VALUES
    (1,1,30),(1,2,12),(1,3,45),(1,4,24),(1,5,10),
    (2,1,20),(2,2,18),(2,3,35),(2,4,16),(2,5,8);
INSERT INTO payments VALUES
    (1,101,15000,'2026-11-01','succeeded','card'),
    (2,102,10000,'2026-11-02','succeeded','bank_transfer'),
    (3,102,5000,'2026-11-03','succeeded','card'),
    (4,104,20000,'2026-11-04','succeeded','card');
INSERT INTO shipments VALUES
    (1,101,1,'2026-11-02','delivered'),(2,102,2,'2026-11-03','delivered'),
    (3,104,1,'2026-11-05','in_transit');
CREATE INDEX orders_by_customer ON orders(customer_id);
CREATE INDEX payments_by_order ON payments(order_id);
CREATE INDEX shipments_by_order ON shipments(order_id);
CREATE INDEX items_by_product ON order_items(product_id);
