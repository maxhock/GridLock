import cosim_toolbox as env
from cosim_toolbox.sims import DockerRunner, FederateConfig
from cosim_toolbox.dbms import create_metadata_manager

# Broker is always the first service at 10.5.0.2 in the Docker network.
BROKER_IP = "10.5.0.2"


def _service(
    name: str, image: str, params: list, cnt: int, depends: str = None
) -> str:
    """Builds the "service" part of the docker-compose.yaml

    Args:
        name (str): Name of the service being defined
        image (str): Name of the image on which the service runs
        params (list): Environment in image the service utilizes
        cnt (int): Index used to define the IP for the service in the Docker virtual network
        depends (str, optional): Dependency for service being defined. Defaults to None.

    Returns:
        str: Docker-compose service block as a string.
    """
    _svc = "  " + name + ":\n"
    _svc += '    image: "' + image + '"\n'
    _svc += '    build: "../federates/' + image + '"\n'
    if params[0] != "":
        _svc += "    environment:\n"
        _svc += params[0]
    # _svc += "    user: worker\n"
    # _svc += "    working_dir: /home/worker/case\n"
    _svc += "    volumes:\n"
    _svc += "      - ../data:/data\n"
    _svc += "      - ../meta_store:/app/meta_store\n"
    if depends is not None:
        _svc += "    depends_on:\n"
        _svc += "      - " + depends + "\n"
    _svc += "    networks:\n"
    _svc += "      cst_net:\n"
    _svc += "        ipv4_address: 10.5.0." + str(cnt) + "\n"
    _svc += '    command: /bin/bash -c "' + params[1] + '"\n'
    return _svc


def define_yaml(
    scenario_name: str,
    use_meta_db: str = "mongo",
    use_data_db: str = "postgres",
) -> None:
    """Create the docker-compose.yaml from the provided scenario

    Args:
        scenario_name (str): Name of the scenario
        use_meta_db (str): Metadata backend type.
        use_data_db (str): Data backend type.
    """
    fed_def = None
    with create_metadata_manager(use_meta_db) as mgr:
        scenario_def = mgr.read_scenario(scenario_name)
        if not scenario_def:
            raise ValueError(
                f"Scenario '{scenario_name}' not found in metadata store."
            )
        analysis_name = scenario_def.get("analysis")
        if not analysis_name:
            raise ValueError(
                f"Scenario '{scenario_name}' does not specify a 'analysis'."
            )
        federation_name = scenario_def.get("federation")
        if not federation_name:
            raise ValueError(
                f"Scenario '{scenario_name}' does not specify a 'federation'."
            )
        fed_def = mgr.read_federation(federation_name)["federation"]
        if not fed_def:
            raise ValueError(
                f"Federation '{federation_name}' not found in metadata store."
            )

    cosim_env = (
        '      CST_HOST: "' + env.cst_host + '"\n'
        # '      LOCAL_USER: "' + env.local_user + '"\n'
        '      POSTGRES_HOST: "' + env.cst_pg_host + '"\n'
        '      MONGO_HOST: "' + env.cst_mg_host + '"\n'
        '      MONGO_PORT: "' + env.cst_mg_port + '"\n'
    )
    # Add helics broker federate
    cnt = 2
    yaml_str = ""
    add_logger = False
    for name in fed_def:
        cnt += 1
        image = fed_def[name]["image"]
        commandline = f"{fed_def[name]['command']}"
        if "prefix" in fed_def[name]:
            if fed_def[name]["prefix"] != "":
                commandline = f"{fed_def[name]['prefix']} && " + commandline
        params = [cosim_env, commandline]
        yaml_str += DockerRunner._service(name, image, params, cnt, depends=None)
        if "logger" in fed_def[name]:
            if fed_def[name]["logger"]:
                add_logger = True

    # Add data logger federate
    if add_logger:
        cnt += 1
        params = [
            cosim_env,
            f"python3 -c \"import cosim_toolbox.federateLogger as datalog; "
            f"datalog.main('FederateLogger', '{analysis_name}', '{scenario_name}', '{use_meta_db}', '{use_data_db}')\"",
        ]
        yaml_str += DockerRunner._service(
            "cst_logger", "cosim-cst:latest", params, cnt, depends=None
        )

    yaml_str += DockerRunner._network()

    # Add helics broker service
    params = [
        cosim_env,
        f"helics_broker --ipv4 -f {cnt - 2} --loglevel=warning --name=broker",
    ]
    yaml_str = (
        "services:\n"
        + DockerRunner._service("helics", "broker", params, 2, depends=None)
        + yaml_str
    )

    with open("./meta_store/" + scenario_name + ".yaml", "w") as op:
        op.write(yaml_str)


def _federate_docker(self, address: int = 0) -> None:
    """Override: fix broker_address and set local_interface for Docker networking.

    The upstream CST implementation sets ``broker_address`` to the
    federate's own container IP.  Per HELICS docs:

    - ``broker_address``: IP a federate should use to contact *its parent broker*
    - ``local_interface``: IP the rest of the federation should use to contact *this federate*

    CST was writing the federate IP into the wrong field.
    """
    if address > 0:
        self.helics.config("broker_address", BROKER_IP)
        self.helics.config("local_interface", f"10.5.0.{address}")


def apply_monkeypatches() -> None:
    """Apply monkey patches to CST DockerRunner and FederateConfig."""
    DockerRunner._service = staticmethod(_service)
    DockerRunner.define_yaml = staticmethod(define_yaml)
    FederateConfig.docker = _federate_docker
