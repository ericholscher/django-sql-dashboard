import time

from django.contrib.auth.decorators import permission_required
from django.db import connections
from django.db.utils import ProgrammingError
from django.shortcuts import get_object_or_404, render
from django.conf import settings

from .models import Dashboard
from .utils import displayable_rows, extract_named_parameters


@permission_required("django_sql_dashboard.execute_sql")
def dashboard_index(request):
    sql_queries = [q for q in request.GET.getlist("sql") if q.strip()]
    return _dashboard_index(request, sql_queries, title="Django SQL Dashboard")


def _dashboard_index(
    request, sql_queries, title=None, description=None, saved_dashboard=False
):
    query_results = []
    alias = getattr(settings, "DASHBOARD_DB_ALIAS", "dashboard")
    connection = connections[alias]
    with connection.cursor() as tables_cursor:
        tables_cursor.execute(
            """
            SELECT table_name
            FROM   information_schema.table_privileges 
            WHERE  grantee = current_user and privilege_type = 'SELECT'
            ORDER BY table_name
        """
        )
        available_tables = [t[0] for t in tables_cursor.fetchall()]

    parameters = []
    for sql in sql_queries:
        for p in extract_named_parameters(sql):
            if p not in parameters:
                parameters.append(p)
    parameter_values = {
        parameter: request.GET.get(parameter, "")
        for parameter in parameters
        if parameter != "sql"
    }

    if sql_queries:
        for sql in sql_queries:
            sql = sql.strip()
            if ";" in sql.rstrip(";"):
                query_results.append(
                    {
                        "sql": sql,
                        "rows": [],
                        "description": [],
                        "truncated": False,
                        "error": "';' not allowed in SQL queries",
                    }
                )
                continue
            with connection.cursor() as cursor:
                duration_ms = None
                try:
                    cursor.execute("BEGIN;")
                    start = time.perf_counter()
                    # Running a SELECT prevents future SET TRANSACTION READ WRITE:
                    cursor.execute("SELECT 1;", parameter_values)
                    cursor.fetchall()
                    cursor.execute(sql, parameter_values)
                    try:
                        rows = list(cursor.fetchmany(101))
                    except ProgrammingError as e:
                        rows = [{"statusmessage": str(cursor.statusmessage)}]
                    duration_ms = (time.perf_counter() - start) * 1000.0
                except Exception as e:
                    query_results.append(
                        {
                            "sql": sql,
                            "rows": [],
                            "description": [],
                            "truncated": False,
                            "error": str(e),
                        }
                    )
                else:
                    query_results.append(
                        {
                            "sql": sql,
                            "rows": displayable_rows(rows[:100]),
                            "description": cursor.description,
                            "truncated": len(rows) == 101,
                            "duration_ms": duration_ms,
                        }
                    )
                finally:
                    cursor.execute("ROLLBACK;")
    return render(
        request,
        "django_sql_dashboard/dashboard.html",
        {
            "query_results": query_results,
            "available_tables": available_tables,
            "title": title,
            "description": description,
            "saved_dashboard": saved_dashboard,
            "user_can_execute_sql": request.user.has_perm(
                "django_sql_dashboard.execute_sql"
            ),
            "parameter_values": parameter_values.items(),
        },
    )


def dashboard(request, slug):
    dashboard = get_object_or_404(Dashboard, slug=slug)
    return _dashboard_index(
        request,
        sql_queries=[query.sql for query in dashboard.queries.all()],
        title=dashboard.title,
        description=dashboard.description,
        saved_dashboard=True,
    )