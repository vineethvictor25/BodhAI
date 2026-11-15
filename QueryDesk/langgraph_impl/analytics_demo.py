"""Deterministic analytics lab: two related databases, local mirror and SQL Server seeds."""
import argparse
from collections import defaultdict
from contextlib import closing
from datetime import date,timedelta
import hashlib
import json
from pathlib import Path
import random
import sqlite3

from .config import APP_DIR

DEFAULT_LAB=APP_DIR/'data'/'analytics_lab'
DATABASES={'commerce':'AnalyticsCommerce','intelligence':'AnalyticsIntelligence'}
SCHEMA={
 'commerce_regions':'region_id INTEGER PRIMARY KEY, region_name TEXT NOT NULL, country TEXT NOT NULL',
 'commerce_calendar':'calendar_date DATE PRIMARY KEY, year INTEGER NOT NULL, month INTEGER NOT NULL, quarter INTEGER NOT NULL, is_weekend INTEGER NOT NULL',
 'commerce_categories':'category_id INTEGER PRIMARY KEY, category_name TEXT NOT NULL',
 'commerce_products':'product_id INTEGER PRIMARY KEY, category_id INTEGER NOT NULL, product_name TEXT NOT NULL, list_price_cents INTEGER NOT NULL, FOREIGN KEY(category_id) REFERENCES commerce_categories(category_id)',
 'intelligence_segments':'segment_id INTEGER PRIMARY KEY, segment_name TEXT NOT NULL, description TEXT NOT NULL',
 'commerce_customers':'customer_id INTEGER PRIMARY KEY, account_code TEXT NOT NULL, region_id INTEGER NOT NULL, segment_id INTEGER NOT NULL, joined_date DATE NOT NULL, email TEXT, phone TEXT, FOREIGN KEY(region_id) REFERENCES commerce_regions(region_id), FOREIGN KEY(segment_id) REFERENCES intelligence_segments(segment_id)',
 'intelligence_campaigns':'campaign_id INTEGER PRIMARY KEY, campaign_name TEXT NOT NULL, region_id INTEGER NOT NULL, channel TEXT NOT NULL, start_date DATE NOT NULL, end_date DATE NOT NULL, FOREIGN KEY(region_id) REFERENCES commerce_regions(region_id)',
 'commerce_orders':'order_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, order_date DATE NOT NULL, status TEXT NOT NULL, channel TEXT NOT NULL, campaign_id INTEGER, gross_sales_cents INTEGER NOT NULL, discount_cents INTEGER NOT NULL, net_sales_cents INTEGER NOT NULL, tax_cents INTEGER NOT NULL, shipping_cents INTEGER NOT NULL, FOREIGN KEY(customer_id) REFERENCES commerce_customers(customer_id), FOREIGN KEY(campaign_id) REFERENCES intelligence_campaigns(campaign_id)',
 'commerce_order_items':'order_item_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity INTEGER NOT NULL, unit_price_cents INTEGER NOT NULL, discount_cents INTEGER NOT NULL, cost_cents INTEGER NOT NULL, FOREIGN KEY(order_id) REFERENCES commerce_orders(order_id), FOREIGN KEY(product_id) REFERENCES commerce_products(product_id)',
 'commerce_payments':'payment_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, payment_date DATE NOT NULL, status TEXT NOT NULL, method TEXT NOT NULL, amount_cents INTEGER NOT NULL, FOREIGN KEY(order_id) REFERENCES commerce_orders(order_id)',
 'commerce_refunds':'refund_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, payment_id INTEGER NOT NULL, refund_date DATE NOT NULL, amount_cents INTEGER NOT NULL, reason TEXT NOT NULL, FOREIGN KEY(order_id) REFERENCES commerce_orders(order_id), FOREIGN KEY(payment_id) REFERENCES commerce_payments(payment_id)',
 'commerce_shipments':'shipment_id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, shipped_date DATE NOT NULL, delivered_date DATE, carrier TEXT NOT NULL, shipping_cost_cents INTEGER NOT NULL, FOREIGN KEY(order_id) REFERENCES commerce_orders(order_id)',
 'intelligence_campaign_daily':'campaign_date DATE NOT NULL, campaign_id INTEGER NOT NULL, impressions INTEGER NOT NULL, clicks INTEGER NOT NULL, spend_cents INTEGER NOT NULL, PRIMARY KEY(campaign_date,campaign_id), FOREIGN KEY(campaign_id) REFERENCES intelligence_campaigns(campaign_id)',
 'intelligence_support_tickets':'ticket_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, created_date DATE NOT NULL, resolved_date DATE, topic TEXT NOT NULL, satisfaction_score INTEGER, FOREIGN KEY(customer_id) REFERENCES commerce_customers(customer_id)',
 'intelligence_business_events':'event_id INTEGER PRIMARY KEY, start_date DATE NOT NULL, end_date DATE NOT NULL, event_type TEXT NOT NULL, description TEXT NOT NULL',
 'intelligence_sales_targets':'month_date DATE NOT NULL, region_id INTEGER NOT NULL, category_id INTEGER NOT NULL, planned_date DATE NOT NULL, target_net_sales_cents INTEGER NOT NULL, target_units INTEGER NOT NULL, PRIMARY KEY(month_date,region_id,category_id), FOREIGN KEY(region_id) REFERENCES commerce_regions(region_id), FOREIGN KEY(category_id) REFERENCES commerce_categories(category_id)',
 'commerce_monthly_sales':'month_date DATE NOT NULL, region_id INTEGER NOT NULL, category_id INTEGER NOT NULL, completed_orders INTEGER NOT NULL, units INTEGER NOT NULL, gross_sales_cents INTEGER NOT NULL, discount_cents INTEGER NOT NULL, net_sales_cents INTEGER NOT NULL, cost_cents INTEGER NOT NULL, PRIMARY KEY(month_date,region_id,category_id), FOREIGN KEY(region_id) REFERENCES commerce_regions(region_id), FOREIGN KEY(category_id) REFERENCES commerce_categories(category_id)',
 'intelligence_customer_month_features':'snapshot_month DATE NOT NULL, customer_id INTEGER NOT NULL, tenure_days INTEGER NOT NULL, completed_orders INTEGER NOT NULL, net_sales_cents INTEGER NOT NULL, units INTEGER NOT NULL, recency_days INTEGER, prior_3m_orders INTEGER NOT NULL, lag_12m_net_sales_cents INTEGER, tickets_last_90d INTEGER NOT NULL, PRIMARY KEY(snapshot_month,customer_id), FOREIGN KEY(customer_id) REFERENCES commerce_customers(customer_id)',
 'intelligence_customer_month_outcomes':'snapshot_month DATE NOT NULL, customer_id INTEGER NOT NULL, label_month DATE NOT NULL, next_month_orders INTEGER NOT NULL, next_month_net_sales_cents INTEGER NOT NULL, inactive_next_month INTEGER NOT NULL, PRIMARY KEY(snapshot_month,customer_id), FOREIGN KEY(snapshot_month,customer_id) REFERENCES intelligence_customer_month_features(snapshot_month,customer_id)',
 'intelligence_sales_forecasts':'forecast_id INTEGER PRIMARY KEY, origin_month DATE NOT NULL, target_month DATE NOT NULL, region_id INTEGER NOT NULL, category_id INTEGER NOT NULL, horizon_months INTEGER NOT NULL, training_max_date DATE NOT NULL, forecast_units INTEGER NOT NULL, lower_scenario_units INTEGER NOT NULL, upper_scenario_units INTEGER NOT NULL, method TEXT NOT NULL, FOREIGN KEY(region_id) REFERENCES commerce_regions(region_id), FOREIGN KEY(category_id) REFERENCES commerce_categories(category_id)'
}


def month_add(value,offset):
    number=value.year*12+value.month-1+offset
    return date(number//12,number%12+1,1)


def season(month):return {1:.82,2:.88,3:.98,4:1.02,5:1.04,6:.93,7:.96,8:1.03,9:1.08,10:1.24,11:1.45,12:1.35}[month]


def insert(connection,table,values):
    connection.execute('INSERT INTO '+table+' VALUES('+','.join('?' for _ in values)+')',values)


def relationships(connection):
    result=[]
    for table in SCHEMA:
        grouped={}
        for key,_,target,local,remote,*_ in connection.execute('PRAGMA foreign_key_list('+table+')'):
            grouped.setdefault(key,{'from_table':table,'to_table':target,'columns':{}})['columns'][local]=remote
        result.extend(grouped.values())
    return result


def populate(connection,seed,customers,months,orders_per_day):
    rng=random.Random(seed);start=date(2023,1,1);end=month_add(start,months)-timedelta(days=1)
    for table,columns in SCHEMA.items():connection.execute('CREATE TABLE '+table+' ('+columns+')')
    for index,name in enumerate(('North','South','East','West'),1):insert(connection,'commerce_regions',(index,name,'India'))
    for index,name in enumerate(('Home','Electronics','Lifestyle','Sports'),1):insert(connection,'commerce_categories',(index,name))
    for index,name in enumerate(('Value','Regular','Premium'),1):insert(connection,'intelligence_segments',(index,name,'Synthetic '+name.lower()+' purchasing segment'))
    for event in ((1,'2024-06-15','2024-06-22','disruption','Synthetic temporary demand shock'),(2,'2025-11-15','2025-11-18','promotion','Synthetic promotion and volume spike')):
        if start<=date.fromisoformat(event[1])<=end:insert(connection,'intelligence_business_events',event)
    day=start-timedelta(days=31)
    while day<=month_add(start,months+5):
        insert(connection,'commerce_calendar',(day.isoformat(),day.year,day.month,(day.month-1)//3+1,int(day.weekday()>=5)));day+=timedelta(days=1)
    products={}
    for identifier in range(1,49):
        category=(identifier-1)//12+1;price=rng.randrange(5000,85000,100)
        products[identifier]=(category,price)
        insert(connection,'commerce_products',(identifier,category,f'Synthetic product {identifier:02}',price))
    people={};propensity={}
    for identifier in range(1,customers+1):
        joined=start if identifier<=min(80,customers) else month_add(start,rng.randrange(min(months,30)))+timedelta(days=rng.randrange(28))
        region=(identifier-1)%4+1;segment=rng.choices([1,2,3],[.3,.5,.2])[0]
        people[identifier]=(joined,region,segment)
        propensity[identifier]=(rng.uniform(.5,2)*(1+segment*.15),rng.randrange(max(8,months//2),months+12))
        insert(connection,'commerce_customers',(identifier,f'ACCT-{identifier:05}',region,segment,joined.isoformat(),
            None if identifier%11==0 else f'account{identifier:05}@example.test',None if identifier%5==0 else f'+1-202-555-01{identifier%100:02}'))
    campaigns={};campaign_id=0
    for offset in range(months):
        beginning=month_add(start,offset);finish=month_add(start,offset+1)-timedelta(days=1)
        for region in range(1,5):
            campaigns[offset,region]=[]
            for channel in ('search','social','email'):
                campaign_id+=1;campaigns[offset,region].append(campaign_id)
                insert(connection,'intelligence_campaigns',(campaign_id,f'{channel.title()} {beginning:%Y-%m} region {region}',region,channel,beginning.isoformat(),finish.isoformat()))
    order_id=item_id=payment_id=refund_id=shipment_id=0
    campaign_orders=defaultdict(int);customer_month=defaultdict(lambda:[0,0,0,None])
    day=start
    while day<=end:
        offset=(day.year-start.year)*12+day.month-start.month
        active=[identifier for identifier,(joined,_,_) in people.items() if joined<=day]
        weights=[propensity[identifier][0]*(1 if offset<propensity[identifier][1] else .07) for identifier in active]
        volume=max(1,round(orders_per_day*(1+.014*offset)*season(day.month)*(1.18 if day.weekday()>=5 else .96)*rng.uniform(.7,1.3)))
        if date(2024,6,15)<=day<=date(2024,6,22):volume=max(1,round(volume*.3))
        if date(2025,11,15)<=day<=date(2025,11,18):volume=round(volume*2.5)
        for customer in rng.choices(active,weights,k=volume):
            order_id+=1;_,region,_=people[customer]
            campaign=rng.choice(campaigns[offset,region]) if rng.random()<.64 else None
            if campaign:campaign_orders[day.isoformat(),campaign]+=1
            status=rng.choices(['completed','cancelled','processing','pending'],[.88,.06,.035,.025])[0]
            lines=[];gross=discount=cost=units=0
            for product in rng.sample(list(products),rng.randint(1,3)):
                quantity=rng.choices([1,2,3,4],[.65,.23,.09,.03])[0]
                price=round(products[product][1]*(1+.035*(day.year-start.year)))
                reduction=round(quantity*price*rng.choice([0,0,.05,.1,.15]))
                historical_cost=round(quantity*price*rng.uniform(.4,.7))
                gross+=quantity*price;discount+=reduction;cost+=historical_cost;units+=quantity
                item_id+=1;lines.append((item_id,order_id,product,quantity,price,reduction,historical_cost))
            net=gross-discount;tax=round(net*.12);shipping=0 if net>=150000 else 4900
            insert(connection,'commerce_orders',(order_id,customer,day.isoformat(),status,rng.choice(['web','mobile','store','marketplace']),campaign,gross,discount,net,tax,shipping))
            connection.executemany('INSERT INTO commerce_order_items VALUES(?,?,?,?,?,?,?)',lines)
            if status=='completed':
                key=(customer,offset);row=customer_month[key];row[0]+=1;row[1]+=net;row[2]+=units;row[3]=day
                if rng.random()<.08:
                    payment_id+=1;insert(connection,'commerce_payments',(payment_id,order_id,day.isoformat(),'failed','card',net+tax+shipping))
                # Some orders have split captures: child fan-out must be aggregated.
                total=net+tax+shipping;parts=[total] if rng.random()<.85 else [total//2,total-total//2]
                captured=[]
                for amount in parts:
                    payment_id+=1;captured.append((payment_id,amount));insert(connection,'commerce_payments',(payment_id,order_id,day.isoformat(),'captured',rng.choice(['card','upi','wallet']),amount))
                if day<=end-timedelta(days=12) and rng.random()<.06:
                    refund_id+=1;paid,amount=captured[0];refund=min(amount,round(net*rng.uniform(.1,.45)))
                    insert(connection,'commerce_refunds',(refund_id,order_id,paid,(day+timedelta(days=rng.randint(3,12))).isoformat(),refund,rng.choice(['damaged','changed_mind','late_delivery'])))
                shipped=day+timedelta(days=rng.randint(0,2))
                delivered=shipped+timedelta(days=rng.randint(1,8))
                if shipped<=end:
                    shipment_id+=1;insert(connection,'commerce_shipments',(shipment_id,order_id,shipped.isoformat(),delivered.isoformat() if delivered<=end else None,rng.choice(['Demo Express','Demo Parcel']),rng.randint(2500,8000)))
        for region in range(1,5):
            for campaign in campaigns[offset,region]:
                attributed=campaign_orders[day.isoformat(),campaign];clicks=attributed+rng.randint(30,160)
                insert(connection,'intelligence_campaign_daily',(day.isoformat(),campaign,clicks*rng.randint(12,30),clicks,rng.randint(12,55)*clicks))
        day+=timedelta(days=1)
    ticket_dates=defaultdict(list);ticket_id=0
    for customer,(joined,_,_) in people.items():
        for _ in range(rng.randrange(5)):
            created=joined+timedelta(days=rng.randrange((end-joined).days+1));resolved=created+timedelta(days=rng.randrange(1,15))
            ticket_id+=1;ticket_dates[customer].append(created)
            insert(connection,'intelligence_support_tickets',(ticket_id,customer,created.isoformat(),resolved.isoformat() if resolved<=end and rng.random()>.12 else None,
                rng.choice(['delivery','billing','quality','account']),None if rng.random()<.2 else rng.randint(1,5)))
    connection.execute("""INSERT INTO commerce_monthly_sales
        SELECT substr(o.order_date,1,7)||'-01',c.region_id,p.category_id,COUNT(DISTINCT o.order_id),SUM(i.quantity),
               SUM(i.quantity*i.unit_price_cents),SUM(i.discount_cents),SUM(i.quantity*i.unit_price_cents-i.discount_cents),SUM(i.cost_cents)
        FROM commerce_orders o JOIN commerce_customers c USING(customer_id) JOIN commerce_order_items i USING(order_id)
        JOIN commerce_products p USING(product_id) WHERE o.status='completed' GROUP BY 1,2,3""")
    for customer,(joined,_,_) in people.items():
        last=None
        for offset in range(months):
            beginning=month_add(start,offset);finish=month_add(start,offset+1)-timedelta(days=1)
            if joined>finish:continue
            counts,net,units,latest=customer_month[customer,offset]
            if latest:last=latest
            lag=customer_month.get((customer,offset-12)) if offset>=12 else None
            insert(connection,'intelligence_customer_month_features',(beginning.isoformat(),customer,(finish-joined).days,counts,net,units,
                (finish-last).days if last else None,sum(customer_month.get((customer,k),[0])[0] for k in range(max(0,offset-3),offset)),
                lag[1] if lag is not None else None,sum(finish-timedelta(days=89)<=d<=finish for d in ticket_dates[customer])))
            if offset+1<months:
                target=customer_month.get((customer,offset+1),[0,0])
                insert(connection,'intelligence_customer_month_outcomes',(beginning.isoformat(),customer,month_add(beginning,1).isoformat(),target[0],target[1],int(target[0]==0)))
    actual={(r,c,m):(units,net) for m,r,c,units,net in connection.execute('SELECT month_date,region_id,category_id,units,net_sales_cents FROM commerce_monthly_sales')}
    forecast_id=0
    for offset in range(months):
        beginning=month_add(start,offset)
        for region in range(1,5):
            for category in range(1,5):
                factor=season(beginning.month)*(1+.014*offset)
                insert(connection,'intelligence_sales_targets',(beginning.isoformat(),region,category,(beginning-timedelta(days=15)).isoformat(),round(4800000*factor*(1+.04*region)),round(200*factor)))
                if offset<12:continue
                prior=sum(actual.get((region,category,month_add(beginning,-12-k).isoformat()),(0,0))[0] for k in range(3))
                recent=sum(actual.get((region,category,month_add(beginning,-k).isoformat()),(0,0))[0] for k in range(3))
                growth=min(1.4,max(.7,recent/prior)) if prior else 1.
                for horizon in (1,2,3):
                    target=month_add(beginning,horizon)
                    baseline=actual.get((region,category,month_add(target,-12).isoformat()),(0,0))[0]
                    predicted=round(baseline*growth);forecast_id+=1
                    insert(connection,'intelligence_sales_forecasts',(forecast_id,beginning.isoformat(),target.isoformat(),region,category,horizon,
                        (month_add(beginning,1)-timedelta(days=1)).isoformat(),predicted,round(predicted*.8),round(predicted*1.2),'seasonal_naive_trend'))
    for table,columns in (('commerce_orders','customer_id,order_date'),('commerce_orders','order_date,status'),('commerce_order_items','order_id'),
                          ('commerce_order_items','product_id'),('commerce_payments','order_id,status'),('commerce_refunds','order_id'),
                          ('commerce_shipments','order_id'),('intelligence_support_tickets','customer_id,created_date')):
        name='idx_'+table+'_'+columns.replace(',','_')
        connection.execute('CREATE INDEX '+name+' ON '+table+'('+columns+')')
    return start,end


def validate(connection):
    checks={
      'foreign_keys':not connection.execute('PRAGMA foreign_key_check').fetchall(),
      'line_totals':not connection.execute('SELECT 1 FROM commerce_orders o JOIN (SELECT order_id,SUM(quantity*unit_price_cents) g,SUM(discount_cents) d FROM commerce_order_items GROUP BY order_id) i USING(order_id) WHERE o.gross_sales_cents<>i.g OR o.discount_cents<>i.d OR o.net_sales_cents<>i.g-i.d LIMIT 1').fetchone(),
      'captured_cash':not connection.execute("SELECT 1 FROM commerce_orders o LEFT JOIN (SELECT order_id,SUM(amount_cents) a FROM commerce_payments WHERE status='captured' GROUP BY order_id) p USING(order_id) WHERE (o.status='completed' AND COALESCE(p.a,0)<>o.net_sales_cents+o.tax_cents+o.shipping_cents) OR (o.status<>'completed' AND COALESCE(p.a,0)<>0) LIMIT 1").fetchone(),
      'refunds':not connection.execute("SELECT 1 FROM commerce_refunds r JOIN commerce_payments p USING(payment_id) WHERE r.order_id<>p.order_id OR p.status<>'captured' OR r.amount_cents>p.amount_cents OR r.refund_date<p.payment_date LIMIT 1").fetchone(),
      'monthly_revenue':connection.execute('SELECT SUM(net_sales_cents) FROM commerce_monthly_sales').fetchone()[0]==connection.execute("SELECT SUM(net_sales_cents) FROM commerce_orders WHERE status='completed'").fetchone()[0],
      'feature_dates':not connection.execute("SELECT 1 FROM intelligence_customer_month_features f JOIN commerce_customers c USING(customer_id) WHERE date(f.snapshot_month,'+1 month','-1 day')<c.joined_date LIMIT 1").fetchone(),
      'labels':not connection.execute("SELECT 1 FROM intelligence_customer_month_outcomes WHERE label_month<>date(snapshot_month,'+1 month') OR label_month>(SELECT MAX(month_date) FROM commerce_monthly_sales) LIMIT 1").fetchone(),
      'forecast_dates':not connection.execute("SELECT 1 FROM intelligence_sales_forecasts WHERE target_month<=origin_month OR training_max_date<>date(origin_month,'+1 month','-1 day') OR target_month<>date(origin_month,'+'||horizon_months||' months') LIMIT 1").fetchone(),
      'campaign_scope':not connection.execute('SELECT 1 FROM commerce_orders o JOIN intelligence_campaigns p USING(campaign_id) JOIN commerce_customers c USING(customer_id) WHERE c.region_id<>p.region_id OR o.order_date<p.start_date OR o.order_date>p.end_date LIMIT 1').fetchone(),
      'targets_preplanned':not connection.execute('SELECT 1 FROM intelligence_sales_targets WHERE planned_date>=month_date LIMIT 1').fetchone()
    }
    checks['order_dates']=not connection.execute('SELECT 1 FROM commerce_orders o JOIN commerce_customers c USING(customer_id) WHERE o.order_date<c.joined_date LIMIT 1').fetchone()
    checks['positive_values']=not connection.execute('SELECT 1 FROM commerce_order_items WHERE quantity<=0 OR unit_price_cents<=0 OR discount_cents<0 OR discount_cents>quantity*unit_price_cents OR cost_cents<0 LIMIT 1').fetchone()
    checks['cumulative_refunds']=not connection.execute('SELECT 1 FROM commerce_refunds r JOIN commerce_payments p USING(payment_id) GROUP BY r.payment_id HAVING SUM(r.amount_cents)>MAX(p.amount_cents) LIMIT 1').fetchone()
    if not all(checks.values()):raise ValueError('Analytics dataset checks failed: '+','.join(key for key,value in checks.items() if not value))
    return checks


def write_json(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')


def split_databases(connection,directory):
    for domain in DATABASES:
        with closing(sqlite3.connect(directory/(domain+'.db'))) as target:
            for table,definition in SCHEMA.items():
                if not table.startswith(domain+'_'):continue
                physical=table.removeprefix(domain+'_')
                # Cross-file foreign keys cannot be declared in SQLite. Reconcile them in the mirror/validator.
                parts=definition.split(', FOREIGN KEY')
                own=parts[0]
                for clause in parts[1:]:
                    parent=clause.split(' REFERENCES ')[1].split('(')[0]
                    if parent.startswith(domain+'_'):own+=', FOREIGN KEY'+clause.replace(parent,parent.removeprefix(domain+'_'))
                target.execute('CREATE TABLE '+physical+' ('+own+')')
                cursor=connection.execute('SELECT * FROM '+table)
                while rows:=cursor.fetchmany(1000):target.executemany('INSERT INTO '+physical+' VALUES('+','.join('?' for _ in cursor.description)+')',rows)
            target.commit()


def sqlserver_seed(connection,path):
    def literal(value):
        if value is None:return 'NULL'
        return str(value) if type(value) is int else "N'"+str(value).replace("'","''")+"'"
    with path.open('w',encoding='utf-8') as output:
        output.write(':ON ERROR EXIT\n')
        output.write("-- Synthetic analytics lab only. Requires permission to create two NEW databases.\n")
        output.write("IF DB_ID(N'AnalyticsCommerce') IS NOT NULL OR DB_ID(N'AnalyticsIntelligence') IS NOT NULL\n    THROW 50000, N'Demo databases already exist. Refusing to overwrite them.', 1;\nGO\n")
        for database in DATABASES.values():output.write('CREATE DATABASE ['+database+'];\nGO\n')
        for table in SCHEMA:
            domain,physical=table.split('_',1);database=DATABASES[domain]
            columns=list(connection.execute('PRAGMA table_info('+table+')'))
            definitions=[];primary=[]
            for _,name,kind,notnull,_,pk in columns:
                datatype='BIGINT' if kind=='INTEGER' else 'DATE' if kind=='DATE' else 'NVARCHAR(200)'
                definitions.append('['+name+'] '+datatype+(' NOT NULL' if notnull or pk else ' NULL'))
                if pk:primary.append((pk,name))
            if primary:definitions.append('PRIMARY KEY ('+','.join('['+name+']' for _,name in sorted(primary))+')')
            output.write('USE ['+database+'];\nGO\nCREATE TABLE [dbo].['+physical+'] ('+', '.join(definitions)+');\nGO\n')
            cursor=connection.execute('SELECT * FROM '+table)
            while rows:=cursor.fetchmany(100):
                output.write('INSERT INTO [dbo].['+physical+'] VALUES\n'+',\n'.join('('+','.join(literal(value) for value in row)+')' for row in rows)+';\n')
            output.write('GO\n')
            for _,index_name,_,origin,*_ in connection.execute('PRAGMA index_list('+table+')'):
                if origin!='c':continue
                names=[row[2] for row in connection.execute('PRAGMA index_info('+index_name+')')]
                output.write('CREATE INDEX ['+index_name+'] ON [dbo].['+physical+'] ('+','.join('['+name+']' for name in names)+');\nGO\n')
        output.write('-- Cross-database relationships are supplied in the mapped source profile; SQL Server cannot declare cross-database foreign keys.\n')


def example_queries():
    return [
      ('monthly_net_sales','Show monthly completed net product sales in INR for trend analysis.',"SELECT month_date,SUM(net_sales_cents)/100.0 AS net_product_sales_inr FROM commerce_monthly_sales GROUP BY month_date ORDER BY month_date",['commerce_monthly_sales']),
      ('regional_sales','Compare completed net product sales by region.',"SELECT r.region_name,SUM(o.net_sales_cents)/100.0 net_product_sales_inr FROM commerce_orders o JOIN commerce_customers c ON c.customer_id=o.customer_id JOIN commerce_regions r ON r.region_id=c.region_id WHERE o.status='completed' GROUP BY r.region_name ORDER BY r.region_name",['commerce_orders','commerce_customers','commerce_regions']),
      ('net_collected','What is net collected cash in INR after refunds?',"WITH paid AS (SELECT SUM(amount_cents) amount FROM commerce_payments WHERE status='captured'), refunded AS (SELECT SUM(amount_cents) amount FROM commerce_refunds) SELECT (paid.amount-COALESCE(refunded.amount,0))/100.0 net_collected_inr FROM paid CROSS JOIN refunded",['commerce_payments','commerce_refunds']),
      ('cross_database_segments','Compare completed sales and order counts by customer segment across both domains.',"SELECT s.segment_name,COUNT(*) completed_orders,SUM(o.net_sales_cents)/100.0 net_product_sales_inr FROM commerce_orders o JOIN commerce_customers c ON c.customer_id=o.customer_id JOIN intelligence_segments s ON s.segment_id=c.segment_id WHERE o.status='completed' GROUP BY s.segment_name ORDER BY s.segment_name",['commerce_orders','commerce_customers','intelligence_segments']),
      ('campaign_roas','Which campaigns have the highest return on ad spend? Aggregate sales and daily spend separately.',"WITH sales AS (SELECT campaign_id,SUM(net_sales_cents) amount FROM commerce_orders WHERE status='completed' AND campaign_id IS NOT NULL GROUP BY campaign_id), spend AS (SELECT campaign_id,SUM(spend_cents) amount FROM intelligence_campaign_daily GROUP BY campaign_id) SELECT c.campaign_name,s.amount/100.0 net_product_sales_inr,p.amount/100.0 spend_inr,1.0*s.amount/NULLIF(p.amount,0) roas FROM sales s JOIN spend p ON p.campaign_id=s.campaign_id JOIN intelligence_campaigns c ON c.campaign_id=s.campaign_id ORDER BY roas DESC LIMIT 20",['commerce_orders','intelligence_campaign_daily','intelligence_campaigns']),
      ('target_attainment','Show monthly net sales, sales targets and percentage attainment.',"WITH actual AS (SELECT month_date,SUM(net_sales_cents) amount FROM commerce_monthly_sales GROUP BY month_date), target AS (SELECT month_date,SUM(target_net_sales_cents) amount FROM intelligence_sales_targets GROUP BY month_date) SELECT a.month_date,a.amount/100.0 actual_inr,t.amount/100.0 target_inr,100.0*a.amount/NULLIF(t.amount,0) attainment_percent FROM actual a JOIN target t ON t.month_date=a.month_date ORDER BY a.month_date",['commerce_monthly_sales','intelligence_sales_targets']),
      ('retention_features','Compare average recency and completed orders by customer segment for the latest scoring month.',"SELECT s.segment_name,AVG(f.recency_days) average_recency_days,SUM(f.completed_orders) completed_orders FROM intelligence_customer_month_features f JOIN commerce_customers c ON c.customer_id=f.customer_id JOIN intelligence_segments s ON s.segment_id=c.segment_id WHERE f.snapshot_month=(SELECT MAX(snapshot_month) FROM intelligence_customer_month_features) GROUP BY s.segment_name ORDER BY s.segment_name",['intelligence_customer_month_features','commerce_customers','intelligence_segments']),
      ('forecast_backtest','Compare one-month-ahead forecasts with observed monthly units by target month.',"WITH actual AS (SELECT month_date,SUM(units) units FROM commerce_monthly_sales GROUP BY month_date), forecast AS (SELECT target_month,SUM(forecast_units) units FROM intelligence_sales_forecasts WHERE horizon_months=1 GROUP BY target_month) SELECT f.target_month,f.units forecast_units,a.units actual_units,f.units-a.units error_units FROM forecast f LEFT JOIN actual a ON a.month_date=f.target_month ORDER BY f.target_month",['commerce_monthly_sales','intelligence_sales_forecasts']),
      ('growth_windows','Show monthly sales, trailing three-month average and year-over-year growth.',"WITH monthly AS (SELECT month_date,SUM(net_sales_cents) amount FROM commerce_monthly_sales GROUP BY month_date) SELECT month_date,amount/100.0 net_product_sales_inr,AVG(amount/100.0) OVER(ORDER BY month_date ROWS BETWEEN 2 PRECEDING AND CURRENT ROW) trailing_3m_average_inr,100.0*(amount-LAG(amount,12) OVER(ORDER BY month_date))/NULLIF(LAG(amount,12) OVER(ORDER BY month_date),0) yoy_growth_percent FROM monthly ORDER BY month_date",['commerce_monthly_sales']),
      ('cohort_retention','Track monthly active account share for the January 2023 acquisition cohort.',"SELECT f.snapshot_month,COUNT(*) cohort_accounts,SUM(CASE WHEN f.completed_orders>0 THEN 1 ELSE 0 END) active_accounts,100.0*SUM(CASE WHEN f.completed_orders>0 THEN 1 ELSE 0 END)/COUNT(*) active_share_percent FROM intelligence_customer_month_features f JOIN commerce_customers c ON c.customer_id=f.customer_id JOIN commerce_calendar d ON d.calendar_date=c.joined_date WHERE d.year=2023 AND d.month=1 GROUP BY f.snapshot_month ORDER BY f.snapshot_month",['intelligence_customer_month_features','commerce_customers','commerce_calendar']),
      ('segment_margin','Compare completed product sales, historical line cost, product margin before refunds and refunded amounts by segment.',"WITH costs AS (SELECT order_id,SUM(cost_cents) amount FROM commerce_order_items GROUP BY order_id), refunds AS (SELECT order_id,SUM(amount_cents) amount FROM commerce_refunds GROUP BY order_id) SELECT s.segment_name,SUM(o.net_sales_cents)/100.0 net_product_sales_inr,SUM(k.amount)/100.0 historical_cost_inr,SUM(o.net_sales_cents-k.amount)/100.0 product_margin_before_refunds_inr,SUM(COALESCE(r.amount,0))/100.0 refunded_inr FROM commerce_orders o JOIN commerce_customers c ON c.customer_id=o.customer_id JOIN intelligence_segments s ON s.segment_id=c.segment_id JOIN costs k ON k.order_id=o.order_id LEFT JOIN refunds r ON r.order_id=o.order_id WHERE o.status='completed' GROUP BY s.segment_name ORDER BY s.segment_name",['commerce_orders','commerce_customers','intelligence_segments','commerce_order_items','commerce_refunds']),
      ('historical_inactivity','Show observed next-month inactivity rates and mean recency for historical labeled months.',"SELECT f.snapshot_month,COUNT(*) labeled_accounts,100.0*SUM(y.inactive_next_month)/COUNT(*) next_month_inactive_percent,AVG(f.recency_days) mean_recency_days FROM intelligence_customer_month_features f JOIN intelligence_customer_month_outcomes y ON y.snapshot_month=f.snapshot_month AND y.customer_id=f.customer_id GROUP BY f.snapshot_month ORDER BY f.snapshot_month",['intelligence_customer_month_features','intelligence_customer_month_outcomes'])
    ]


def assets(connection,directory,final):
    relations=relationships(connection)
    mapping={table:{'database':DATABASES[table.split('_',1)[0]],'schema':'dbo','table':table.split('_',1)[1]} for table in SCHEMA}
    common={'allowed_tables':list(SCHEMA),'knowledge_directory':str(final/'knowledge'),'row_limit':500,'timeout_seconds':15}
    local={**common,'id':'analytics_lab','name':'Analytics lab · local combined mirror','kind':'sqlite','connection_env':'ANALYTICS_LAB_DATABASE'}
    native={**common,'id':'analytics_lab_sqlserver','name':'Analytics lab · two SQL Server databases','kind':'mssql','connection_env':'ANALYTICS_LAB_SQLSERVER_URL','table_mappings':mapping,'relationships':relations}
    write_json(directory/'sources.local.json',[local]);write_json(directory/'sources.sqlserver.json',[native])
    commerce=[t for t in SCHEMA if t.startswith('commerce_')];intelligence=[t for t in SCHEMA if t.startswith('intelligence_')]
    spaces={'commerce':{'name':'Commerce','description':'Orders, products, historical item costs, captured payments, refunds and monthly sales','keywords':['order','sales','product','cash','region','trend','margin'],'tables':commerce+['intelligence_segments']},
            'growth':{'name':'Customer and marketing intelligence','description':'Segments, campaigns, daily ad costs, support and sales planning','keywords':['marketing','campaign','spend','segment','support','target'],'tables':intelligence+['commerce_customers','commerce_orders','commerce_regions','commerce_monthly_sales']},
            'predictive':{'name':'Predictive analysis lab','description':'As-of customer features, separately timestamped outcomes and seasonal baseline forecasts','keywords':['forecast','prediction','churn','retention','feature','cohort','backtest'],'tables':intelligence+['commerce_customers','commerce_regions','commerce_categories','commerce_products','commerce_monthly_sales']}}
    metrics=[{'id':'net_product_sales','name':'Completed net product sales','aliases':['net product sales','net sales','sales trend'],'workspaces':['commerce'],
        'definition':"SUM(net_sales_cents) for completed orders, after item discounts and before refunds, tax and shipping. Monthly fact totals reconcile to this amount.",
        'unit':'INR; source columns are integer paise','date':'commerce_orders.order_date','filters':["status = completed"],'grain':'one order',
        'columns':{'commerce_orders':['order_id','order_date','status','net_sales_cents']}},
        {'id':'collected_cash','name':'Net collected cash','aliases':['net collected','cash after refunds'],'workspaces':['commerce'],
         'definition':"Captured payments minus refunds; captured amounts include product sales, tax and charged shipping. Failed payments are excluded.",
         'unit':'INR; integer paise / 100.0','date':'payment_date / refund_date','filters':["payments.status = captured"],'grain':'payment/refund separately aggregated',
         'columns':{'commerce_payments':['payment_id','amount_cents','status'],'commerce_refunds':['refund_id','payment_id','amount_cents']}},
        {'id':'roas','name':'Return on ad spend','aliases':['roas','return on ad spend'],'workspaces':['growth'],
         'definition':'Completed attributed net product sales / ad spend, with orders and campaign daily rows aggregated independently before joining. This is attribution, not causal uplift.',
         'unit':'ratio','date':'order_date / campaign_date','filters':["orders.status = completed"],'grain':'campaign',
         'columns':{'commerce_orders':['campaign_id','net_sales_cents','status'],'intelligence_campaign_daily':['campaign_id','spend_cents']}}]
    policy=['All records are fictional and generated deterministically; no claims about real customers or forecast quality.',
        'Money is integer paise in INR. Divide by 100.0 for display. Historical item cost_cents is total line COGS, not per-unit cost.',
        'Aggregate independent child facts before joins. Monthly completed_orders counts category participation and is NOT additive across categories; use commerce_orders for unique overall order counts.',
        'Features are as of snapshot month end. Outcomes are next-month observations kept separately; use label_month cutoff for temporal splits to prevent leakage.',
        'Baseline forecasts use only observations through training_max_date. Scenario bands are heuristic ±20%, not calibrated confidence intervals. Future target months have no observed actuals.',
        'Static charts have no hover/zoom. For time-series charts filter to a single series and return ISO month_date; do not sum unrelated units on one axis.']
    metrics.extend([
        {'id':'average_order_value','name':'Completed average order value','aliases':['average order value','aov'],
         'workspaces':['commerce'],'definition':"SUM(net_sales_cents)/COUNT(*) on completed orders, before refunds/tax/shipping.",
         'unit':'INR; integer paise / 100.0','date':'commerce_orders.order_date','filters':['status = completed'],'grain':'completed order',
         'columns':{'commerce_orders':['order_id','status','order_date','net_sales_cents']}},
        {'id':'product_margin','name':'Product margin before refunds','aliases':['product margin','historical cost'],
         'workspaces':['commerce'],'definition':'Completed net product sales minus historical line cost_cents summed once per line. Refunds, tax, charged shipping and carrier cost are excluded.',
         'unit':'INR; integer paise / 100.0','date':'commerce_orders.order_date','filters':['status = completed'],'grain':'line costs aggregated to order',
         'columns':{'commerce_orders':['order_id','status','net_sales_cents'],'commerce_order_items':['order_id','cost_cents']}}
    ])
    catalog={'version':1,'workspaces':spaces,'metrics':metrics,'policies':policy,
        'ambiguous_terms':{'revenue':'Do you mean completed net product sales, or captured cash after refunds including tax/shipping?','profit':'Do you mean product margin before refunds, or another cost/refund/shipping treatment?','churn':'Use next-month inactivity as a synthetic proxy, or specify another churn definition.'},
        'relationship_rules':[f"{r['from_table']} joins {r['to_table']} using "+', '.join(f'{a}={b}' for a,b in r['columns'].items()) for r in relations]}
    write_json(directory/'knowledge'/'catalog.json',catalog)
    examples=[{'id':key,'question':question,'sql':sql,'parameters':{},'tables':tables,'tags':question.lower().split(),
               'review':'Synthetic reference query verified against generated SQLite data. Revalidate dialect before remote use.'} for key,question,sql,tables in example_queries()]
    write_json(directory/'knowledge'/'examples.json',examples)
    # SQLite LIMIT is transpiled for the SQL Server variant of the same logical schema.
    import sqlglot
    write_json(directory/'knowledge_sqlserver'/'catalog.json',catalog)
    remote=[{**item,'sql':sqlglot.transpile(item['sql'],read='sqlite',write='tsql')[0]} for item in examples]
    write_json(directory/'knowledge_sqlserver'/'examples.json',remote)
    native['knowledge_directory']=str(final/'knowledge_sqlserver');write_json(directory/'sources.sqlserver.json',[native])
    references=[]
    for item in examples:
        cursor=connection.execute(item['sql'])
        references.append({'id':item['id'],'question':item['question'],'sql':item['sql'],'columns':[c[0] for c in cursor.description],'rows':cursor.fetchall()})
    write_json(directory/'reference_answers.json',references)
    pii={'commerce_customers':['email','phone']}
    write_json(directory/'pii.sources.json',{'analytics_lab':pii,'analytics_lab_sqlserver':pii})
    write_json(directory/'model_policy.sources.json',{'analytics_lab':{'classification':'internal','require_local':False},'analytics_lab_sqlserver':{'classification':'internal','require_local':False}})
    rules={table:{'mode':'global'} for table in SCHEMA}
    rules['commerce_regions']={'mode':'filtered','where':[{'column':'region_name','op':'in','attribute':'regions'}]}
    parent_rules={'commerce_customers':('commerce_regions','region_id'),'intelligence_campaigns':('commerce_regions','region_id'),
        'commerce_orders':('commerce_customers','customer_id'),'commerce_order_items':('commerce_orders','order_id'),'commerce_payments':('commerce_orders','order_id'),
        'commerce_refunds':('commerce_orders','order_id'),'commerce_shipments':('commerce_orders','order_id'),
        'intelligence_campaign_daily':('intelligence_campaigns','campaign_id'),'intelligence_support_tickets':('commerce_customers','customer_id'),
        'intelligence_customer_month_features':('commerce_customers','customer_id'),'intelligence_customer_month_outcomes':('commerce_customers','customer_id'),
        'intelligence_sales_targets':('commerce_regions','region_id'),'commerce_monthly_sales':('commerce_regions','region_id'),
        'intelligence_sales_forecasts':('commerce_regions','region_id')}
    for table,(parent,column) in parent_rules.items():rules[table]={'mode':'filtered','parents':[{'table':parent,'columns':{column:column}}]}
    write_json(directory/'row_security.sources.json',{'analytics_lab':rules,'analytics_lab_sqlserver':rules})
    return references


def file_hash(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        while chunk:=stream.read(1024*1024):digest.update(chunk)
    return digest.hexdigest()


def build_lab(directory=DEFAULT_LAB,*,seed=20261115,customers=1200,months=46,orders_per_day=30):
    if not 12<=months<=60 or not 100<=customers<=5000 or not 5<=orders_per_day<=100:raise ValueError('Use 12–60 months, 100–5000 customers and 5–100 base orders/day')
    target=Path(directory).resolve();settings={'seed':seed,'customers':customers,'months':months,'orders_per_day':orders_per_day}
    if target.exists():
        manifest=target/'manifest.json'
        if not manifest.is_file():raise FileExistsError('Target exists without a complete manifest; choose a new directory.')
        saved=json.loads(manifest.read_text(encoding='utf-8'))
        if saved['settings']!=settings or any(file_hash(target/name)!=digest for name,digest in saved['database_sha256'].items()):raise FileExistsError('Existing lab differs or has been edited; it will not be overwritten.')
        return saved
    target.mkdir(parents=True)
    with closing(sqlite3.connect(target/'analytics_mirror.db')) as connection:
        connection.execute('PRAGMA foreign_keys=ON');connection.execute('PRAGMA defer_foreign_keys=ON')
        start,end=populate(connection,**settings);connection.commit()
        checks=validate(connection)
        split_databases(connection,target);sqlserver_seed(connection,target/'sqlserver_setup.sql')
        references=assets(connection,target,target)
        counts={table:connection.execute('SELECT COUNT(*) FROM '+table).fetchone()[0] for table in SCHEMA}
    manifest={'version':1,'synthetic':True,'settings':settings,'observed_start':start.isoformat(),'observed_end':end.isoformat(),
        'counts':counts,'total_rows':sum(counts.values()),'checks':checks,'reference_questions':len(references),
        'database_sha256':{name:file_hash(target/name) for name in ('commerce.db','intelligence.db','analytics_mirror.db')}}
    write_json(target/'manifest.json',manifest)
    return manifest


def register_local(directory=DEFAULT_LAB):
    """Add only the synthetic source; preserve existing grants, profiles and security flags."""
    from .config import environment_values
    values=environment_values();root=APP_DIR.resolve();lab=Path(directory).resolve()
    env=Path(values.get('NLQ_ENV_FILE') or root/'.env').resolve()
    if env!=root/'.env':raise ValueError('For a custom environment file, merge the generated source/configuration snippets manually.')
    def local_path(key,default):
        value=Path(values.get(key) or default)
        result=(root/value).resolve() if not value.is_absolute() else value.resolve()
        if not result.is_relative_to(root):raise ValueError('External configuration paths require a manual merge: '+key)
        return result
    source_path=local_path('DATA_SOURCES_FILE','sources.json')
    model_path=local_path('MODEL_POLICY_FILE','model_policy.json')
    pii_path=local_path('PII_CONFIG_FILE','encryption.json')
    row_path=local_path('ROW_SECURITY_FILE','row_security.json')
    source=json.loads((lab/'sources.local.json').read_text(encoding='utf-8'))[0]
    profiles=json.loads(source_path.read_text(encoding='utf-8')) if source_path.exists() else []
    if not isinstance(profiles,list):raise ValueError('Existing source profile file must contain a list')
    old=next((profile for profile in profiles if profile.get('id')==source['id']),None)
    if old is not None and old!=source:raise ValueError('An existing analytics_lab profile differs; refusing to replace it')
    if old is None:profiles.append(source)
    policies=json.loads(model_path.read_text(encoding='utf-8'))
    pii=json.loads(pii_path.read_text(encoding='utf-8'))
    rows=json.loads(row_path.read_text(encoding='utf-8'))
    patches=[(policies['sources'],{'analytics_lab':{'classification':'internal','require_local':False}}),
        (pii['sources'],{'analytics_lab':json.loads((lab/'pii.sources.json').read_text(encoding='utf-8'))['analytics_lab']}),
        (rows['sources'],{'analytics_lab':json.loads((lab/'row_security.sources.json').read_text(encoding='utf-8'))['analytics_lab']})]
    for existing,patch in patches:
        for identifier,item in patch.items():
            if identifier in existing and existing[identifier]!=item:raise ValueError('An existing analytics lab security mapping differs; merge it manually')
            existing[identifier]=item
    database=(lab/'analytics_mirror.db').as_posix()
    if values.get('ANALYTICS_LAB_DATABASE') and values['ANALYTICS_LAB_DATABASE']!=database:raise ValueError('ANALYTICS_LAB_DATABASE already points elsewhere; refusing to overwrite it')
    # Validate before changing any configuration. Never approve providers or alter member grants.
    from .model_policy import ModelPolicy
    from .row_security import validate as validate_rows
    ModelPolicy(policies);validate_rows(rows)
    write_json(source_path,profiles);write_json(model_path,policies);write_json(pii_path,pii);write_json(row_path,rows)
    additions=[]
    if not values.get('DATA_SOURCES_FILE'):additions.append('DATA_SOURCES_FILE='+json.dumps(source_path.relative_to(root).as_posix()))
    if not values.get('ANALYTICS_LAB_DATABASE'):additions.append('ANALYTICS_LAB_DATABASE='+json.dumps(database))
    if additions:
        with env.open('a',encoding='utf-8') as stream:stream.write('\n# Synthetic analytics lab (existing security flags and credentials are unchanged).\n'+'\n'.join(additions)+'\n')
    return {'source':'analytics_lab','registered':True,'restart_required':True,'provider_approvals_changed':False,'existing_grants_changed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',default=str(DEFAULT_LAB));parser.add_argument('--seed',type=int,default=20261115)
    parser.add_argument('--customers',type=int,default=1200);parser.add_argument('--months',type=int,default=46)
    parser.add_argument('--orders-per-day',type=int,default=30)
    parser.add_argument('--register-local',action='store_true',help='Merge the local mirror into app profiles and source-specific policies; never approves a provider or grants users access')
    args=parser.parse_args();result=build_lab(args.directory,seed=args.seed,customers=args.customers,months=args.months,orders_per_day=args.orders_per_day)
    if args.register_local:result={**result,'registration':register_local(args.directory)}
    print(json.dumps(result,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
