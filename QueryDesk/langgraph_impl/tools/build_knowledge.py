"""Maintainer utility: write reviewed SQL examples and independently specified golden rows.

Run explicitly after reviewing changes; application startup never executes this utility.
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "knowledge"

CAPTURED = "SELECT SUM(amount_cents)/100.0 FROM payments WHERE status=:status"
REFUNDS = "SELECT SUM(amount_cents)/100.0 FROM refunds WHERE status=:status"
NET = """SELECT (SELECT COALESCE(SUM(amount_cents),0) FROM payments WHERE status=:status)/100.0
 - (SELECT COALESCE(SUM(amount_cents),0) FROM refunds WHERE status=:status)/100.0 AS net_cash_inr"""
MONTHLY = """SELECT month,SUM(amount)/100.0 AS net_cash_inr FROM (
 SELECT substr(paid_at,1,7) month,amount_cents amount FROM payments WHERE status=:status
 UNION ALL SELECT substr(refunded_at,1,7),-amount_cents FROM refunds WHERE status=:status
) GROUP BY month ORDER BY month"""
GROSS = "SELECT SUM(quantity*unit_price_cents)/100.0 FROM order_items"
DISCOUNTED = """SELECT (SELECT SUM(quantity*unit_price_cents) FROM order_items)/100.0
 - (SELECT SUM(amount_cents) FROM order_discounts)/100.0"""
OUTSTANDING = """WITH lines AS (
 SELECT order_id,SUM(quantity*unit_price_cents) gross FROM order_items GROUP BY order_id
), discounts AS (SELECT order_id,SUM(amount_cents) amount FROM order_discounts GROUP BY order_id),
paid AS (SELECT order_id,SUM(amount_cents) amount FROM payments WHERE status=:paid GROUP BY order_id)
SELECT SUM(l.gross-COALESCE(d.amount,0)-COALESCE(p.amount,0))/100.0 FROM orders o
JOIN lines l USING(order_id) LEFT JOIN discounts d USING(order_id) LEFT JOIN paid p USING(order_id)
WHERE o.status <> :cancelled"""
INBOUND = """WITH receipts AS (
 SELECT purchase_order_id,product_id,SUM(quantity_change) qty FROM inventory_movements
 WHERE movement_type=:kind GROUP BY purchase_order_id,product_id
) SELECT p.name,i.ordered_quantity-COALESCE(r.qty,0) AS outstanding_units
FROM purchase_order_items i JOIN purchase_orders o USING(purchase_order_id)
JOIN products p USING(product_id) LEFT JOIN receipts r USING(purchase_order_id,product_id)
WHERE o.purchase_order_id=:po ORDER BY p.product_id"""
SEGMENTS = """SELECT s.name,SUM(p.amount_cents)/100.0 AS captured_inr FROM payments p
JOIN orders o USING(order_id) JOIN customer_profiles cp ON cp.customer_id=o.customer_id
JOIN customer_segments s USING(segment_id) WHERE p.status=:status GROUP BY s.segment_id,s.name ORDER BY s.name"""
SUCCESS = {"status":"succeeded"}


def build():
    examples = []

    def example(identifier,question,sql,parameters,tables,tags):
        examples.append({"id":identifier,"question":question,"sql":sql,"parameters":parameters,
                         "tables":tables,"tags":tags,"review":"Reviewed sample SQL; adapt filters to the user's question"})

    example("sales_region_orders","Count completed orders by customer region",
        "SELECT c.region,COUNT(*) order_count FROM orders o JOIN customers c USING(customer_id) WHERE o.status=:status GROUP BY c.region ORDER BY c.region",
        {"status":"completed"},["orders","customers"],["sales","orders","completed","region"])
    example("sales_order_amounts","Compute order amounts after applied discounts",
        """WITH items AS (SELECT order_id,SUM(quantity*unit_price_cents) amount FROM order_items GROUP BY order_id),
        discounts AS (SELECT order_id,SUM(amount_cents) amount FROM order_discounts GROUP BY order_id)
        SELECT o.order_id,(i.amount-COALESCE(d.amount,0))/100.0 total_inr FROM orders o
        JOIN items i USING(order_id) LEFT JOIN discounts d USING(order_id) ORDER BY o.order_id""",
        {},["orders","order_items","order_discounts"],["discounted order value","discounts","orders","sales"])
    example("payments_customer","Show succeeded payment totals per customer in INR",
        """SELECT c.name,SUM(p.amount_cents)/100.0 captured_inr FROM payments p
        JOIN orders o USING(order_id) JOIN customers c USING(customer_id) WHERE p.status=:status
        GROUP BY c.customer_id,c.name ORDER BY c.customer_id""", SUCCESS,
        ["payments","orders","customers"],["captured payments","customer","cash"])
    example("payments_net_cash","Compute net collected cash after successful refunds",NET,SUCCESS,
        ["payments","refunds"],["net cash","net collected cash","refunds"])
    example("payments_monthly_cash","Report monthly net cash using capture and refund event dates",MONTHLY,SUCCESS,
        ["payments","refunds"],["monthly net cash","cash","month","refunds","paid_at"])
    example("payments_outstanding","Compute outstanding balances on non-cancelled orders",OUTSTANDING,
        {"paid":"succeeded","cancelled":"cancelled"},["orders","order_items","order_discounts","payments"],
        ["outstanding balance","discounted","unpaid","payments"])
    example("customers_no_orders","Find customers without any orders",
        "SELECT c.name FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.customer_id) ORDER BY c.customer_id",
        {},["customers","orders"],["customer","never","no orders"])
    example("inventory_threshold","List product and warehouse names with stock at or below a supplied threshold",
        """SELECT p.name,w.name,i.quantity FROM inventory i JOIN products p USING(product_id)
        JOIN warehouses w USING(warehouse_id) WHERE i.quantity<=:threshold ORDER BY w.warehouse_id,p.product_id""",
        {"threshold":5},["inventory","products","warehouses"],["low stock","warehouse","inventory","quantity"])
    example("inventory_inbound","Show remaining receipt quantities for a purchase order",INBOUND,
        {"kind":"purchase_receipt","po":2},["inventory_movements","purchase_order_items","purchase_orders","products"],
        ["purchase","partially received","outstanding inbound","receipt"])
    example("segments_captured","Compare succeeded payment totals across customer segments",SEGMENTS,SUCCESS,
        ["payments","orders","customer_profiles","customer_segments"],["segment","captured payments","customer"])

    cases = []

    def golden(identifier,question,sql,parameters,tables,rows,ordered=False):
        cases.append({"id":identifier,"question":question,"reference_sql":sql,"parameters":parameters,
                      "tables":tables,"expected_status":"answered","expected_rows":rows,
                      "ordered":ordered,"absolute_tolerance":0.000001})

    golden("order_count","How many orders exist across all dates? Return only the count.",
        "SELECT COUNT(*) FROM orders",{},["orders"],[[25]])
    golden("completed_count","How many orders have completed status? Return only the count.",
        "SELECT COUNT(*) FROM orders WHERE status=:status",{"status":"completed"},["orders"],[[16]])
    golden("orders_monthly","Return year-month and order count for all orders, sorted by month.",
        "SELECT substr(ordered_at,1,7),COUNT(*) FROM orders GROUP BY 1 ORDER BY 1",{},["orders"],
        [["2026-09",4],["2026-10",7],["2026-11",14]],True)
    golden("customers_without_orders","Return only the names of customers who have never placed an order.",
        "SELECT name FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id=c.customer_id)",
        {},["customers","orders"],[["Noor"]])
    golden("unsold_products","Return only the names of products never included in an order.",
        "SELECT name FROM products p WHERE NOT EXISTS (SELECT 1 FROM order_items i WHERE i.product_id=p.product_id)",
        {},["products","order_items"],[["Portable SSD"]])
    golden("captured_total","Across all dates, return only the total succeeded payments in INR.",CAPTURED,SUCCESS,["payments"],[[9125.0]])
    golden("refund_total","Across all dates, return only the total succeeded refunds in INR.",REFUNDS,SUCCESS,["refunds"],[[1630.0]])
    golden("net_cash","Across all dates, return only net collected cash in INR: succeeded payments minus succeeded refunds.",NET,SUCCESS,
        ["payments","refunds"],[[7495.0]])
    golden("pending_refunds","Return only the total pending refund amount in INR.",REFUNDS,{"status":"pending"},["refunds"],[[225.0]])
    golden("discount_total","Return only the total actual applied order discount amount in INR.",
        "SELECT SUM(amount_cents)/100.0 FROM order_discounts",{},["order_discounts"],[[1045.0]])
    golden("outstanding_total","Return only total outstanding order balance in INR, after discounts and succeeded payments, excluding cancelled orders.",
        OUTSTANDING,{"paid":"succeeded","cancelled":"cancelled"},["orders","order_items","order_discounts","payments"],[[1880.0]])
    golden("monthly_net_cash","Return year-month and net collected cash in INR, ordered by month; use payment dates and refund dates respectively.",
        MONTHLY,SUCCESS,["payments","refunds"],[["2026-09",1890.0],["2026-10",1940.0],["2026-11",3665.0]],True)
    golden("zero_stock","Return product name and warehouse name for zero stock locations, with no other columns.",
        "SELECT p.name,w.name FROM inventory i JOIN products p USING(product_id) JOIN warehouses w USING(warehouse_id) WHERE i.quantity=0",
        {},["inventory","products","warehouses"],[["Webcam","Kolkata Depot"]])
    golden("purchase_partial","For purchase order 2, return product name and remaining units awaiting receipt, sorted by product id.",
        INBOUND,{"kind":"purchase_receipt","po":2},["inventory_movements","purchase_order_items","purchase_orders","products"],
        [["USB-C Dock",3],["Laptop Stand",5]],True)
    golden("failed_payments","Return only the number of failed payment attempts.",
        "SELECT COUNT(*) FROM payments WHERE status=:status",{"status":"failed"},["payments"],[[3]])
    golden("segment_cash","Return segment name and succeeded payment total in INR, sorted alphabetically by segment name.",
        SEGMENTS,SUCCESS,["payments","orders","customer_profiles","customer_segments"],
        [["Business",4185.0],["New",980.0],["Retail",2190.0],["VIP",1770.0]],True)
    golden("stacked_promotions","Return order id and count of promotion-linked discounts for orders with at least two, sorted by order id.",
        "SELECT order_id,COUNT(*) FROM order_discounts WHERE promotion_id IS NOT NULL GROUP BY order_id HAVING COUNT(*)>=2 ORDER BY order_id",
        {},["order_discounts"],[[119,2]],True)
    golden("received_returns","Return only the number of received item return records.",
        "SELECT COUNT(*) FROM returns WHERE status=:status",{"status":"received"},["returns"],[[3]])
    golden("gross_value","Return only gross order value in INR, before discounts, for all order statuses.",GROSS,{},["order_items"],[[12250.0]])
    golden("discounted_value","Return only discounted order value in INR across all statuses, using actual applied discounts.",
        DISCOUNTED,{},["order_items","order_discounts"],[[11205.0]])
    golden("customer_counts","Return customer name and order count including customers with zero orders, sorted by customer id.",
        "SELECT c.name,COUNT(o.order_id) FROM customers c LEFT JOIN orders o USING(customer_id) GROUP BY c.customer_id ORDER BY c.customer_id",
        {},["customers","orders"],[["Mira",3],["Arjun",2],["Leena",2],["Kabir",2],["Dev",3],["Nisha",3],
         ["Ravi",3],["Sana",2],["Omar",2],["Isha",2],["Tara",1],["Noor",0]],True)
    golden("total_stock","Return only the sum of stock on hand units across all warehouse/product snapshots.",
        "SELECT SUM(quantity) FROM inventory",{},["inventory"],[[590]])
    golden("inbound_units","Return only total outstanding inbound units across non-cancelled purchase orders, subtracting actual received units.",
        """WITH r AS (SELECT purchase_order_id,product_id,SUM(quantity_change) qty FROM inventory_movements
        WHERE movement_type=:kind GROUP BY purchase_order_id,product_id)
        SELECT SUM(i.ordered_quantity-COALESCE(r.qty,0)) FROM purchase_order_items i
        JOIN purchase_orders p USING(purchase_order_id) LEFT JOIN r USING(purchase_order_id,product_id)
        WHERE p.status<>:cancelled""", {"kind":"purchase_receipt","cancelled":"cancelled"},
        ["inventory_movements","purchase_order_items","purchase_orders"],[[13]])
    golden("missing_emails","Return only customer names with a NULL email in their profile, sorted by customer id.",
        "SELECT c.name FROM customers c JOIN customer_profiles p USING(customer_id) WHERE p.email IS NULL ORDER BY c.customer_id",
        {},["customers","customer_profiles"],[["Omar"],["Noor"]],True)
    golden("manual_discount","Return only total manual order discounts in INR, where no promotion is linked.",
        "SELECT SUM(amount_cents)/100.0 FROM order_discounts WHERE promotion_id IS NULL",{},["order_discounts"],[[100.0]])
    golden("cancellation_refund","Return only total succeeded refund amount in INR for refunds without an associated return.",
        "SELECT SUM(amount_cents)/100.0 FROM refunds WHERE status=:status AND return_id IS NULL",SUCCESS,["refunds"],[[1000.0]])
    golden("noncancelled_value","Return only discounted order value in INR for non-cancelled orders.",
        """WITH i AS (SELECT order_id,SUM(quantity*unit_price_cents) amount FROM order_items GROUP BY order_id),
        d AS (SELECT order_id,SUM(amount_cents) amount FROM order_discounts GROUP BY order_id)
        SELECT SUM(i.amount-COALESCE(d.amount,0))/100.0 FROM orders o JOIN i USING(order_id)
        LEFT JOIN d USING(order_id) WHERE o.status<>:cancelled""",{"cancelled":"cancelled"},["orders","order_items","order_discounts"],[[10005.0]])
    golden("unpaid_order_count","Return only the count of non-cancelled orders that have no succeeded payment.",
        "SELECT COUNT(*) FROM orders o WHERE o.status<>:cancelled AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.order_id=o.order_id AND p.status=:paid)",
        {"cancelled":"cancelled","paid":"succeeded"},["orders","payments"],[[3]])
    for identifier,question in (("ambiguous_revenue","What is our revenue?"),("ambiguous_profit","What is our profit?")):
        cases.append({"id":identifier,"question":question,"reference_sql":None,"parameters":{},"tables":[],
                      "expected_status":"clarification","expected_rows":[],"ordered":False,"absolute_tolerance":0.000001})
    OUT.mkdir(exist_ok=True)
    (OUT/"examples.json").write_text(json.dumps(examples,indent=2)+"\n",encoding="utf-8")
    (OUT/"golden.json").write_text(json.dumps(cases,indent=2)+"\n",encoding="utf-8")
    print(f"Wrote {len(examples)} reviewed examples and {len(cases)} golden questions")


if __name__ == "__main__":
    build()
