# pytest-ansible-network-integration

An experimental pytest plugin designed to run ansible network integration tests against an appliance defined in an CML lab topology.

## Required environment variables:

- ANSIBLE_NETWORK_OS
- CML_SSH_PASSWORD
- CML_SSH_PORT
- CML_SSH_USER
- VIRL_HOST
- VIRL_PASSWORD
- VIRL_USERNAME

## Required pytest command line parameters:

- --integration-tests-path
- ---cml-lab

## Sample pyproject.toml:

```
[tool.pytest.ini_options]
addopts = "-s -vvv --integration-tests-path=tests/integration/targets --cml-lab=./tests/integration/labs/single.yaml"
testpaths = ["tests"]
filterwarnings = [
    'ignore:AnsibleCollectionFinder has already been configured',
    'ignore:_AnsibleCollectionFinder.find_spec().*',
]
```
