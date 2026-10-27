"""Cost / depth / carbon normalization and weighted score.

Spec section 4.6. Will min-max normalize cost, max depth, and carbon to 0-1 across
all candidates **within one system** and combine them with ``objective_weights``
(lower is better). Cross-system comparison is the dashboard's job; we report raw totals.
"""
