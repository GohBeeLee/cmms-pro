"""
Shared stock-severity classification for spare parts.

Used anywhere a part's stock status is shown or checked (Inventory list,
barcode scan lookup, work-order parts-used display, dashboard KPI, low-stock
websocket alerts) so all of them agree on the same tiers instead of each
re-implementing (or half-implementing) the comparison.
"""


def compute_stock_status(quantity_on_hand: int, reorder_level: int, is_critical: bool = False) -> str:
    """
    Returns one of "ok" | "low" | "critical".

    - "critical": completely out of stock, OR at/below the reorder point
      with either less than 40% of the reorder level remaining, or the part
      is manually flagged as always-critical (is_critical) — a flag only
      escalates a part that's already at/below its reorder point; it does
      nothing while stock is still healthy.
    - "low": at or below the reorder point, but not urgent yet.
    - "ok": above the reorder point.
    """
    reord = reorder_level or 0
    qty = quantity_on_hand or 0
    if qty <= 0:
        return "critical"
    if qty > reord:
        return "ok"
    if is_critical or qty <= reord * 0.4:
        return "critical"
    return "low"


# SQL fragment mirroring the same tiers, for endpoints that need to filter
# or count in the database rather than in Python (e.g. dashboard KPIs).
# Keep this in sync with compute_stock_status() above.
CRITICAL_SQL = (
    "(quantity_on_hand <= 0 OR is_critical = 1 "
    "OR (quantity_on_hand <= reorder_level AND quantity_on_hand <= reorder_level * 0.4))"
)
LOW_OR_CRITICAL_SQL = "(quantity_on_hand <= reorder_level)"