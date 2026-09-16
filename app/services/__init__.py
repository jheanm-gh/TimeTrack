"""Impure helpers that sit between the pure core and the GUI.

These touch the operating system - the input-idle counter, the wall and
monotonic clocks, the Startup folder, the single-instance guard - but they
know nothing about widgets, so they can be tested without a screen.
"""
