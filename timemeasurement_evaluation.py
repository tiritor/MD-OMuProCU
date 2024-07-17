import ast
import glob
from statistics import mean
import pandas as pd

output = """
Deployment time: {} secs
Validation time: {} secs
Schedule time: {} secs
Deployment node times: {}
"""
mean_output = """
MEAN Times:
-----------

Deployment time: {} secs
Validation time: {} secs
Schedule time: {} secs
Deployment node times: {}
"""

files = glob.glob('timemeasurements-*.csv')
files.sort(reverse=True)

# Load the CSV data
print("Loading file {}...".format(files[0]))
data = pd.read_csv(files[0], delimiter=";", index_col=False)

deployment_times = []
validation_times = []
schedule_times = []
deployment_node_times = {}

print("Processing data...")

for row in data.iterrows():
    deployment_time = row[1]["deploymentEnd"] - row[1]["deploymentStart"]
    validation_time = row[1]["validationEndTimestamp"] - row[1]["submissionTimestamp"]
    schedule_time = row[1]["deploymentStart"] - row[1]["scheduleTimestamp"] 

    deployment_times.append(deployment_time)
    validation_times.append(validation_time)
    schedule_times.append(schedule_time)
    nodeTimestamps = row[1]["nodesTimestamps"]
    nodeTimestamps_dict = ast.literal_eval(ast.literal_eval(nodeTimestamps[1:]))
    for key, time_tuple in nodeTimestamps_dict.items():
        if key in deployment_node_times.keys():
            deployment_node_times[key].append(time_tuple[1] - time_tuple[0])
        else:
            deployment_node_times[key] = [time_tuple[1] - time_tuple[0]]
    

#     # Print the data
#     print(output.format(deployment_time, validation_time, schedule_time, str(deployment_node_times)))
# deployment_time = data["deploymentEnd"] - data["deploymentStart"]
# validation_time = data["validationEndTimestamp"] - data["deploymentStart"]
# schedule_time = data["scheduleTimestamp"] - data["validationEndTimestamp"]
# deployment_node_times = {
#     key : tuple[1] - tuple[0] for key, tuple in ast.literal_eval(data["nodes"]).items()
# }

print("Printing data...\n")

# Print the data
print(output.format(deployment_times, validation_times, schedule_times, deployment_node_times))
print(mean_output.format(mean(deployment_times), mean(validation_times), mean(schedule_times), {key: mean(value) for key, value in deployment_node_times.items()}))
