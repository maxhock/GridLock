import helics as h
import pandapower as pp
import pandapower.networks as pn

def create_federate():
    # 1. Create the pandapower network
    net = pn.create_kerber_landnetz_freileitung_1()
    print("Kerber Landnetz Freileitung 1 network created.")

    # 2. Create the HELICS federate in code
    # fedinfo = h.helicsCreateFederateInfo()
    # h.helicsFederateInfoSetCoreTypeFromString(fedinfo, "zmq")
    # h.helicsFederateInfoSetCoreInitString(fedinfo, "--federates=1")
    # h.helicsFederateInfoSetBroker(fedinfo, "broker")
    # h.helicsFederateInfoSetTimeProperty(fedinfo, h.helics_property_time_delta, 1.0)
    # h.helicsFederateInfoSetFlagOption(fedinfo, h.helics_flag_uninterruptible, True)
    # # h.helicsFederateInfoSetFederateName(fedinfo, "pandapower_federate")
    # fed = h.helicsCreateValueFederate("pandapower_federate", fedinfo)
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

        # a. Update p_mw for each load from HELICS subscriptions
        for pp_idx, sub in load_subs:
            if h.helicsInputIsUpdated(sub):
                value = h.helicsInputGetDouble(sub) / 1000
                # Only update if a numeric value is provided
                if value is not None:
                    net.load.at[pp_idx, "p_mw"] = float(value)
                    # print(f"Set load {pp_idx} p_mw to {value} from HELICS subscription.")

        # b. Run pandapower power flow
        pp.runpp(net, numba=False)
        print("Power flow executed.")

        # c. Publish ext_grid p_mw values to HELICS
        for pp_idx, pub in ext_grid_pubs:
            p_mw = net.res_ext_grid.at[pp_idx, "p_mw"]
            h.helicsPublicationPublishDouble(pub, float(p_mw))
            print(f"Published ext_grid {pp_idx} p_mw: {p_mw}")

        # d. Request next time step
        current_time = h.helicsFederateRequestTime(fed, current_time + time_step)
        print(f"Granted time: {current_time}")

    h.helicsFederateFinalize(fed)
    print("Federate finalized.")

def cleanup_federate(fed):
    h.helicsFederateDisconnect(fed)
    # h.helicsCloseLibrary()
    print("Federate freed and HELICS library closed.")

def main():
    fed, net, load_subs, ext_grid_pubs = create_federate()
    try:
        run_federate(fed, net, load_subs, ext_grid_pubs)
    except:
        pass
    # finally:
    #     cleanup_federate(fed)

if __name__ == "__main__":
    main()