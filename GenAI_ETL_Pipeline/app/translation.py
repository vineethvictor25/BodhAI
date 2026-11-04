import re
import sqlglot
from sqlglot import exp
from sqlglot.errors import ErrorLevel, SqlglotError
from .models import TranslateRequest


def translate(request: TranslateRequest):
    if re.search(r"\b(?:PROCEDURE|TRIGGER|CURSOR|DECLARE|EXECUTE|EXEC|BEGIN)\b", request.sql, re.I):
        return {"status": "manual_review", "sql": None, "warnings": ["Procedural SQL requires a semantic migration; this tool only translates declarative SQL. Extract queries from the procedure and translate them individually."]}
    try:
        trees = sqlglot.parse(request.sql, read=request.source, error_level=ErrorLevel.RAISE)
        if any(tree is None or tree.find(exp.Command) for tree in trees):
            raise ValueError("Unparsed SQL command requires manual review")
        output = sqlglot.transpile(request.sql, read=request.source, write=request.target, pretty=True, error_level=ErrorLevel.RAISE, unsupported_level=ErrorLevel.RAISE)
        for statement in output:
            sqlglot.parse(statement, read=request.target, error_level=ErrorLevel.RAISE)
        return {"status": "translated", "sql": ";\n".join(output) + ";", "warnings": ["Syntax translation only. Validate null, date, timezone, precision, collation and transaction behavior against the real target. Output is never executed by this service."]}
    except (SqlglotError, ValueError) as error:
        return {"status": "manual_review", "sql": None, "warnings": [str(error)[:1000]]}
