"""Typed errors, so callers can distinguish "PubMed is down" from "we sent junk"."""

from __future__ import annotations


class EngineError(Exception):
    """Base for everything the engine raises deliberately."""


class TransportError(EngineError):
    """A network call failed after exhausting retries."""


class RateLimitError(TransportError):
    """The upstream service asked us to slow down and kept asking."""


class ParseError(EngineError):
    """Upstream returned something we could not parse as the expected format."""


class SummariserError(EngineError):
    """The summarisation provider failed or returned unusable output."""
