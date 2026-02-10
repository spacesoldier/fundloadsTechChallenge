# Execution package marker.
#
# Keep __init__ import-light to avoid circular imports during package bootstrap.
# Import concrete symbols from submodules directly, e.g.:
#   from stream_kernel.execution.runner import SyncRunner
#   from stream_kernel.execution.observer import observer_factory

__all__: list[str] = []
