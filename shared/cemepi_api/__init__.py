"""Minimal client for the Inteligência Fiscal research dump API."""
from .client import CemepiClient, load_settings

__all__ = ["CemepiClient", "load_settings"]
