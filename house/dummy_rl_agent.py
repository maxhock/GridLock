import helics as h
import random
import time

# --- Configuration (remains the same) ---
federate_name = "dummy_rl_agent"
federate_period_sec = 900
simulation_duration_sec = 24 * 3600

action_publication_topics = [
    "action.battery_1.normalized_power",
    "action.battery_2.normalized_power",
    "action.battery_4.normalized_power",
]
observation_subscription_topics = [
    "obs.battery_1.electrical_flow",
    "obs.battery_1.electrical_soc",
    "obs.battery_2.electrical_flow",
    "obs.battery_2.electrical_soc",
    "obs.battery_4.electrical_flow",
    "obs.battery_4.electrical_soc",
    "obs.data",
    "obs.thermal.temperature",
    "reward",
    "done",
]

def publish_random_actions(pub_objects):
    """Helper function to publish random values to all action topics."""
    print(f"[{federate_name}] Sending random actions:")
    for topic, pub in pub_objects.items():
        random_action = random.uniform(-1.0, 1.0)
        h.helicsPublicationPublishDouble(pub, random_action)
        print(f"  - Published to '{topic}': {random_action:.4f}")

def run_dummy_agent():
    fed = None
    try:
        print(f"[{federate_name}] Creating Federate...")
        fedinfo = h.helicsCreateFederateInfo()
        h.helicsFederateInfoSetCoreTypeFromString(fedinfo, "zmq")
        h.helicsFederateInfoSetCoreInitString(fedinfo, "--federates=1")
        # ###################### CHANGE 1: Set Uninterruptible Flag ######################
        # This prevents the federate from exiting early if it receives a message
        # intended for a future time. It's good practice for this pattern.
        h.helicsFederateInfoSetFlagOption(fedinfo, h.helics_flag_uninterruptible, True)
        h.helicsFederateInfoSetTimeProperty(fedinfo, h.helics_property_time_delta, federate_period_sec)
        h.helicsFederateInfoSetIntegerProperty(fedinfo, h.helics_property_int_log_level, 1)

        fed = h.helicsCreateValueFederate(federate_name, fedinfo)
        print(f"[{federate_name}] Federate created.")

        # --- Register Publications and Subscriptions (no changes here) ---
        pub_objects = {}
        for topic in action_publication_topics:
            pub_objects[topic] = h.helicsFederateRegisterGlobalPublication(fed, topic, h.HELICS_DATA_TYPE_DOUBLE, "")
        sub_objects = {}
        for topic in observation_subscription_topics:
            sub_objects[topic] = h.helicsFederateRegisterSubscription(fed, topic, "")
        print(f"[{federate_name}] Publications and Subscriptions registered.")

        # --- Enter Execution Mode ---
        print(f"[{federate_name}] Entering execution mode...")
        h.helicsFederateEnterExecutingMode(fed)
        print(f"[{federate_name}] Execution mode entered.")

        # ###################### CHANGE 2: Initial Publication ######################
        # Publish the first set of actions for t=0 BEFORE the loop starts.
        # This ensures the environment has a value to read at the very beginning.
        publish_random_actions(pub_objects)
        
        current_time = 0

        # --- Main Simulation Loop ---
        while current_time <= simulation_duration_sec:
            # Request the next time step. The federate will block here until granted.
            requested_time = current_time + federate_period_sec
            current_time = h.helicsFederateRequestTime(fed, requested_time)
            
            print(f"\n--- Simulation Time: {current_time / 3600:.2f} hours ---")

            # 1. GET DATA (from subscriptions) - This is data valid for the CURRENT time
            print(f"[{federate_name}] Receiving data:")
            for topic, sub in sub_objects.items():
                if h.helicsInputIsUpdated(sub):
                    value_str = h.helicsInputGetString(sub)
                    print(f"  - Received from '{topic}': {value_str}")

            # 2. SEND DATA (to publications) - This is data for the NEXT time step
            if current_time < simulation_duration_sec:
                 publish_random_actions(pub_objects)

    except Exception as e:
        print(f"[{federate_name}] An error occurred: {e}")
    finally:
        if fed is not None:
            h.helicsFederateFinalize(fed)
            print(f"[{federate_name}] Federate finalized.")
        h.helicsCloseLibrary()
        print(f"[{federate_name}] HELICS library closed.")


if __name__ == "__main__":
    run_dummy_agent()