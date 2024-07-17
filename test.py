
import time
import argparse

from orchestrator_utils.md_orchestrator_client import MDOMuProCUClient

from conf.config import DEPLOYMENT_ADDRESS

MDTDC_TEST_FILE = "./mdtdc-files/MD-TDC-1.yaml"
SLEEP_TIME = 140
DEPLOYMENT_ADDRESS = "127.0.0.1:" + DEPLOYMENT_ADDRESS.split(":")[-1]
ITERATION_COUNT = 10

def experiment(deployment_address=DEPLOYMENT_ADDRESS, mdtdc_test_file=MDTDC_TEST_FILE, sleep_time=SLEEP_TIME, iteration_count=ITERATION_COUNT):
    print(deployment_address)
    client = MDOMuProCUClient(deployment_address)
    print ("This script takes ~ {} secs ({} mins)".format(sleep_time * 2 * iteration_count, (sleep_time * 2 * iteration_count) / 60 ))
    for i in range(iteration_count):
        print ("--- Iteration {} ---".format(i))
        print(client.create(mdtdc_test_file))

        print("Waiting some time ({} secs)".format(sleep_time))
        time.sleep(sleep_time)

        print(client.delete(mdtdc_test_file))
        print("Waiting some time ({} secs)".format(sleep_time))
        time.sleep(sleep_time)

    client.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the experiment.")
    parser.add_argument('-d', '--deployment_address', default=DEPLOYMENT_ADDRESS, help='Deployment address')
    parser.add_argument('-f', '--mdtdc_test_file', default=MDTDC_TEST_FILE, help='MDTDC test file path')
    parser.add_argument('-t', '--sleep_time', type=int, default=SLEEP_TIME, help='Sleep time in seconds')
    parser.add_argument('-i', '--iteration_count', type=int, default=ITERATION_COUNT, help='Number of iterations')

    args = parser.parse_args()

    DEPLOYMENT_ADDRESS = args.deployment_address
    MDTDC_TEST_FILE = args.mdtdc_test_file
    SLEEP_TIME = args.sleep_time
    ITERATION_COUNT = args.iteration_count

    experiment(DEPLOYMENT_ADDRESS, MDTDC_TEST_FILE, SLEEP_TIME, ITERATION_COUNT)