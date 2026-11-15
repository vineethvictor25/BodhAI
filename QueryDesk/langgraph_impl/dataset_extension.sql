-- Additive sample dataset v2. The caller owns the transaction.
CREATE TABLE customer_segments (
    segment_id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT NOT NULL);
CREATE TABLE customer_profiles (
    customer_id INTEGER PRIMARY KEY REFERENCES customers(customer_id),
    segment_id INTEGER NOT NULL REFERENCES customer_segments(segment_id),
    joined_at TEXT NOT NULL, email TEXT);
CREATE TABLE promotions (
    promotion_id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE,
    discount_type TEXT NOT NULL CHECK(discount_type IN ('percent','fixed')),
    discount_value INTEGER NOT NULL CHECK(discount_value > 0),
    starts_at TEXT NOT NULL, ends_at TEXT NOT NULL CHECK(ends_at >= starts_at));
CREATE TABLE order_discounts (
    discount_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(order_id),
    promotion_id INTEGER REFERENCES promotions(promotion_id),
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0), description TEXT NOT NULL);
CREATE TABLE returns (
    return_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL REFERENCES orders(order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    quantity INTEGER NOT NULL CHECK(quantity > 0), requested_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('requested','received','rejected')),
    reason TEXT NOT NULL,
    FOREIGN KEY(order_id, product_id) REFERENCES order_items(order_id, product_id));
CREATE TABLE refunds (
    refund_id INTEGER PRIMARY KEY, payment_id INTEGER NOT NULL REFERENCES payments(payment_id),
    return_id INTEGER REFERENCES returns(return_id),
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0), refunded_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('succeeded','pending','failed')), reason TEXT NOT NULL);
CREATE TABLE purchase_orders (
    purchase_order_id INTEGER PRIMARY KEY, supplier_id INTEGER NOT NULL REFERENCES suppliers(supplier_id),
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(warehouse_id), ordered_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ordered','partially_received','received','cancelled')));
CREATE TABLE purchase_order_items (
    purchase_order_id INTEGER NOT NULL REFERENCES purchase_orders(purchase_order_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    ordered_quantity INTEGER NOT NULL CHECK(ordered_quantity > 0),
    unit_cost_cents INTEGER NOT NULL CHECK(unit_cost_cents >= 0),
    PRIMARY KEY(purchase_order_id, product_id));
CREATE TABLE inventory_movements (
    movement_id INTEGER PRIMARY KEY, warehouse_id INTEGER NOT NULL REFERENCES warehouses(warehouse_id),
    product_id INTEGER NOT NULL REFERENCES products(product_id),
    movement_type TEXT NOT NULL CHECK(movement_type IN ('opening','purchase_receipt','sale','return','adjustment')),
    quantity_change INTEGER NOT NULL CHECK(quantity_change <> 0), occurred_at TEXT NOT NULL,
    purchase_order_id INTEGER, order_id INTEGER, return_id INTEGER REFERENCES returns(return_id),
    note TEXT NOT NULL,
    FOREIGN KEY(purchase_order_id, product_id) REFERENCES purchase_order_items(purchase_order_id, product_id),
    FOREIGN KEY(order_id, product_id) REFERENCES order_items(order_id, product_id),
    CHECK((movement_type IN ('opening','purchase_receipt','return') AND quantity_change > 0)
        OR (movement_type = 'sale' AND quantity_change < 0) OR movement_type = 'adjustment'),
    CHECK((movement_type='purchase_receipt' AND purchase_order_id IS NOT NULL AND order_id IS NULL AND return_id IS NULL)
        OR (movement_type='sale' AND order_id IS NOT NULL AND purchase_order_id IS NULL AND return_id IS NULL)
        OR (movement_type='return' AND return_id IS NOT NULL AND order_id IS NULL AND purchase_order_id IS NULL)
        OR (movement_type IN ('opening','adjustment') AND purchase_order_id IS NULL AND order_id IS NULL AND return_id IS NULL)));

INSERT INTO customers VALUES
    (5,'Dev','South'),(6,'Nisha','North'),(7,'Ravi','East'),(8,'Sana','West'),
    (9,'Omar','South'),(10,'Isha','North'),(11,'Tara','West'),(12,'Noor','East');
INSERT INTO customer_segments VALUES
    (1,'Retail','Individual retail purchases'),(2,'Business','Business accounts'),
    (3,'VIP','Loyalty customers'),(4,'New','Newly registered customers');
INSERT INTO customer_profiles VALUES
    (1,3,'2026-08-01','mira@example.test'),(2,2,'2026-08-01','arjun@example.test'),
    (3,1,'2026-08-10','leena@example.test'),(4,4,'2026-08-15','kabir@example.test'),
    (5,2,'2026-08-01','dev@example.test'),(6,3,'2026-08-03','nisha@example.test'),
    (7,1,'2026-08-04','ravi@example.test'),(8,2,'2026-08-05','sana@example.test'),
    (9,1,'2026-08-06',NULL),(10,3,'2026-08-07','isha@example.test'),
    (11,4,'2026-09-01','tara@example.test'),(12,4,'2026-11-01',NULL);
INSERT INTO products VALUES
    (6,'USB-C Dock','Accessories'),(7,'Laptop Stand','Accessories'),
    (8,'Desk Speaker','Audio'),(9,'HDMI Cable','Accessories'),(10,'Portable SSD','Storage');
INSERT INTO suppliers VALUES (4,'East Digital','East');
INSERT INTO product_suppliers VALUES (6,3,70000),(7,3,20000),(8,4,12000),(9,4,2500),(10,4,60000);
INSERT INTO warehouses VALUES (3,'Kolkata Depot','East');
INSERT INTO inventory VALUES
    (1,6,8),(1,7,20),(1,8,12),(1,9,100),(1,10,6),
    (2,6,3),(2,7,15),(2,8,10),(2,9,80),(2,10,5),
    (3,1,10),(3,2,5),(3,3,15),(3,4,10),(3,5,0),
    (3,6,5),(3,7,8),(3,8,6),(3,9,50),(3,10,4);
INSERT INTO orders VALUES
    (106,5,'2026-09-05','completed','INR'),(107,6,'2026-09-10','completed','INR'),
    (108,7,'2026-09-20','completed','INR'),(109,8,'2026-09-28','completed','INR'),
    (110,9,'2026-10-03','completed','INR'),(111,10,'2026-10-08','completed','INR'),
    (112,5,'2026-10-12','cancelled','INR'),(113,6,'2026-10-16','processing','INR'),
    (114,7,'2026-10-20','completed','INR'),(115,8,'2026-10-24','completed','INR'),
    (116,9,'2026-10-29','pending','INR'),(117,10,'2026-11-02','completed','INR'),
    (118,1,'2026-11-04','completed','INR'),(119,2,'2026-11-06','completed','INR'),
    (120,3,'2026-11-08','shipped','INR'),(121,4,'2026-11-09','completed','INR'),
    (122,5,'2026-11-10','completed','INR'),(123,6,'2026-11-11','pending','INR'),
    (124,7,'2026-11-11','cancelled','INR'),(125,11,'2026-11-12','processing','INR');
INSERT INTO order_items VALUES
    (106,6,1,100000),(106,9,2,5000),(107,7,2,30000),(108,8,1,20000),(108,3,1,5000),
    (109,2,2,15000),(110,1,3,10000),(111,5,1,25000),(112,6,1,100000),
    (113,7,1,30000),(113,9,4,5000),(114,4,1,10000),(114,8,2,20000),
    (115,6,1,100000),(115,7,1,30000),(116,9,10,5000),(117,1,1,10000),(117,3,2,5000),
    (118,5,2,25000),(119,7,1,30000),(119,9,2,5000),(120,6,1,100000),(120,8,1,20000),
    (121,4,3,10000),(122,2,1,15000),(122,9,3,5000),(123,3,4,5000),(124,1,1,10000),
    (125,6,1,100000),(125,7,2,30000);
INSERT INTO promotions VALUES
    (1,'SEP10','percent',10,'2026-09-01','2026-09-30'),
    (2,'LOYAL50','fixed',5000,'2026-09-01','2026-12-31'),
    (3,'OCT10','percent',10,'2026-10-01','2026-10-31'),
    (4,'NOV15','percent',15,'2026-11-01','2026-11-30'),
    (5,'FLAT50','fixed',5000,'2026-11-01','2026-11-30');
INSERT INTO order_discounts VALUES
    (1,106,1,11000,'September ten percent'),(2,107,2,5000,'Loyalty fixed discount'),
    (3,110,3,3000,'October ten percent'),(4,113,3,5000,'October ten percent'),
    (5,114,3,5000,'October ten percent'),(6,115,NULL,10000,'Manual business discount'),
    (7,117,4,3000,'November fifteen percent'),(8,118,5,5000,'Fixed fifty rupees'),
    (9,119,4,6000,'Stacked fifteen percent'),(10,119,5,5000,'Stacked fixed fifty rupees'),
    (11,120,4,18000,'November fifteen percent'),(12,122,4,4500,'November fifteen percent'),
    (13,125,4,24000,'November fifteen percent');
INSERT INTO payments VALUES
    (5,106,99000,'2026-09-05','succeeded','card'),(6,107,55000,'2026-09-10','succeeded','card'),
    (7,108,25000,'2026-09-20','succeeded','bank_transfer'),
    (8,109,20000,'2026-09-28','succeeded','card'),(9,109,10000,'2026-09-29','succeeded','bank_transfer'),
    (10,110,27000,'2026-10-03','failed','card'),(11,110,27000,'2026-10-04','succeeded','card'),
    (12,111,25000,'2026-10-08','succeeded','card'),(13,112,100000,'2026-10-12','succeeded','card'),
    (14,113,20000,'2026-10-16','succeeded','bank_transfer'),(15,114,45000,'2026-10-20','succeeded','card'),
    (16,115,60000,'2026-10-24','succeeded','bank_transfer'),(17,115,60000,'2026-10-25','succeeded','card'),
    (18,116,50000,'2026-10-29','failed','card'),(19,117,17000,'2026-11-02','succeeded','card'),
    (20,118,45000,'2026-11-04','succeeded','card'),(21,119,29000,'2026-11-06','succeeded','card'),
    (22,120,102000,'2026-11-08','succeeded','bank_transfer'),(23,121,30000,'2026-11-09','succeeded','card'),
    (24,122,25500,'2026-11-10','succeeded','card'),(25,125,68000,'2026-11-12','succeeded','bank_transfer'),
    (26,125,68000,'2026-11-12','failed','card'),(27,125,68000,'2026-11-12','pending','card');
INSERT INTO shipments VALUES
    (4,106,1,'2026-09-06','delivered'),(5,107,2,'2026-09-11','delivered'),
    (6,108,3,'2026-09-21','delivered'),(7,109,1,'2026-09-29','delivered'),
    (8,110,1,'2026-10-04','delivered'),(9,111,2,'2026-10-09','delivered'),
    (10,114,1,'2026-10-21','delivered'),(11,115,3,'2026-10-25','delivered'),
    (12,117,2,'2026-11-03','delivered'),(13,118,1,'2026-11-05','delivered'),
    (14,119,2,'2026-11-07','delivered'),(15,120,1,'2026-11-09','in_transit'),
    (16,121,3,'2026-11-10','delivered'),(17,122,1,'2026-11-11','delivered');
INSERT INTO returns VALUES
    (1,108,8,1,'2026-09-22','received','Defective speaker'),
    (2,111,5,1,'2026-10-10','received','Changed mind'),
    (3,114,8,1,'2026-10-22','received','Damaged item'),
    (4,118,5,1,'2026-11-09','requested','No longer needed'),
    (5,121,4,1,'2026-11-11','rejected','Outside return policy');
INSERT INTO refunds VALUES
    (1,7,1,20000,'2026-09-25','succeeded','Returned speaker'),
    (2,12,2,25000,'2026-10-12','succeeded','Full webcam return'),
    (3,13,NULL,100000,'2026-10-13','succeeded','Prepaid cancelled order'),
    (4,15,3,18000,'2026-10-24','succeeded','Returned speaker after ten percent discount'),
    (5,20,4,22500,'2026-11-10','pending','Requested return awaiting receipt');
INSERT INTO purchase_orders VALUES
    (1,1,1,'2026-09-01','received'),(2,3,2,'2026-09-15','partially_received'),
    (3,4,3,'2026-10-01','received'),(4,2,1,'2026-11-07','ordered'),(5,4,3,'2026-11-09','cancelled');
INSERT INTO purchase_order_items VALUES
    (1,1,10,7000),(1,3,20,3000),(2,6,6,70000),(2,7,10,20000),
    (3,8,4,12000),(3,9,30,2500),(4,2,5,11000),(5,10,10,60000);
INSERT INTO inventory_movements
    (warehouse_id,product_id,movement_type,quantity_change,occurred_at,purchase_order_id,note) VALUES
    (1,1,'purchase_receipt',10,'2026-09-03',1,'Full supplier receipt'),
    (1,3,'purchase_receipt',20,'2026-09-03',1,'Full supplier receipt'),
    (2,6,'purchase_receipt',3,'2026-09-18',2,'Partial supplier receipt'),
    (2,7,'purchase_receipt',5,'2026-09-18',2,'Partial supplier receipt'),
    (3,8,'purchase_receipt',4,'2026-10-03',3,'Full supplier receipt'),
    (3,9,'purchase_receipt',30,'2026-10-03',3,'Full supplier receipt');
INSERT INTO inventory_movements
    (warehouse_id,product_id,movement_type,quantity_change,occurred_at,order_id,note)
SELECT s.warehouse_id,i.product_id,'sale',-i.quantity,s.shipped_at,s.order_id,'Dispatched order line'
FROM shipments s JOIN order_items i ON i.order_id=s.order_id;
INSERT INTO inventory_movements
    (warehouse_id,product_id,movement_type,quantity_change,occurred_at,return_id,note) VALUES
    (3,8,'return',1,'2026-09-24',1,'Received and restocked'),
    (2,5,'return',1,'2026-10-11',2,'Received and restocked'),
    (1,8,'return',1,'2026-10-23',3,'Received and restocked');
INSERT INTO inventory_movements
    (warehouse_id,product_id,movement_type,quantity_change,occurred_at,note) VALUES
    (1,9,'adjustment',-2,'2026-11-10','Cycle count: damaged stock'),
    (3,10,'adjustment',1,'2026-11-11','Cycle count: found stock');
-- Synthetic opening balances reconcile to the preserved/current inventory snapshot.
INSERT INTO inventory_movements
    (warehouse_id,product_id,movement_type,quantity_change,occurred_at,note)
SELECT i.warehouse_id,i.product_id,'opening',i.quantity-COALESCE(m.net_change,0),
       '2026-09-01','Opening balance for sample history'
FROM inventory i LEFT JOIN (
    SELECT warehouse_id,product_id,SUM(quantity_change) net_change
    FROM inventory_movements GROUP BY warehouse_id,product_id
) m ON m.warehouse_id=i.warehouse_id AND m.product_id=i.product_id
WHERE i.quantity-COALESCE(m.net_change,0) > 0;

CREATE INDEX discounts_by_order ON order_discounts(order_id);
CREATE INDEX returns_by_order ON returns(order_id);
CREATE INDEX refunds_by_payment ON refunds(payment_id);
CREATE INDEX movements_by_product_warehouse ON inventory_movements(product_id,warehouse_id,occurred_at);
CREATE INDEX movements_by_purchase ON inventory_movements(purchase_order_id,product_id);
PRAGMA user_version=2;
