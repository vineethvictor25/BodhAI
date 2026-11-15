# QueryDesk business walkthrough

Audience: business and BI users. English synthetic narration: Microsoft Zira Desktop.

Demo disclosure: scripted model responses; real LangGraph workflow, protected read-only SQLite execution, authentication and browser UI. No live model provider or remote warehouse connection is represented.

## A simple question. A long wait.

A business question sounds simple: which customer groups have outstanding balances? But the answer can be spread across orders, payments, discounts, and refunds. Business teams wait for someone to write SQL. Analysts spend time tracing joins, checking definitions, and explaining where a number came from.

## Ask in English. Review the evidence.

Query Desk turns an English question into a reviewable database query and a plain language answer. Business and BI users work in a browser. They can see the proposed tables, inspect the SQL before it runs, and read the returned rows in a table, together with a concise explanation.

## You stay in the approval loop.

Choose a configured data source and business domain, then ask your question. The application interprets the task and proposes relevant tables. You confirm those tables before SQL generation. Next, review the SQL, filters, and limits. Only after you approve does the application read data and present the answer.

## Specialized roles. One coordinated workflow.

Behind the interface, a central Lang Graph orchestrator coordinates specialized roles. The intent agent understands the question. Schema and column selection identify useful metadata. The SQL agent creates the query, and validation checks its structure and reviews its meaning. Execution runs approved reads. The response agent explains the returned results. Business definitions and reviewed examples provide context throughout the workflow.

## Useful answers need clear boundaries.

Access starts with a signed in user. Source and domain permissions define available data, with configurable row and personal data restrictions. SQL approval, read only access, and result limits provide additional controls. Remote connections default to verified TLS. These safeguards make the process reviewable, but they do not guarantee that every generated query is semantically correct.

## Ask a cross-table business question.

Let's see the application. This is an isolated demonstration with scripted agent responses, the real graph workflow, and a real SQLite sample database. We ask for order counts, payments, refunds, and outstanding balances by customer segment and region, all in rupees.

## Confirm the relevant tables.

The application proposes eight related tables and explains why they are needed. Orders connect to customers, profiles, and segments. Items, discounts, payments, and refunds supply the financial measures. We review the selection, then confirm the tables to continue.

## Review SQL before any data read.

Now the SQL is ready for review. Filter values are separate bound parameters. Item values and the different cash streams are aggregated before joining, which avoids multiplying totals. We inspect the selected tables and execution limits, then explicitly approve the read.

## Read the answer and the actual rows.

The database returns ten summary rows for twenty two non-cancelled orders. The New segment in the West has six hundred and eighty rupees outstanding. The table shows the other groups and measures, while the explanation states the filters and calculation. The executed SQL remains available below for review.

## From a number to an explainable result.

The output is more than a sentence. It combines an answer, a reason, the data rows, and the executed SQL. Users can provide feedback. Wide results scroll horizontally, and limited results are identified. Saved history may omit raw rows, so restoring a review does not silently rerun a query.

## Start with one domain. Prove it on your data.

Start with a reviewed business domain, approved definitions, and a small set of known questions. Configure access and a suitable model provider, then validate results with your business owners. Query Desk supports local and cloud connector options. Live warehouse verification and broader accuracy evaluation remain deployment specific work.

