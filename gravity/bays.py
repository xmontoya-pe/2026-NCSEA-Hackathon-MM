"""Bay framing lookup for each bay of a candidate grid, plus the depth constraint.

Spec sections 4.3 and 4.4. Will:

- Look up framing via ``db_adapter.framing_options`` with spans and loads rounded up.
- Try both orientations (beams spanning X vs Y) and keep the better one.
- Mark a bay ``infeasible`` with a warning when no DB row fits, and penalize the whole
  candidate rather than crash.
- Check ``allowable_depth_in = (floor_to_floor_ft - min_ceiling_height_ft)*12
  - plenum_allowance_in`` (overridden by ``max_structure_depth_in`` if given);
  exceedances get a heavy penalty, not removal.
"""
