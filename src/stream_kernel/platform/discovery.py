from __future__ import annotations


def platform_discovery_modules() -> list[str]:
    # Central framework discovery entrypoint for platform-managed modules.
    return [
        "stream_kernel.adapters.file_io",
        "stream_kernel.execution.transport.carriers.ipc.ipc_adapters",
        "stream_kernel.platform.services",
        "stream_kernel.execution.orchestration.lifecycle.leaf.command.control_plane_service",
        "stream_kernel.execution.orchestration.lifecycle.leaf.command.control_ingress_service",
        "stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_activation_service",
        "stream_kernel.execution.orchestration.lifecycle.leaf.runtime.boundary_execution_service",
        "stream_kernel.execution.orchestration.lifecycle.leaf.startup.runtime_bootstrap_service",
        "stream_kernel.execution.orchestration.lifecycle.leaf.runtime.runtime_boundary_service",
        "stream_kernel.execution.orchestration.lifecycle.leaf.command.command_loop_service",
        "stream_kernel.execution.orchestration.lifecycle.root.startup.lifecycle_service",
        "stream_kernel.execution.orchestration.lifecycle.root.startup.log_factory_service",
        "stream_kernel.execution.orchestration.lifecycle.root.startup.console_log_dispatch_service",
        "stream_kernel.execution.orchestration.lifecycle.root.runtime.lifecycle_manager",
        "stream_kernel.execution.orchestration.control_plane.root.leaf_command_service",
        "stream_kernel.execution.orchestration.control_plane.root.reply_ingress_service",
        "stream_kernel.execution.orchestration.control_plane.root.discovery_snapshot_service",
        "stream_kernel.execution.orchestration.control_plane.root.runtime_bootstrap_service",
        "stream_kernel.execution.orchestration.control_plane.root.boundary_execution_service",
        "stream_kernel.execution.orchestration.control_plane.root.boundary_handoff_service",
        "stream_kernel.execution.orchestration.control_plane.root.stop_execution_service",
        "stream_kernel.execution.orchestration.control_plane.root.shutdown_service",
        "stream_kernel.integration.work_queue",
        "stream_kernel.routing.routing_service",
        "stream_kernel.observability.adapters",
    ]
