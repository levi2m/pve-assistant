"""Does the gate put the right tool in front of the model? Pure code, no model call, runs in seconds.

An integration test: it needs your tool servers connected, because it checks real tool names and descriptions.
    python -m pve_assistant gate-eval
Add your own wording to CASES. Questions use generic words; lab-specific names belong in [hints] in the config file.
"""
import json

from . import gate as G

# (question, tools of which at least one must be offered, tools that must NOT be offered)
CASES = [
    # Proxmox
    ("is the web container running?", {"pve_get_lxc_status", "pve_list_lxc_containers", "docker_list_containers"}, set()),
    ("list all LXC containers", {"pve_list_lxc_containers"}, set()),
    ("how much memory is the node using", {"pve_get_node_status"}, set()),
    ("which storage is almost full", {"pve_list_storage", "pve_get_storage_status", "pve_list_node_storage"}, set()),
    ("show the config of container 105", {"pve_get_lxc_config"}, set()),
    ("what proxmox tasks ran recently", {"pve_list_tasks"}, set()),
    ("overview of all cluster resources", {"pve_list_cluster_resources"}, set()),
    ("list the backup jobs", {"pve_list_backup_jobs"}, set()),
    ("start container 999", {"pve_start_lxc_container"}, set()),
    ("reboot container 105", {"pve_reboot_lxc_container"}, set()),
    ("shut down vm 200", {"pve_shutdown_qemu_vm"}, set()),
    ("which proxmox guests are running?", {"pve_list_cluster_resources", "pve_list_lxc_containers"}, set()),
    ("how much cpu is container 100 using", {"pve_get_lxc_status", "pve_get_lxc_rrddata"}, set()),
    ("what is the uptime of the node", {"pve_get_node_status"}, set()),
    ("list the vms on the node", {"pve_list_qemu_vms"}, set()),
    ("is there a snapshot of container 100", {"pve_list_lxc_snapshots"}, set()),
    ("what is the status of the proxmox cluster", {"pve_get_cluster_status"}, set()),
    ("show the proxmox firewall rules", {"pve_list_firewall_rules"}, set()),
    # Zabbix reads
    ("what problems are open right now", {"problem_get", "problem_active_get"}, set()),
    ("which hosts does zabbix monitor", {"host_get"}, set()),
    ("show the triggers on host web1 in zabbix", {"trigger_get"}, set()),
    ("are there any maintenance windows", {"maintenance_get"}, set()),
    ("what zabbix templates does host web1 have", {"template_get", "host_get"}, set()),
    ("show the latest zabbix events", {"event_get"}, set()),
    ("list the zabbix dashboards", {"dashboard_get"}, set()),
    ("anything critical on zabbix?", {"problem_active_get", "problem_get"}, {"health_check"}),
    ("how are your health checks on zabbix?", {"health_check"}, set()),
    # Zabbix recipes (changes)
    ("acknowledge the disk problem on host web1", {"zabbix_ack_problem"}, set()),
    ("add a comment to the load problem on host web1", {"zabbix_comment_problem"}, set()),
    ("change the severity of the load problem to warning", {"zabbix_set_problem_severity"}, set()),
    ("close the restarted problem on host web1", {"zabbix_close_problem"}, set()),
    ("disable monitoring of host web1 for 3 hours", {"zabbix_set_host_enabled"}, set()),
    ("turn off the trigger high memory utilization on web1", {"zabbix_set_trigger_enabled"}, set()),
    ("set the macro {$X} on host web1 to 5", {"zabbix_set_host_macro"}, set()),
    ("add host web1 to the zabbix group servers", {"zabbix_host_group"}, set()),
    ("remove host web1 from the zabbix group servers", {"zabbix_host_group"}, set()),
    # Docker, Ollama, router, n8n
    ("which docker containers are running", {"docker_list_containers"}, set()),
    ("show the logs of the nginx container in docker", {"docker_container_logs"}, set()),
    ("restart the proxy container in docker", {"docker_restart_container"}, set()),
    ("how much disk do docker images use", {"docker_disk_usage", "docker_list_images"}, set()),
    ("which ollama models are installed", {"ollama_list_models"}, set()),
    ("is a model loaded in memory", {"ollama_running_models"}, set()),
    ("unload the model", {"ollama_unload_model"}, set()),
    ("what version is open webui", {"openwebui_status"}, set()),
    ("is the second wan online", {"openwrt_wan_status", "openwrt_interfaces"}, set()),
    ("who is connected to the wifi", {"openwrt_wifi_clients", "openwrt_known_hosts"}, set()),
    ("router uptime and load", {"openwrt_system_info"}, set()),
    ("what is the device at 192.0.2.50", {"openwrt_known_hosts"}, {"pve_get_lxc_status", "pve_get_node_status"}),
    ("what firmware is the router running", {"openwrt_board"}, set()),
    ("list my n8n workflows", {"search_workflows"}, set()),
    ("how many n8n workflows exist", {"search_workflows"}, set()),
    ("find the n8n workflow called backup", {"search_workflows"}, set()),
    ("show recent workflow executions", {"search_workflow_executions"}, set()),
    # things that must never be offered (the deny list and the delete-mode rule)
    ("delete container 999", set(), {"pve_delete_lxc_container"}),
    ("destroy the storage", set(), {"pve_delete_storage"}),
    ("run a backup of container 105 now", set(), {"pve_run_backup", "pve_create_backup_job"}),
    ("roll back the snapshot of container 100", set(), {"pve_rollback_lxc_snapshot"}),
    ("destroy vm 200", set(), {"pve_delete_qemu_vm"}),
    ("remove the backup job", set(), {"pve_delete_backup_job"}),
    ("delete the zabbix host web1", set(), {"zabbix_set_host_enabled", "zabbix_host_group", "host_delete"}),
    ("delete the docker container proxy", set(), {"docker_stop_container", "docker_restart_container", "docker_start_container"}),
    ("create a zabbix dashboard called test", set(), {"dashboard_create"}),
    ("update the host web1 in zabbix", set(), {"host_update"}),
    ("how are you doing", set(), set()),
]


def deny_check(gate, reg):
    """Every denied tool must (a) exist, (b) be refused by check_call, (c) never be offered, whatever the question says."""
    missing = [n for n in G.DENY_NAMES if n not in reg.tools]
    unrefused = [n for n in G.DENY_NAMES if n in reg.tools and not G.check_call(reg.tools[n], {})]
    probes = [f"{verb} {n.replace('pve_', '').replace('_', ' ')}" for n in G.DENY_NAMES for verb in ("please run", "delete", "start")]
    offered = {n for q in probes for n in gate.select(q)[0] if n in G.DENY_NAMES}
    raw = [k for k, t in reg.tools.items() if t.server == "zabbix" and not t.read_only and not getattr(t, "virtual", False)]
    raw_refused = [k for k in raw if not G.check_call(reg.tools[k], {})]
    raw_probes = [f"{v} {k.replace('_', ' ')}" for k in raw for v in ("please run", "delete")]
    raw_offered = {n for q in raw_probes for n in gate.select(q)[0] if n in raw}
    print(f"raw zabbix writes: {len(raw)} tools | not refused: {raw_refused or 'none'} | ever offered: {sorted(raw_offered) or 'never'}")
    print(f"deny rules       : {len(G.DENY_NAMES)} tools | not in registry: {missing or 'none'} | not refused: {unrefused or 'none'} | ever offered: {sorted(offered) or 'never'}\n")
    return not (unrefused or offered or raw_refused or raw_offered)


def run(reg):
    gate = G.Gate(reg.tools)
    safe = deny_check(gate, reg)
    ok = bad = 0
    sizes = []
    for q, want, forbid in CASES:
        picked, hinted, action = gate.select(q)
        sizes.append(len(json.dumps([G.slim(k, reg.tools[k]) for k in picked])) // 4)
        if ((not want) or bool(want & set(picked))) and not (forbid & set(picked)):
            ok += 1
        else:
            bad += 1
            print(f"MISS  {q!r}\n      want any of {sorted(want)} forbid {sorted(forbid)}\n      got  {picked} (servers {hinted}, action={action})")
    print(f"gate: {ok}/{ok + bad} cases pass | schema tokens per request: avg {sum(sizes) // len(sizes)}, max {max(sizes)}")
    if not safe:
        print("DENY CHECK FAILED")
