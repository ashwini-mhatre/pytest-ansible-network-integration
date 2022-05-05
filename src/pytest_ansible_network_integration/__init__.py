# cspell:ignore nodeid
"""Common fixtures for tests."""
import argparse
import json
import logging
import os
import time

from pathlib import Path
from typing import Any
from typing import Dict
from typing import Generator
from typing import List

import pytest

from .defs import AnsibleProject
from .defs import CmlWrapper
from .defs import VirshWrapper


logger = logging.getLogger(__name__)


@pytest.fixture(scope="function")
def network_test_vars(request: pytest.FixtureRequest) -> Dict[str, Any]:
    """Provide the network test vars.

    :param request: The request
    :returns: The network test vars
    """
    requesting_test = Path(request.node.nodeid)

    test_fixture_directory = Path(
        Path(requesting_test.parts[0]) / "integration/fixtures" / Path(*requesting_test.parts[1:])
    ).resolve()
    test_mode = os.environ.get("ANSIBLE_NETWORK_TEST_MODE", "playback").lower()

    play_vars = {
        "ansible_network_test_parameters": {
            "fixture_directory": str(test_fixture_directory),
            "match_threshold": 0.90,
            "mode": test_mode,
        }
    }
    return play_vars


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add options to pytest.

    :param parser: The pytest argument parser
    """
    parser.addoption(
        "--integration-tests-path",
        action="store",
        required=True,
        help="The integration test path",
    )
    parser.addoption(
        "--cml-lab",
        action="store",
        required=True,
        help="The CML lab to use",
    )


OPTIONS = None


def pytest_configure(config: pytest.Config) -> None:
    """Make cmdline arguments available.

    :param config: The pytest configuration object
    """
    global OPTIONS  # pylint: disable=global-statement
    OPTIONS = config.option


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    """Generate tests.

    :param metafunc: The pytest metafunc object
    """
    if "integration_test_path" in metafunc.fixturenames:
        rootdir = metafunc.config.getoption("integration_tests_path")
        roles = [path for path in Path(rootdir).iterdir() if path.is_dir()]
        role_names = [role.name for role in roles]
        metafunc.parametrize("integration_test_path", roles, ids=role_names)


def _inventory(
    host: str,
    httpapi_port: int,
    network_os: str,
    password: str,
    port: int,
    username: str,
) -> Dict[str, Any]:
    # pylint: disable=too-many-arguments
    """Build an ansible inventory.

    :param host: The hostname
    :param httpapi_port: The HTTPAPI port
    :param network_os: The network OS
    :param password: The password
    :param port: The port
    :param username: The username
    :returns: The inventory
    """
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


def playbook(role: str) -> List[Dict[str, object]]:
    """Return the playbook.

    :param role: The role's path
    :returns: The playbook
    """
    task = {"name": f"Run role {role}", "include_role": {"name": role}}
    play = {"hosts": "all", "gather_facts": False, "tasks": [task]}
    playbook_obj = [play]
    return playbook_obj


@pytest.fixture(scope="session", name="env_vars")
def required_environment_variables() -> Dict[str, str]:
    """Return the required environment variables.

    :raises Exception: If the environment variables are not set
    :returns: The required environment variables
    """
    variables = {
        "cml_host": os.environ.get("VIRL_HOST"),
        "cml_ui_user": os.environ.get("VIRL_USERNAME"),
        "cml_ui_password": os.environ.get("VIRL_PASSWORD"),
        "cml_ssh_user": os.environ.get("CML_SSH_USER"),
        "cml_ssh_password": os.environ.get("CML_SSH_PASSWORD"),
        "cml_ssh_port": os.environ.get("CML_SSH_PORT"),
        "network_os": os.environ.get("ANSIBLE_NETWORK_OS"),
    }
    if not all(variables.values()):
        raise Exception("CML environment variables not set")

    return variables  # type: ignore[return-value]


@pytest.fixture(scope="session", name="appliance_dhcp_address")
def _appliance_dhcp_address(env_vars: Dict[str, str]) -> Generator[str, None, None]:
    """Build the lab and collect the appliance DHCP address.

    :param env_vars: The environment variables
    :raises Exception: Missing environment variables, lab, or appliance
    :yields: The appliance DHCP address
    """
    logger.info("Starting lab provisioning")

    if not OPTIONS:
        raise Exception("Missing CML lab")
    lab_file = OPTIONS.cml_lab
    if not os.path.exists(lab_file):
        raise Exception(f"Missing lab file '{lab_file}'")

    start = time.time()
    cml = CmlWrapper(
        host=env_vars["cml_host"],
        username=env_vars["cml_ui_user"],
        password=env_vars["cml_ui_password"],
    )
    cml.bring_up(file=lab_file)
    lab_id = cml.current_lab_id

    virsh = VirshWrapper(
        host=env_vars["cml_host"],
        user=env_vars["cml_ssh_user"],
        password=env_vars["cml_ssh_password"],
        port=int(env_vars["cml_ssh_port"]),
    )

    try:
        ip_address = virsh.get_dhcp_lease(lab_id)
    except Exception as exc:
        virsh.close()
        cml.remove()
        raise Exception("Failed to get DHCP lease for the appliance") from exc

    end = time.time()
    logger.info("Elapsed time to provision %s seconds", end - start)

    virsh.close()

    yield ip_address

    cml.remove()


@pytest.fixture
def ansible_project(
    appliance_dhcp_address: str,
    env_vars: Dict[str, str],
    integration_test_path: Path,
    tmp_path: Path,
) -> AnsibleProject:
    """Build the ansible project.

    :param appliance_dhcp_address: The appliance DHCP address
    :param env_vars: The environment variables
    :param integration_test_path: The integration test path
    :param tmp_path: The temporary path
    :returns: The ansible project
    """
    octets = appliance_dhcp_address.split(".")
    ssh_port = 2000 + int(octets[-1])
    _https_port = 4000 + int(octets[-1])
    http_port = 8000 + int(octets[-1])
    _netconf_port = 3000 + int(octets[-1])

    inventory = _inventory(
        network_os=env_vars["network_os"],
        host=env_vars["cml_host"],
        username="ansible",
        password="ansible",
        port=ssh_port,
        httpapi_port=http_port,
    )
    inventory_path = tmp_path / "inventory.json"
    with inventory_path.open(mode="w", encoding="utf-8") as fh:
        json.dump(inventory, fh)
    playbook_contents = playbook(str(integration_test_path))
    playbook_path = tmp_path / "site.json"
    with playbook_path.open(mode="w", encoding="utf-8") as fh:
        json.dump(playbook_contents, fh)
    logger.info("Inventory path: %s", inventory_path)
    logger.info("Playbook path: %s", playbook_path)
    return AnsibleProject(playbook=playbook_path, inventory=inventory_path, directory=tmp_path)


@pytest.fixture
def environment() -> Dict[str, Any]:
    """Build the environment, adding the virtual environment if present.

    :returns: The environment
    """
    env = os.environ.copy()
    if "VIRTUAL_ENV" in os.environ:
        env["PATH"] = os.path.join(os.environ["VIRTUAL_ENV"], "bin") + os.pathsep + env["PATH"]
    return env
