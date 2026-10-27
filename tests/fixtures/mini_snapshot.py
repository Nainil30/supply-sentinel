"""A tiny hand-built snapshot so scenario ops can be tested against numbers worked by hand."""

from datetime import date, datetime

import duckdb

from supply_sentinel.datagen.schema import create_schema

AS_OF = date(2026, 11, 2)
PREVIOUS = date(2026, 10, 26)


def make_mini(path: str = ":memory:") -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(path)
    create_schema(con)
    con.execute("INSERT INTO meta VALUES ('mini', ?, 1, 'test', ?)", [AS_OF, datetime(2026, 11, 2)])
    con.execute(
        "INSERT INTO platforms VALUES ('ORB-S4', 'test platform', 'SUSTAIN', ?, ?, ?, 31000)",
        [date(2021, 2, 1), date(2027, 6, 30), date(2032, 6, 30)],
    )
    con.execute("INSERT INTO suppliers VALUES ('SUP-01', 'Test Supplier', 'x', 'NA', 0.9, 'LOW')")
    con.execute(
        "INSERT INTO parts VALUES "
        "('P-A', 'part a', 'X', 'SUP-01', 10.0, 12, 10, 10, 'ACTIVE', NULL), "
        "('P-B', 'part b', 'X', 'SUP-01', 11.0, 12, 10, 10, 'ACTIVE', NULL)"
    )
    con.execute("INSERT INTO bom VALUES ('ORB-S4', 'P-A', 1, ?, NULL)", [date(2021, 2, 1)])
    con.execute("INSERT INTO alternates VALUES ('P-A', 'P-B', 'IN_QUAL', 8, 'test')")
    con.execute("INSERT INTO inventory VALUES ('P-A', 'CM_HUB', 100, 0)")
    con.execute(
        "INSERT INTO purchase_orders VALUES ('PO-1', 'P-A', 'SUP-01', 50, 3, 3, 'OPEN', 1, 10.0)"
    )
    con.execute("INSERT INTO supplier_commits VALUES ('P-A', 'SUP-01', 3, 50, 'on schedule')")
    # versions: current (as-of) weekly weeks 0-2 = 10 each, monthly bucket from week 3 = 100
    for v, factor in ((AS_OF, 1.0), (PREVIOUS, 0.9)):
        for start, kind, units in (
            (date(2026, 11, 2), "WEEK", 10),
            (date(2026, 11, 9), "WEEK", 10),
            (date(2026, 11, 16), "WEEK", 10),
            (date(2026, 11, 23), "MONTH", 100),
        ):
            con.execute(
                "INSERT INTO demand_forecast VALUES ('ORB-S4', ?, ?, ?, ?)",
                [start, kind, units * factor, v],
            )
    return con
