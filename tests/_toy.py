"""A small made-up tool table with realistic names, for tests that must run without any tool server."""
import types


def T(server, name, desc, read_only=True, virtual=False, props=None, required=None):
    schema = {"type": "object", "properties": props or {}, "required": required or []}
    return types.SimpleNamespace(server=server, name=name, description=desc, schema=schema, read_only=read_only, virtual=virtual)


def toy_tools():
    ts = [
        T("pve", "pve_list_lxc_containers", "List LXC containers on a node."),
        T("pve", "pve_get_lxc_status", "Get the status of one LXC container: running or stopped, uptime, cpu and memory.", props={"node": {"type": "string"}, "vmid": {"type": "integer"}}, required=["node", "vmid"]),
        T("pve", "pve_start_lxc_container", "Start an LXC container.", False, props={"node": {"type": "string"}, "vmid": {"type": "integer"}}, required=["node", "vmid"]),
        T("pve", "pve_reboot_lxc_container", "Reboot an LXC container.", False),
        T("pve", "pve_delete_lxc_container", "Permanently delete an LXC container.", False),
        T("pve", "pve_get_node_status", "Memory, cpu, uptime and load of a node."),
        T("pve", "pve_list_storage", "List storages and how full they are."),
        T("pve", "pve_delete_storage", "Delete a storage definition.", False),
        T("pve", "pve_list_backup_jobs", "List scheduled backup jobs."),
        T("pve", "pve_run_backup", "Run a backup now.", False),
        T("pve", "pve_disarm_ha", "Disarm the HA manager.", False),
        T("pve", "pve_bulk_shutdown_guests", "Shut down many guests at once.", False),
        T("pve", "pve_list_cluster_resources", "List all guests, nodes and storages of the cluster with their status."),
        T("pve", "pve_list_tasks", "List recent tasks."),
        T("zabbix", "problem_active_get", "Real, actionable problems right now: the primary tool for what is wrong.", props={"severities": {"type": "array"}, "limit": {"type": "integer"}}),
        T("zabbix", "problem_get", "Low-level retrieval of unresolved problems with raw rows."),
        T("zabbix", "host_get", "Retrieve monitored hosts.", props={"output": {"type": "string"}, "limit": {"type": "integer"}, "filter": {"type": "object"}}),
        T("zabbix", "health_check", "Health of the Zabbix server itself: version, queue, processes."),
        T("zabbix", "host_delete", "Delete hosts.", False),
        T("zabbix", "host_update", "Update a host.", False, props={"params": {"type": "object"}}, required=["params"]),
        T("zabbix", "event_acknowledge", "Acknowledge, close or change severity of events with a bitmask.", False),
        T("zabbix", "zabbix_ack_problem", "Acknowledge one open Zabbix problem, optionally with a message.", False, True, props={"problem": {"type": "string"}, "message": {"type": "string"}}, required=["problem"]),
        T("zabbix", "zabbix_set_host_enabled", "Enable or disable monitoring of one Zabbix host; a disabled host is enabled again automatically.", False, True),
        T("zabbix", "zabbix_host_group", "Add a Zabbix host to an existing host group, or remove it from one.", False, True),
        T("docker", "docker_list_containers", "List containers with name, image, state and status."),
        T("docker", "docker_container_logs", "Last lines of a container's logs."),
        T("docker", "docker_stop_container", "Stop a running container.", False),
        T("docker", "docker_restart_container", "Restart a container.", False),
        T("ollama", "ollama_list_models", "Installed models with size."),
        T("ollama", "ollama_running_models", "Models currently loaded in memory."),
        T("openwrt", "openwrt_known_hosts", "Every device the router has seen on the LAN: MAC, name and IPv4 addresses."),
        T("openwrt", "openwrt_wan_status", "Load balancer status per WAN interface."),
        T("n8n", "search_workflows", "Search for workflows with optional filters.", props={"query": {"type": "string"}}),
        T("n8n", "get_instance_context", "Opening picture of the instance: which workflows exist."),
        T("n8n", "execute_workflow", "Execute a workflow by id.", False),
    ]
    return {t.name: t for t in ts}
