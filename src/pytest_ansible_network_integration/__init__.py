# cspell:ignore nodeid
"""Common fixtures for tests."""
import json
import logging
import os
import time

from pathlib import Path

import pytest

from .defs import AnsibleProject
from .defs import CmlWrapper
from .defs import VirshWrapper


logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def network_test_vars(request):
    """Provide the network test vars."""

    requesting_test = Path(request.node.nodeid)

    test_fixture_directory = Path(
        Path(requesting_test.parts[0]) / "integration/fixtures" / Path(*requesting_test.parts[1:])
    ).resolve()
    test_mode = os.environ.get("ANSIBLE_NETWORK_TEST_MODE", "playback").lower()

    vars = {
        "ansible_network_test_parameters": {
            "fixture_directory": str(test_fixture_directory),
            "match_threshold": 0.90,
            "mode": test_mode,
        }
    }
    return vars


def pytest_addoption(parser):
    """Add options to pytest."""
    parser.addoption(
        "--integration-tests-path",
        action="store",
        help="The integration test path",
    )
    parser.addoption(
        "--cml-lab",
        action="store",
        help="The CML lab to use",
    )


options = None


def pytest_configure(config):
    """Make cmdline arguments available."""
    global options
    options = config.option


def pytest_generate_tests(metafunc):
    """Generate tests."""
    if "integration_test_path" in metafunc.fixturenames:
        rootdir = metafunc.config.getoption("integration_tests_path")
        roles = [path for path in Path(rootdir).iterdir() if path.is_dir()]
        metafunc.parametrize("integration_test_path", roles, ids=lambda x: x.name)


def _inventory(host, network_os, username, password, port, httpapi_port):
    inventory = {
        "all": {
            "hosts": {
                "appliance": {
                    "ansible_become": False,
                    "ansible_host": host,
                    "ansible_user": username,
                    "ansible_password": password,
                    "ansible_port": port,
                    "ansible_httpapi_port": httpapi_port,
                    "ansible_connection": "ansible.netcommon.network_cli",
                    "ansible_network_cli_ssh_type": "libssh",
                    "ansible_python_interpreter": "python",
                    "ansible_network_import_modules": True,
                }
            },
            "vars": {"ansible_network_os": network_os},
        }
    }
    return inventory


def playbook(role):
    """Return the playbook."""
    task = {"name": f"Run role {role}", "include_role": {"name": role}}
    play = {"hosts": "all", "gather_facts": False, "tasks": [task]}
    playbook = [play]
    return playbook


@pytest.fixture(scope="session")
def appliance_dhcp_address():
    logger.info("Starting lab provisioning")
    cml_host = os.environ.get("VIRL_HOST")
    cml_ui_user = os.environ.get("VIRL_USERNAME")
    cml_ui_password = os.environ.get("VIRL_PASSWORD")
    cml_ssh_user = os.environ.get("CML_SSH_USER")
    cml_ssh_password = os.environ.get("CML_SSH_PASSWORD")
    cml_ssh_port = os.environ.get("CML_SSH_PORT")
    network_os = os.environ.get("ANSIBLE_NETWORK_OS")

    if not any(
        [
            cml_host,
            cml_ui_user,
            cml_ui_password,
            cml_ssh_user,
            cml_ssh_password,
            cml_ssh_port,
            network_os,
        ]
    ):
        raise Exception("Missing CML environment variables")

    lab_file = options.cml_lab
    if not os.path.exists(lab_file):
        raise Exception(f"Missing lab file '{lab_file}'")

    start = time.time()
    cml = CmlWrapper(host=cml_host, username=cml_ui_user, password=cml_ui_password)
    cml.up(file=lab_file)
    lab_id = cml.current_lab_id

    virsh = VirshWrapper(
        host=cml_host,
        user=cml_ssh_user,
        password=cml_ssh_password,
        port=int(cml_ssh_port),
    )

    try:
        ip = virsh.get_dhcp_lease(lab_id)
    except Exception:
        virsh.close()
        cml.rm()
        raise Exception("Failed to get DHCP lease for the appliance")

    end = time.time()
    logger.info(f"Elapsed time to provision {end - start} seconds")

    virsh.close()

    yield ip

    cml.rm()


@pytest.fixture
def ansible_project(appliance_dhcp_address, integration_test_path, tmp_path):
    network_os = os.environ.get("ANSIBLE_NETWORK_OS")
    cml_host = os.environ.get("VIRL_HOST")

    octets = appliance_dhcp_address.split(".")
    ssh_port = 2000 + int(octets[-1])
    https_port = 4000 + int(octets[-1])
    http_port = 8000 + int(octets[-1])
    netconf_port = 3000 + int(octets[-1])

    inventory = _inventory(
        network_os=network_os,
        host=cml_host,
        username="ansible",
        password="ansible",
        port=ssh_port,
        httpapi_port=http_port,
    )
    inventory_path = tmp_path / "inventory.json"
    with open(inventory_path, "w") as f:
        json.dump(inventory, f)
    playbook_contents = playbook(str(integration_test_path))
    playbook_path = tmp_path / "site.json"
    with open(playbook_path, "w") as f:
        json.dump(playbook_contents, f)
    logger.info(f"Inventory path: {inventory_path}")
    logger.info(f"Playbook path: {playbook_path}")
    return AnsibleProject(playbook=playbook_path, inventory=inventory_path, directory=tmp_path)


@pytest.fixture
def environment():
    env = os.environ.copy()
    if "VIRTUAL_ENV" in os.environ:
        env["PATH"] = os.path.join(os.environ["VIRTUAL_ENV"], "bin") + os.pathsep + env["PATH"]
    return env
