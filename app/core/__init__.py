"""Pure calculation layer.

Every function in this package is side-effect free: no database, no files,
no clock reads except where a time source is passed in explicitly. That is
what makes the billing maths unit-testable without starting a GUI.
"""
