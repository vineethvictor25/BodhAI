# Expanded sample dataset

Customer records now include `phone`, `mobile` and `email`. All 12 customers have synthetic contact values: phone `+1-202-555-0101`…`0112`, mobile `+1-202-555-0151`…`0162`, and `customer01@customers.example.test`…`customer12@customers.example.test`. These columns and customer names are classified as PII in the default security policy. Profile-email NULL edge cases remain unchanged. Re-running `--expand-demo` adds missing contact columns/values without replacing existing ones; query/security controls are documented in `SECURITY.md`.

This synthetic retail dataset spans September–November 2026, with a stock snapshot through November 12. It has **19 tables, 25 orders, 12 customers and 10 products**. All original sample rows were preserved. New installations receive the same expanded dataset; `--expand-demo` upgrades the original sample in one transaction and is safe to repeat.

## Domains and relationships

| Domain | Tables | Main joins |
| --- | --- | --- |
| Sales | customers, customer_profiles, customer_segments, orders, order_items, products, promotions, order_discounts | Customer → orders → items/products; profile → segment; order → discounts → promotion |
| Payments and returns | payments, returns, refunds | Order → payments → refunds; order/item → return; refund → optional return |
| Inventory and purchasing | suppliers, product_suppliers, warehouses, inventory, inventory_movements, purchase_orders, purchase_order_items, shipments | Product/supplier bridge; warehouse/product stock; supplier → purchase order → items; purchase/item → receipt movements; shipment → order/warehouse |

Foreign keys are declared in SQLite and available to the Schema Agent. The added return/order/product and purchase-order/product relationships also use composite foreign keys.

## Tables and rows

| Table | Rows | Meaning |
| --- | ---: | --- |
| customers | 12 | Customer identity and region |
| customer_segments | 4 | Retail, Business, VIP and New |
| customer_profiles | 12 | Segment, signup date and optional email |
| products | 10 | Products and categories |
| orders | 25 | Customer, order date, status and currency |
| order_items | 36 | Ordered quantities and original unit prices |
| promotions | 5 | Percent/fixed promotion definitions and validity dates |
| order_discounts | 13 | Actual applied order-level discounts, including manual/stacked discounts |
| payments | 27 | Successful, failed and pending payment attempts |
| returns | 5 | Received, requested and rejected item returns |
| refunds | 5 | Successful and pending refunds; some have no item return |
| suppliers | 4 | Supplier identity and region |
| product_suppliers | 11 | Products offered by each supplier and reference costs |
| warehouses | 3 | Chennai, Jaipur and Kolkata depots |
| inventory | 30 | Current product quantity in each warehouse |
| inventory_movements | 65 | Opening balances, receipts, dispatches, returns and adjustments |
| purchase_orders | 5 | Received, partially received, ordered and cancelled purchases |
| purchase_order_items | 8 | Ordered quantities and agreed unit costs |
| shipments | 17 | Dispatched customer orders, warehouse and delivery status |

## Metric definitions

- **Currency:** all current orders are INR. Existing `_cents` names mean integer hundredths of INR (paise). Divide by `100.0` for rupees. No tax, shipping charge or currency conversion is modeled.
- **Gross order value:** `SUM(order_items.quantity * unit_price_cents)`, before discounts. State any order-status/date filters explicitly; totals over all orders include cancelled and pending orders.
- **Discounted order value:** gross line value minus actual `order_discounts.amount_cents`. Use applied amounts, not another calculation from promotion percentages. Discounts can stack; a NULL promotion means a manual discount.
- **Captured payments:** sum payment amounts only where `payments.status='succeeded'`. Failed/pending attempts are not collected cash. Captured cash includes a prepaid cancelled order until its refund is subtracted.
- **Successful refunds:** sum only `refunds.status='succeeded'`. Pending refunds do not reduce collected cash yet. A NULL return on a refund can represent cancellation rather than an item return.
- **Net collected cash:** captured payments minus successful refunds. It is a cash metric, not a formal accounting definition of recognized revenue. A bare question about “revenue” may need clarification.
- **Outstanding order balance:** discounted order value minus successful payments, excluding cancelled orders. Returns/refunds are reported separately; do not add a refunded amount back as a customer debt.
- **Monthly cash:** group captures by `payments.paid_at` and refunds by `refunds.refunded_at`; do not use the order date for cash movement reporting.
- **Stock on hand:** `inventory.quantity`. The movement ledger reconciles to this snapshot. Sale movements occur on dispatch; only received returns are restocked. Opening balances are synthetic starting quantities for this sample history.
- **Purchase receipts:** sum positive `purchase_receipt` movements per purchase-order/product. Outstanding inbound quantity is ordered minus received; exclude cancelled purchases.
- **Product supplier costs:** reference sourcing prices, not a historical cost-of-goods or profit ledger. Purchase lines record their own agreed costs.

These definitions are represented in `knowledge/catalog.json` and supplied to the agents. Workspace routing, approved-example retrieval, column pruning and golden-question evaluation are implemented; see [ENHANCEMENTS.md](ENHANCEMENTS.md).

## Verified totals

| Question/measure | Expected result |
| --- | --- |
| How many orders? | 25 |
| Orders by month | September 4; October 7; November 14 |
| Orders by status | Completed 16; cancelled 3; pending 3; processing 2; shipped 1 |
| Successful captured payments | ₹9,125 |
| Successful refunds | ₹1,630 |
| Net collected cash | ₹7,495 |
| Pending refunds | ₹225 |
| Applied discounts | ₹1,045 |
| Outstanding balances on non-cancelled orders | ₹1,880 |
| Monthly net cash | September ₹1,890; October ₹1,940; November ₹3,665 |
| Customers without any order | Noor |
| Products never ordered | Portable SSD |
| Zero-stock product/location | Webcam, Kolkata Depot |
| Purchase order 2 still awaiting receipt | 3 USB-C Docks and 5 Laptop Stands |

Examples deliberately include multiple payments, multiple items and multiple discounts. **Aggregate each independent child table before joining totals** to avoid multiplying amounts.

## Example questions

For an eight-table report, ask:

> Create a report by customer segment and region for non-cancelled orders placed from 1 September through 30 November 2026. Show order count, gross order value, applied discounts, discounted order value, successful payments, successful refunds, net collected cash and outstanding balance, all monetary amounts in INR. Include unpaid orders with zero payments. Use cash associated with these selected orders regardless of payment/refund dates. Avoid double counting when an order has multiple items, discounts, payments or refunds. Outstanding balance is discounted order value minus successful payments; do not add refunds back as debt. Sort by outstanding balance descending, then net collected cash descending.

This uses `orders`, `customers`, `customer_profiles`, `customer_segments`, `order_items`, `order_discounts`, `payments` and `refunds`. Independently aggregated reference SQL on the unrestricted sample returns 10 segment/region rows covering 22 orders. The first three groups are New/West (1 order, INR 680 outstanding), Retail/South (2 orders, INR 500 outstanding) and VIP/North (5 orders, INR 450 outstanding). These are reference-query checks, not a live model-generation accuracy claim. The UI now displays authorized query rows in a native result table; restricted source/domain/row access can change or block this report.

The full report contains: segment, region, order count, gross order value, applied discounts, discounted order value, successful payments, successful refunds, net collected cash and outstanding balance. A preview of selected columns is:

| Segment | Region | Orders | Net collected cash (INR) | Outstanding balance (INR) |
| --- | --- | ---: | ---: | ---: |
| New | West | 1 | 680 | 680 |
| Retail | South | 2 | 270 | 500 |
| VIP | North | 5 | 920 | 450 |

The [UI guide](UI.md#what-users-see) explains table display and missing-history/limited-result notices. A model-generated query still needs review against these definitions and expected values.

```powershell
.\run.ps1 "How many orders were placed in each month?" --json
.\run.ps1 "Which customers have never placed an order?" --json
.\run.ps1 "Which products have never been ordered?" --json
.\run.ps1 "What are succeeded payments minus succeeded refunds, in INR? Aggregate the two cash streams separately." --json
.\run.ps1 "Which non-cancelled orders have an outstanding balance after applied discounts and succeeded payments?" --json
.\run.ps1 "Which orders used more than one promotion?" --json
.\run.ps1 "Show received and requested returns with customer and product names." --json
.\run.ps1 "Which warehouse/product combinations have at most five units?" --json
.\run.ps1 "For partially received purchase orders, show ordered, received and outstanding quantity by product." --json
.\run.ps1 "Show succeeded payment totals by customer segment in INR." --json
```

The expected figures above are deterministic SQLite checks. They are not a claim that every model will generate the correct SQL for every phrasing. Run the app with your configured provider to evaluate natural-language accuracy.
## Larger analytics dataset

The original retail sample remains available. See [Synthetic analytics lab](ANALYTICS_LAB.md) for an additional 20-table, 399,408-row dataset spanning January 2023–October 2026, with related commerce/intelligence databases, a combined local mirror, trend/KPI examples and point-in-time predictive fixtures.

