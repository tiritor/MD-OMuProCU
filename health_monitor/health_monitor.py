#!/usr/bin/python3


import logging
from multiprocessing import Process
import threading
import time
import grpc

from google.protobuf.json_format import MessageToDict

from orchestrator_utils.til.md_orchestrator_msg_pb2_grpc import TopologyUpdateCommunicatorStub
from orchestrator_utils.til.md_orchestrator_msg_pb2 import TopologyUpdateRequest, TopologyUpdateResponse, TopologyUpdate, Node, DeploymentNodeUpdate
from orchestrator_utils.til.til_msg_pb2_grpc import DeploymentCommunicatorStub
from orchestrator_utils.til.til_msg_pb2 import DeploymentRequest, DeploymentResponse, TenantMetadata
from orchestrator_utils.states import DeploymentStatus
from orchestrator_utils.logger.logger import init_logger

from conf.config import GLOBALVIEW_ADDRESS, INFRASTRUCTURE_FILE_PATH
from health_monitor.states import OrchestratorHealthStates
from topology import GlobalView


class OMuProCUHealthMonitor(Process):
    """
    Monitors the health of OMuProCU devices and deployments.
    """
    def __init__(self):
        self.logger = init_logger(self.__class__.__name__, logging.INFO)
        Process.__init__(self)

        self.sleep_interval = 5 # in secs

        self.hardware_health_monitor_running = False
        self.deployment_health_monitor_running = False

    def run(self) -> None:
        super().run()

        self.hardware_health_monitor_thread = threading.Thread(target=self.hardware_health_monitor_loop)
        self.deployment_health_monitor_thread = threading.Thread(target=self.deployment_health_monitor_loop)

        self.hardware_health_monitor_running = True
        self.hardware_health_monitor_thread.start()

        self.deployment_health_monitor_running = True
        self.deployment_health_monitor_thread.start()

    def __exit__(self, exc_typ, exc_value, traceback):
        self.stop()

    def stop(self):
        self.logger.info("Stopping GlobalView Process...")
        self.hardware_health_monitor_running = False
        self.deployment_health_monitor_running = False
        self.deployment_health_monitor_thread.join()
        self.hardware_health_monitor_thread.join()
        self.logger.info("GlobalView Process stopped.")
    
    def hardware_health_monitor_loop(self):
        """
        Monitors the health of hardware devices.

        This method continuously checks the health status of hardware devices by communicating with the global view and individual devices.
        It retrieves the device information from the global view and performs health checks on each device.
        If a device is disabled or in an error state, it updates the device status in the global view accordingly.

        Note: This method runs in a loop until the `hardware_health_monitor_running` flag is set to False.

        Returns:
            None
        """
        time.sleep(5)
        while self.hardware_health_monitor_running:
            devices = {}
            with grpc.insecure_channel(GLOBALVIEW_ADDRESS) as channel:
                stub = TopologyUpdateCommunicatorStub(channel)
                resp : TopologyUpdateResponse = stub.GetAllDeviceNodes(TopologyUpdateRequest( ))
                if resp.status == 200:
                    for node in resp.topologyUpdate.nodes:
                        devices[node.nodeName] = MessageToDict(node, including_default_value_fields=True, use_integers_for_enums=True)
            for device_name, device_data in devices.items():
                self.logger.debug("{} : {}".format(device_name, device_data))
                if not device_data["OMuProCUEnabled"] and OrchestratorHealthStates(device_data["orchestratorHealthState"]) != OrchestratorHealthStates.DISABLED:
                    self.logger.debug( device_name + ": disabled")
                    with grpc.insecure_channel(GLOBALVIEW_ADDRESS) as channel:
                        stub = TopologyUpdateCommunicatorStub(channel)
                        resp : TopologyUpdateResponse = stub.SetDeviceNodeStatus(TopologyUpdateRequest(
                            topologyUpdate=TopologyUpdate(
                                nodes=[
                                    Node(
                                        nodeName=device_name,
                                        orchestratorHealthState=OrchestratorHealthStates.DISABLED.value
                                    )]
                            )
                        ))
                    continue
                elif OrchestratorHealthStates(device_data["orchestratorHealthState"]) == OrchestratorHealthStates.DISABLED:
                    self.logger.debug( device_name + ": disabled")
                    continue
                try:
                    resp = None
                    self.logger.debug("Trying to connect to {}...".format(device_data["OMuProCUAddress"]))
                    with grpc.insecure_channel(device_data["OMuProCUAddress"]) as channel:
                        stub = DeploymentCommunicatorStub(channel)
                        resp : DeploymentResponse = stub.CheckHealth(DeploymentRequest())
                    if resp.status == 200:
                        self.logger.debug( device_name + ": healthy")
                        with grpc.insecure_channel(GLOBALVIEW_ADDRESS) as channel:
                            stub = TopologyUpdateCommunicatorStub(channel)
                            resp : TopologyUpdateResponse = stub.SetDeviceNodeStatus(TopologyUpdateRequest(
                                topologyUpdate=TopologyUpdate(
                                    nodes=[
                                        Node(
                                            nodeName=device_name,
                                            orchestratorHealthState=OrchestratorHealthStates.RUNNING.value
                                        )]
                                )
                            ))
                            resp : TopologyUpdateResponse = stub.GetDeviceNode(TopologyUpdateRequest(
                                topologyUpdate=TopologyUpdate(
                                    nodes=[
                                        Node(
                                            nodeName=device_name
                                        )
                                    ]
                                )
                            ))
                            device_data = MessageToDict(resp.topologyUpdate.nodes[0], including_default_value_fields=True, use_integers_for_enums=True)
                            self.logger.debug("{} : {}".format(device_name, device_data))
                    else:
                        # Unhealthy/Error state
                        self.logger.warn(device_name + ": " + resp.message)
                    if OrchestratorHealthStates(device_data["orchestratorHealthState"]) != OrchestratorHealthStates.RUNNING and device_data["OMuProCUEnabled"]:
                        self.logger.warning("OMuProCU which should be running on Device {} has state {}".format(device_name, OrchestratorHealthStates(device_data["orchestratorHealthState"]).name))
                    continue
                except grpc.RpcError as err:
                    if err.code() == grpc.StatusCode.UNAVAILABLE:
                        self.logger.error(device_name + ": not reachable")
                        # self.global_view.set_device_node_status(device_name, OrchestratorHealthStates.OFFLINE)
                        with grpc.insecure_channel(GLOBALVIEW_ADDRESS) as channel:
                            stub = TopologyUpdateCommunicatorStub(channel)
                            resp : TopologyUpdateResponse = stub.SetDeviceNodeStatus(TopologyUpdateRequest(
                                topologyUpdate=TopologyUpdate(
                                    nodes=[
                                        Node(
                                            nodeName=device_name,
                                            orchestratorHealthState=OrchestratorHealthStates.OFFLINE.value
                                        )]
                                )
                            ))
                    elif err.code() == grpc.StatusCode.CANCELLED:
                        self.logger.debug(device_name + ": Connection cancelled")
                    continue
            time.sleep(self.sleep_interval)
        self.logger.debug("Hardware Monitor Loop terminated.")

    def get_md_deployment_device_state(self, name, tenantId, tenantFuncName):
        """
        Get the MD deployment status of a device.

        Args:
            name (str): The name of the device.
            tenantId (str): The ID of the tenant.
            tenantFuncName (str): The functional name of the tenant.

        Returns:
            DeploymentStatus: The deployment status of the device.
        """
        with grpc.insecure_channel(GLOBALVIEW_ADDRESS) as channel:
            stub = TopologyUpdateCommunicatorStub(channel)
            resp : TopologyUpdateResponse = stub.GetDeviceNodeMDDeploymentStatus(
                TopologyUpdateRequest(
                    deploymentNodeUpdates=[
                        DeploymentNodeUpdate(
                            tenantMetadatas=[
                                TenantMetadata(
                                    tenantId=tenantId,
                                    tenantFuncName=tenantFuncName
                                )
                            ],
                            node = Node(
                                nodeName=name
                            )
                        )
                    ]
                )
            )
            if resp.status == 200:
                self.logger.debug("Get MD Deployment Status of {} ({}) @ {}: {} (Parsed: {})".format(tenantFuncName, tenantId, name, resp.deploymentNodeUpdates[0].mdDeploymentStatus, DeploymentStatus(resp.deploymentNodeUpdates[0].mdDeploymentStatus)))
                return DeploymentStatus(resp.deploymentNodeUpdates[0].mdDeploymentStatus)
            else:
                return DeploymentStatus.UNSPECIFIED
            
    def set_md_deployment_state_by_device_deployment_state(self, name, tenantId, tenantFuncName, deploymentState : DeploymentStatus, mdDeploymentState : DeploymentStatus):
        """
        Sets the deployment state of the MD (Managed Device) based on the deployment state of the device.

        Args:
            name (str): The name of the device.
            tenantId (str): The ID of the tenant.
            tenantFuncName (str): The functional name of the tenant.
            deploymentState (DeploymentStatus): The deployment state of the device.
            mdDeploymentState (DeploymentStatus): The current deployment state of the MD.

        Returns:
            None

        """
        if deploymentState == DeploymentStatus.VALIDATING or deploymentState == DeploymentStatus.SCHEDULED or deploymentState == DeploymentStatus.NOS_UPDATED or deploymentState == DeploymentStatus.IN_UPDATED:
            self.set_md_deployment_device_state(name, tenantId, tenantFuncName, DeploymentStatus.UPDATING)
        elif (deploymentState == DeploymentStatus.DELETED or deploymentState == DeploymentStatus.RUNNING or deploymentState == DeploymentStatus.FAILED) and mdDeploymentState != DeploymentStatus.SCHEDULED:
            self.set_md_deployment_device_state(name, tenantId, tenantFuncName, deploymentState)
        self.logger.info("MDDeployment Status of {} ({}) @ {}: {}".format(tenantFuncName, tenantId, name, self.get_md_deployment_device_state(name, tenantId, tenantFuncName)))

    def set_md_deployment_device_state(self, name, tenantId, tenantFuncName, mdDeploymentStatus : DeploymentStatus):
        """
        Sets the MD deployment device state for a given device.

        Args:
            name (str): The name of the device.
            tenantId (str): The ID of the tenant.
            tenantFuncName (str): The functional name of the tenant.
            mdDeploymentStatus (DeploymentStatus): The deployment status of the MD.

        Returns:
            None
        """
        with grpc.insecure_channel(GLOBALVIEW_ADDRESS) as channel:
            stub = TopologyUpdateCommunicatorStub(channel)
            resp : TopologyUpdateResponse = stub.SetDeviceNodeMDDeploymentStatus(
                TopologyUpdateRequest(
                    deploymentNodeUpdates=[DeploymentNodeUpdate(
                        tenantMetadatas=[
                            TenantMetadata(
                                tenantId=tenantId,
                                tenantFuncName=tenantFuncName
                            )
                        ],
                        node = Node(
                            nodeName=name
                        ),
                        mdDeploymentStatus=mdDeploymentStatus.value
                    )])
            )

    def deployment_health_monitor_loop(self):
        """
        Monitors the health of deployments on devices.

        This method continuously checks the health status of deployments on devices.
        It retrieves the deployment status from the global view and updates the
        corresponding metadata deployment status. If any errors occur during the
        process, appropriate error messages are logged.

        Note: This method runs in a loop until the `deployment_health_monitor_running`
        flag is set to False.

        Returns:
            None
        """
        time.sleep(5)
        while self.deployment_health_monitor_running:
            devices = {}
            with grpc.insecure_channel(GLOBALVIEW_ADDRESS) as channel:
                stub = TopologyUpdateCommunicatorStub(channel)
                resp : TopologyUpdateResponse = stub.GetAllDeviceNodes(TopologyUpdateRequest())
                if resp.status == 200:
                    for node in resp.topologyUpdate.nodes:
                        devices[node.nodeName] = MessageToDict(node, including_default_value_fields=True, use_integers_for_enums=True)
            for device_name, device_data in devices.items():
                self.logger.debug("{} : {}".format(device_name, device_data))
                if OrchestratorHealthStates(device_data["orchestratorHealthState"]) != OrchestratorHealthStates.RUNNING and device_data["OMuProCUEnabled"]:
                    self.logger.warning("OMuProCU which should be running on Device {} has state {}".format(device_name, device_data["orchestratorHealthState"]))
                    continue
                elif not device_data["OMuProCUEnabled"]:
                    continue
                for deployment in device_data["deployments"]:
                    try:
                        resp = None
                        self.logger.debug("Trying to connect to {}...".format(device_data["OMuProCUAddress"]))
                        with grpc.insecure_channel(device_data["OMuProCUAddress"]) as channel:
                            stub = DeploymentCommunicatorStub(channel)
                            resp : DeploymentResponse = stub.GetDeploymentMonitorStatus(DeploymentRequest(
                                tenantMetadata = TenantMetadata(
                                    tenantId = deployment["tenantId"],
                                    tenantFuncName = deployment["tenantFuncName"],
                                )
                            ))
                            if resp.status == 200:
                                deploymentStatus = DeploymentStatus(resp.deploymentStatus)
                                mdDeploymentStatus = DeploymentStatus.UNSPECIFIED
                                with grpc.insecure_channel(GLOBALVIEW_ADDRESS) as channel:
                                    stub = TopologyUpdateCommunicatorStub(channel)
                                    resp : TopologyUpdateResponse = stub.GetDeviceNodeMDDeploymentStatus(
                                        TopologyUpdateRequest(
                                            deploymentNodeUpdates=[
                                                DeploymentNodeUpdate(
                                                    tenantMetadatas=[
                                                        TenantMetadata(
                                                            tenantId=deployment["tenantId"],
                                                            tenantFuncName=deployment["tenantFuncName"]
                                                        )
                                                    ],
                                                    node=Node(
                                                        nodeName=device_name
                                                    )
                                                )
                                            ]
                                        )
                                    )
                                    if resp.status == 200:
                                        mdDeploymentStatus = DeploymentStatus(resp.deploymentNodeUpdates[0].mdDeploymentStatus)
                                    else:
                                        self.logger.error("Error while getting state for {} of Tenant ID {}: {} (Status: {})".format(deployment["tenantFuncName"], deployment["tenantId"], resp.message, resp.status))
                                self.set_md_deployment_state_by_device_deployment_state(device_name, deployment["tenantId"], deployment["tenantFuncName"], DeploymentStatus(deploymentStatus), mdDeploymentStatus)
                                # FIXME: This is only working for one deployment!
                                self.logger.info( "Deployment @ " + device_name + ": " + str(DeploymentStatus(deploymentStatus)))
                                with grpc.insecure_channel(GLOBALVIEW_ADDRESS) as channel:
                                    stub = TopologyUpdateCommunicatorStub(channel)
                                    resp : TopologyUpdateResponse = stub.SetDeviceNodeDeploymentStatus(
                                        TopologyUpdateRequest(
                                            deploymentNodeUpdates=[DeploymentNodeUpdate(
                                                tenantMetadatas=[
                                                    TenantMetadata(
                                                        tenantId=deployment["tenantId"],
                                                        tenantFuncName=deployment["tenantFuncName"]
                                                    )
                                                ],
                                                node=Node(
                                                    nodeName=device_name
                                                ),
                                                deploymentStatus=deploymentStatus.value
                                            )]
                                        )
                                    )                                        
                                    resp : TopologyUpdateResponse = stub.GetDeviceNode(TopologyUpdateRequest(
                                        topologyUpdate=TopologyUpdate(
                                            nodes=[
                                                Node(
                                                    nodeName=device_name
                                                )
                                            ]
                                        )
                                    ))
                                    device_data = MessageToDict(resp.topologyUpdate.nodes[0])
                                # device_data = self.global_view.get_device_node(device_name)
                                self.logger.debug("{} : {}".format(device_name, device_data))
                            else:
                                # Unhealthy/Error state
                                if self.get_md_deployment_device_state(device_name, deployment["tenantId"], deployment["tenantFuncName"]) != DeploymentStatus.SCHEDULED and \
                                    self.get_md_deployment_device_state(device_name, deployment["tenantId"], deployment["tenantFuncName"]) != DeploymentStatus.VALIDATING and \
                                    self.get_md_deployment_device_state(device_name, deployment["tenantId"], deployment["tenantFuncName"]) != DeploymentStatus.FAILED:
                                    self.set_md_deployment_device_state(device_name, deployment["tenantId"], deployment["tenantFuncName"], DeploymentStatus.FAILED)
                                    self.logger.error("Error while getting deployment state at device node {} for {} of Tenant ID {}: {} (Status: {})".format(device_name, deployment["tenantFuncName"], deployment["tenantId"], resp.message, resp.status))
                    except grpc.RpcError as err:
                        # self.logger.exception(err, exc_info=True)
                        if err.code() == grpc.StatusCode.UNAVAILABLE:
                            self.logger.error("Could not request deployment status on " + device_name + ": not reachable")
                            resp : TopologyUpdateResponse = stub.SetDeviceNodeStatus(TopologyUpdateRequest(
                            topologyUpdate=TopologyUpdate(
                                nodes=[
                                    Node(
                                        nodeName=device_name,
                                        orchestratorHealthState=OrchestratorHealthStates.OFFLINE.value
                                    )]
                            )
                            ))
                        elif err.code() == grpc.StatusCode.CANCELLED:
                            self.logger.debug(device_name + ": Connection cancelled")
                        else:
                            self.logger.exception(err, exc_info=True)
                        continue
            time.sleep(self.sleep_interval)
        self.logger.debug("Deployment Node Health Monitor terminated.")