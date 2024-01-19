
import time
from orchestrator_utils.md_orchestrator_client import MDOMuProCUClient

from conf.config import DEPLOYMENT_ADDRESS

MDTDC_TEST_FILE = "./mdtdc-files/MD-TDC-1.yaml"
SLEEP_TIME = 135
DEPLOYMENT_ADDRESS = "127.0.0.1:" + DEPLOYMENT_ADDRESS.split(":")[-1]
ITERATION_COUNT = 3

print(DEPLOYMENT_ADDRESS)
print ("This script takes ~ {} secs ({} mins)".format(SLEEP_TIME * 2 * ITERATION_COUNT, (SLEEP_TIME * 2 * ITERATION_COUNT) / 60 ))

client = MDOMuProCUClient(DEPLOYMENT_ADDRESS)

for i in range(ITERATION_COUNT):
    print ("--- Iteration {} ---".format(i))
    print(client.create(MDTDC_TEST_FILE))

    print("Waiting some time ({} secs)".format(SLEEP_TIME))
    time.sleep(SLEEP_TIME)

    print(client.delete(MDTDC_TEST_FILE))
    print("Waiting some time ({} secs)".format(SLEEP_TIME))
    time.sleep(SLEEP_TIME)

client.close()