"""
Loads ParcelPilot_Assessment_Data.xlsx into SQLite and exposes safe,
scoped query functions for the structured-data tool.

Access control note: every lookup function takes an explicit
requesting-user context and filters at the query level (not left to
the LLM to "decide" not to look at other accounts). This is what the
assessment means by "enforce access control in the data/tool layer".
"""

import sqlite3
import pandas as pd
from pathlib import Path
from dataclasses import dataclass

DATA_DIR = Path(__file__).parent / "data"
XLSX_PATH = DATA_DIR / "ParcelPilot_Assessment_Data.xlsx"
DB_PATH = Path(__file__).parent / "parcelpilot.db"


@dataclass
class UserContext:
    """Mocked auth context. role='ops' can see all accounts (internal
    tool). role='customer' is scoped to a single account_id — kept here
    so the same DB layer could later serve a customer-facing agent too."""
    user_id: str
    role: str  # "ops" | "customer"
    account_id: str | None = None  # required if role == "customer"


def build_database():
    """Reads the xlsx and writes it into a fresh SQLite file."""
    xl = pd.ExcelFile(XLSX_PATH)
    conn = sqlite3.connect(DB_PATH)

    accounts = pd.read_excel(xl, "accounts")
    orders = pd.read_excel(xl, "orders")
    tickets = pd.read_excel(xl, "tickets")

    accounts.to_sql("accounts", conn, if_exists="replace", index=False)
    orders.to_sql("orders", conn, if_exists="replace", index=False)
    tickets.to_sql("tickets", conn, if_exists="replace", index=False)

    # snapshot time comes from the README sheet — pull it out as a
    # constant table so every tool call can reference "now" consistently
    readme_raw = pd.read_excel(xl, "README", header=None)
    snapshot_time = None
    for _, row in readme_raw.iterrows():
        cell = str(row[0]) if pd.notna(row[0]) else ""
        if "snapshot" in cell.lower():
            snapshot_time = str(row[1]) if len(row) > 1 and pd.notna(row[1]) else cell
    conn.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT, value TEXT)")
    conn.execute("DELETE FROM meta")
    conn.execute("INSERT INTO meta VALUES (?, ?)", ("snapshot_time", snapshot_time or "unknown"))
    conn.commit()
    conn.close()
    return snapshot_time


def get_snapshot_time() -> str:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute("SELECT value FROM meta WHERE key='snapshot_time'").fetchone()
    conn.close()
    return row[0] if row else "unknown"


def _check_access(ctx: UserContext, account_id: str):
    if ctx.role == "customer" and ctx.account_id != account_id:
        raise PermissionError(
            f"Access denied: user is scoped to {ctx.account_id}, "
            f"cannot access {account_id}."
        )


def get_account(ctx: UserContext, account_id: str) -> dict:
    _check_access(ctx, account_id)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM accounts WHERE account_id = ?", (account_id,)).fetchone()
    conn.close()
    return dict(row) if row else {"error": f"No account found for {account_id}"}


def get_order(ctx: UserContext, order_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    conn.close()
    if not row:
        return {"error": f"No order found for {order_id}"}
    order = dict(row)
    _check_access(ctx, order["account_id"])
    return order


def get_tickets_for_account(ctx: UserContext, account_id: str) -> list[dict]:
    _check_access(ctx, account_id)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM tickets WHERE account_id = ?", (account_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_ticket(ctx: UserContext, ticket_id: str) -> dict:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)).fetchone()
    conn.close()
    if not row:
        return {"error": f"No ticket found for {ticket_id}"}
    ticket = dict(row)
    _check_access(ctx, ticket["account_id"])
    return ticket


def get_all_tickets_internal(ctx: UserContext) -> list[dict]:
    """Ops-only: used by proactive issue detection. Never exposed to customer role."""
    if ctx.role != "ops":
        raise PermissionError("Only authorised ops users can list all tickets.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT t.*, a.account_name, a.plan, a.premium_support
        FROM tickets t JOIN accounts a ON t.account_id = a.account_id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_all_orders_internal(ctx: UserContext) -> list[dict]:
    if ctx.role != "ops":
        raise PermissionError("Only authorised ops users can list all orders.")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT o.*, a.account_name, a.plan
        FROM orders o JOIN accounts a ON o.account_id = a.account_id
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    snap = build_database()
    print(f"DB built. Snapshot time: {snap}")
    ctx = UserContext(user_id="ops1", role="ops")
    print(get_order(ctx, "ORD-1001"))
    print(get_account(ctx, "ACCT-001"))
    print(len(get_tickets_for_account(ctx, "ACCT-001")), "tickets for ACCT-001")

    # access control test — should raise
    cust_ctx = UserContext(user_id="cust1", role="customer", account_id="ACCT-002")
    try:
        get_order(cust_ctx, "ORD-1001")  # belongs to ACCT-001, should be denied
        print("FAIL: access control did not block cross-account read")
    except PermissionError as e:
        print("OK access control blocked:", e)
