"""Occupancy -> LL/SDL, zone-to-bay mapping, and live load reduction.

Spec section 4.1, with inputs from 2.1 (zone notes) and 2.2 (config files). Will:

- Read occupancy LL / partition / reducibility from ``config/occupancies.json`` and
  SDL per system from ``config/sdl.json`` (editable by engineers, not hardcoded).
- Copy typical zones up to all levels when ``typical_zones_apply_to_all_levels``;
  explicit zones on a level override the copy. Uncovered floor area gets
  ``default_occupancy`` (config, default ``office``) plus a warning.
- Map each bay polygon to overlapping zones (shapely intersection); governing load is
  the max LL of any overlapping zone, with area fractions kept for later refinement.
- Apply ASCE 7-22 section 4.7 live load reduction
  (``L = Lo * (0.25 + 15/sqrt(KLL*AT))`` with the 0.5Lo / 0.4Lo floors), skipped for
  LL > 100 psf and assembly; roof per section 4.8.
- Add partition load only where the occupancy table says so.
"""
