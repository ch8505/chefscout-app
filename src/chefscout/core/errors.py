"""Exceptions used across the harness."""

from __future__ import annotations


class ChefScoutError(Exception):
    """Base class for all ChefScout errors."""


class ToolError(ChefScoutError):
    """A tool failed in a way the model can recover from (bad input, not found, site down).

    The message is sent back to the model as a tool_result with is_error=true.
    """


class MissingApiKeyError(ChefScoutError):
    """No Anthropic API key is configured."""


class InvalidIdError(ChefScoutError, ValueError):
    """A user or conversation id is not safe to use in a file path."""
