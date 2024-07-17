from update_strategy import UpdateStrategy


DEPLOYMENT_ADDRESS = "0.0.0.0:49009"
GLOBALVIEW_ADDRESS = "0.0.0.0:49009"


INFRASTRUCTURE_FILE_PATH = "conf/topology-5G.json" # Topology file for 5G Setup in the Evaluation for the latest paper
# INFRASTRUCTURE_FILE_PATH = "conf/topology-LAG.json" # Topology file for LAG Setup for the last two papers

UPDATE_ORDER_STRATEGY = UpdateStrategy.DATACENTER_DEVICES_FIRST