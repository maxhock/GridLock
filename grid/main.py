
import helics as h
import pandapower as pp
import pandapower.networks as pn
import argparse
import os


def parse_args():
    parser = argparse.ArgumentParser(description="Grid federate for HELICS co-simulation.")
    parser.add_argument("--grid_file", type=str, default=None, help="Path to pandapower Excel file.")
    args, unknown = parser.parse_known_args()
    return args



def create_federate(grid_file=None):
    # 1. Create the pandapower network
    net = None
    if grid_file:
        if os.path.isfile(grid_file):
            print(f"Loading pandapower network from Excel: {grid_file}")
            net = pp.from_excel(grid_file)
            print("Custom pandapower network provided.")
        else:
            print(f"ERROR: grid_file '{grid_file}' not found. Using built-in Kerber Landnetz Freileitung 1 network.")
    if net is None:
        net = pn.create_kerber_landnetz_freileitung_1()
        print("Kerber Landnetz Freileitung 1 network created.")

    # 2. Create the HELICS federate from config file
    fed = h.helicsCreateValueFederateFromConfig("/config/helics_grid_config.json")
    print("Created HELICS federate in code.")

    # 3. Register all subscriptions (for p_mw setpoints for each load)
    load_index_list = list(net.load.index)
    load_subs = []  # list of (pp_load_idx, helics_input)
    for pp_idx in load_index_list:
        sub_key = f"house_{pp_idx}/house_load"
        sub = h.helicsFederateRegisterSubscription(fed, sub_key, "double")
        load_subs.append((pp_idx, sub))
    print(f"Registered {len(load_subs)} HELICS subscriptions for load p_mw setpoints.")
    print("Subscriptions registered:")
    for pp_idx, sub in load_subs:
        key = h.helicsInputGetName(sub)
        print(f"  Subscription: {key}")

    # 4. Register all publications (for ext_grid p_mw)
    ext_grid_index_list = list(net.ext_grid.index)
    ext_grid_pubs = []  # list of (pp_ext_idx, helics_pub)
    for pp_idx in ext_grid_index_list:
        pub_key = f"Grid/transformer_power"
        pub = h.helicsFederateRegisterGlobalPublication(fed, pub_key, h.HELICS_DATA_TYPE_DOUBLE, "MW")
        ext_grid_pubs.append((pp_idx, pub))
    print(f"Registered {len(ext_grid_pubs)} HELICS publications for ext_grid p_mw.")
    print("Publications registered:")
    for pp_idx, pub in ext_grid_pubs:
        key = h.helicsPublicationGetName(pub)
        print(f"  Publication: {key}")

    # Return all state needed for simulation
    return fed, net, load_subs, ext_grid_pubs

def run_federate(fed, net, load_subs, ext_grid_pubs):
    h.helicsFederateEnterExecutingMode(fed)
    print("Federate entered execution mode.")

    current_time = 0
    end_time = 82800+3600  # You can adjust this as needed
    time_step = 3600

    while current_time < end_time:
        print(f"\n=== HELICS time step: {current_time} ===")

        # a. Request next time step
        current_time = h.helicsFederateRequestTime(fed, current_time + time_step)
        print(f"Granted time: {current_time}")

        # b. Update p_mw for each load from HELICS subscriptions
        for pp_idx, sub in load_subs:
            if h.helicsInputIsUpdated(sub):
                value = h.helicsInputGetDouble(sub) / 1000
                # Only update if a numeric value is provided
                if value is not None:
                    net.load.at[pp_idx, "p_mw"] = float(value)
                    # print(f"Set load {pp_idx} p_mw to {value} from HELICS subscription.")

        # c. Run pandapower power flow
        pp.runpp(net, numba=False)
        print("Power flow executed.")

        # d. Publish ext_grid p_mw values to HELICS
        for pp_idx, pub in ext_grid_pubs:
            p_mw = net.res_ext_grid.at[pp_idx, "p_mw"]
            h.helicsPublicationPublishDouble(pub, float(p_mw))
            print(f"Published ext_grid {pp_idx} p_mw: {p_mw}")


    h.helicsFederateFinalize(fed)
    print("Federate finalized.")

def cleanup_federate(fed):
    h.helicsFederateDisconnect(fed)
    # h.helicsCloseLibrary()
    print("Federate freed and HELICS library closed.")

def main():
    args = parse_args()
    fed, net, load_subs, ext_grid_pubs = create_federate(args.grid_file)
    try:
        run_federate(fed, net, load_subs, ext_grid_pubs)
    except Exception as e:
        print(f"An error occurred during federate execution: {e}")
    finally:
        cleanup_federate(fed)

if __name__ == "__main__":
    main()