"""FY27 planning engine (Phase 1): data, access scope, rollups, draft-plan moves, move advice, territory purity.

Deterministic and testable. The Advisor (rule engine or an optional model) calls these functions through an
allowlisted tool registry; it never computes numbers itself. Source CSVs under data/fy27 are read-only; all
planning edits live in a draft plan file under local_state/.
"""
