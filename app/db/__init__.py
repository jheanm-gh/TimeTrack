"""SQLite storage. The database is the single source of truth.

The workbook produced in :mod:`app.export` is an *output* only - application
state is never read back out of a spreadsheet.
"""
