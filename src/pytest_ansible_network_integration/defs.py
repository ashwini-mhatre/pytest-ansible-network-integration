"""Common objects."""
import logging
import os
import re
import subprocess
import time

from dataclasses import asdict
from dataclasses import dataclass
from dataclasses import field
from pathlib import Path
from typing import Dict
from typing import List
from uuid import uuid4

import xmltodict

from pylibsshext.errors import LibsshSessionException
from pylibsshext.session import Session


logger = logging.getLogger(__name__)


@dataclass
class AnsibleProject:
    """Ansible project."""

    playbook: Path
    inventory: Path
    directory: Path


class SshWrapper:
    """Wrapper for pylibssh."""

    def __init__(self, host, user, password, port=22):
        """Initialize the wrapper.

        :param host: The host
        :param user: The user
        :param password: The password
        :param port: The port
        """
        self.host = host
        self.user = user
        self.password = password
        self.port = port

        self.session = Session()

    def connect(self):
        """Connect to the host.

        :raises LibsshSessionException: If the connection fails
        """
        try:
            logger.debug("Connecting to {}".format(self.host))
            self.session.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                host_key_checking=False,
                look_for_keys=False,
            )
        except LibsshSessionException as e:
            raise e
        self.ssh_channel = self.session.new_channel()

    def execute(self, command):
        """Execute the command.

        :param command: The command
        :return: The result
        :raises LibsshSessionException: If the channel fails
        """
        if not self.session.is_connected:
            self.close()
            self.connect()
        try:
            result = self.ssh_channel.exec_command(command)
            stdout = result.stdout.decode()
            stderr = result.stderr.decode()
            return stdout, stderr
        except LibsshSessionException as e:
            raise e

    def close(self):
        """Close the channel."""
        self.ssh_channel.close()


class CmlWrapper:
    """Wrapper for cml."""

    def __init__(self, host, username, password):
        """Initialize the wrapper.

        :param host: The host
        :param username: The username
        :param password: The password
        """
        self.current_lab_id: str
        self._host = host
        self._auth_env = {
            "VIRL_HOST": host,
            "VIRL_USERNAME": username,
            "VIRL_PASSWORD": password,
            "CML_VERIFY_CERT": "False",
        }

    def up(self, file: str):
        """Bring the lab up.

        :param file: The file
        """
        logger.info("Bringing up lab '{}' on '{}'".format(file, self._host))
        # Using --provision was not reliable
        stdout, stderr = self._run(f"up -f {file}")
        logger.debug("CML up stdout: '{}'".format(stdout))
        # Starting lab xxx (ID: 9fde5f)\n
        current_lab_match = re.match(r".*ID: (?P<id>\S+)\)\n", stdout, re.DOTALL)
        if not current_lab_match:
            raise RuntimeError("Could not get lab ID: {} {}".format(stdout, stderr))
        self.current_lab_id = current_lab_match.groupdict()["id"]
        logger.info("Started lab id '{}'".format(self.current_lab_id))

    def rm(self):
        """Remove the lab."""
        logger.info("Deleting lab '{}' on '{}'".format(self.current_lab_id, self._host))
        stdout, stderr = self._run(f"use --id {self.current_lab_id}")
        logger.debug("CML use stdout: '{}'".format(stdout))
        stdout, stderr = self._run("rm --force --no-confirm")
        logger.debug("CML rm stdout: '{}'".format(stdout))

    def _run(self, command):
        """Run the command.

        :param command: The command
        :return: The result, stdout and stderr
        """
        cml_command = f"cml {command}"
        logger.info("Running command '{}' on '{}'".format(cml_command, self._host))
        env = os.environ.copy()
        if "VIRTUAL_ENV" in os.environ:
            env["PATH"] = os.path.join(os.environ["VIRTUAL_ENV"], "bin") + os.pathsep + env["PATH"]

        env.update(self._auth_env)

        logger.debug("Running command '{}' with environment '{}'".format(cml_command, env))
        process = subprocess.Popen(
            cml_command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        stdout, stderr = process.communicate()
        return stdout.decode(), stderr.decode()


class VirshWrapper:
    """Wrapper for virsh."""

    def __init__(self, host, user, password, port):
        """Initialize the wrapper.

        :param host: The host
        :param user: The user
        :param password: The password
        :param port: The port
        """
        self.ssh = SshWrapper(host=host, user=user, password=password, port=port)
        self.ssh.connect()

    def get_dhcp_lease(self, current_lab_id):
        """Get the dhcp lease.

        :param current_lab_id: The current lab id
        :raises Exception: If the dhcp lease cannot be found
        :return: The ip address
        """
        iter = 0
        current_lab = {}

        logger.info("Getting current lab from virsh")
        while not current_lab:
            logger.info("Attempt {}".format(iter))
            stdout, stderr = self.ssh.execute("sudo virsh list --all")

            virsh_ids = [re.match(r"^\s(?P<id>\d+)", line) for line in stdout.splitlines()]
            virsh_ids = [virsh_id.groupdict()["id"] for virsh_id in virsh_ids if virsh_id]

            for virsh_id in virsh_ids:
                stdout, stderr = self.ssh.execute(f"sudo virsh dumpxml {virsh_id}")
                if current_lab_id in stdout:
                    logger.debug("Found lab {} in virsh dumpxml: {}".format(current_lab_id, stdout))
                    current_lab = xmltodict.parse(stdout)
                    break
            if current_lab:
                break
            iter += 1
            if iter == 10:
                raise Exception("Could not find current lab")
            time.sleep(5)

        macs = [
            interface["mac"]["@address"]
            for interface in current_lab["domain"]["devices"]["interface"]
        ]
        logger.info("Found macs: {}".format(macs))

        logger.info("Getting a DHCP lease for any of {}".format(macs))
        ips = []
        iter = 0
        while not ips:
            logger.info("Attempt {}".format(iter))
            stdout, stderr = self.ssh.execute("sudo virsh net-dhcp-leases default")
            leases = {
                p[2]: p[4].split("/")[0]
                for p in [line.split() for line in stdout.splitlines()]
                if len(p) == 7
            }

            ips = [leases[mac] for mac in macs if mac in leases]
            iter += 1
            if iter == 30:
                raise Exception("Could not find IPs")
            time.sleep(10)

        logger.debug("Found IPs: {}".format(ips))

        if len(ips) > 1:
            raise Exception("Found more than one IP")

        return ips[0]

    def close(self):
        """Close the connection."""
        self.ssh.close()
