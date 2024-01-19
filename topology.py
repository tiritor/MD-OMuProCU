#!/usr/bin/python3

from enum import Enum
import json
from typing import Union
from matplotlib import pyplot as plt
from networkx import DiGraph, MultiDiGraph, all_simple_paths
import networkx

from orchestrator_utils.device_categories import InfrastructureDeviceCategories
from orchestrator_utils.states import DeploymentStatus
from orchestrator_utils.til.orchestrator_msg_pb2 import UpdateAction
from orchestrator_utils.til.tif_control_pb2 import DpPort, RoutingTableConfiguration
from conf.config import UPDATE_ORDER_STRATEGY

from health_monitor.states import OrchestratorHealthStates
from update_strategy import UpdateStrategy, UpdateStrategyException


class TopologyNode(object):
    """
    Represents a node in a topology.

    Attributes:
        category (str): The category of the node.
        name (str): The name of the node.
    """
    category = ""
    name = ""

    def __init__(self, name, category) -> None:
        self.name = name
        self.category = category


class Endpoint(TopologyNode):
    """
    Represents an endpoint in the network topology.

    Args:
        name (str): The name of the endpoint.
        category (str, optional): The category of the endpoint. Defaults to "endpoint".
    """
    def __init__(self, name, category="endpoint") -> None:
        super().__init__(name, category)


class AcceleratorDevice(TopologyNode):
    """
    Represents an accelerator device in the topology.

    Args:
        name (str): The name of the accelerator device.
        category (str): The category of the accelerator device.

    Attributes:
        name (str): The name of the accelerator device.
        category (str): The category of the accelerator device.
    """
    def __init__(self, name, category) -> None:
        super().__init__(name, category)


class GlobalView(MultiDiGraph):
    """
    Represents the global view of the network topology.

    Attributes:
        edge_devices (dict): Dictionary to store edge devices.
        datacenter_devices (dict): Dictionary to store datacenter devices.
        endpoints (dict): Dictionary to store endpoints.

    Methods:
        load_topology_file(file_path): Loads the topology data from a file.
        get_device_node(name): Returns the node with the specified name.
        get_device_node_deployment(name, tenantId, deploymentName): Returns the deployment of a specific device node.
        get_update_order(start_device, end_device): Returns the update order for devices between two specified devices.
        get_src_dp_port(device_link_1, device_link_2): Returns the source datapath port for a device link.
        get_dest_dp_port(device_link_1, device_link_2): Returns the destination datapath port for a device link.
        get_node_links_to_edge_nodes(device): Returns the links from a device node to edge nodes.
        get_links_to_node(start_device, end_device, direct_links): Returns the links between two specified devices.
        get_lag_ecmp_groups_for_device(device): Returns the LAG/ECMP groups for a device.
        get_lag_ecmp_groups_for_device_by_id(device_name, id): Returns the LAG/ECMP group with the specified ID for a device.
        get_table_entries_for_devices(device, table): Returns the table entries for a device.
        find_lag_by_dp_port_member(device, src_dp_port): Finds the LAG/ECMP group that contains the specified datapath port.
        update_device_node_deployment(name, tenantId, deploymentName, deployment, updateAction, deploymentStatus, mdDeploymentStatus): Updates the deployment information for a device node.
        get_device_node_deployments_by_status(status, node): Returns the deployments with the specified status for a device node.
        get_device_node_deployments_by_md_status(mdStatus, node): Returns the deployments with the specified MD status for a device node.
    """
    
    edge_devices = {}
    datacenter_devices = {}
    endpoints = {}

    # Topology = Network
    # Nodes = Switches/Acceleration Hardware
    # Edges = Connections between Host/Switches

    def load_topology_file(self, file_path):
        """
        Load the topology data from a JSON file and update the network graph accordingly.

        Args:
            file_path (str): The path to the JSON file containing the topology data.

        Raises:
            ValueError: If the category of a device in the topology data is not valid.

        Returns:
            None
        """
        topology_data = None
        with open(file_path) as f:
            topology_data = json.load(f)

        tenant_security_config = None
        with open("conf/tenant_security_config.json") as f:
            tenant_security_config = json.load(f)

        # Add nodes to the network graph
        for name, metadata in topology_data["devices"].items():
            if metadata["category"] not in [category.value for category in InfrastructureDeviceCategories]:
                raise ValueError("Invalid device category")
            if metadata["category"] != InfrastructureDeviceCategories.ENDPOINT_DEVICE.value:
                self.add_node(name, **{
                    "category": metadata["category"], 
                    "OMuProCUAddress": metadata["OMuProCUAddress"],
                    "OMuProCU-TIFAddress": metadata["OMuProCU-TIFAddress"],
                    "OMuProCUEnabled": metadata["OMuProCUEnabled"],
                    "status": OrchestratorHealthStates.UNKNOWN,
                    "deployments": {},
                    "lagEcmpGroups": metadata["lag_ecmp_groups"],
                    "nexthopMap": metadata["nexthop_map"],
                    "ipv4HostEntries": metadata["ipv4_host_entries"],
                    "arpTableHostEntries": metadata["arp_table_host_entries"],
                })
                for id, tenant_metadata in tenant_security_config.items():
                    if name not in tenant_metadata["devices"].keys():
                        tenant_security_config[id]["devices"][name] = {}
                        tenant_security_config[id]["devices"][name]["Deployed_TDC_Names"] = []
                        tenant_security_config[id]["devices"][name]["mainIngressNames"] = []
                    else:
                        if "Deployed_TDC_Names" not in tenant_security_config[id]["devices"][name].keys():
                            tenant_security_config[id]["devices"][name]["Deployed_TDC_Names"] = []
                        if "mainIngressNames" not in tenant_security_config[id]["devices"][name].keys():
                            tenant_security_config[id]["devices"][name]["mainIngressNames"] = []
            else:
                self.add_node(name, **{
                    "category": metadata["category"], 
                })

        # Add edges to the network graph
        for name, device_data in topology_data["devices"].items():
            for connection in device_data["connections"]:
                self.add_edge(name, connection["dest_device"], weight=connection["weight"], src_port= connection["src_port"], dest_port= connection["dest_port"], src_dp_port= connection["src_dp_port"], dest_dp_port= connection["dest_dp_port"], speed=connection["speed"], fec=connection["fec"], an=connection["an"], enabled=connection["enabled"])

        # Update the tenant security configuration file
        with open("conf/tenant_security_config.json", "w") as f:
            f.write("")
            json.dump(tenant_security_config, f, indent=2)

    def get_device_node(self, name: str):
        """
        Get the device node with the specified name.

        Parameters:
        name (str): The name of the device node.

        Returns:
        Node: The device node with the specified name.
        """
        return self.nodes[name]
    
    def get_device_node_deployment(self, name: str, tenantId: int, deploymentName: str):
        """
        Get the deployment of a device node.

        Args:
            name (str): The name of the device node.
            tenantId (int): The ID of the tenant.
            deploymentName (str): The name of the deployment.

        Returns:
            The deployment of the device node.
        """
        return self.nodes[name]["deployments"][tenantId][deploymentName]["deployment"]

    def get_update_order(self, start_device="s1", end_device="s3"):
        """
        Returns the update order for devices in the network topology.

        Args:
            start_device (str): The starting device for the update order. Defaults to "s1".
            end_device (str): The ending device for the update order. Defaults to "s3".

        Returns:
            tuple: The update order of devices in the network topology.

        Raises:
            UpdateStrategyException: If the update strategy is not supported.
        """
        paths = all_simple_paths(self, start_device, end_device)
        update_order = ()
        edge_devices = set()
        datacenter_devices = set()
        if UPDATE_ORDER_STRATEGY == UpdateStrategy.DATACENTER_DEVICES_FIRST:
            for path in paths:
                middle_nodes = path[1:-1]
                update_order += tuple([node for node in middle_nodes])
                edge_devices.add(path[0])
                edge_devices.add(path[-1])
            update_order += tuple([node for node in edge_devices])
        elif UPDATE_ORDER_STRATEGY == UpdateStrategy.EDGE_DEVICES_FIRST:
            for path in paths:
                middle_nodes = path[1:-1]
                datacenter_devices += tuple([node for node in middle_nodes])
                edge_devices.add(path[0])
                edge_devices.add(path[-1])
            update_order += tuple([node for node in datacenter_devices])
            update_order += tuple([node for node in edge_devices])
        elif UPDATE_ORDER_STRATEGY == UpdateStrategy.ALL_AT_ONCE:
            raise NotImplementedError
        else:
            raise UpdateStrategyException("Strategy not supported!")
        return update_order
    
    def get_src_dp_port(self, device_link_1, device_link_2=None):
        """
        Get the source datapath port for a given device link.

        Args:
            device_link_1 (str): The first device link.
            device_link_2 (str, optional): The second device link. Defaults to None.

        Returns:
            str: The source datapath port.
        """
        return self._get_connections_attribute(device_link_1, "src_dp_port", device_link_2)

    def get_dest_dp_port(self, device_link_1, device_link_2=None):
        """
        Get the destination datapath port for a given device link.

        Args:
            device_link_1: The first device link.
            device_link_2: The second device link (optional).

        Returns:
            The destination datapath port.
        """
        return self._get_connections_attribute(device_link_1, "dest_dp_port", device_link_2)
        
    def _get_connections_attribute(self, device_link_1, attribute, device_link_2=None):
        """
        Get the attribute value for the connection between two devices.

        Args:
            device_link_1 (tuple or list or any): The first device link.
            attribute (str): The attribute to retrieve.
            device_link_2 (any, optional): The second device link. Required if device_link_1 is not a tuple or list.

        Returns:
            The value of the specified attribute for the connection between device_link_1 and device_link_2.

        """
        attributes = networkx.get_edge_attributes(self, attribute)
        if isinstance(device_link_1, tuple):
            device_link_1 = list(device_link_1)  # Convert tuple to list
            return attributes[tuple(device_link_1)]
        elif isinstance(device_link_1, list):
            return attributes[tuple(device_link_1)]
        else:
            return attributes[(device_link_1, device_link_2)]

    def get_node_links_to_edge_nodes(self, device):
        """
        Returns a list of paths from the given device to all edge devices.

        Args:
            device: The name of the device to find paths from.

        Returns:
            paths: A list of paths from the given device to all edge devices.
        """
        paths = []
        for edge_device in self.get_edge_device_names():
            paths.extend(self.get_links_to_node(device, edge_device))
        return paths

    def get_links_to_node(self, start_device, end_device, direct_links= False):
            """
            Returns a list of links between the start_device and end_device.

            Args:
                start_device (str): The starting device.
                end_device (str): The ending device.
                direct_links (bool): Flag to indicate whether to consider only direct links.

            Returns:
                path_list (list): A list of links between the start_device and end_device.
            """
            path_list = []
            paths = all_simple_paths(self, start_device, end_device)
            for path in paths:
                if len(path) <= 2:
                    if [path[0], path[1], 0] in path_list:
                        path_list.append([path[0], path[1], max([existing_path[2] for existing_path in path_list if path[0] == existing_path[0] and path[1] == existing_path[1]]) + 1])
                        # path_list.append([path[0], path[-1], 1])
                    else:
                        path_list.append([path[0], path[1], 0])
            return path_list
    
    def get_lag_ecmp_groups_for_device(self, device):
        """
        Get the LAG ECMP groups for a specific device.

        Args:
            device (str): The name of the device.

        Returns:
            list: A list of LAG ECMP groups for the specified device.
        """
        return self.get_device_node(device)["lagEcmpGroups"]
    
    def get_lag_ecmp_groups_for_device_by_id(self, device_name: str, id: int):
        """
        Retrieve the LAG/ECMP group metadata for a specific device by its ID.

        Args:
            device_name (str): The name of the device.
            id (int): The ID of the LAG/ECMP group.

        Returns:
            dict or None: The metadata of the LAG/ECMP group if found, None otherwise.
        """
        for name, metadata in self.get_device_node(device_name)["lagEcmpGroups"].items():
            if metadata["id"] == id:
                return metadata
        return None
    
    def get_table_entries_for_devices(self, device, table):
        """
        Get the table entries for a specific device.

        Args:
            device (str): The name of the device.
            table (str): The name of the table.

        Returns:
            list: The table entries for the specified device and table.
        """
        return self.get_device_node(device)[table]

    def find_lag_by_dp_port_member(self, device, src_dp_port):
        """
        Find the LAG (Link Aggregation Group) by the given datapath port member.

        Args:
            device (str): The name of the device.
            src_dp_port (str): The datapath port ID of the member.

        Returns:
            str: The name of the LAG if found, None otherwise.
        """
        for name, metadata in self.get_device_node(device)["lagEcmpGroups"].items():
            for dp in metadata["dpPorts"]:
                if dp["portId"] == src_dp_port:
                    return name

    def update_device_node_deployment(self, name: str, tenantId: int, deploymentName: str, deployment: dict, updateAction: UpdateAction, deploymentStatus: DeploymentStatus = DeploymentStatus.UNSPECIFIED, mdDeploymentStatus: DeploymentStatus = DeploymentStatus.UNSPECIFIED):
        """
        Update the deployment information for a device node.

        Args:
            name (str): The name of the device node.
            tenantId (int): The ID of the tenant.
            deploymentName (str): The name of the deployment.
            deployment (dict): The deployment information.
            updateAction (UpdateAction): The update action.
            deploymentStatus (DeploymentStatus, optional): The deployment status. Defaults to DeploymentStatus.UNSPECIFIED.
            mdDeploymentStatus (DeploymentStatus, optional): The MD deployment status. Defaults to DeploymentStatus.UNSPECIFIED.
        """
        if tenantId not in self.nodes[name]["deployments"]:
            self.nodes[name]["deployments"][tenantId] = {}
        if deploymentName not in self.nodes[name]["deployments"][tenantId]:
            self.nodes[name]["deployments"][tenantId][deploymentName] = {}
        if "deployment" in self.nodes[name]["deployments"][tenantId][deploymentName]:
            self.nodes[name]["deployments"][tenantId][deploymentName]["prevDeployment"] = self.nodes[name]["deployments"][tenantId][deploymentName]["deployment"]
            self.nodes[name]["deployments"][tenantId][deploymentName]["prevUpdateAction"] = self.nodes[name]["deployments"][tenantId][deploymentName]["updateAction"]
        self.nodes[name]["deployments"][tenantId][deploymentName]["deployment"] = deployment
        self.update_device_node_deployment_update_action(name, tenantId, deploymentName, updateAction)
        self.update_device_node_deployment_status(name, tenantId, deploymentName, deploymentStatus)
        self.update_device_node_md_deployment_status(name, tenantId, deploymentName, mdDeploymentStatus)

    def get_device_node_deployments_by_status(self, status : DeploymentStatus, node : str = None):
        """
        Get the device node deployments by status.

        Args:
            status (DeploymentStatus): The deployment status to filter by.
            node (str, optional): The specific node to filter by. Defaults to None.

        Returns:
            dict: A dictionary containing the deployments filtered by status.
        """
        deployments = {}
        if node is None:
            for name in self.nodes:
                deployments[name] = self.get_device_node_deployments_by_status(name, status)
        else:
            for id in self.nodes[node]["deployments"].keys():
                for deploymentName in self.nodes[node]["deployments"][id].keys():
                    if self.get_device_node_deployment_status(node, id, deploymentName) == status:
                        if id not in deployments.keys():
                            deployments[id] = {}
                        if deploymentName not in deployments.keys():
                            deployments[id][deploymentName] = {}
                        deployments[id][deploymentName] = {
                            "deployment" : self.get_device_node_deployment(node, id, deploymentName), 
                            "updateAction": self.get_device_node_deployment_update_action(node, id, deploymentName)
                        }
        return deployments
    
    def get_device_node_deployments_by_md_status(self, mdStatus: DeploymentStatus, node: str = None):
        """
        Get device node deployments by MD status.

        Args:
            mdStatus (DeploymentStatus): The MD status to filter the deployments by.
            node (str, optional): The name of the node to filter the deployments for. If None, all nodes will be considered.

        Returns:
            dict: A dictionary containing the deployments filtered by MD status.

        """
        deployments = {}
        if node is None:
            for name in self.nodes:
                deployments[name] = self.get_device_node_deployments_by_md_status(name, mdStatus)
        else:
            for id in self.nodes[node]["deployments"].keys():
                for deploymentName in self.nodes[node]["deployments"][id].keys():
                    if self.get_device_node_md_deployment_status(node, id, deploymentName) == mdStatus:
                        if id not in deployments.keys():
                            deployments[id] = {}
                        if deploymentName not in deployments.keys():
                            deployments[id][deploymentName] = {}
                        deployments[id][deploymentName] = {
                            "deployment": self.get_device_node_deployment(node, id, deploymentName),
                            "updateAction": self.get_device_node_deployment_update_action(node, id, deploymentName)
                        }
        return deployments

    def is_device_node_enabled(self, name: str):
        """
        Check if a device node is enabled.

        Args:
            name (str): The name of the device node.

        Returns:
            bool: True if the device node is enabled, False otherwise.
        """
        return self.nodes[name]["OMuProCUEnabled"]

    def check_deployment_existence_at_device_node(self, name: str, tenantId: int, deploymentName: str):
        """
        Check if a deployment exists at a device node.

        Args:
            name (str): The name of the device node.
            tenantId (int): The ID of the tenant.
            deploymentName (str): The name of the deployment.

        Returns:
            bool: True if the deployment exists, False otherwise.
        """
        if tenantId in self.nodes[name]["deployments"]:
            return deploymentName in self.nodes[name]["deployments"][tenantId]
        return False

    def get_device_node_deployment_status(self, name: str, tenantId: int, deploymentName: str):
        """
        Get the deployment status of a device node.

        Args:
            name (str): The name of the device node.
            tenantId (int): The ID of the tenant.
            deploymentName (str): The name of the deployment.

        Returns:
            str: The deployment status of the device node.
        """
        return self.nodes[name]["deployments"][tenantId][deploymentName]["status"]
    
    def get_device_node_md_deployment_status(self, name : str, tenantId : int, deploymentName : str):
        """
        Get the MD deployment status of a device node.

        Args:
            name (str): The name of the device node.
            tenantId (int): The ID of the tenant.
            deploymentName (str): The name of the deployment.

        Returns:
            str: The MD deployment status of the device node.
        """
        return self.nodes[name]["deployments"][tenantId][deploymentName]["mdstatus"]

    def get_device_node_deployment_update_action(self, name: str, tenantId: int, deploymentName: str):
        """
        Get the update action for a specific deployment of a device node.

        Args:
            name (str): The name of the device node.
            tenantId (int): The ID of the tenant.
            deploymentName (str): The name of the deployment.

        Returns:
            str: The update action for the specified deployment.
        """
        return self.nodes[name]["deployments"][tenantId][deploymentName]["updateAction"]

    def update_device_node_deployment_status(self, name: str, tenantId: int, deploymentName: str, status: DeploymentStatus = DeploymentStatus.UNSPECIFIED):
        """
        Update the deployment status of a device node.

        Args:
            name (str): The name of the device node.
            tenantId (int): The ID of the tenant.
            deploymentName (str): The name of the deployment.
            status (DeploymentStatus, optional): The deployment status. Defaults to DeploymentStatus.UNSPECIFIED.
        """
        self.nodes[name]["deployments"][tenantId][deploymentName]["status"] = status

    def update_device_node_md_deployment_status(self, name: str, tenantId: int, deploymentName: str, status: DeploymentStatus = DeploymentStatus.UNSPECIFIED):
        """
        Update the MD deployment status of a device node.

        Args:
            name (str): The name of the device node.
            tenantId (int): The ID of the tenant.
            deploymentName (str): The name of the deployment.
            status (DeploymentStatus, optional): The MD deployment status. Defaults to DeploymentStatus.UNSPECIFIED.
        """
        self.nodes[name]["deployments"][tenantId][deploymentName]["mdstatus"] = status

    def update_device_node_deployment_update_action(self, name: str, tenantId: int, deploymentName: str, updateAction: UpdateAction):
        """
        Update the update action for a specific deployment of a device node.

        Args:
            name (str): The name of the device node.
            tenantId (int): The ID of the tenant.
            deploymentName (str): The name of the deployment.
            updateAction (UpdateAction): The update action to be set.

        Returns:
            None
        """
        self.nodes[name]["deployments"][tenantId][deploymentName]["updateAction"] = updateAction

    def remove_device_node_deployment(self, name: str, tenantId, deploymentName: str):
        """
        Remove a deployment from a device node.

        Args:
            name (str): The name of the device node.
            tenantId: The ID of the tenant.
            deploymentName (str): The name of the deployment to remove.
        """
        if deploymentName in self.nodes[name]["deployments"][tenantId]:
            self.nodes[name]["deployments"][tenantId].pop(deploymentName)

    def _create_device_node_status_dict(self, name: str):
        """
        Create a dictionary containing the status of deployments for a given device node.

        Args:
            name (str): The name of the device node.

        Returns:
            dict: A dictionary containing the status of deployments for the device node.
                  The dictionary structure is as follows:
                  {
                      tenantId1: {
                          deployment1: status1,
                          deployment2: status2,
                          ...
                      },
                      tenantId2: {
                          deployment1: status1,
                          deployment2: status2,
                          ...
                      },
                      ...
                  }
        """
        status_dict = {}
        for tenantId in self.nodes[name]["deployments"].keys():
            status_dict[tenantId] = {}
            for deployment in self.nodes[name]["deployments"][tenantId].items():
                status_dict[tenantId][deployment] = self.get_device_node_deployment_status(name, tenantId, deployment)
        return status_dict

    def change_lag_member_state_for_device(self, device_name: str, lag, member_port_id, state: bool, dp_port_number=True):
        """
        Change the state of a member port in a LAG (Link Aggregation Group) for a specific device.

        Args:
            device_name (str): The name of the device.
            lag: The LAG identifier.
            member_port_id: The identifier of the member port.
            state (bool): The desired state of the member port (True for active, False for inactive).
            dp_port_number (bool, optional): Whether the member_port_id is a data plane port number. Defaults to True.

        Raises:
            ValueError: If dp_port_number is False.

        """
        def find_portId(dp_portId: int, dp_port_list: list):
            for i, dp_port in enumerate(dp_port_list):
                if dp_port["portId"] == dp_portId:
                    return i, dp_port

        if not dp_port_number:
            raise ValueError("The port number must be a data plane port number!")
        dp_port_index, _ = find_portId(member_port_id, self.nodes[device_name]["lagEcmpGroups"][lag]["dpPorts"])
        self.nodes[device_name]["lagEcmpGroups"][lag]["dpPorts"][dp_port_index]["active"] = state

    def check_device_lag_group_equality(self, device_name: str, dict: dict):
        """
        Check if the lag group for a device is equal to a given dictionary.

        Args:
            device_name (str): The name of the device.
            dict (dict): The dictionary representing the lag group.

        Returns:
            bool: True if the lag group is equal to the given dictionary, False otherwise.
        """
        dict_new = self.get_lag_ecmp_groups_for_device_by_id(device_name, dict["id"])
        return dict == dict_new

    def is_device_lag_group_in_topology(self, device_name: str, key: Union[str, int]):
        """
        Check if a device's lag group is present in the topology.

        Args:
            device_name (str): The name of the device.
            key (Union[str, int]): The key to search for in the lagEcmpGroups dictionary.

        Returns:
            bool: True if the lag group is present, False otherwise.
        """
        if isinstance(key, int):
            for k, v in self.get_device_node(device_name)["lagEcmpGroups"].items():
                if v["id"] == key:
                    return True
            return False
        else:
            return key in self.get_device_node(device_name)["lagEcmpGroups"].keys()

    def is_in_device_nexthop_map(self, device_name: str, key: str, value: int = None):
        """
        Check if the given key exists in the nexthopMap table for the specified device.

        Args:
            device_name (str): The name of the device.
            key (str): The key to check in the nexthopMap table.
            value (int, optional): The value to check for equality. If provided, the equality
                of the value associated with the key will also be checked.

        Returns:
            bool: True if the key exists in the nexthopMap table. If value is provided, it
                also checks for equality with the associated value.

        """
        entries: dict = self.get_table_entries_for_devices(device_name, "nexthopMap")
        return key in entries.keys() if value is None else key in entries.keys() and value == entries[key]

    def get_device_nexthop_map(self, device_name: str, key: str = None, complete: bool = False):
        """
        Get the nexthop map for a specific device.

        Parameters:
            device_name (str): The name of the device.
            key (str, optional): The specific key to retrieve from the nexthop map. Defaults to None.
            complete (bool, optional): Flag indicating whether to return the complete nexthop map or just a specific key. Defaults to False.

        Returns:
           dict or None: The nexthop map for the device. If `complete` is True, returns the complete nexthop map. If `key` is provided and exists in the nexthop map, returns a dictionary with only that key. If `key` is provided but does not exist in the nexthop map, returns None.
        """
        if complete:
            return self.get_device_node(device_name)["nexthopMap"]
        else:
            return {key: self.get_device_node(device_name)["nexthopMap"][key]} if key in self.get_device_node(device_name)["nexthopMap"] else None

    def get_device_routing_table_configuration_entry(self, device_name: str, table, key: str, value: str):
        """
        Get the configuration entry from the routing table for a specific device.

        Args:
            device_name (str): The name of the device.
            table: The routing table.
            key (str): The key to search for in the entries.
            value (str): The value to match with the key.

        Returns:
            dict: The configuration entry if found, None otherwise.
        """
        entries: list = self.get_table_entries_for_devices(device_name, table)
        for entry in entries:
            if entry[key] == value:
                return entry
        return None

    def is_in_device_routing_table_configuration(self, device_name : str, table : str, key : str, value : str):
        """
        Check if a given key-value pair exists in the routing table configuration of a specific device.

        Args:
            device_name (str): The name of the device.
            table (str): The name of the routing table.
            key (str): The key to search for in the routing table entries.
            value (str): The value to match with the key in the routing table entries.

        Returns:
            bool: True if the key-value pair exists in the routing table configuration, False otherwise.
        """
        entries : list = self.get_table_entries_for_devices(device_name, table)
        for entry in entries:
            if entry[key] == value:
                return True
        return False

    def check_device_routing_table_configuration_equality(self, device_name: str, table: str, key, table_config: RoutingTableConfiguration):
        """
        Check if the routing table configuration for a specific device and table is equal to the provided configuration.

        Args:
            device_name (str): The name of the device.
            table (str): The name of the routing table.
            key: The key to identify the routing table entry.
            table_config (RoutingTableConfiguration): The configuration to compare with.

        Returns:
            bool: True if the routing table configuration is equal, False otherwise.
        """
        if self.is_in_device_routing_table_configuration(device_name, table, key, table_config.key):
            entries: list = self.get_table_entries_for_devices(device_name, table)
            for entry in entries:
                if entry["ip"] == table_config.key and entry["nexthop_id"] == table_config.nextHopId:
                    return True
        return False

    def is_in_device_port_configuration(self, device_name: str, port: Union[DpPort, dict, int]):
        """
        Check if the given device and port combination exists in the device port configuration.

        Args:
            device_name (str): The name of the device.
            port (Union[DpPort, dict, int]): The port number or port object.

        Returns:
            bool: True if the device and port combination exists, False otherwise.
        """
        port_num = None
        if isinstance(port, DpPort):
            port_num = port.slotId
        elif isinstance(port, int):
            port_num = port
        elif isinstance(port, dict):
            port_num = port["src_port"]
        topo_port_mds = [d for u, v, d in self.edges(data=True) if u == device_name and d['src_port'] == port_num]
        return len(topo_port_mds) > 0

    def check_port_configuration_equality(self, device_name, port_md: Union[DpPort, dict]):
        """
        Check if the port configuration is equal to the given port metadata.

        Args:
            device_name (str): The name of the device.
            port_md (Union[DpPort, dict]): The port metadata to compare.

        Returns:
            bool: True if the port configuration is equal, False otherwise.
        """
        if isinstance(port_md, DpPort):
            topo_port_mds = [d for u, v, d in self.edges(data=True) if u == device_name and d['src_port'] == port_md.slotId]
            topo_port_md = None
            if len(topo_port_mds) > 0:
                topo_port_md = topo_port_mds[0]
            else:
                return False
            return topo_port_md["src_port"] == port_md.slotId and \
                topo_port_md["src_dp_port"] == port_md.portId and \
                topo_port_md["speed"] == port_md.speed and \
                topo_port_md["enabled"] == port_md.active and \
                topo_port_md["fec"] == port_md.fec
        else:
            topo_port_mds = [d for u, v, d in self.edges(data=True) if u == device_name and d['src_port'] == port_md["src_port"]]
            topo_port_md = topo_port_mds[0]
            return topo_port_md["src_port"] == port_md["src_port"] and \
                topo_port_md["src_dp_port"] == port_md["src_dp_port"] and \
                topo_port_md["speed"] == port_md["speed"] and \
                topo_port_md["enabled"] == port_md["enabled"] and \
                topo_port_md["fec"] == port_md["fec"]
    
    def get_port_configuration_for_device_name(self, device_name, port_num):
        """
        Get the port configuration for a specific device name and port number.

        Args:
            device_name (str): The name of the device.
            port_num (int): The port number.

        Returns:
            dict or None: The port configuration dictionary if found, None otherwise.
        """
        ports = [d for u,v,d in self.edges(data=True) if u == device_name and d['src_port'] == port_num]
        return ports[0] if len(ports) > 0 else None
    
    def get_port_configurations_for_device_name(self, device_name):
        """
        Returns a list of port configurations for a given device name.

        Parameters:
            device_name (str): The name of the device.

        Returns:
            list: A list of port configurations for the specified device name.
        """
        return [d for u,v,d in self.edges(data=True) if u == device_name]

    def get_device_node_deployment_status_by_tenantId(self, name: str, tenantId):
        """
        Get the deployment status of a device node for a specific tenant.

        Args:
            name (str): The name of the device node.
            tenantId: The ID of the tenant.

        Returns:
            The deployment status of the device node for the specified tenant.
        """
        status_dict = self._create_device_node_status_dict(name)
        return status_dict[tenantId]
    
    def get_device_node_deployments_status(self, name : str):
        """
        Get the deployment status of a device node.

        Args:
            name (str): The name of the device node.

        Returns:
            dict: A dictionary containing the deployment status of the device node.
        """
        status_dict = self._create_device_node_status_dict(name)
        return status_dict

    def get_device_node_deployment(self, name: str, tenantId, deploymentName: str):
        """
        Get the deployment of a device node.

        Args:
            name (str): The name of the device node.
            tenantId: The ID of the tenant.
            deploymentName (str): The name of the deployment.

        Returns:
            The deployment of the device node.
        """
        return self.nodes[name]["deployments"][tenantId][deploymentName]["deployment"]

    def get_device_node_deployment_status_by_deploymentName(self, name : str, tenantId, deploymentName : str):
        """
        Get the deployment status of a device node by deployment name.

        Args:
            name (str): The name of the device node.
            tenantId: The ID of the tenant.
            deploymentName (str): The name of the deployment.

        Returns:
            str: The deployment status of the device node.
        """
        return self.nodes[name]["deployments"][tenantId][deploymentName]["status"]

    def set_device_node_deployment_status(self, name: str, tenantId, deploymentName: str, deploymentStatus: DeploymentStatus):
        """
        Sets the deployment status of a device node.

        Args:
            name (str): The name of the device node.
            tenantId: The ID of the tenant.
            deploymentName (str): The name of the deployment.
            deploymentStatus (DeploymentStatus): The status of the deployment.

        Returns:
            None
        """
        self.nodes[name]["deployments"][tenantId][deploymentName]["status"] = deploymentStatus

    def set_device_node_status(self, name: str, status: OrchestratorHealthStates):
        """
        Set the status of a device node.

        Args:
            name (str): The name of the device node.
            status (OrchestratorHealthStates): The status to set.

        Returns:
            None
        """
        self.nodes[name]["status"] = status

    def get_device_node_status(self, name: str) -> OrchestratorHealthStates:
        """
        Get the status of a device node.

        Args:
            name (str): The name of the device node.

        Returns:
            OrchestratorHealthStates: The status of the device node.
        """
        return self.nodes[name]["status"]
    
    def get_devices_by_category(self, category: str):
        """
        Returns a list of devices based on the given category.

        Args:
            category (str): The category of devices to retrieve.

        Returns:
            list: A list of devices that belong to the specified category.

        Raises:
            ValueError: If the given category does not exist.
        """
        if category == InfrastructureDeviceCategories.EDGE_DEVICE.value:
            return self.get_edge_devices()
        elif category == InfrastructureDeviceCategories.DATACENTER_DEVICE.value:
            return self.get_datacenter_devices()
        elif category == InfrastructureDeviceCategories.ENDPOINT_DEVICE.value:
            return self.get_endpoint_devices()
        else:
            raise ValueError("Given Category does not exist!")

    def get_device_names_by_category(self, category: str):
        """
        Returns a list of device names based on the given category.

        Args:
            category (str): The category of devices to retrieve names for.

        Returns:
            list: A list of device names.

        Raises:
            ValueError: If the given category does not exist.
        """
        if category == InfrastructureDeviceCategories.EDGE_DEVICE.value:
            return self.get_edge_device_names()
        elif category == InfrastructureDeviceCategories.DATACENTER_DEVICE.value:
            return self.get_datacenter_device_names()
        elif category == InfrastructureDeviceCategories.ENDPOINT_DEVICE.value:
            return self.get_endpoint_devices()
        else:
            raise ValueError("Given Category does not exist!")
    
    def get_all_devices(self):
        """
        Returns a dictionary containing all devices categorized as edge devices or datacenter devices.

        Returns:
            dict: A dictionary containing the devices categorized as edge devices or datacenter devices.
        """
        return {node: self.nodes[node] for node in self if self.nodes[node]["category"] == InfrastructureDeviceCategories.EDGE_DEVICE.value or self.nodes[node]["category"] == InfrastructureDeviceCategories.DATACENTER_DEVICE.value}
    
    def get_edge_devices(self):
        """
        Returns a dictionary containing the edge devices in the topology.

        Returns:
            dict: A dictionary where the keys are the node names and the values are the corresponding node information.
        """
        return {node: self.nodes[node] for node in self if self.nodes[node]["category"] == InfrastructureDeviceCategories.EDGE_DEVICE.value}

    def get_datacenter_devices(self):
        """
        Returns a dictionary containing the datacenter devices in the topology.

        Returns:
            dict: A dictionary where the keys are the node names and the values are the node information.
        """
        return {node: self.nodes[node] for node in self if self.nodes[node]["category"] == InfrastructureDeviceCategories.DATACENTER_DEVICE.value}
    
    def get_endpoint_devices(self):
        """
        Returns a dictionary containing the endpoint devices in the topology.

        Returns:
            dict: A dictionary where the keys are the node names and the values are the corresponding node information.
        """
        return {node: self.nodes[node] for node in self if self.nodes[node]["category"] == InfrastructureDeviceCategories.ENDPOINT_DEVICE.value}

    def get_all_device_names(self):
        """
        Returns a list of all device names in the topology.

        Returns: 
            list: A list of device names.
        """
        return [node[0] for node in self.nodes(data='category') if node[1] == InfrastructureDeviceCategories.EDGE_DEVICE.value or node[1] == InfrastructureDeviceCategories.DATACENTER_DEVICE.value]

    def get_edge_device_names(self):
        """
        Returns a list of names of edge devices in the topology.

        Returns:
            list: A list of names of edge devices.
        """
        return [node[0] for node in self.nodes(data='category') if node[1] == InfrastructureDeviceCategories.EDGE_DEVICE.value]
    
    def get_datacenter_device_names(self):
        """
        Returns a list of names of datacenter devices in the topology.

        Returns:
            list: A list of strings representing the names of datacenter devices.
        """
        return [node[0] for node in self.nodes(data='category') if node[1] == InfrastructureDeviceCategories.DATACENTER_DEVICE.value]
    
    def get_endpoint_names(self):
        """
        Returns a list of names of all endpoint devices in the topology.

        Returns: 
            list: A list of endpoint device names.
        """
        return [node[0] for node in self.nodes(data='category') if node[1] == InfrastructureDeviceCategories.ENDPOINT_DEVICE.value]
    
    def remove_mainIngressName_from_tenant_security_config(self, mainIngressName: str):
        """
        Remove the specified mainIngressName from the tenant_security_config.

        Args:
            mainIngressName (str): The mainIngressName to be removed.

        Returns:
            None
        """
        with open("conf/tenant_security_config.json") as f:
            tenant_security_config = json.load(f)
        for id, tenant in tenant_security_config.items():
            for device in tenant["devices"].values():
                if mainIngressName in device["mainIngressNames"]:
                    device["mainIngressNames"].remove(mainIngressName)
        with open("conf/tenant_security_config.json", "w") as f:
            json.dump(tenant_security_config, f, indent=2)

    def get_link_status(self, device_link_1, device_link_2=None):
        """
        Get the status of a link between two devices.

        Parameters:
            device_link_1 (str): The name of the first device.
            device_link_2 (str, optional): The name of the second device. If not provided, the link status of the first device will be returned.

        Returns:
            bool: The status of the link. True if enabled, False if disabled.
        """
        return self._get_connections_attribute(device_link_1, "enabled", device_link_2)
        
    def plot_view(self):
        """
        Plot the view of the network topology.

        This method prints the current object, the data of the nodes, and draws the networkx graph.
        It then saves the plot as a PDF file named "test.pdf".
        """
        print(self)
        print(self.nodes.data())
        networkx.draw_networkx(self)
        plt.draw()
        plt.savefig("test.pdf")


if __name__ == "__main__":
    gv = GlobalView()
    gv.load_topology_file("conf/topology.json")
    gv.plot_view()
    print(gv.get_edge_devices())
    print(gv.get_datacenter_devices())
    print(gv.get_endpoint_devices())
    print(list(networkx.all_simple_paths(gv,"endpoint1", "endpoint2")))