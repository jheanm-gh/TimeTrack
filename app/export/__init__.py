"""Workbook and CSV generation.

The spreadsheet is an **output**, never storage: it is rewritten from the
database and application state is never read back out of it. Everything in
this package is therefore one-directional - database in, file out.
"""
