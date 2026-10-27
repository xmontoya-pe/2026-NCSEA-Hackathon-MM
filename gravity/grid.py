"""Candidate column grid generation and clipping to the building outline.

Spec section 4.2. Will generate a small (dozens, not thousands) set of candidate
grids per system from ``db_adapter.available_spans(system)`` within
``min_bay_ft``..``max_bay_ft``:

- Strategy A: snap to the recognized ``dimension_lines``, optionally subdividing a
  dimension span into 2 or 3 equal bays.
- Strategy B: uniform grids filling the bounding box (one odd end bay allowed).

Then clip to the outline (drop columns outside, flag columns within 2 ft of an
outline edge for a perimeter line) and remove columns inside ``no_columns`` zones,
merging the affected bays into a long-span bay.
"""
