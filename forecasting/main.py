import time
import helics as h
import requests
import json
from typing import Tuple, List

from forecasting import ForecastInput, predict_agg_load


def create_federate() -> (
    Tuple[h.HelicsFederate, List[Tuple[int, h.HelicsInput]], h.HelicsPublication]
):
    fed_info = h.helicsCreateFederateInfo()
    h.helicsFederateInfoSetCoreName(fed_info, "Forecaster")
    h.helicsFederateInfoSetCoreTypeFromString(fed_info, "zmq")
    h.helicsFederateInfoSetBroker(fed_info, "broker")  # Add broker connection

    h.helicsFederateInfoSetFlagOption(fed_info, h.HELICS_FLAG_ENABLE_INIT_ENTRY, True)

    fed = h.helicsCreateValueFederate("Forecasting", fed_info)

    # TODO for now only subscribe
    load_subs = []  # list of (pp_load_idx, helics_input)
    for pp_idx in range(13):  # TODO hardcoded for now
        sub_key = f"house_{pp_idx}/house_load"
        sub = h.helicsFederateRegisterSubscription(fed, sub_key, "double")
        load_subs.append((pp_idx, sub))

    # Create publication for total load forecast
    forecast_pub = h.helicsFederateRegisterPublication(
        fed, "Forecaster/total_load_forecast", h.HELICS_DATA_TYPE_DOUBLE, ""
    )

    print("Registered forecasting publication: Forecaster/total_load_forecast")

    return fed, load_subs, forecast_pub


def run_federate(
    fed: h.HelicsFederate,
    load_subs: List[Tuple[int, h.HelicsInput]],
    forecast_pub: h.HelicsPublication,
) -> None:
    print("Forecasting federate entering execution mode...")
    h.helicsFederateEnterExecutingMode(fed)
    print("Successfully entered execution mode!")

    # Print all subscription keys we're listening to
    for pp_idx, sub in load_subs:
        key = h.helicsInputGetName(sub)
        print(f"Listening to: {key}")

    # TODO should be centralised?
    current_time = 0
    end_time = 82800 + 3600  # You can adjust this as needed
    time_step = 3600

    while current_time < end_time:
        print(f"\n=== FORECASTING time step: {current_time} ===")

        # a. Request next time step
        current_time = h.helicsFederateRequestTime(fed, current_time + time_step)
        print(f"Granted time: {current_time}")

        current_loads = []
        # b. Update p_mw for each load from HELICS subscriptions
        # TODO error handling
        for pp_idx, sub in load_subs:
            if h.helicsInputIsUpdated(sub):
                value = h.helicsInputGetDouble(sub) / 1000
                # Only update if a numeric value is provided
                if value is not None:

                    current_loads.append(value)
                    print(
                        f"Set load {pp_idx} p_mw to {value} from HELICS subscription."
                    )

                # FORECAST: Generate prediction
        forecast_input = ForecastInput(
            current_loads=current_loads, current_time=current_time, time_step=time_step
        )

        forecast_output = predict_agg_load(forecast_input=forecast_input)
        h.helicsPublicationPublishDouble(
            forecast_pub, forecast_output.total_load_forecast
        )

        print(
            f"📤 Published total load forecast: {forecast_output.total_load_forecast:.6f} MW "
        )

    h.helicsFederateFinalize(fed)
    print("Federate finalized.")


def cleanup_federate(fed: h.HelicsFederate) -> None:
    h.helicsFederateDisconnect(fed)
    # h.helicsCloseLibrary()
    print("Federate freed and HELICS library closed.")


def main() -> None:
    fed, load_subs, forecast_pub = create_federate()
    try:

        run_federate(fed, load_subs, forecast_pub)
    except Exception as e:
        print(f"An error occurred during federate execution: {e}")
    finally:
        cleanup_federate(fed)


if __name__ == "__main__":
    main()
