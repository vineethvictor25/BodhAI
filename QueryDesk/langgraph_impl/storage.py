"""Standalone SQLite metadata and read-only query boundary."""
import json
import re
import sqlite3
import time
from decimal import Decimal
from contextlib import contextmanager
from pathlib import Path


class QueryError(ValueError):
    pass


SAFE_FUNCTIONS = frozenset("count sum avg min max total round abs coalesce ifnull nullif lower upper "
    "trim ltrim rtrim length substr substring replace instr date datetime time strftime julianday "
    "group_concat row_number rank dense_rank lag lead typeof".split())


class Database:
    def __init__(self, filename, allowed_tables=None, row_limit=100, timeout=3):
        self.filename = Path(filename).resolve()
        if not self.filename.is_file():
            raise QueryError("Database missing. Run this application's --init-demo command first")
        if not 1 <= row_limit <= 1000 or not 0 < timeout <= 60:
            raise QueryError("Invalid query result limit or timeout")
        self.row_limit, self.timeout = row_limit, timeout
        with self.connection() as conn:
            available = {r[1] for r in conn.execute("PRAGMA table_list")
                         if r[0] == "main" and r[2] == "table" and not r[1].startswith("sqlite_")}
        self.allowed = available if allowed_tables is None else set(allowed_tables)
        if not self.allowed <= available:
            raise QueryError("Table scope includes nonexistent or non-regular tables")

    @contextmanager
    def connection(self, scope=None, reads=None):
        conn = self.open_connection()
        try:
            conn.execute("PRAGMA query_only=1")
            conn.execute("PRAGMA trusted_schema=0")
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1_000_000)
            conn.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 20_000)
            if scope is not None:
                deadline = time.monotonic() + self.timeout
                from .cancellation import cancelled
                conn.set_progress_handler(lambda: int(time.monotonic() >= deadline or cancelled()), 500)

                def access(operation, table, column, namespace, trigger):
                    permitted = operation in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}
                    if operation == sqlite3.SQLITE_READ:
                        permitted = table in scope and (namespace == "main" or (namespace is None and column == ""))
                        if permitted and reads is not None:
                            reads.setdefault(table, set())
                            if column:
                                reads[table].add(column)
                    elif operation == sqlite3.SQLITE_FUNCTION:
                        permitted = (column or "").lower() in SAFE_FUNCTIONS
                    return sqlite3.SQLITE_OK if permitted else sqlite3.SQLITE_DENY

                conn.set_authorizer(access)
            from .cancellation import interruptible
            with interruptible(conn.interrupt):
                yield conn
        except sqlite3.Error as exc:
            raise QueryError(str(exc)) from None
        finally:
            conn.close()

    def open_connection(self):
        return sqlite3.connect(self.filename.as_uri() + "?mode=ro", uri=True, timeout=1)

    def catalog(self):
        return {"dialect": "SQLite", "tables": sorted(self.allowed)}

    def schema(self, tables):
        requested = set(tables)
        if not requested or not requested <= self.allowed:
            raise QueryError("Choose at least one permitted table from the catalog")
        metadata = []
        with self.connection() as conn:
            for name in sorted(requested):
                identifier = '"' + name.replace('"', '""') + '"'
                columns = [{"name": row[1], "type": row[2], "primary_key": bool(row[5]),
                            "nullable": not (row[3] or row[5])}
                           for row in conn.execute(f"PRAGMA table_info({identifier})")]
                links = []
                for row in conn.execute(f"PRAGMA foreign_key_list({identifier})"):
                    if row[2] not in self.allowed:
                        continue
                    target_column = row[4]
                    if target_column is None:
                        target_name = '"' + row[2].replace('"', '""') + '"'
                        primary = sorted((c[5], c[1]) for c in conn.execute(f"PRAGMA table_info({target_name})") if c[5])
                        if row[1] < len(primary):
                            target_column = primary[row[1]][1]
                    links.append({"column": row[3], "references_table": row[2], "references_column": target_column})
                metadata.append({"table": name, "columns": columns, "foreign_keys": links})
        return {"dialect": "SQLite", "tables": metadata}

    def inputs(self, sql, parameters, tables):
        if not isinstance(sql, str) or len(sql) > 20000 or not re.match(r"\s*(SELECT|WITH)\b", sql, re.I):
            raise QueryError("Submit a single SELECT or WITH statement of at most 20,000 characters")
        if not set(tables) <= self.allowed:
            raise QueryError("Query table scope is not permitted")
        if not isinstance(parameters, dict):
            raise QueryError("Bound parameters must be a dictionary")
        for key, value in parameters.items():
            if not isinstance(key,str):raise QueryError('Bound parameter names must be strings')
            if isinstance(value,Decimal):
                if not getattr(self,'supports_decimal_parameters',False) or not value.is_finite():
                    raise QueryError('Exact decimal parameters require a supported warehouse adapter; use integer minor units for SQLite')
                continue
            if not isinstance(key, str) or (value is not None and type(value) not in {str, int, float}):
                raise QueryError("Bound parameter values must be scalar")

    def plan(self,sql,parameters,tables):
        from .planning import sqlite_plan
        return sqlite_plan(self,sql,parameters,tables)

    def validate(self, sql, parameters, tables):
        self.inputs(sql, parameters, tables)
        reads = {}
        with self.connection(set(tables), reads) as conn:
            rows = conn.execute("EXPLAIN QUERY PLAN " + sql, parameters).fetchall()
        return {"valid": True, "query_plan": [row[3] for row in rows],
                "referenced_columns": {t: sorted(cols) for t, cols in reads.items()}}

    def execute(self, sql, parameters, tables):
        self.inputs(sql, parameters, tables)
        with self.connection(set(tables)) as conn:
            # Compile and execute under the same connection authorizer and deadline.
            conn.execute("EXPLAIN QUERY PLAN " + sql, parameters).fetchall()
            cursor = conn.execute(sql, parameters)
            if cursor.description is None:
                raise QueryError("The query must return rows")
            columns = [col[0] for col in cursor.description]
            raw = cursor.fetchmany(self.row_limit + 1)
        return self.format_result(columns,raw)

    def format_result(self,columns,raw):
        partial = len(raw) > self.row_limit
        data = []
        for row in raw[:self.row_limit]:
            converted = []
            for cell in row:
                if isinstance(cell, bytes):
                    cell, partial = "[binary content omitted]", True
                elif isinstance(cell, str) and len(cell) > 2000:
                    cell, partial = cell[:2000] + " [clipped]", True
                converted.append(cell)
            if len(json.dumps(data + [converted]).encode()) > 65536:
                partial = True
                break
            data.append(converted)
        return {"columns": columns, "rows": data, "returned_rows": len(data),
                "truncated": partial, "row_limit": self.row_limit}
