# Detailed Polish appearance descriptors — 2026-10-26

130 unit tests passed. The isolated PostgreSQL integration check covers structured
attribute persistence, input upgrades with revision history, stale-replay protection,
and avoiding a row-lock wait when replaying an already queued image.

Real saved images were evaluated in both calibrated camera views. The main view
has a white T-shirt, short brown hair, glasses and a watch; the inset view has grey
clothing, brown hair and a beard with no visible glasses. The replacement local
LLaVA-Phi3 model produces structured responses in about two minutes with two CPUs.
The initial main-view saved result predates the parser fix for a color-only glasses
answer (`black`); the current parser treats that as visible black glasses, covered
by regression tests. Raw results remain preserved here.

Earlier Moondream responses were empty for several appearance attributes, both
with cached and fresh inference. Those results are preserved as diagnostics.
The implementation uses one structured request and Polish rendering, with unknown
and not-visible states distinguished. No sex/gender identity inference is stored.
See `docs/clothing-descriptions.md` for schema, migration and operational details.
