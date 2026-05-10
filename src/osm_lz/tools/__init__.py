"""CLI helpers for the osm-lz workflow catalog.

Unlike the per-facet tool dirs in fwh_osm / fwh_noaa_weather /
fwh_jenkins, osm-lz contributes only FFL workflows — the operations it
ultimately performs live in fwh_osm. So the CLIs here wrap *workflow
submission*, not individual operations:

- ``list-workflows.sh`` — print the catalog
- ``submit.sh`` — submit a workflow by qualified name; library FFL files
  are resolved automatically
"""
