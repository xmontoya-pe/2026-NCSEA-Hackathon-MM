"""Per-system search loop.

Spec section 4.7. For each requested system: generate grids (``grid``), evaluate bays
(``bays``), size columns (``columns``), score (``scoring``), and return the best scheme
plus the top 3 alternates. Deterministic: ties sort by fewest columns, then grid id.
"""
