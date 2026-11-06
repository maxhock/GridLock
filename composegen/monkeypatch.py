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

# Images whose entry point runs several federates as threads of one process
# when ``--federate_name`` and the arguments paired with it are repeated.
# Federates of these images that share a parent share a container (see
# _service_groups); every other image still gets one container per federate.
GROUPED_IMAGES = ("grid", "house_player")
# Where a federate's own arguments start in the command composegen gives it.
_FEDERATE_ARG = " --federate_name "


def _service(
    name: str,
    image: str,
    params: list,
    cnt: int,
    depends: Optional[str] = None,
    build: bool = True,
) -> str:
    """Builds the "service" part of the docker-compose.yaml

    Args:
        name (str): Name of the service being defined
        image (str): Name of the image on which the service runs
        params (list): Environment in image the service utilizes
        cnt (int): Federate counter, used by the caller to size the broker's
            -f argument. No longer used to assign an address.
        depends (str, optional): Dependency for service being defined. Defaults to None.
        build (bool, optional): Whether this service carries the image's `build`
            section. Only one service per image should; see define_yaml.

    Returns:
        str: Docker-compose service block as a string.
    """
    _svc = "  " + name + ":\n"
    _svc += '    image: "' + image + '"\n'
    if build:
        _svc += "    build:\n"
        if image in ["house", "controller"]:
            _svc += "      context: ..\n"
            _svc += "      dockerfile: federates/" + image + "/Dockerfile\n"
        else:
            _svc += "      context: ../federates/" + image + "\n"
            _svc += "      dockerfile: Dockerfile\n"
    else:
        # The service that carries the build section builds the image; this
        # one only runs it. Without "never", a missing local image would send
        # Compose to pull e.g. "grid" from Docker Hub instead of failing.
        _svc += "    pull_policy: never\n"
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


def _service_groups(fed_def: dict) -> dict[str, list[str]]:
    """Map each Compose service to the federates it runs.

    Federates of a grouped image that sit under the same parent run in one
    container. One container per federate loaded pandapower, numba and CST
    once per grid: the full ÜZW federation (256 grids, 252 load players)
    needed ~66 GB and was stopped at 236 of 509 containers.

    Composegen names every federate ``<parent federate>.<local id>``, so the
    parent is the name without its last segment. A root federate, and a
    group of one, keeps the federate's own name as its service name.
    """
    groups: dict[tuple[str, str], list[str]] = {}
    for name, fed in fed_def.items():
        parent = name.rpartition(".")[0]
        image = fed["image"]
        key = (parent, image) if parent and image in GROUPED_IMAGES else (name, "")
        groups.setdefault(key, []).append(name)

    services: dict[str, list[str]] = {}
    for (parent, image), members in groups.items():
        service = members[0] if len(members) == 1 else f"{parent}.{image}-group"
        if service in services or (len(members) > 1 and service in fed_def):
            raise ValueError(
                f"Compose service '{service}' would run two sets of federates; "
                f"rename the federate called '{service}'."
            )
        services[service] = members
    return services


def _group_command(members: list[str], fed_def: dict) -> str:
    """One command line that starts every member of a service.

    Members share the start of their command (entry point and scenario); each
    then contributes its own ``--federate_name ...`` arguments, which the entry
    point pairs up in order. A service of one runs its command unchanged.
    """
    prefixes = {fed_def[name].get("prefix", "") for name in members}
    if len(members) == 1:
        commandline = fed_def[members[0]]["command"]
    else:
        heads: set[str] = set()
        own_args = ""
        for name in members:
            head, sep, tail = fed_def[name]["command"].partition(_FEDERATE_ARG)
            if not sep:
                raise ValueError(
                    f"Federate '{name}' has no{_FEDERATE_ARG.rstrip()} in its "
                    f"command, so it cannot share a container: "
                    f"{fed_def[name]['command']!r}"
                )
            heads.add(head)
            own_args += sep + tail
        if len(heads) != 1 or len(prefixes) != 1:
            raise ValueError(
                f"Federates {members} share a container but not an entry point: "
                f"{sorted(heads)}, prefixes {sorted(prefixes)}"
            )
        commandline = heads.pop() + own_args
    prefix = prefixes.pop()
    return f"{prefix} && {commandline}" if prefix else commandline


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
    # Only the first service of each image carries its build section. Compose
    # builds once per service that has one, so with a build on every service the
    # ÜZW Nord federation (194 services, 3 images) started 194 parallel builds of
    # the same three images, and BuildKit lost one of them ("failed to receive
    # status: rpc error: code = NotFound desc = no such job") before any federate
    # ran. 59 services still got through.
    #
    # Interim: long term the federate images are meant to be built once, pushed
    # to a registry and pulled, which would replace local builds altogether.
    built_images: set[str] = set()

    def claims_build(image: str) -> bool:
        if image in built_images:
            return False
        built_images.add(image)
        return True

    # Add helics broker federate
    #
    # cnt counts federates, not services: the broker's -f is the number of
    # federates that will connect, however many containers they share.
    cnt = 2
    yaml_str = ""
    add_logger = False
    for service, members in _service_groups(fed_def).items():
        cnt += len(members)
        image = fed_def[members[0]]["image"]
        params = [cosim_env, _group_command(members, fed_def)]
        yaml_str += _service(
            service, image, params, cnt, depends="helics", build=claims_build(image)
        )
        if any(fed_def[name].get("logger") for name in members):
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
        yaml_str += _service(
            "cst_logger",
            "broker",
            params,
            cnt,
            depends="helics",
            build=claims_build("broker"),
        )

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
        "services:\n"
        + _service(
            "helics", "broker", params, 2, depends=None, build=claims_build("broker")
        )
        + yaml_str
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
