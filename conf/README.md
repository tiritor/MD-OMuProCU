# Configuration 

This directory holds the configuration of MD-OMuProCU which consists of:

- Topology Files
- MD-OMuProCU generic config
- Extern blacklist
- Tenant Security Configuration


## Topology Files

There are two different topology configurations for our testbed available:

### [topology-5G.json](conf/topology-5G.json)

This is the testbed setup used in the paper `Resilient Multi-Tenant Code Updates for Adaptive Network State Changes`.
The edge switches are configured as different routes between the hosts. 

### [topology-lag.json](conf/topology-lag.json)

This is the testbed setup used in the paper [`Orchestrating Multi-Tenant Code Updates Across Multiple Programmable Switches`](https://ieeexplore.ieee.org/document/10575368) and described in the resilience setup `Resilient Multi-Tenant Code Updates for Adaptive Network State Changes`.
The edge switches are configured as LAGs between the hosts. 