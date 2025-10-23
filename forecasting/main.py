import helics as h
from typing import Tuple, List


def create_federate() -> Tuple[h.HelicsFederate, List[Tuple[int, h.HelicsInput]]]:
    fed_info = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreName(fed_info, "Forecaster")
    h.helicsFederateInfoSetCoreTypeFromString(fed_info, "zmq")
    h.helicsFederateInfoSetBroker(fed_info, "broker")  # Add broker connection
    fed = h.helicsCreateValueFederate(
        "Forecaster", fed_info
    )  # TODO should match corename?

    # TODO for now only subscribe
    load_subs = []  # list of (pp_load_idx, helics_input)
    for pp_idx in range(13):  # TODO hardcoded for now
        sub_key = f"house_{pp_idx}/house_load"
        sub = h.helicsFederateRegisterSubscription(fed, sub_key, "double")
        load_subs.append((pp_idx, sub))

    return fed, load_subs


def run_federate(
    fed: h.HelicsFederate, load_subs: List[Tuple[int, h.HelicsInput]]
) -> None:
    print("🚀 Forecasting federate entering execution mode...")
    h.helicsFederateEnterExecutingMode(fed)
    print("✅ Successfully entered execution mode!")
    print(f"📡 Monitoring {len(load_subs)} house load subscriptions...")

    # Print all subscription keys we're listening to
    for pp_idx, sub in load_subs:
        key = h.helicsInputGetName(sub)
        print(f"  📻 Listening to: {key}")

    # TODO should be centralised?
    current_time = 0
    end_time = 82800 + 3600  # You can adjust this as needed
    time_step = 3600

    while current_time < end_time:
        print(f"\n=== FORECASTING time step: {current_time} ===")

        # a. Request next time step
        current_time = h.helicsFederateRequestTime(fed, current_time + time_step)
        print(f"Granted time: {current_time}")

        # b. Update p_mw for each load from HELICS subscriptions
        for pp_idx, sub in load_subs:
            if h.helicsInputIsUpdated(sub):
                value = h.helicsInputGetDouble(sub) / 1000
                # Only update if a numeric value is provided
                if value is not None:
                    # net.load.at[pp_idx, "p_mw"] = float(value)
                    print(
                        f"Set load {pp_idx} p_mw to {value} from HELICS subscription."
                    )

    h.helicsFederateFinalize(fed)
    print("Federate finalized.")


def cleanup_federate(fed: h.HelicsFederate) -> None:
    h.helicsFederateDisconnect(fed)
    # h.helicsCloseLibrary()
    print("Federate freed and HELICS library closed.")


def main() -> None:

    fed, load_subs = create_federate()
    try:
        run_federate(fed, load_subs)
    except Exception as e:
        print(f"An error occurred during federate execution: {e}")
    finally:
        cleanup_federate(fed)


if __name__ == "__main__":
    # NOTES
    # What is runtime for inner loop? Should be same for all?
    main()
