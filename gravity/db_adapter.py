"""Read-only adapter around the bay study SQLite database. ALL SQL lives here.

Spec section 2.3 (and section 1: the DB is read-only; one adapter so a schema
change touches one file). To be written after the DB is inspected (spec 6,
prompts 2-3), not from assumptions. Will expose:

- ``framing_options(system, span_x_ft, span_y_ft, ll_psf, sdl_psf) -> list[BayFraming]``
- ``available_spans(system) -> tuple[list[float], list[float]]``
- ``available_loads(system) -> tuple[list[float], list[float]]``  (LL and SDL values in the table)

Lookups round spans and loads UP to the next table row, never interpolating
unconservatively (spec 1). ``BayFraming`` will carry whatever the DB supports:
deck/slab, infill beam size & spacing, girder size, total depth (in),
self-weight (psf), cost ($/sf), embodied carbon (kgCO2e/sf), and source row id.

The DB has no column sizes; columns are sized in ``columns.py`` (spec 4.5).
"""
