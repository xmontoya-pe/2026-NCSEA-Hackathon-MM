"""Output writers.

Spec sections 3.1 and 3.2. Will write ``output.json`` from ``models.GravityOutput``,
plus an optional lateral-team format (grid lines and column locations, seismic weight
and center of mass per level, diaphragm type, gravity column axial loads) once the
lateral team says what they want. Other formats are added here as exporters; the core
model in ``models.py`` does not change for them.
"""
