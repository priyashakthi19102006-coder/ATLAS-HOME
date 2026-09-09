"""ATLAS Context and Memory Layer.

Provides bounded temporal situational context, track history, and timeline aggregation.
"""

from atlas.context.memory import ContextManager, get_context_manager

__all__ = [
    "ContextManager",
    "get_context_manager",
]
