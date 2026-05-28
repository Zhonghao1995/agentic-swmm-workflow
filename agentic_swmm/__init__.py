"""Unified command-line entry points for Agentic SWMM."""

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("aiswmm")
except _metadata.PackageNotFoundError:
    __version__ = "0.0.0+unknown"
