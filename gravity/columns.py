"""Tributary areas, gravity load accumulation, and simplified column sizing.

Spec section 4.5 (the bay DB has no column sizes). Seismic weight per level for the
lateral team (spec 3.2) is planned alongside this module (spec 6, prompt 6). Will:

- Compute tributary areas by the half-bay method.
- Accumulate D, L (reduced per spec 4.1 with cumulative AT and KLL), and Lr from the
  roof down; report Pd, Pl, Plr, Pu and the governing ASCE 7-22 section 2.3.1 combo
  (1.4D; 1.2D + 1.6L + 0.5Lr; 1.2D + 1.6Lr + 1.0L).
- Steel (built first): W12/W14 shapes from ``data/aisc_shapes.csv``, Fy = 50 ksi,
  E = 29,000 ksi; AISC 360 E3 weak-axis flexural buckling (K = 1.0, L = story height),
  slender shapes excluded; lightest shape with Pu/phiPn <= target_ratio; constant size
  through each ``steel_splice_every_n_levels`` tier; tonnage priced from
  ``config/systems.json``.
- Concrete and mass timber per spec 4.5, only after steel works end to end.
"""
