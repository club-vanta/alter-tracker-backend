"""
Shared domain type aliases.

Kept in a separate module so they can be imported by both models and services
without creating circular dependencies.
"""

from typing import NewType

# Mazmo's internal user ID - distinct from our internal DB IDs.
MazmoUserId = NewType("MazmoUserId", int)
