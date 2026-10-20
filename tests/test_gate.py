import os, sys
sys.path.insert(0, os.path.dirname(__file__)); sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from _toy import toy_tools
from pve_assistant import gate as G

TOOLS = toy_tools()
GATE = G.Gate(TOOLS)
offered = lambda q: GATE.select(q)[0]


def test_reads_hide_writes_and_actions_show_them():
    assert not any(not TOOLS[k].read_only for k in offered("how much memory is the node using"))
    assert "pve_start_lxc_container" in offered("start container 999")


def test_deny_list_is_never_offered_and_always_refused():
    for name in G.DENY_NAMES:
        if name not in TOOLS:
            continue
        assert G.check_call(TOOLS[name], {}), f"{name} should be refused"
        for verb in ("please run", "delete", "start"):
            assert name not in offered(f"{verb} {name.replace('pve_', '').replace('_', ' ')}"), f"{name} offered"


def test_raw_zabbix_writes_hidden_recipes_used():
    for raw in ("host_delete", "host_update", "event_acknowledge"):
        assert raw not in offered(f"delete or update or acknowledge {raw.replace('_', ' ')} on zabbix")
        assert G.check_call(TOOLS[raw], {})
    assert "zabbix_ack_problem" in offered("acknowledge the disk problem on host web1")


def test_delete_mode_offers_no_lookalikes():
    got = offered("delete the docker container proxy")
    assert not {"docker_stop_container", "docker_restart_container"} & set(got)
    assert "zabbix_host_group" in offered("remove host web1 from the zabbix group servers")
    assert "zabbix_host_group" not in offered("delete the zabbix host web1")


def test_critical_means_problems_not_server_health():
    got = offered("anything critical on zabbix?")
    assert "problem_active_get" in got and "health_check" not in got
    assert "health_check" in offered("how are your health checks on zabbix?")


def test_ip_addresses_go_to_the_router_and_ids_to_proxmox():
    got = offered("what is the device at 192.0.2.101?")
    assert "openwrt_known_hosts" in got and not any(k.startswith("pve_") for k in got)
    assert offered("start container 999")[0] == "pve_start_lxc_container"


def test_generic_words_do_not_pick_the_wrong_n8n_tool():
    assert "get_instance_context" not in offered("how many n8n workflows exist?")
    assert "search_workflows" in offered("find the n8n workflow called backup")


def test_slim_hides_arguments_the_model_fills_in_wrongly():
    s = G.slim("problem_active_get", TOOLS["problem_active_get"])["function"]["parameters"]["properties"]
    assert "limit" not in s and "severities" not in s
    h = G.slim("host_get", TOOLS["host_get"])["function"]["parameters"]["properties"]
    assert "output" not in h and "limit" not in h and "filter" in h


def test_protected_n8n_workflows_cannot_be_run():
    assert G.check_call(TOOLS["execute_workflow"], {"workflowId": "abc"}, frozenset({"abc"}))
    assert not G.check_call(TOOLS["execute_workflow"], {"workflowId": "other"}, frozenset({"abc"}))
