#!/usr/bin/python3



from concurrent import futures
import json
import logging
from multiprocessing import Process
import threading
import time

import grpc
from google.protobuf.json_format import ParseDict, MessageToDict

from orchestrator_utils.til.md_orchestrator_msg_pb2_grpc import MDDeploymentCommunicatorServicer, MDRulesUpdaterCommunicatorServicer, TopologyUpdateCommunicatorServicer, add_MDDeploymentCommunicatorServicer_to_server, add_TopologyUpdateCommunicatorServicer_to_server, add_MDRulesUpdaterCommunicatorServicer_to_server
from orchestrator_utils.til.md_orchestrator_msg_pb2 import MDDeploymentRequest, MDDeploymentResponse, Node, TopologyUpdate, TopologyUpdateResponse,  TopologyUpdateRequest, DeploymentNodeUpdate
from orchestrator_utils.til.orchestrator_msg_pb2 import TIFControlRequest, TIFControlResponse, UpdateAction, UPDATE_ACTION_CREATE, UPDATE_ACTION_DELETE, UPDATE_ACTION_UPDATE
from orchestrator_utils.til.orchestrator_msg_pb2_grpc import TIFControlCommunicatorStub, RulesUpdaterCommunicatorStub
from orchestrator_utils.til.tif_control_pb2 import DpPort, Lag, RoutingTableConfiguration
from orchestrator_utils.til.til_msg_pb2_grpc import DeploymentCommunicatorStub
from orchestrator_utils.til.til_msg_pb2 import DeploymentResponse, DeploymentRequest, DeploymentMessage, TenantMetadata
from orchestrator_utils.device_categories import InfrastructureDeviceCategories
from orchestrator_utils.logger.logger import init_logger
from orchestrator_utils.validator import MDTDCValidator
from orchestrator_utils.states import DeploymentStatus

from conf.config import DEPLOYMENT_ADDRESS, INFRASTRUCTURE_FILE_PATH
from health_monitor.states import OrchestratorHealthStates
from topology import GlobalView
from health_monitor.health_monitor import OMuProCUHealthMonitor


class ManagementProcess(Process, MDDeploymentCommunicatorServicer, TopologyUpdateCommunicatorServicer, MDRulesUpdaterCommunicatorServicer):
    """
    The ManagementProcess class represents the main orchestrator process responsible for managing the deployment and
    configuration of services in the network infrastructure.

    Attributes:
        logger: The logger instance for logging messages.
        validator: The validator instance for validating TDC manifests.
        running: A boolean indicating whether the orchestrator is running or not.
        rollout_running: A boolean indicating whether the rollout of deployments is running or not.
        switch_port_configuration_running: A boolean indicating whether the switch port configuration is running or not.
        switch_port_configuration_thread: The thread for running the switch port configuration loop.
        table_entries_configuration_running: A boolean indicating whether the table entries configuration is running or not.
        table_entries_configuration_thread: The thread for running the table entries configuration loop.
        lag_ecmp_configuration_running: A boolean indicating whether the LAG/ECMP configuration is running or not.
        lag_ecmp_configuration_thread: The thread for running the LAG/ECMP configuration loop.
        rollout_lock: The lock for synchronizing the rollout of deployments.
        rollout_thread: The thread for running the rollout of deployments loop.
        global_view: The instance of the GlobalView class representing the network topology.
        grpc_server: The gRPC server instance for communication with other components.
        tenant_security_config: The configuration for tenant security.
    """

    def __init__(self, log_level=logging.INFO, daemon=False) -> None:
        """
        Initializes a new instance of the ManagementProcess class.

        Args:
            log_level: The log level for the logger (default is logging.INFO).
            daemon: A boolean indicating whether the process should run as a daemon (default is False).
        """
        self.logger = init_logger(self.__class__.__name__, log_level)
        Process.__init__(self, daemon=daemon)
        self.validator = MDTDCValidator()
        self.running = False
        self.rollout_running = False
        self.switch_port_configuration_running = False
        self.switch_port_configuration_thread = threading.Thread(target=self.switch_port_configuration_loop)
        self.table_entries_configuration_running = False
        self.table_entries_configuration_thread = threading.Thread(target=self.table_entries_configuration_loop)
        self.lag_ecmp_configuration_running = False
        self.lag_ecmp_configuration_thread = threading.Thread(target=self.lag_ecmp_configuration_loop)
        self.rollout_lock = threading.Lock()
        self.rollout_thread = threading.Thread(target=self._rollout_deployments_loop)
        self.logger.info("Loading Global View from infrastructure file...")
        self.global_view = GlobalView()
        self.global_view.load_topology_file(INFRASTRUCTURE_FILE_PATH)
        self.grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
        self.grpc_server.add_insecure_port(DEPLOYMENT_ADDRESS)
        self.logger.info("Loaded Global View.")
        self.tenant_security_config = self.load_tenant_security_config()
        self.deployment_timestamps = {}
        self.write_timemeasurements_to_file(False)

    def start(self) -> None:
        super().start()
        add_MDDeploymentCommunicatorServicer_to_server(self, self.grpc_server)
        add_TopologyUpdateCommunicatorServicer_to_server(self, self.grpc_server)
        add_MDRulesUpdaterCommunicatorServicer_to_server(self, self.grpc_server)
        self.grpc_server.start()
        self.logger.info("Management Process GRPC Server started.")
        self.rollout_running = True
        self.rollout_thread.start()
        self.switch_port_configuration_running = True
        self.switch_port_configuration_thread.start()
        self.table_entries_configuration_running = True
        self.table_entries_configuration_thread.start()
        self.lag_ecmp_configuration_running = True
        self.lag_ecmp_configuration_thread.start()

    def __exit__(self, exc_typ, exc_value, traceback):
        self.stop()

    def terminate(self) -> None:
        self.rollout_running = False
        self.lag_ecmp_configuration_running = False
        self.switch_port_configuration_running = False
        self.table_entries_configuration_running = False
        self.lag_ecmp_configuration_thread.join()
        self.switch_port_configuration_thread.join()
        self.table_entries_configuration_thread.join()
        self.rollout_thread.join()
        self.stop()
        return super().terminate()

    def stop(self) -> None:
        self.logger.info("Stopping Orchestrator.")
        self.running = False
        self.grpc_server.stop(10)
        self.logger.info("Orchestrator stopped.")

    def write_timemeasurements_to_file(self, append=True):
        """
        docstring
        """
        if not append:
            self.timemeasurement_file = "timemeasurements-{}.csv".format(time.strftime("%Y%m%d_%H%M%S"))
        with open(self.timemeasurement_file, "a" if append else "w") as f:
            if not append:
                f.write("CNF_ID,Operation,deploymentStart,scheduleTimestamp,submissionTimestamp,validationEndTimestamp,deploymentEnd,nodesTimestamps\n")
            for cnf_id, timestamps in self.deployment_timestamps.items():
                f.write("{},{},{},{},{},{},{},{}\n".format(
                    cnf_id, 
                    timestamps["deploymentType"],
                    timestamps["deploymentStart"], 
                    timestamps["scheduleTimestamp"], 
                    timestamps["submissionTimestamp"], 
                    timestamps["validationEndTimestamp"], 
                    timestamps["deploymentEnd"], 
                    str(timestamps["nodes"]).encode("utf-8")
                ))

    @staticmethod
    def _build_tenant_cnf_id(tenantId, tenantFuncName):
        return "t" + str(tenantId) + "-" + tenantFuncName

    @staticmethod
    def _split_tenant_cnf_id(tenant_cnf_id : str):
        return tenant_cnf_id.split("-")[0][1:], "-".join(tenant_cnf_id.split("-")[1:])

    def load_tenant_security_config(self):
        """
        Load the tenant security configuration from the 'tenant_security_config.json' file.

        Returns:
            dict: The loaded tenant security configuration.
        """
        with open("conf/tenant_security_config.json") as f:
            self.tenant_security_config = json.load(f)
        return self.tenant_security_config

    def save_tenant_security_config(self):
        """
        Saves the tenant security configuration to a JSON file.

        This method writes the current tenant security configuration to a JSON file
        named 'tenant_security_config.json' in the 'conf' directory. The configuration
        is serialized using the JSON format and indented with 2 spaces.

        Parameters:
            None

        Returns:
            None
        """
        with open("conf/tenant_security_config.json", "w") as f:
            f.write(json.dumps(self.tenant_security_config, indent=2))

    def _find_tdc_manifest_for_device_name(self, device_name, mdtdc):
        """
        Find the TDC manifest for a given device name.

        Args:
            device_name (str): The name of the device.
            mdtdc (dict): The MDTDC data.

        Returns:
            dict or None: The TDC manifest if found, None otherwise.
        """
        for tdc in mdtdc["TDCs"]:
            if tdc["deviceName"] == device_name:
                tdc["manifest"]["id"] = self.validator.tdc_id
                return tdc["manifest"]
        return None
    
    def _deploy_TDC_to_nodes(self, node, deployments):
        """
        Deploy the TDC (Tenant Deployment Configuration) to the specified node.

        Args:
            node (str): The node where the TDC should be deployed.
            deployments (dict): A dictionary containing the deployment configurations for each tenant.

        Returns:
            None

        Raises:
            None
        """
        for tenantId in deployments.keys():
            for tenantFuncName in deployments[tenantId].keys():
                if self.global_view.get_device_node_deployment_status(node, tenantId, tenantFuncName) == DeploymentStatus.RUNNING \
                    or self.global_view.get_device_node_deployment_status(node, tenantId, tenantFuncName) == DeploymentStatus.DELETED \
                    or self.global_view.get_device_node_deployment_status(node, tenantId, tenantFuncName) == DeploymentStatus.UNSPECIFIED:
                    updateAction = deployments[tenantId][tenantFuncName]["updateAction"]
                    if updateAction == UPDATE_ACTION_CREATE:
                        resp = self._create(node, deployments[tenantId][tenantFuncName]["deployment"])
                    elif updateAction == UPDATE_ACTION_UPDATE:
                        resp = self._update(node, deployments[tenantId][tenantFuncName]["deployment"])
                    elif updateAction == UPDATE_ACTION_DELETE:
                        resp = self._delete(node, deployments[tenantId][tenantFuncName]["deployment"])
                    else:
                        self.logger.error("Unknown Update Action: {}".format(updateAction))
                        return
                    if resp.status == 200:
                        self.logger.info("Deployment @ {} for tenant {}-{} submitted successful.".format(node, tenantId, tenantFuncName))
                    else:
                        self.global_view.update_device_node_md_deployment_status(node, tenantId, tenantFuncName, DeploymentStatus.FAILED)
                        self.logger.error("Deployment failed @ {}: {}".format(node, resp.message))
                else:
                    self.logger.warning("Deployment process @ {} is in progress. Waiting for the next cycle.".format(node))
                    return
        self.logger.info("Waiting for deployment to finish @ {}.".format(node))
        device_rollout_running = True
        while device_rollout_running:
            status_of_deployments = {}
            for tenantId in deployments.keys():
                for tenantFuncName in deployments[tenantId].keys():
                    status_of_deployments[self._build_tenant_cnf_id(tenantId, tenantFuncName)] = self.global_view.get_device_node_md_deployment_status(node, tenantId, tenantFuncName)
            status_list = [status == DeploymentStatus.SCHEDULED or status == DeploymentStatus.UPDATING for status in status_of_deployments.values()]
            device_rollout_running = any(status_list)
            self.logger.debug("Device Rollout Running : {}".format(device_rollout_running))
            time.sleep(10)
        self.logger.debug("Left Device Rollout Running Check Loop")

    def _cleanup_tenant_security_config(self, node : str, deployments: dict, set_to_deleted : bool = False):
        """
        Cleans up the tenant security configuration by removing the specified deployments from the configuration.

        Args:
            node (str): The node identifier.
            deployments (dict): A dictionary containing the deployments to be removed.
            set_to_deleted (bool, optional): Flag indicating whether to set the deployment status to DELETED. Defaults to False.
        """
        self.load_tenant_security_config()
        for tenantId in deployments.keys():
            for tenantFuncName in deployments[tenantId].keys():
                if deployments[tenantId][tenantFuncName]["updateAction"] == UPDATE_ACTION_DELETE:
                    if set_to_deleted: 
                        self.global_view.update_device_node_md_deployment_status(node, tenantId, tenantFuncName, DeploymentStatus.DELETED)
                    self.tenant_security_config[str(tenantId)]["devices"][node]["mainIngressNames"].remove(deployments[tenantId][tenantFuncName]["deployment"]["INC"]["mainIngressName"]) 
        self.save_tenant_security_config()
    
    def _restart_deployment_rollout_loop(self):
        """
        Restarts the deployment rollout loop.

        This method stops the current rollout deployment thread, waits for it to terminate,
        and then starts a new thread for the rollout deployment.
        """
        self.rollout_running = False
        self.logger.info("Waiting for terminating of the Rollout Deployment Thread.")
        self.rollout_thread.join()
        self.rollout_running = True
        self.rollout_thread.start()

    def _rollout_deployments_loop(self):
        """
        Executes the rollout deployment loop.

        This method continuously checks for scheduled deployments on each node in the update order.
        If there are deployments scheduled for a node, it performs the necessary actions to deploy
        the TDC (Tenant Definition Code) to the node, cleanup tenant security configurations,
        and update the device state.

        Note: This method assumes that the `global_view` object is available and properly initialized.

        Returns:
            None
        """
        self.logger.info("Rollout Deployment Loop started.")
        while self.rollout_running:
            with self.rollout_lock:
                deployment_start_timestamp = time.time()
                nodes_timestamps = {}
                cnf_deployment_names = set()
                update_order = self.global_view.get_update_order()
                for node in update_order:
                    self.logger.debug("{}:{}".format(node, self.global_view.get_device_node_status(node)))
                    self.logger.debug("Collecting deployments for node {}.".format(node))
                    deployments = self.global_view.get_device_node_deployments_by_md_status(DeploymentStatus.SCHEDULED, node)
                    for id in deployments.keys():
                        cnf_deployment_names.update(["{}-{}".format(id, name) for name in deployments[id].keys()])
                    self.logger.debug("Deployments for node {}: {}".format(node, deployments))
                    if len(deployments.keys()) > 0:
                        self.logger.info("Deployments rollout for node {} started.".format(node))
                        if self.global_view.is_device_node_enabled(node) and self.global_view.get_device_node_status(node) == OrchestratorHealthStates.RUNNING:
                            try:
                                deployment_node_start_timestamp = time.time()
                                # Degrade device if possible (e.g., set links down)
                                self.set_state_for_device_on_edge_devices(node, False)
                                # Deploy TDCs to device
                                self._deploy_TDC_to_nodes(node, deployments)
                                self._cleanup_tenant_security_config(node, deployments)
                                # Upgrade device again if possible (e.g., set links up)
                                self.set_state_for_device_on_edge_devices(node, True)
                                deployment_node_end_timestamp = time.time()
                                nodes_timestamps.update({node: (deployment_node_start_timestamp,deployment_node_end_timestamp)})
                            except Exception as ex:
                                self.logger.error("Error while deploying TDC @ {}: {}".format(node, ex))
                                self.logger.exception(ex)
                                continue
                            time.sleep(5)
                        elif self.global_view.is_device_node_enabled(node):
                            self.logger.warning("Node {} is not reachable! Submitting Deployment failed!".format(node))
                            # Since the OMuProCU at this device should be reachable, all tasks will be kept.
                            # FIXME: Maybe deletion tasks can be deleted, since these could be not available at the switch anymore. 
                            continue
                        else:
                            self.logger.debug("Node {} is disabled. Skipping scheduled Update/Creation deployment tasks.")
                            # Since deployments are deleted from the switch by terminating the corresponding OMuProCU and OMuProCU is disabled at the device, there is no needed to track them further
                            self.logger.debug("Cleanup deployments that should be deleted at the node")
                            self._cleanup_tenant_security_config(node, deployments, True)
                            continue
                deployment_end_timestamp = time.time()
                for cnf in list(cnf_deployment_names):
                    self.deployment_timestamps[cnf]["deploymentStart"] = deployment_start_timestamp
                    self.deployment_timestamps[cnf]["deploymentEnd"] = deployment_end_timestamp
                    self.deployment_timestamps[cnf]["nodes"] = nodes_timestamps
                self.write_timemeasurements_to_file()
                self.deployment_timestamps.clear()
            time.sleep(15)

    def switch_port_configuration_loop(self):
        """
        Continuously checks and updates the port configuration for devices.

        This method runs in a loop and periodically checks the port configuration for each enabled device.
        It retrieves the current port configuration from the device and compares it with the stored configuration.
        If there are any differences, it adds, updates, or deletes the port configuration accordingly.

        Note: This method requires the `global_view` and `logger` attributes to be properly initialized.

        Returns:
            None
        """
        def port_in_resp(port_list, port):
            """
            Helper function to check if a port is present in the response.

            Args:
                port_list (list): List of ports in the response.
                port (Port): Port object to check.

            Returns:
                bool: True if the port is present in the response, False otherwise.
            """
            for port_sw in port_list:
                if port["src_port"] == port_sw.slotId:
                    return True
            return False

        self.logger.info("Switch Port Configuration Loop started.")
        while self.switch_port_configuration_running:
            for device_name, device_metadata in self.global_view.get_all_devices().items():
                if self.global_view.is_device_node_enabled(device_name) and self.global_view.get_device_node_status(device_name) == OrchestratorHealthStates.RUNNING:
                    with self.rollout_lock:
                        try:
                            with grpc.insecure_channel(device_metadata["OMuProCU-TIFAddress"]) as channel:
                                stub = TIFControlCommunicatorStub(channel)
                                resp: TIFControlResponse = stub.GetPortConfiguration(TIFControlRequest(dpPorts=[]))
                                if resp.status == 200:
                                    dp_ports_to_add = []
                                    dp_ports_to_update = []
                                    dp_ports_to_delete = []
                                    for port in resp.dpPorts:
                                        if self.global_view.is_in_device_port_configuration(device_name, port):
                                            if self.global_view.check_port_configuration_equality(device_name, port):
                                                continue
                                            else:
                                                port_md = self.global_view.get_port_configuration_for_device_name(device_name, port.slotId)
                                                if port_md is not None:
                                                    dp_ports_to_update.append(ParseDict({
                                                        "portId" : port_md["src_dp_port"], 
                                                        "slotId" : port_md["src_port"],
                                                        "speed" : port_md["speed"],
                                                        "active" : port_md["enabled"],
                                                        "fec" : port_md["fec"],
                                                        "an": port_md["an"],
                                                    }, DpPort()))
                                                else:
                                                    pass
                                        else:
                                            dp_ports_to_delete.append(port)
                                    for port in self.global_view.get_port_configurations_for_device_name(device_name):
                                        if port_in_resp(resp.dpPorts, port):
                                            continue
                                        else:
                                            dp_ports_to_add.append(ParseDict({
                                                "portId" : port["src_dp_port"], 
                                                "slotId" : port["src_port"],
                                                "speed" : port["speed"],
                                                "active" : port["enabled"],
                                                "fec" : port["fec"],
                                                "an": port["an"],
                                                }, DpPort()))
                                    if len(dp_ports_to_add) > 0:
                                        resp: TIFControlResponse = stub.UpdatePortConfiguration(
                                            TIFControlRequest(
                                                dpPorts=dp_ports_to_add
                                            )
                                        )
                                        if resp.status == 200:
                                            self.logger.debug("Port configuration for device {} added: {}".format(device_name, str(dp_ports_to_add)))
                                            continue
                                        else:
                                            self.logger.error("Error while adding port configuration for device {}: {}".format(device_name, resp.message))
                                    else:
                                        self.logger.debug("No port configuration to add at device {}. Continue.".format(device_name))
                                    if len(dp_ports_to_update) > 0:
                                        resp: TIFControlResponse = stub.UpdatePortConfiguration(
                                            TIFControlRequest(
                                                dpPorts=dp_ports_to_update
                                            )
                                        )
                                        if resp.status == 200:
                                            self.logger.debug("Port configuration for device {} updated: {}".format(device_name, str(dp_ports_to_update)))
                                            continue
                                        else:
                                            self.logger.error("Error while updating port configuration for device {}: {}".format(device_name, resp.message))
                                    else:
                                        self.logger.debug("No port configuration to update at device {}. Continue.".format(device_name))
                                    if len(dp_ports_to_delete) > 0:
                                        resp: TIFControlResponse = stub.DeletePortConfiguration(
                                            TIFControlRequest(
                                                dpPorts=dp_ports_to_delete
                                            )
                                        )
                                        if resp.status == 200:
                                            self.logger.debug("Port configuration for device {} deleted: {}".format(device_name, str(dp_ports_to_delete)))
                                            continue
                                        else:
                                            self.logger.error("Error while deletingt port configuration for device {}: {}".format(device_name, resp.message))
                                    else:
                                        self.logger.debug("No port configuration to delete at device {}. Continue.".format(device_name))
                                else:
                                    self.logger.warning("Error while getting port configuration for device {}: {}".format(device_name, resp.message))
                        except grpc.RpcError as err:
                            if err.code() == grpc.StatusCode.UNAVAILABLE:
                                self.logger.error("Could not connect to " + device_name + ": not reachable")
                            else:
                                self.logger.exception(err, exc_info=True)
                elif self.global_view.is_device_node_enabled(device_name):
                    self.logger.warning("Node {} is not reachable! Update Port Configuration Check failed!".format(device_name))
                else:
                    self.logger.debug("Node {} is disabled. Skipping port configuration update check for node.")
            time.sleep(15)
        self.logger.info("Port Configuration Loop terminated.")

    def table_entries_configuration_loop(self):
        """
        This method is responsible for configuring the table entries for each device in the network.
        It retrieves the table entries from each device, compares them with the current configuration,
        and adds, updates, or deletes entries as necessary.
        """
        def entry_in_resp(entry_list, entry):
            """
            Check if an entry exists in the response list.

            Args:
                entry_list (list): The list of entries to search in.
                entry (dict): The entry to check for existence.

            Returns:
                bool: True if the entry exists in the list, False otherwise.
            """
            for entry_sw in entry_list:
                if entry["ip"] == entry_sw.key:
                    return True
            return False

        def nexthop_map_in_resp(map, key):
            """
            Check if a given key exists in the map.

            Args:
                map (list): The map to search in.
                key: The key to search for.

            Returns:
                bool: True if the key exists in the map, False otherwise.
            """
            for entry in map: 
                if entry.key == key:
                    return True
            return False
        
        self.logger.info("Switch Table Entry Configuration Loop started.")
        while self.table_entries_configuration_running:
            for device_name, device_metadata in self.global_view.get_all_devices().items():
                if self.global_view.is_device_node_enabled(device_name) and self.global_view.get_device_node_status(device_name) == OrchestratorHealthStates.RUNNING:
                    with self.rollout_lock:
                        try:
                            with grpc.insecure_channel(device_metadata["OMuProCU-TIFAddress"]) as channel:
                                stub = TIFControlCommunicatorStub(channel)
                                resp: TIFControlResponse = stub.GetTableEntries(TIFControlRequest())
                                if resp.status == 200:
                                    arp_table_to_add =[]
                                    arp_table_entries_to_update = []
                                    arp_table_entries_to_delete = []
                                    ipv4_hosts_to_add = []
                                    ipv4_hosts_to_update = []
                                    ipv4_hosts_to_delete = []
                                    nexthop_map_to_add = []
                                    nexthop_map_to_update = []
                                    nexthop_map_to_delete = []
                                    # port_configurations = self.global_view.get_port_configurations_for_device_name(device_name)
                                    for entry in resp.arpHostEntries:
                                        # self.logger.debug(self.global_view.edges(data=True))
                                        if self.global_view.is_in_device_routing_table_configuration(device_name, "arpTableHostEntries", "ip", entry.key):
                                            if self.global_view.check_device_routing_table_configuration_equality(device_name, "arpTableHostEntries", "ip", entry):
                                                # port_configurations.pop()
                                                continue
                                            else:
                                                entry = self.global_view.get_device_routing_table_configuration_entry(device_name, "arpTableHostEntries", "ip", entry.key)
                                                if entry is not None:
                                                    arp_table_entries_to_update.append(ParseDict({
                                                        "key" : entry["ip"],
                                                        "nextHopId": entry["nexthop_id"],
                                                    }, RoutingTableConfiguration()))
                                                else:
                                                    pass
                                        else:
                                            arp_table_entries_to_delete.append(entry)
                                    for entry in self.global_view.get_table_entries_for_devices(device_name, "arpTableHostEntries"):
                                        if entry_in_resp(resp.arpHostEntries, entry):
                                            continue
                                        else:
                                            arp_table_to_add.append(ParseDict({
                                                "key" : entry["ip"],
                                                "nextHopId": entry["nexthop_id"],
                                            }, RoutingTableConfiguration()))
                                    for entry in resp.ipv4HostEntries:
                                        if self.global_view.is_in_device_routing_table_configuration(device_name, "ipv4HostEntries", "ip", entry.key):
                                            if self.global_view.check_device_routing_table_configuration_equality(device_name, "ipv4HostEntries", "ip", entry):
                                                continue
                                            else:
                                                entry = self.global_view.get_device_routing_table_configuration_entry(device_name, "ipv4HostEntries", "ip", entry.key)
                                                if entry is not None:
                                                    ipv4_hosts_to_update.append(ParseDict({
                                                        "key" : entry["ip"],
                                                        "nextHopId": entry["nexthop_id"],
                                                    }, RoutingTableConfiguration()))
                                                else:
                                                    pass
                                        else:
                                            ipv4_hosts_to_delete.append(entry)
                                    for entry in self.global_view.get_table_entries_for_devices(device_name, "ipv4HostEntries"):
                                        if entry_in_resp(resp.ipv4HostEntries, entry):
                                            continue
                                        else:
                                            ipv4_hosts_to_add.append(ParseDict({
                                                "key" : entry["ip"],
                                                "nextHopId": entry["nexthop_id"],
                                            }, RoutingTableConfiguration()))
                                    for entry in resp.nexthopMapEntries:
                                        if self.global_view.is_in_device_nexthop_map(device_name, entry.key):
                                            if self.global_view.is_in_device_nexthop_map(device_name, entry.key, entry.nextHopId):
                                                continue
                                            else:
                                                entry = self.global_view.get_device_nexthop_map(device_name, entry.key)
                                                if entry is not None:
                                                    nexthop_map_to_update.append(ParseDict({
                                                        "key" : entry.keys()[0],
                                                        "nextHopId": entry.values()[0],
                                                    }, RoutingTableConfiguration()))
                                                else:
                                                    pass
                                        else:
                                            nexthop_map_to_delete.append(entry)
                                    for key, value in self.global_view.get_device_nexthop_map(device_name, complete=True).items():
                                        if nexthop_map_in_resp(resp.nexthopMapEntries, key):
                                            continue
                                        else:
                                            nexthop_map_to_add.append(ParseDict({
                                                "key" : key,
                                                "nextHopId": value,
                                            }, RoutingTableConfiguration()))

                                    if len(arp_table_to_add) > 0 or len(ipv4_hosts_to_add) > 0 or len(nexthop_map_to_add) > 0:
                                        resp: TIFControlResponse = stub.AddTableEntries(
                                            TIFControlRequest(
                                                arpHostEntries=arp_table_to_add,
                                                ipv4HostEntries=ipv4_hosts_to_add,
                                                nexthopMapEntries=nexthop_map_to_add
                                            )
                                        )
                                        if resp.status == 200:
                                            self.logger.debug("Routing table configuration for device {} added: \n ARP: {} \n IPv4: {} \n NexthopMap: {}".format(device_name, str(arp_table_to_add), str(ipv4_hosts_to_add), str(nexthop_map_to_add)))
                                            continue
                                        else:
                                            self.logger.error("Error while adding routing table configuration for device {}: {}".format(device_name, resp.message))
                                    else:
                                        self.logger.debug("No routing table configuration to add at device {}. Continue.".format(device_name))
                                    if len(arp_table_entries_to_update) > 0 or len(ipv4_hosts_to_update) > 0 or len(nexthop_map_to_update) > 0:
                                        resp: TIFControlResponse = stub.UpdateTableEntries(
                                            TIFControlRequest(
                                                arpHostEntries=arp_table_entries_to_update,
                                                ipv4HostEntries=ipv4_hosts_to_update,
                                                nexthopMapEntries=nexthop_map_to_update
                                            )
                                        )
                                        if resp.status == 200:
                                            self.logger.debug("Routing table configuration for device {} updated: \n ARP: {} \n IPv4: {} \n NexthopMap: {}".format(device_name, str(arp_table_entries_to_update), str(ipv4_hosts_to_update), str(nexthop_map_to_update)))
                                            continue
                                        else:
                                            self.logger.error("Error while updating routing table configuration for device {}: {}".format(device_name, resp.message))
                                    else:
                                        self.logger.debug("No routing table configuration to update at device {}. Continue.".format(device_name))
                                    if len(arp_table_entries_to_delete) > 0 or len(ipv4_hosts_to_delete) > 0 or len(nexthop_map_to_delete) > 0:
                                        resp: TIFControlResponse = stub.DeleteTableEntries(
                                            TIFControlRequest(
                                                arpHostEntries=arp_table_entries_to_delete,
                                                ipv4HostEntries=ipv4_hosts_to_delete,
                                                nexthopMapEntries=nexthop_map_to_delete
                                            )
                                        )
                                        if resp.status == 200:
                                            self.logger.debug("Routing table configuration for device {} deleted: \n ARP: {} \n IPv4: {} \n NexthopMap: {}".format(device_name, str(arp_table_entries_to_delete), str(ipv4_hosts_to_delete), str(nexthop_map_to_delete)))
                                            continue
                                        else:
                                            self.logger.error("Error while deleting table configuration for device {}: {}".format(device_name, resp.message))
                                    else:
                                        self.logger.debug("No routing table configuration to delete at device {}. Continue.".format(device_name))
                                else:
                                    self.logger.warning("Error while getting routing table configuration for device {}: {}".format(device_name, resp.message))
                        except grpc.RpcError as err:
                            if err.code() == grpc.StatusCode.UNAVAILABLE:
                                self.logger.error("Could not connect to " + device_name + ": not reachable")
                            else:
                                self.logger.error("Could not call gRPC to " + device_name + ": " + str(err))
                elif self.global_view.is_device_node_enabled(device_name):
                    self.logger.warning("Node {} is not reachable! Update Table Configuration Check failed!".format(device_name))
                else:
                    self.logger.debug("Node {} is disabled. Skipping Table configuration update check for node.")
            time.sleep(15)
        self.logger.info("Switch Table Entry Configuration Loop terminated.")

    def lag_ecmp_configuration_loop(self):
        """
        Periodically checks and updates the LAG ECMP configuration for devices.

        This method retrieves the LAG configuration for each enabled and running device
        from the global view and compares it with the current LAG configuration in the
        system. It then adds, updates, or deletes LAG ECMP groups as necessary.

        Note: This method runs in a loop until the `lag_ecmp_configuration_running` flag
        is set to False.

        Returns:
            None
        """
        def entry_in_resp(entry_list, entry):
            """
            Helper function to check if an entry exists in the response list.

            Args:
                entry_list (list): The list of entries to check.
                entry (dict): The entry to search for.

            Returns:
                bool: True if the entry exists in the list, False otherwise.
            """
            for entry_sw in entry_list:
                if entry["id"] == entry_sw.id:
                    return True
            return False

        self.logger.info("LAG ECMP Configuration Loop started.")
        while self.lag_ecmp_configuration_running:
            for device_name, device_metadata in self.global_view.get_all_devices().items():
                if self.global_view.is_device_node_enabled(device_name) and self.global_view.get_device_node_status(device_name) == OrchestratorHealthStates.RUNNING:
                    with self.rollout_lock:
                        try:
                            with grpc.insecure_channel(device_metadata["OMuProCU-TIFAddress"]) as channel:
                                stub = TIFControlCommunicatorStub(channel)
                                resp: TIFControlResponse = stub.GetLAGConfiguration(TIFControlRequest())
                                if resp.status == 200:
                                    lag_ecmp_group_to_add = []
                                    lag_ecmp_group_to_update = []
                                    lag_ecmp_group_to_delete = []
                                    for entry in resp.lagGroups:
                                        if self.global_view.is_device_lag_group_in_topology(device_name, entry.id):
                                            if self.global_view.check_device_lag_group_equality(device_name, MessageToDict(entry)):
                                                continue
                                            else:
                                                entry = self.global_view.get_lag_ecmp_groups_for_device_by_id(device_name, entry.id)
                                                if entry is not None:
                                                    lag_ecmp_group_to_update.append(ParseDict(entry, Lag()))
                                                else:
                                                    pass
                                        else:
                                            lag_ecmp_group_to_delete.append(entry)
                                    for name, entry in self.global_view.get_lag_ecmp_groups_for_device(device_name).items():
                                        if entry_in_resp(resp.lagGroups, entry):
                                            continue
                                        else:
                                            lag_ecmp_group_to_add.append(ParseDict(entry, Lag()))

                                    if len(lag_ecmp_group_to_add) > 0:
                                        resp: TIFControlResponse = stub.AddLAG(
                                            TIFControlRequest(
                                                lagGroups=lag_ecmp_group_to_add
                                            )
                                        )
                                        if resp.status == 200:
                                            self.logger.debug("Routing LAG ECMP configuration for device {} added: {}".format(device_name, str(lag_ecmp_group_to_add)))
                                            continue
                                        else:
                                            self.logger.error("Error while adding LAG ECMP configuration for device {}: {}".format(device_name, resp.message))
                                    else:
                                        self.logger.debug("No LAG ECMP configuration to add at device {}. Continue.".format(device_name))
                                    if len(lag_ecmp_group_to_update) > 0:
                                        resp: TIFControlResponse = stub.UpdateLAG(
                                            TIFControlRequest(
                                                lagGroups=lag_ecmp_group_to_update,
                                            )
                                        )
                                        if resp.status == 200:
                                            self.logger.debug("LAG ECMP configuration for device {} updated: {}".format(device_name, str(lag_ecmp_group_to_update)))
                                            continue
                                        else:
                                            self.logger.error("Error while updating LAG ECMP configuration for device {}: {}".format(device_name, resp.message))
                                    else:
                                        self.logger.debug("No LAG ECMP configuration to update at device {}. Continue.".format(device_name))
                                    if len(lag_ecmp_group_to_delete) > 0:
                                        resp: TIFControlResponse = stub.DeleteLAG(
                                            TIFControlRequest(
                                                lagGroups=lag_ecmp_group_to_delete,
                                            )
                                        )
                                        if resp.status == 200:
                                            self.logger.debug("LAG ECMP configuration for device {} deleted: {}".format(device_name, str(lag_ecmp_group_to_delete)))
                                            continue
                                        else:
                                            self.logger.error("Error while deleted LAG ECMP configuration for device {}: {}".format(device_name, resp.message))
                                    else:
                                        self.logger.debug("No LAG ECMP configuration to delete at device {}. Continue.".format(device_name))
                                else:
                                    self.logger.warning("Error while getting LAG Groups configuration for device {}: {}".format(device_name, resp.message))
                        except grpc.RpcError as err:
                            if err.code() == grpc.StatusCode.UNAVAILABLE:
                                self.logger.error("Could not connect to " + device_name + ": not reachable")
                            else:
                                self.logger.error("Could not call gRPC to " + device_name + ": " + str(err))
                elif self.global_view.is_device_node_enabled(device_name):
                    self.logger.warning("Node {} is not reachable! Update LAG Group Configuration Check failed!".format(device_name))
                else:
                    self.logger.debug("Node {} is disabled. Skipping LAG Group configuration update check for node.")

            time.sleep(15)
        self.logger.info("LAG ECMP Configuration Loop terminated.")

    def schedule_mdtdc(self, mdtdc, updateAction : UpdateAction):
        """
        Schedule the deployment of a manifest to devices based on the given MDTDC (Manifest Deployment To Device Category) object.

        Args:
            mdtdc (dict): The MDTDC object containing information about the manifest and target devices.
            updateAction (UpdateAction): The type of update action to perform (CREATE, UPDATE, DELETE).

        Returns:
            MDDeploymentResponse: The response object indicating the status and message of the deployment request.
        """
        with self.rollout_lock:
            for tdc in mdtdc["TDCs"]:
                if "deviceName" in tdc.keys():
                    if updateAction == UPDATE_ACTION_DELETE and not self.global_view.check_deployment_existence_at_device_node(tdc["deviceName"], tdc["manifest"]["id"], tdc["manifest"]["name"]):
                        return MDDeploymentResponse(
                            status = 400,
                            message = "Requested deletion of the manifest is not possible since manifest is not available at device {}".format(tdc["deviceName"])
                        )
                    self.global_view.update_device_node_deployment(tdc["deviceName"], tdc["manifest"]["id"], tdc["manifest"]["name"], tdc["manifest"], updateAction, DeploymentStatus.UNSPECIFIED, DeploymentStatus.SCHEDULED)
                elif "deviceCategory" in tdc.keys():
                    device_names = self.global_view.get_device_names_by_category(tdc["deviceCategory"])
                    for device_name in device_names:
                        self.global_view.update_device_node_deployment(device_name, tdc["manifest"]["id"], tdc["manifest"]["name"], tdc["manifest"], updateAction, DeploymentStatus.UNSPECIFIED, DeploymentStatus.SCHEDULED)
                else:
                    pass
        operation = ""
        if updateAction == UPDATE_ACTION_CREATE:
            operation = "Create"
        elif updateAction == UPDATE_ACTION_UPDATE:
            operation = "Update"
        elif updateAction == UPDATE_ACTION_DELETE:
            operation = "Delete"
        schedule_timestamp = time.time()
        self.deployment_timestamps["{}-{}".format(mdtdc["id"], mdtdc["name"])]["scheduleTimestamp"] = schedule_timestamp
        
        return MDDeploymentResponse(
            status=200,
            message="{} request submitted successfully".format(operation)
        )
    
    def _create(self, node_name : str, tdc_manifest : dict):
            """
            Helper method to send create TDC deployment messages
            
            Args:
                node_name (str): The name of the node where the deployment will be created.
                tdc_manifest (dict): The TDC manifest containing deployment information.
            
            Returns:
                MDDeploymentResponse: The deployment response object.
            """
            device_node = self.global_view.get_device_node(node_name)
            with grpc.insecure_channel(device_node["OMuProCUAddress"]) as channel:
                stub = DeploymentCommunicatorStub(channel)
                resp : DeploymentResponse = stub.Create(
                    DeploymentRequest(
                        tenantMetadata=TenantMetadata(
                            tenantId=int(tdc_manifest["id"]),
                            tenantFuncName=tdc_manifest["name"]
                        ),
                        deploymentMessage=DeploymentMessage(
                            deploymentRaw = str(tdc_manifest)
                        )
                    )
                )
                self.logger.debug(resp)
                if resp.status == 200:
                    return MDDeploymentResponse(
                        status = 200,
                        message = "Create request submitted successful."
                    )
                else:
                    return MDDeploymentResponse(
                        status = 500,
                        message = "Error while submitting deployment creation: {}".format(resp.message)
                    )

    def _update(self, node_name : str, tdc_manifest : dict):
            """
            Helper method to send update TDC deployment messages

            Args:
                node_name (str): The name of the node to update.
                tdc_manifest (dict): The TDC manifest containing the deployment information.

            Returns:
                MDDeploymentResponse: The deployment response object.

            Raises:
                None
            """
            device_node = self.global_view.get_device_node(node_name)
            with grpc.insecure_channel(device_node["OMuProCUAddress"]) as channel:
                stub = DeploymentCommunicatorStub(channel)
                resp : DeploymentResponse = stub.Update(
                    DeploymentRequest(
                        tenantMetadata=TenantMetadata(
                            tenantId=int(tdc_manifest["id"]),
                            tenantFuncName=tdc_manifest["name"]
                        ),
                        deploymentMessage=DeploymentMessage(
                            deploymentRaw = str(tdc_manifest)
                        )
                    )
                )
                self.logger.debug(resp)
                if resp.status == 200:
                    return MDDeploymentResponse(
                        status = 200,
                        message = "Update request submitted successful"
                    )
                else:
                    return MDDeploymentResponse(
                        status = 500,
                        message = "Error while submitting deployment update: {}".format(resp.message)
                    )

    def _delete(self, node_name : str, tdc_manifest : dict):
        """
        Helper method to send delete TDC deployment messages

        Args:
            node_name (str): The name of the node where the TDC deployment is located.
            tdc_manifest (dict): The TDC manifest containing the deployment information.

        Returns:
            MDDeploymentResponse: An object representing the response of the delete request.
        """
        device_node = self.global_view.get_device_node(node_name)
        with grpc.insecure_channel(device_node["OMuProCUAddress"]) as channel:
            stub = DeploymentCommunicatorStub(channel)
            resp : DeploymentResponse = stub.Delete(
                DeploymentRequest(
                    tenantMetadata=TenantMetadata(
                        tenantId=tdc_manifest["id"],
                        tenantFuncName=tdc_manifest["name"]
                    ),
                    deploymentMessage=DeploymentMessage(
                        deploymentRaw = str(tdc_manifest)
                    )
                )
            )
            self.logger.debug(resp)
            if resp.status == 200:
                return MDDeploymentResponse(
                    status = 200,
                    message = "Delete request submitted successfully"
                )
            else:
                return MDDeploymentResponse(
                    status = 500,
                    message = "Error while submitting deployment deletion: {}".format(resp.message)
                )

    def cleanup(self, all_devices : bool = False):
        """
        Cleans up the tenant security configuration by clearing the main ingress names
        for all devices except the default device.
        """
        if all_devices:
            for device_name, device_data in self.global_view.get_all_devices().items():
                try:
                    if device_data["category"] == InfrastructureDeviceCategories.EDGE_DEVICE.value:
                        self.logger.info("Skipping edge device {} cleanup because of LAG freeze bug.".format(device_name))
                    else:
                        self.logger.info("Cleaning up device: " + device_name)
                        # Degrade device if possible (e.g., set links down)
                        self.set_state_for_device_on_edge_devices(device_name, False)
                        # Try to cleanup device by using local OMuProCU
                        with grpc.insecure_channel(device_data["OMuProCUAddress"]) as channel:
                                stub = DeploymentCommunicatorStub(channel)
                                resp : DeploymentResponse = stub.Cleanup(DeploymentRequest())
                                if resp.status == 200:
                                    self.logger.debug("Cleanup request submitted successfully.")
                                else:
                                    self.logger.error("Error while submitting cleanup request: {}".format(resp.message))
                        # Upgrade device again if possible (e.g., set links up)
                        self.set_state_for_device_on_edge_devices(device_name, True)
                except grpc.RpcError as err:
                    if err.code() == grpc.StatusCode.UNAVAILABLE:
                        self.logger.error("Could not connect to " + device_name + ": not reachable")
                    else:
                        self.logger.error("Could not call gRPC to " + device_name + ": " + str(err))
                except Exception as ex:
                    self.logger.exception(ex)
        self.load_tenant_security_config()
        for tenantId in self.tenant_security_config.keys():
            for device_name, device_metadata in self.tenant_security_config[tenantId]["devices"].items():
                if device_name != "default":
                    self.tenant_security_config[tenantId]["devices"][device_name]["mainIngressNames"].clear()
        self.save_tenant_security_config()

    def Create(self, request, context):
        """
        Creates a deployment based on the provided MD-TDC.

        Args:
            request: The request object containing the MD-TDC.
            context: The context object for the gRPC request.

        Returns:
            If the MD-TDC is valid, returns the scheduled MD-TDC with the status set to UPDATE_ACTION_CREATE.
            If there is an error during deployment creation, returns an MDDeploymentResponse object with status 500 and an error message.
            If the MD-TDC is invalid, returns an MDDeploymentResponse object with status 400 and an error message.
        """
        if self.rollout_lock.locked():
            return MDDeploymentResponse(
                status = 500,
                message = "Rollout is currently running. Please try again later."
            )
        deployment_submission_timestamp = time.time()
        if self.validator.validate(deployment=request.mdtdc_raw):
            validation_end_timestamp = time.time()
            try:
                self.deployment_timestamps.update({
                    "{}-{}".format(self.validator.mdtdc_id, self.validator.mdtdc_name) : {
                        "deploymentType": "CREATE",
                        "submissionTimestamp": deployment_submission_timestamp,
                        "validationEndTimestamp": validation_end_timestamp,
                        "scheduleTimestamp" : None,
                        "deploymentStart": None,
                        "deploymentEnd": None,
                        "nodes": {}
                    }
                })
                return self.schedule_mdtdc(self.validator.mdtdc, UPDATE_ACTION_CREATE)
            except Exception as ex:
                self.logger.exception(ex)
                return MDDeploymentResponse(
                    status = 500,
                    message = "Error while submitting deployment creation."
                )
        else: 
            return MDDeploymentResponse(
                status = 400,
                message = "Error while validating the MD-TDC."
            )
    
    def Update(self, request, context):
        """
        Updates the MD-TDC deployment.

        Args:
            request: The request object containing the updated MD-TDC deployment.
            context: The context object for the gRPC communication.

        Returns:
            If the MD-TDC deployment is successfully validated, the function schedules the update action and returns the response.
            Otherwise, it returns an error response with the appropriate status code and message.
        """
        if self.rollout_lock.locked():
            return MDDeploymentResponse(
                status = 500,
                message = "Rollout is currently running. Please try again later."
            )
        deployment_submission_timestamp = time.time()
        if self.validator.validate(deployment=request.mdtdc_raw, update_request=True):
            validation_end_timestamp = time.time()
            self.deployment_timestamps.update({
                    "{}-{}".format(self.validator.mdtdc_id, self.validator.mdtdc_name) : {
                        "deploymentType": "UPDATE",
                        "submissionTimestamp": deployment_submission_timestamp,
                        "validationEndTimestamp": validation_end_timestamp,
                        "scheduleTimestamp" : None,
                        "deploymentStart": None,
                        "deploymentEnd": None,
                        "nodes": {}
                    }
            })
            return self.schedule_mdtdc(self.validator.mdtdc, UPDATE_ACTION_UPDATE)
        else: 
            return MDDeploymentResponse(
                status = 400,
                message = "Error while validating the MD-TDC."
            )
    
    def Delete(self, request, context):
        """
        Deletes the MD-TDC deployment.

        Args:
            request: The request object containing the MD-TDC deployment information.
            context: The context object for the gRPC communication.

        Returns:
            If the MD-TDC deployment is successfully deleted, returns the scheduled MD-TDC deployment response.
            If the MD-TDC deployment fails validation, returns an error response with status code 400 and an error message.
        """
        if self.rollout_lock.locked():
            return MDDeploymentResponse(
                status = 500,
                message = "Rollout is currently running. Please try again later."
            )
        deployment_submission_timestamp = time.time()
        if self.validator.validate(deployment=request.mdtdc_raw, delete_request=True):
            validation_end_timestamp = time.time()
            self.deployment_timestamps.update({
                    "{}-{}".format(self.validator.mdtdc_id, self.validator.mdtdc_name) : {
                        "deploymentType": "DELETE",
                        "submissionTimestamp": deployment_submission_timestamp,
                        "validationEndTimestamp": validation_end_timestamp,
                        "scheduleTimestamp" : None,
                        "deploymentStart": None,
                        "deploymentEnd": None,
                        "nodes": {}
                    }
            })
            return self.schedule_mdtdc(self.validator.mdtdc, UPDATE_ACTION_DELETE)
        else: 
            return MDDeploymentResponse(
                status = 400,
                message = "Error while validating the MD-TDC."
            )
    
    def Cleanup(self, request, context):
        """
        Cleans up the resources used by the management process.

        Args:
            request: The cleanup request.
            context: The context of the cleanup.

        Returns:
            The response after cleaning up the resources.
        """
        if self.rollout_lock.locked():
            return MDDeploymentResponse(
                status = 500,
                message = "Rollout is currently running. Please try again later."
            )
        try:
            self.cleanup(all_devices=True)
            return MDDeploymentResponse(
                status = 200,
                message = "Cleanup successful."
            )
        except Exception as ex:
            self.logger.exception(ex)
            return MDDeploymentResponse(
                status = 500,
                message = "Error while cleaning up."
            )
    
    def CheckHealth(self, request, context):
        """
        Checks the health of the management process.

        Args:
            request: The health check request.
            context: The context of the health check.

        Returns:
            The health check response.
        """
        return MDDeploymentResponse(
            status = 200,
            message = "Global View is healthy." 
        )

    def GetDeviceNode(self, request, context):
        """
        Retrieves the device node from the global view based on the given request.

        Args:
            request: The request containing the topology update.
            context: The context of the gRPC call.

        Returns:
            A dictionary containing the device name as the key and the corresponding device node from the global view as the value.
        """
        device_name = request.topologyUpdate.nodes[0].nodeName
        device_node = self.global_view.get_device_node(device_name)
        return self._build_topology_update_response({device_name: device_node})
    
    def GetEdgeNodes(self, request, context):
        """
        Retrieves the edge nodes from the global view.

        Args:
            request: The request object.
            context: The context object.

        Returns:
            The topology update response containing the edge devices.
        """
        edge_devices = self.global_view.get_edge_devices()
        return self._build_topology_update_response(edge_devices)
    
    def GetAllDeviceNodes(self, request, context):
        """
        Retrieves all device nodes from the global view.

        Args:
            request: The request object.
            context: The context object.

        Returns:
            The topology update response containing all device nodes.
        """
        all_devices = self.global_view.get_all_devices()
        return self._build_topology_update_response(all_devices)
    
    def GetDatacenterNodes(self, request, context):
        """
        Retrieves the datacenter nodes from the global view.

        Args:
            request: The request object.
            context: The context object.

        Returns:
            The topology update response containing the datacenter devices.
        """
        datacenter_devices = self.global_view.get_datacenter_devices()
        return self._build_topology_update_response(datacenter_devices)
    
    def SetDeviceNodeStatus(self, request, context):
        """
        Sets the status of a device node in the global view.

        Args:
            request: The request object containing the topology update.
            context: The context object for the gRPC request.

        Returns:
            A TopologyUpdateResponse object indicating the status of the operation.
        """
        try:
            nodeName = request.topologyUpdate.nodes[0].nodeName
            status = OrchestratorHealthStates(request.topologyUpdate.nodes[0].orchestratorHealthState)
            self.global_view.set_device_node_status(nodeName, status)
            return TopologyUpdateResponse(
                status=200,
                message= "Updated node status of {} successfully to {}".format(nodeName, status.name)
            )
        except Exception as ex:
            self.logger.exception(ex)
            return TopologyUpdateResponse(
                status=500,
                message="Could not set node status!"
            )
    
    def GetDeviceNodeStatus(self, request, context):
        """
        Retrieves the status of a device node.

        Args:
            request: The request object containing the topology update.
            context: The context object for the gRPC request.

        Returns:
            A TopologyUpdateResponse object containing the device node status.

        Raises:
            Exception: If there is an error retrieving the node status.
        """
        try:
            status = self.global_view.get_device_node_status(request.topologyUpdate.nodes[0].nodeName)
            return TopologyUpdateResponse(
                status = 200,
                topologyUpdate=TopologyUpdate(
                    nodes=[
                        Node(
                            nodeName=request.topologyUpdate.nodes[0].nodeName,
                            orchestratorHealthState=status.value
                        )
                    ]
                )
            )
        except Exception as ex:
            self.logger.exception(ex)
            return TopologyUpdateResponse(
                status=500,
                message="Could not retrieve node status"
            )

    def GetDeviceNodeDeploymentStatus(self, request, context):
        """
        Retrieves the deployment status of a device node.

        Args:
            request: The request object containing the deployment node update information.
            context: The context object for the gRPC request.

        Returns:
            A TopologyUpdateResponse object containing the deployment status of the device node.
        """
        tenantId = request.deploymentNodeUpdate.tenantMetadatas[0].tenantId
        tenantFuncName = request.deploymentNodeUpdate.tenantMetadatas[0].tenantFuncName
        nodeName = request.deploymentNodeUpdate.node.nodeName
        
        status = self.global_view.get_device_node_deployment_status(nodeName, tenantId, tenantFuncName)

        return TopologyUpdateResponse(
            status=200,
            message = "",
            deploymentNodeUpdates=[DeploymentNodeUpdate(
                tenantMetadatas = [TenantMetadata(
                    tenantId =tenantId,
                    tenantFuncName=tenantFuncName
                )],
                node = Node(
                    nodeName=nodeName
                ),
                deploymentStatus=status.value
            )]
        )
    
    def SetDeviceNodeDeploymentStatus(self, request, context):
        """
        Sets the deployment status of a device node.

        Args:
            request: The request object containing the deployment node updates.
            context: The context object for the gRPC request.

        Returns:
            A TopologyUpdateResponse object with the status and message.

        Raises:
            Exception: If an error occurs during the process.
        """
        try:
            name = request.deploymentNodeUpdates[0].node.nodeName
            deployment_status = request.deploymentNodeUpdates[0].deploymentStatus
            for tenantMetadata in request.deploymentNodeUpdates[0].tenantMetadatas:
                tenantId = tenantMetadata.tenantId
                tenantFuncName = tenantMetadata.tenantFuncName
                self.global_view.set_device_node_deployment_status(name, tenantId, tenantFuncName, DeploymentStatus(deployment_status))
        except Exception as ex:
            self.logger.exception(ex, exc_info=True)
        return TopologyUpdateResponse(
            status = 200,
            message = ""
        )
    
    def SetDeviceNodeMDDeploymentStatus(self, request, context):
        """
        Sets the deployment status of a device node in the MD (Management Domain).

        Args:
            request: The request object containing the deployment node updates.
            context: The context object for the gRPC request.

        Returns:
            A TopologyUpdateResponse object with the status and message.

        """
        name = request.deploymentNodeUpdates[0].node.nodeName
        deployment_status = request.deploymentNodeUpdates[0].mdDeploymentStatus
        for tenantMetadata in request.deploymentNodeUpdates[0].tenantMetadatas:
            tenantId = tenantMetadata.tenantId
            tenantFuncName = tenantMetadata.tenantFuncName
            self.global_view.update_device_node_md_deployment_status(name, tenantId, tenantFuncName, DeploymentStatus(deployment_status))
        return TopologyUpdateResponse(
            status = 200,
            message = ""
        )
    
    def GetDeviceNodeMDDeploymentStatus(self, request, context):
        """
        Retrieves the deployment status of a device node in the MD (Management Domain).

        Args:
            request: The request object containing the deployment node updates.
            context: The context object for the gRPC communication.

        Returns:
            A TopologyUpdateResponse object containing the deployment status of the device node.

        Raises:
            KeyError: If the status for the specified tenant and function is not found.
            Exception: If there is an error while retrieving the status.

        """
        tenantId = request.deploymentNodeUpdates[0].tenantMetadatas[0].tenantId
        tenantFuncName = request.deploymentNodeUpdates[0].tenantMetadatas[0].tenantFuncName
        nodeName = request.deploymentNodeUpdates[0].node.nodeName
        
        try: 
            status = self.global_view.get_device_node_md_deployment_status(nodeName, tenantId, tenantFuncName)
        except KeyError as err:
            return TopologyUpdateResponse(
                status=404,
                message = "Status for Tenant {} for function {} not found!".format(tenantId, tenantFuncName)
            )
        except Exception as ex:
            self.logger.exception(ex, exc_info=True)
            return TopologyUpdateResponse(
                status=500,
                message = "Error while getting status for Tenant {} for function {} not found!".format(tenantId, tenantFuncName)
            )
        return TopologyUpdateResponse(
            status=200,
            message = "",
            deploymentNodeUpdates=[DeploymentNodeUpdate(
                tenantMetadatas = [TenantMetadata(
                    tenantId =tenantId,
                    tenantFuncName=tenantFuncName
                )],
                node = Node(
                    nodeName=nodeName
                ),
                mdDeploymentStatus=status.value
            )]
        )
    
    def CreateRule(self, request, context):
        """
        Creates a rule in the global view.

        Args:
            request: The request object containing the rule information.
            context: The context object for the gRPC communication.

        Returns:
            A TopologyUpdateResponse object containing the status of the rule creation.
        """
        try:
            for i, device_rule_request in enumerate(request.tifControlRequests):
                device_name = request.infrastructureDeviceNames[i]
                if request.infrastructureDeviceNames[i] not in self.global_view.get_all_devices():
                    return TopologyUpdateResponse(
                        status = 404,
                        message = "Device {} not found in the global view.".format(device_name)
                    )
                tif_control_request = device_rule_request
                scheduled_for_create = {}
                scheduled_for_create["runtime_rules"] = []
                if tif_control_request.runtimeRules:
                    if len(tif_control_request.runtimeRules) > 0:
                        for runtime_rule in tif_control_request.runtimeRules:
                            self.global_view.add_tenant_table_entry(device_name, runtime_rule.table, runtime_rule.matches, runtime_rule.actionName, runtime_rule.actionParams)
                            scheduled_for_create["runtime_rules"].append(runtime_rule)
                    # Update rule in the topology if it is a provider rule
                scheduled_for_create["nexthop_map"] = []
                if tif_control_request.nexthopMapEntries:
                    if len(tif_control_request.nexthopMapEntries) > 0:
                        # FIXME: This does not work this way! The nexthopMap entries have different structure!
                        for next_hop_entry in tif_control_request.nexthopMapEntries:
                            self.global_view.set_device_routing_table_configuration_entry(device_name, "nexthopMap", next_hop_entry.key, next_hop_entry.nextHopId)
                            scheduled_for_create["nexthop_map"].append(next_hop_entry)
                scheduled_for_create["arp_table_host_entries"] = []
                if tif_control_request.arpHostEntries:
                    if len(tif_control_request.arpHostEntries) > 0:
                        for arp_host_entry in tif_control_request.arpHostEntries:
                            self.global_view.set_device_routing_table_configuration_entry(device_name, "arpTableHostEntries", "ip", arp_host_entry.key, arp_host_entry.nextHopId)
                            scheduled_for_create["arp_table_host_entries"].append(arp_host_entry)
                scheduled_for_create["ipv4_host_entries"] = []
                if tif_control_request.ipv4HostEntries:
                    if len(tif_control_request.ipv4HostEntries) > 0:
                        for ipv4_host_entry in tif_control_request.ipv4HostEntries:
                            self.global_view.set_device_routing_table_configuration_entry(device_name, "ipv4HostEntries", "ip", ipv4_host_entry.key, ipv4_host_entry.nextHopId)
                            scheduled_for_create["ipv4_host_entries"].append(ipv4_host_entry)
                with grpc.insecure_channel(self.global_view.get_device_node(device_name)["OMuProCUAddress"].split(":")[0] + ":49056") as channel:
                    stub = RulesUpdaterCommunicatorStub(channel)
                    resp: TIFControlResponse = stub.CreateRules(
                        TIFControlRequest(
                                runtimeRules=scheduled_for_create["runtime_rules"],
                                nexthopMapEntries=scheduled_for_create["nexthop_map"],
                                arpHostEntries=scheduled_for_create["arp_table_host_entries"],
                                ipv4HostEntries=scheduled_for_create["ipv4_host_entries"]
                        )
                    )
                    if resp.status == 200:
                        self.logger.debug("Rule(s) created successfully on device {}".format(device_name))
                    else:
                        self.logger.error("Error while creating rule on device {}: {}".format(device_name, resp.message))
                        return TopologyUpdateResponse(
                            status = 500,
                            message = "Error while creating rule on device {}: {}".format(device_name, resp.message)
                        )
            return TopologyUpdateResponse(
                status = 200,
                message = "Rules created successfully"
            )
        except Exception as ex:
            self.logger.exception(ex)
            return TopologyUpdateResponse(
                status = 500,
                message = "Error while creating rules: {}".format(str(ex))
            )

    def UpdateRule(self, request, context):
        """
        Updates a rule in the global view.

        Args:
            request: The request object containing the rule information.
            context: The context object for the gRPC communication.

        Returns:
            A TopologyUpdateResponse object containing the status of the rule update.
        """
        try:
            for i, device_rule_request in enumerate(request.tifControlRequests):
                device_name = request.infrastructureDeviceNames[i]
                if request.infrastructureDeviceNames[i] not in self.global_view.get_all_devices():
                    return TopologyUpdateResponse(
                        status = 404,
                        message = "Device {} not found in the global view.".format(device_name)
                    )
                tif_control_request = device_rule_request
                scheduled_for_update = {}
                scheduled_for_update["runtime_rules"] = []
                if tif_control_request.runtimeRules:
                    if len(tif_control_request.runtimeRules) > 0:
                        for runtime_rule in tif_control_request.runtimeRules:
                            if self.global_view.is_entry_in_tenant_table(device_name, runtime_rule.table, runtime_rule.matches):
                                self.global_view.set_tenant_table_entry(device_name, runtime_rule.table, runtime_rule.matches, runtime_rule.actionName, runtime_rule.actionParams)
                            else:
                                self.global_view.add_tenant_table_entry(device_name, runtime_rule.table, runtime_rule.matches, runtime_rule.actionName, runtime_rule.actionParams)
                            scheduled_for_update["runtime_rules"].append(runtime_rule)
                scheduled_for_update["nexthop_map"] = []
                # Update rule in the topology if it is a provider rule
                if tif_control_request.nexthopMapEntries:
                    if len(tif_control_request.nexthopMapEntries) > 0:
                        # FIXME: This does not work this way! The nexthopMap entries have different structure!
                        for next_hop_entry in tif_control_request.nexthopMapEntries:
                            self.global_view.set_device_routing_table_configuration_entry(device_name, "nexthopMap", next_hop_entry.key, next_hop_entry.nextHopId)
                            scheduled_for_update["nexthop_map"].append(next_hop_entry)
                scheduled_for_update["arp_table_host_entries"] = []
                if tif_control_request.arpHostEntries:
                    if len(tif_control_request.arpHostEntries) > 0:
                        for arp_host_entry in tif_control_request.arpHostEntries:
                            self.global_view.set_device_routing_table_configuration_entry(device_name, "arpTableHostEntries", "ip", arp_host_entry.key, arp_host_entry.nextHopId)
                            scheduled_for_update["arp_table_host_entries"].append(arp_host_entry)
                scheduled_for_update["ipv4_host_entries"] = []
                if tif_control_request.ipv4HostEntries:
                    if len(tif_control_request.ipv4HostEntries) > 0:
                        for ipv4_host_entry in tif_control_request.ipv4HostEntries:
                            self.global_view.set_device_routing_table_configuration_entry(device_name, "ipv4HostEntries", "ip", ipv4_host_entry.key, ipv4_host_entry.nextHopId)
                            scheduled_for_update["ipv4_host_entries"].append(ipv4_host_entry)
                with grpc.insecure_channel(self.global_view.get_device_node(device_name)["OMuProCUAddress"].split(":")[0] + ":49056") as channel:
                    stub = RulesUpdaterCommunicatorStub(channel)
                    resp : TIFControlResponse = stub.UpdateRules(
                        TIFControlRequest(
                            runtimeRules=scheduled_for_update["runtime_rules"],
                            nexthopMapEntries=scheduled_for_update["nexthop_map"],
                            arpHostEntries=scheduled_for_update["arp_table_host_entries"],
                            ipv4HostEntries=scheduled_for_update["ipv4_host_entries"]
                        )
                    )
                    if resp.status == 200:
                        self.logger.debug("Rule updated successfully on device {}".format(device_name))
                    else:
                        self.logger.error("Error while updating rule on device {}: {}".format(device_name, resp.message))
                        return TopologyUpdateResponse(
                            status = 500,
                            message = "Error while updating rule on device {}: {}".format(device_name, resp.message)
                        )
            return TopologyUpdateResponse(
                status = 200,
                message = "Rules updated successfully"
            )
        except Exception as ex:
            self.logger.exception(ex)
            return TopologyUpdateResponse(
                status = 500,
                message = "Error while updating rules: {}".format(str(ex))
            )
        
    def DeleteRule(self, request, context):
        """
        Deletes a rule in the global view.

        Args:
            request: The request object containing the rule information.
            context: The context object for the gRPC communication.

        Returns:
            A TopologyUpdateResponse object containing the status of the rule deletion.
        """
        try:
            for i, device_rule_request in enumerate(request.tifControlRequests):
                device_name = request.infrastructureDeviceNames[i]
                if request.infrastructureDeviceNames[i] not in self.global_view.get_all_devices():
                    return TopologyUpdateResponse(
                        status = 404,
                        message = "Device {} not found in the global view.".format(device_name)
                    )
                tif_control_request = device_rule_request
                scheduled_for_delete = {}
                scheduled_for_delete["runtime_rules"] = []
                if tif_control_request.runtimeRules:
                    if len(tif_control_request.runtimeRules) > 0:
                        for runtime_rule in tif_control_request.runtimeRules:
                            if self.global_view.is_entry_in_tenant_table(device_name, runtime_rule.table, runtime_rule.matches):
                                self.global_view.delete_tenant_table_entry(device_name, runtime_rule.table, runtime_rule.matches)
                                scheduled_for_delete["runtime_rules"].append(runtime_rule)
                # Update rule in the topology if it is a provider rule
                scheduled_for_delete["nexthop_map"] = []
                if tif_control_request.nexthopMapEntries:
                    if len(tif_control_request.nexthopMapEntries) > 0:
                        # FIXME: This does not work this way! The nexthopMap entries have different structure!
                        for next_hop_entry in tif_control_request.nexthopMapEntries:
                            if self.global_view.delete_device_routing_table_configuration_entry(device_name, "nexthopMap", "ip", next_hop_entry.key):
                                scheduled_for_delete["nexthop_map"].append(next_hop_entry)
                scheduled_for_delete["arp_table_host_entries"] = []
                if tif_control_request.arpHostEntries:
                    if len(tif_control_request.arpHostEntries) > 0:
                        for arp_host_entry in tif_control_request.arpHostEntries:
                            if self.global_view.delete_device_routing_table_configuration_entry(device_name, "arpTableHostEntries", "ip", arp_host_entry.key):
                                scheduled_for_delete["arp_table_host_entries"].append(arp_host_entry)
                scheduled_for_delete["ipv4_host_entries"] = []
                if tif_control_request.ipv4HostEntries:
                    if len(tif_control_request.ipv4HostEntries) > 0:
                        for ipv4_host_entry in tif_control_request.ipv4HostEntries:
                            if self.global_view.delete_device_routing_table_configuration_entry(device_name, "ipv4HostEntries", "ip", ipv4_host_entry.key):
                                scheduled_for_delete["ipv4_host_entries"].append(ipv4_host_entry)
                with grpc.insecure_channel(self.global_view.get_device_node(device_name)["OMuProCUAddress"].split(":")[0] + ":49056") as channel:
                    stub = RulesUpdaterCommunicatorStub(channel)
                    resp : TIFControlResponse = stub.DeleteRules(
                        TIFControlRequest(
                            runtimeRules=scheduled_for_delete["runtime_rules"],
                            nexthopMapEntries=scheduled_for_delete["nexthop_map"],
                            arpHostEntries=scheduled_for_delete["arp_table_host_entries"],
                            ipv4HostEntries=scheduled_for_delete["ipv4_host_entries"]
                        )
                    )
                    if resp.status == 200:
                        self.logger.debug("Rule deleted successfully on device {}".format(device_name))
                    else:
                        self.logger.error("Error while deleting rule on device {}: {}".format(device_name, resp.message))
                        return TopologyUpdateResponse(
                            status = 500,
                            message = "Error while deleting rule on device {}: {}".format(device_name, resp.message)
                        )
            return TopologyUpdateResponse(
                status = 200,
                message = "Rules deleted successfully"
            )
        except Exception as ex:
            self.logger.exception(ex)
            return TopologyUpdateResponse(
                status = 500,
                message = "Error while deleting rules: {}".format(str(ex))
            )

    def GetRule(self, request, context):
        """
        Retrieves a rule from the global view.

        Args:
            request: The request object containing the rule information.
            context: The context object for the gRPC communication.

        Returns:
            A TopologyUpdateResponse object containing the rule information.
        """
        try:
            for i, device_rule_request in enumerate(request.tifControlRequests):
                device_name = request.infrastructureDeviceNames[i]
                if request.infrastructureDeviceNames[i] not in self.global_view.get_all_devices():
                    return TopologyUpdateResponse(
                        status = 404,
                        message = "Device {} not found in the global view.".format(device_name)
                    )
                tif_control_request = device_rule_request.tifControlRequests[i]
                runtime_rules = []
                if len(tif_control_request.runtimeRules) > 0:
                    for runtime_rule in tif_control_request.runtimeRules:
                        runtime_rules.append(self.global_view.get_device_routing_table_configuration_entry(device_name, "runtime_rules", runtime_rule.key))
                nexthop_map = []
                if len(tif_control_request.nextHopEntries) > 0:
                    for next_hop_entry in tif_control_request.nextHopEntries:
                        nexthop_map.append(self.global_view.get_device_routing_table_configuration_entry(device_name, "nexthop_map", next_hop_entry.key))
                arp_table_host_entries = []
                if len(tif_control_request.arpHostEntries) > 0:
                    for arp_host_entry in tif_control_request.arpHostEntries:
                        arp_table_host_entries.append(self.global_view.get_device_routing_table_configuration_entry(device_name, "arp_table_host_entries", arp_host_entry.key))
                ipv4_host_entries = []
                if len(tif_control_request.ipv4HostEntries) > 0:
                    for ipv4_host_entry in tif_control_request.ipv4HostEntries:
                        ipv4_host_entries.append(self.global_view.get_device_routing_table_configuration_entry(device_name, "ipv4_host_entries", ipv4_host_entry.key))
                return TopologyUpdateResponse(
                    status = 200,
                    message = "",
                    tifControlResponses = [
                        TIFControlResponse(
                            runtimeRules=runtime_rules,
                            nextHopEntries=nexthop_map,
                            arpHostEntries=arp_table_host_entries,
                            ipv4HostEntries=ipv4_host_entries
                        )
                    ]
                )
        except Exception as ex:
            self.logger.exception(ex)
            return TopologyUpdateResponse(
                status = 500,
                message = "Error while retrieving rules: {}".format(str(ex))
            )

    def set_state_for_device_on_edge_devices(self, device: str, state: bool):
        """
        Sets the state for a specific device on edge devices.

        Args:
            device (str): The device for which the state needs to be set.
            state (bool): The state to be set for the device.

        Returns:
            None
        """
        links = self.global_view.get_node_links_to_edge_nodes(device)
        link: tuple = None
        for link in links:
            if not self.global_view.get_link_status(link):
                continue
            src_dp_port = self.global_view.get_dest_dp_port(link)
            edge_device = None
            assert len(link) == 3
            if link.index(device) == 1:
                edge_device = link[0]
            else:
                edge_device = link[1]
            if self._check_if_device_enabled_and_running(edge_device):
                self._change_lag_member_state(edge_device, src_dp_port, state)
                self.logger.info("Changed LAG Member Port {} to {} for Device {}".format(src_dp_port, state, edge_device))
            elif self.global_view.is_device_node_enabled(edge_device):
                self.logger.error("Cannot change LAG Member Port {} to {}: Device {} is not reachable or does not run OMuProCU!".format(src_dp_port, state, edge_device))
            else:
                self.logger.warn("OMuProCU @ Device {} is disabled, so not changing the LAG member state for Port {} to {}".format(edge_device, src_dp_port, state))

    def _change_lag_member_state(self, managed_device : str, member_dp_port : int, member_state : bool):
            """
            Change the state of a member port in a Link Aggregation Group (LAG) for a managed device.

            Args:
                managed_device (str): The name of the managed device.
                member_dp_port (int): The datapath port of the member to change the state for.
                member_state (bool): The desired state of the member port (True for active, False for inactive).

            Raises:
                ValueError: If the member datapath port is not assigned in the LAG as a source port.

            Returns:
                None
            """
            device_node = self.global_view.get_device_node(managed_device)
            groups = self.global_view.get_lag_ecmp_groups_for_device(managed_device)
            lag_name = self.global_view.find_lag_by_dp_port_member(managed_device, member_dp_port)
            if lag_name is None:
                raise ValueError("DP Port {} not assigned in LAG as source port @ device {}".format(member_dp_port, managed_device))
            lag_group = groups[lag_name]
            member_ports = [DpPort(portId=port["portId"], active=port["active"]) for port in lag_group["dpPorts"] if port["portId"] != member_dp_port]
            member_ports.append(DpPort(portId=member_dp_port, active=member_state))
            with grpc.insecure_channel(device_node["OMuProCU-TIFAddress"]) as channel:
                stub = TIFControlCommunicatorStub(channel)
                resp: TIFControlResponse = stub.ChangeLAGMemberState(TIFControlRequest(
                    lagGroups=[Lag(
                        id=lag_group["id"],
                        memberbase=lag_group["memberbase"],
                        dp_ports=member_ports
                    )]
                ))
                self.logger.debug("Response from OMuProCU @ {}: {}".format(managed_device, resp))
                self.global_view.change_lag_member_state_for_device(managed_device, lag_name, member_dp_port, member_state, member_dp_port)

    def _check_if_device_enabled_and_running(self, device_name):
        """
        Check if a device is enabled and running.

        Args:
            device_name (str): The name of the device.

        Returns:
            bool: True if the device is enabled and running, False otherwise.
        """
        return self.global_view.is_device_node_enabled(device_name) and self.global_view.get_device_node_status(device_name) == OrchestratorHealthStates.RUNNING

    def _build_topology_update_response(self, nodes : dict):
        """
        Build the topology update response based on the given nodes.

        Args:
            nodes (dict): A dictionary containing the nodes and their data.

        Returns:
            TopologyUpdateResponse: The constructed topology update response.
        """
        try:
            deployments_dict = {}
            for node, data in nodes.items():
                deployments_dict[node] = {}
                for tenantId in data["deployments"]:
                    for deploymentName in data["deployments"][tenantId]:
                        tenant_cnf_id = str(tenantId) + "-" + deploymentName
                        deployments_dict[node][tenant_cnf_id] = {}
                        deployments_dict[node][tenant_cnf_id]["tenantId"] = tenantId
                        deployments_dict[node][tenant_cnf_id]["tenantFuncName"] = deploymentName
            return TopologyUpdateResponse(
                status = 200,
                topologyUpdate = TopologyUpdate(
                    nodes = [
                        Node(
                            nodeName=name,
                            orchestratorHealthState=data["status"].value,
                            category=data["category"],
                            OMuProCUAddress = data["OMuProCUAddress"],
                            OMuProCUEnabled = data["OMuProCUEnabled"],
                            deployments = [
                                ParseDict(deployment_metadata, TenantMetadata()) for deployment_name, deployment_metadata in deployments_dict[name].items()
                            ]
                        ) for name, data in nodes.items()
                    ]
                )
            )
        except Exception as ex:
            self.logger.exception(ex, exc_info=True)
    
    def ChangeLAGMemberState(self, request : TopologyUpdateRequest, context):
        """
        Changes the state of a LAG member.

        Args:
            request (TopologyUpdateRequest): The request containing the topology update information.
            context: The context of the gRPC request.

        Returns:
            TopologyUpdateResponse: The response containing the status and message.
        """
        try:
            if len(request.topologyUpdate.nodes) > 1:
                raise NotImplementedError
            else:
                device_name = request.topologyUpdate.nodes[0].nodeName
                if len(request.topologyUpdate.nodes[0].lagGroups) > 1:
                    raise NotImplementedError
                else: 
                    lag = request.topologyUpdate.nodes[0].lagGroups[0]
                    # Connection to the device OMuProCU and update the LAG 
                    device_node = self.global_view.get_device_node(device_name)
                    with grpc.insecure_channel(device_node["OMuProCU-TIFAddress"]) as channel:
                        tifcontrol_stub = TIFControlCommunicatorStub(channel)
                        tifcontrol_stub.ChangeLAGMemberState(
                            TIFControlRequest(
                                lagGroups = [lag]
                            )
                        )
                    if len(lag.dp_ports) > 1:
                        for dp_port in lag.dp_ports:
                            self.global_view.change_lag_member_state_for_device(device_name, lag.id, dp_port.portId)
        except Exception as ex:
            self.logger.exception(ex)
            return TopologyUpdateResponse(
                status= 500,
                message=""
            )

if __name__ == "__main__":
    logger = init_logger("Main")
    logger.info("Starting Health Monitor Process")
    health_monitor = OMuProCUHealthMonitor()
    health_monitor.start()
    logger.info("Starting Management Process.")
    management = ManagementProcess(daemon=True)
    management.start()
    try:
        logger.info("OMuProCU-MD started. To end the Orchestrator, type <Control-C>.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Got Interrupt. Terminating.")
        management.terminate()
    finally:
        management.running = False
        management.rollout_running = False
        management.cleanup()
        management.join()
        health_monitor.hardware_health_monitor_running = False
        health_monitor.deployment_health_monitor_running = False
        health_monitor.join()
