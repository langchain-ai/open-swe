"""Jev (``runtime_api.classifier``) over the backend's pooled connections."""

from runtime_api.classifier import Jev, JevDecision, JevUnavailable

from openswe.utils.http_pool import pooled_transport

JEV = Jev(transport=pooled_transport)

__all__ = ["JEV", "Jev", "JevDecision", "JevUnavailable"]
