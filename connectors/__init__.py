"""Connectors package for Pakistan Stock Market data sources.

Each connector inherits from BaseConnector and implements:
- test(): a fast health check
- fetch(): the actual data pull
"""

from connectors.base import BaseConnector, ConnectionResult, FetchResult

__all__ = ["BaseConnector", "ConnectionResult", "FetchResult"]
