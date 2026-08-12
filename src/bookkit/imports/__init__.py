"""Bulk data loading: readers → tablemap → mappers → staging → commit.

Zero SQL in this package — all persistence goes through repo/. The seam with
towerkit (spec 2026-08-11): bookkit maps messy headers to canonical names and
resolves carriers; towerkit.ingest decides what a tower means.
"""
