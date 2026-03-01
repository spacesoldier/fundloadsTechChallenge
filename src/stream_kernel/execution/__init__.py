# Execution package marker.
#
# Keep __init__ import-light to avoid circular imports during package bootstrap.
# Import concrete symbols from submodules directly, e.g.:
#   from stream_kernel.execution.runtime.runner import SyncRunner

__all__: list[str] = []
