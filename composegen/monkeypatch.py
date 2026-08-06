"""Replacements for the CST DockerRunner and FederateConfig methods that emit compose.

Everything that changes the shape of `generated/docker-compose.yaml` belongs here rather
than in the upstream CoSim Toolbox.
"""

from typing import Optional
from pathlib import Path

import cosim_toolbox as env
from cosim_toolbox.sims import DockerRunner, FederateConfig
from cosim_toolbox.dbms import create_metadata_manager

# The broker's Compose service name. Federates reach it through Compose's own
# DNS rather than a fixed address, which is what lets the generated file drop
# static IPs and a reserved subnet entirely (see _service).
BROKER_SERVICE = "helics"


def _service(
    name: str, image: str, params: list, cnt: int, depends: Optional[str] = None
) -> str:
    """Builds the "service" part of the docker-compose.yaml

    Args:
        name (str): Name of the service being defined
        image (str): Name of the image on which the service runs
        params (list): Environment in image the service utilizes
        cnt (int): Federate counter, used by the caller to size the broker's
            -f argument. No longer used to assign an address.
        depends (str, optional): Dependency for service being defined. Defaults to None.

    Returns:
        str: Docker-compose service block as a string.
    """
    _svc = "  " + name + ":\n"
    _svc += '    image: "' + image + '"\n'
    _svc += "    build:\n"
    if image in ["house", "controller"]:
        _svc += "      context: ..\n"
        _svc += "      dockerfile: federates/" + image + "/Dockerfile\n"
    else:
        _svc += "      context: ../federates/" + image + "\n"
        _svc += "      dockerfile: Dockerfile\n"
    if params[0] != "":
        _svc += "    environment:\n"
        _svc += params[0]
    # _svc += "    user: worker\n"
    # _svc += "    working_dir: /home/worker/case\n"
    _svc += "    volumes:\n"
    _svc += "      - ../data:/data\n"
    _svc += "      - ../generated:/app/meta_store\n"
    _svc += "    extra_hosts:\n"
    _svc += '      - "host.docker.internal:host-gateway"\n'
    if depends is not None:
        _svc += "    depends_on:\n"
        _svc += "      - " + depends + "\n"
    # No networks block and no ipv4_address: services land on the Compose
    # project's own default network and find each other by service name.
    # Upstream pinned every container to 10.5.0.<cnt> inside a reserved
    # 10.5.0.0/16, which meant two experiments could not run at once ("Pool
    # overlaps with other one on this address space"), a leftover network
    # blocked the next run, the range could collide with a VPN or another
    # project, and only ~253 of the 65k reserved addresses were reachable
    # before <cnt> produced an invalid address. A grid whose loads are filled
    # with houses now needs two federates per load, so that ceiling was close.
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
    runtime_cst_host = env.environ.get(
        "CST_BROKER_HOST",
        env.cst_host,
    )
    runtime_pg_host = env.environ.get(
        "CST_RUNTIME_POSTGRES_HOST",
        runtime_cst_host,
    )
    runtime_pg_port = env.environ.get(
        "CST_POSTGRES_PORT",
        env.cst_pg_port,
    )
    runtime_mg_host = env.environ.get(
        "CST_RUNTIME_MONGO_HOST",
        f"mongodb://{runtime_cst_host}",
    )
    runtime_mg_port = env.environ.get(
        "CST_MONGO_PORT",
        env.cst_mg_port,
    )

    fed_def = None
    with create_metadata_manager(use_meta_db) as mgr:
        scenario_def = mgr.read_scenario(scenario_name)
        if not scenario_def:
            raise ValueError(f"Scenario '{scenario_name}' not found in metadata store.")
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
        federation_def = mgr.read_federation(federation_name)
        if not federation_def:
            raise ValueError(
                f"Federation '{federation_name}' not found in metadata store."
            )
        fed_def = federation_def["federation"]
        if not fed_def:
            raise ValueError(
                f"Federation '{federation_name}' not found in metadata store."
            )

    cosim_env = (
        '      CST_HOST: "' + runtime_cst_host + '"\n'
        '      POSTGRES_HOST: "' + runtime_pg_host + '"\n'
        '      POSTGRES_PORT: "' + runtime_pg_port + '"\n'
        '      MONGO_HOST: "' + runtime_mg_host + '"\n'
        '      MONGO_PORT: "' + runtime_mg_port + '"\n'
        '      CST_USE_META_DB: "' + use_meta_db + '"\n'
        '      CST_USE_DATA_DB: "' + use_data_db + '"\n'
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
        yaml_str += _service(name, image, params, cnt, depends="helics")
        if "logger" in fed_def[name]:
            if fed_def[name]["logger"]:
                add_logger = True

    add_logger = False

    # Add data logger federate
    if add_logger:
        cnt += 1
        params = [
            cosim_env,
            f"python3 -c 'import cosim_toolbox.sims.federateLogger as datalog; "
            f'datalog.main(\\"FederateLogger\\", \\"{scenario_name}\\", \\"{use_meta_db}\\", \\"{use_data_db}\\")\'',
        ]
        yaml_str += _service("cst_logger", "broker", params, cnt, depends="helics")

    # DockerRunner._network() is deliberately not appended: it emits a
    # cst_net with a hard-coded 10.5.0.0/16 subnet and gateway. Without it
    # Compose provisions a per-project network and allocates the subnet
    # itself, so concurrent experiments no longer collide.

    # Add helics broker service
    #
    # --global_disconnect delays every federate's disconnect until the whole
    # federation is done (HELICS 3.6+). Without it, a federate that reaches
    # its own stop_time disconnects immediately, and if a sibling is still
    # finishing its own final-timestep publish at that same instant (e.g.
    # HEMS -> house), the broker has already torn down the route and drops
    # the message with a "commWarning...unknown route" warning.
    params = [
        cosim_env,
        f"helics_broker --ipv4 -f {cnt - 2} --loglevel=warning --name=broker "
        f"--global_disconnect",
    ]
    yaml_str = (
        "services:\n" + _service("helics", "broker", params, 2, depends=None) + yaml_str
    )

    Path("./generated").mkdir(parents=True, exist_ok=True)
    with open("./generated/docker-compose.yaml", "w") as op:
        op.write(yaml_str)


def _federate_docker(self, address: int = 0) -> None:
    """Override: point each federate at the broker by Compose service name.

    ``broker_address`` is the address a federate uses to contact *its parent
    broker*. The upstream CST implementation wrote the federate's own
    container IP into that field, which is the wrong value; the first fix
    here supplied the broker's fixed IP and set ``local_interface`` to the
    federate's own.

    Neither address is needed. Compose resolves service names on the project
    network, so the broker is simply ``helics``, and each container has a
    single interface, so ``local_interface`` has nothing to disambiguate.
    Dropping both is what allows the generated file to carry no static
    addresses at all.

    ``address`` is retained because CST calls this positionally.
    """
    self.helics.config("broker_address", BROKER_SERVICE)


def apply_monkeypatches() -> None:
    """Apply monkey patches to CST DockerRunner and FederateConfig."""
    DockerRunner._service = staticmethod(_service)
    DockerRunner.define_yaml = staticmethod(define_yaml)
    FederateConfig.docker = _federate_docker
