# MD-OMuProCU

This repository contains the source code for global management plane of [OMuProCU Core](https://github.com/tiritor/OMuProCU-core). The whole project can be seen [here](https://github.com/tiritor/OMuProCU)

This part of the framework is used in the paper `Orchestrating Multi-Tenant Code Updates Across Multiple Programmable Switches`.

## Disclaimer

> The framework proposed in the used proprietary software and APIs to initialize hardware, etc. after updating the hardware. Since these are licensed, this was removed, and must be added again or done manually, to get the full functional version used in the paper!

## Requirements

- Python 3.8
- Pip 3.21.1
- Kubernetes Distribution (e.g. K3s) on OMuProCU managed devices
- Accelerator Hardware (Tofino 1 chip or BMv2) with managed OMuProCU

## Installation

As first step, we recommend to create virtual environment:

```
python3 -m venv .venv
source .venv.bin
```

Then, you need to install the python packages needed in this repository:

```
pip3 install -r requirements.txt
```

Also, you need to build and install **OMuProCU-utils** pip packages. 

```
cd ../OMuProCU_utils
pip3 install -r requirements.txt
python3 setup.py sdist
pip3 install ../OMuProCU_utils
```

## Usage

**ATTENTION: The programmable switch must be started and ready for reconfiguration! Also, OMuProCU must be started at all desired devices! This is not part of the global view framework!**

The [management_process.py](management_process.py) contains the mangement process which starts also all other components in the right order. 
So running ```python3 management_process.py``` will start the orchestrator with default parameters.
But it is also possible to use the ```./start-mdomuprocu.sh``` script.

## Structure

The OMuProCU core consists of different microservices:

- [Management](#management)
- [Topology Manager](#topology-manager)
- [Health Monitor](#health-monitor)

Also, there are some modules which are used from the [OMuProCU-utils](https://github.com/tiritor/OMuProCU-utils) package like:

- Validator
- Persistor
- Tenant Communcation Controller
- Protobuf Message Descriptions and its GRPC interfaces

### Management

The core of the OMuProCU is the Management Process which is covered in the [management_process.py](management_process.py). 
It contains the submission process for TDCs and health check pipeline control as well as the topology manager as global view.
Also, the state management for LAG/port configurations and provider maintained tables is done by this component. 

### Health Monitor

This component [health_monitor.py](health_monitor/health_monitor.py) checks the health of OMuProCU nodes and deployments applied to nodes. 

### Topology Manager 

This contains the structure of the topology which should be managed. 
Also, the state of each node in the topology is managed there.

## Configuration 

### [tenant_security_config.json](conf/tenant_security_config.json)

In this file, the configuration for tenants is saved which consist of:

- Tenant ID as key (number)
- Name of the tenant
- VNIs which the tenant can use for its accelerated CNFs. 
- Names of deployed TDCs 
- Names of the main ingresses defined in the submitted TDCs **(ATTENTION: This will be automatically configured!)**

### [extern_blacklist.json](conf/extern_blacklist.json)

If any extern should not be used by a tenant, this can be added into the list of the associated accelerator. Validate it the same way as the OMuProCU does.


### [topology.json](conf/topology.json)

This file contains the network topology configuration for the OMuProCU system that describes the structure and state of each node in the network topology.

There are three different categories available:
- Endpoint (e.g., Hosts or other OMuProCU unmanaged devices/networks)
- Edge Device (e.g., devices at the edge which possesses links to unmanaged nodes in the topology)
- Center Device (e.g., central devices in the topology)


### [config.py](conf/config.py)

This is the main config file for MD-OMuProCU where e.g., GRPC Server addresses can be defined.