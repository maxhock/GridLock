
import helics as h
import logging
import numpy as np
import os


logger = logging.getLogger(__name__)
logger.addHandler(logging.StreamHandler())
logger.setLevel(logging.WARNING)


def calculate_net_power_demand(fed, subid, sub_count, grantedtime):
    """
    Calculate the net power demand by combining power from all subscribed nodes.
    
    :param fed: HELICS federate
    :param subid: Dictionary of subscription IDs
    :param sub_count: Number of subscriptions
    :param grantedtime: Current simulation time
    :return: Net power demand and individual power demands dictionary
    """
    power_demand = {}
    
    # Get the total power demand of all nodes
    for j in range(0, sub_count):
        logger.debug(f"Node {j + 1} time {grantedtime}")
        # Count the power of all nodes in the co-simulation
        power_demand[j] = h.helicsInputGetDouble((subid[j]))
        logger.debug(f"\tPower demand: {power_demand[j]:.2f} from"
                    f" input {h.helicsInputGetTarget(subid[j])}")

    # Combine the power demand from all nodes
    net_demand = 0
    for j in range(0, sub_count):
        net_demand += power_demand[j]
    
    return net_demand, power_demand


def destroy_federate(fed):
    """
    As part of ending a HELICS co-simulation it is good housekeeping to
    formally destroy a federate. Doing so informs the rest of the
    federation that it is no longer a part of the co-simulation and they
    should proceed without it (if applicable). Generally this is done
    when the co-simulation is complete and all federates end execution
    at more or less the same wall-clock time.

    :param fed: Federate to be destroyed
    :return: (none)
    """
    # Adding extra time request to clear out any pending messages to avoid
    #   annoying errors in the broker log. Any message are tacitly disregarded.
    grantedtime = h.helicsFederateRequestTime(fed, h.HELICS_TIME_MAXTIME)
    status = h.helicsFederateDisconnect(fed)
    h.helicsFederateDestroy(fed)
    logger.info("Federate finalized")

if __name__ == "__main__":
    np.random.seed(1490)

    ##############  Registering  federate from json  ##########################
    fed = h.helicsCreateValueFederateFromConfig("/config/GridSimulationConfig.json")
    federate_name = h.helicsFederateGetName(fed)
    logger.info(f"Created federate {federate_name}")

    sub_count = h.helicsFederateGetInputCount(fed)
    logger.debug(f"\tNumber of subscriptions: {sub_count}")
    pub_count = h.helicsFederateGetPublicationCount(fed)
    logger.debug(f"\tNumber of publications: {pub_count}")

    # Diagnostics to confirm JSON config correctly added the required
    #   publications, and subscriptions.
    subid = {}
    for i in range(0, sub_count):
        subid[i] = h.helicsFederateGetInputByIndex(fed, i)
        sub_name = h.helicsInputGetTarget(subid[i])
        logger.debug(f"\tRegistered subscription---> {sub_name}")

    pubid = {}
    for i in range(0, pub_count):
        pubid[i] = h.helicsFederateGetPublicationByIndex(fed, i)
        pub_name = h.helicsPublicationGetName(pubid[i])
        logger.debug(f"\tRegistered publication---> {pub_name}")

    ##############  Entering Execution Mode  ##################################
    h.helicsFederateEnterExecutingMode(fed)
    logger.info("Entered HELICS execution mode")



    hours = 24
    total_interval = int(60 * 60 * hours)
    update_interval = int(h.helicsFederateGetTimeProperty(fed, h.HELICS_PROPERTY_TIME_PERIOD))
    grantedtime = 0

    # Data collection lists
    time_sim = []
    power = []

    # Blocking call for a time request at simulation time 0
    initial_time = 0
    logger.debug(f"Requesting initial time {initial_time}")
    grantedtime = h.helicsFederateRequestTime(fed, initial_time)
    logger.debug(f"Granted time {grantedtime}")

    # Apply initial charging voltage
    # for j in range(0, pub_count):
    #     h.helicsPublicationPublishDouble(pubid[j], 0)
    #     logger.debug(f"\tPublishing {h.helicsPublicationGetName(pubid[j])} of 0.0"
    #                 f" at time {grantedtime}")

    ########## Main co-simulation loop ########################################
    # As long as granted time is in the time range to be simulated...
    while grantedtime < total_interval:

        # Time request for the next physical interval to be simulated
        requested_time = grantedtime + update_interval
        logger.debug(f"Requesting time {requested_time}")
        grantedtime = h.helicsFederateRequestTime(fed, requested_time)
        logger.debug(f"Granted time {grantedtime}")

        # Calculate net power demand from all nodes
        net_demand, power_demand = calculate_net_power_demand(fed, subid, sub_count, grantedtime)

        # Publish updated Transformer power
        for j in range(0, pub_count):
            logger.debug(f"Node {j + 1} time {grantedtime}")
            # Publish updated Transformer power
            h.helicsPublicationPublishDouble(pubid[j], net_demand)
            logger.debug(f"\tPublishing {h.helicsPublicationGetName(pubid[j])} of {net_demand:.2f}"
                         f" at time {grantedtime}")

        # Data collection vectors
        time_sim.append(grantedtime)
        power.append(net_demand)

    # Cleaning up HELICS stuff once we've finished the co-simulation.
    destroy_federate(fed)
