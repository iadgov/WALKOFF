import logging
import asyncio
import pathlib
import argparse
import copy
import sys
import os
import re
import shutil
import base64
import zipfile
from pathlib import Path

import aiodocker
import aiohttp
import yaml
import yaml.scanner
from tenacity import retry, stop_after_attempt, wait_exponential

from common.config import config, static
from common.docker_helpers import (create_secret, get_secret, delete_secret, get_network, connect_to_aiodocker,
                                   docker_context, stream_docker_log, logger as docker_logger, disconnect_from_network,
                                   update_service, get_replicas, remove_volume)

logging.basicConfig(level=logging.DEBUG, format="{asctime} - {name} - {levelname}:{message}", style='{')

logger = logging.getLogger("BOOTLOADER")
static.set_local_hostname("local_bootloader")

COMPOSE_BASE = {"version": "3.5",
                "services": {},
                "networks": {"walkoff_default": {"driver": "overlay", "name": "walkoff_default", "attachable": True}},
                "secrets": {"encryption_key": {"external": True}}}

APP_NAME_PREFIX = "walkoff_"

DOCKER_HOST_IP = os.getenv("DOCKER_HOST_IP")
p = Path('./apps').glob('**/*')


def bannerize(text, fill='='):
    columns = shutil.get_terminal_size().columns
    border = "".center(columns, fill)
    banner = f" {text} ".center(columns, fill)
    print(f"\n\n{border}\n{banner}\n{border}\n")


def parse_yaml(path):
    with open(path) as fp:
        try:
            return yaml.safe_load(fp)
        except yaml.YAMLError as e:
            logger.info(f"Invalid yaml: {path}. {e}")
        except yaml.scanner.ScannerError as e:
            logger.info(f"Invalid yaml: {path}. {e}")


def dump_yaml(path, obj):
    with open(path, 'w') as fp:
        try:
            return yaml.dump(obj, fp)
        except yaml.YAMLError as e:
            logger.info(f"Invalid yaml: {path}. {e}")


def parse_env_file(path):
    with open(path) as fp:
        return [line.strip() for line in fp]


def compose_from_app(path: pathlib.Path, name):
    env_txt = path / "env.txt"
    env_file = {}
    if env_txt.exists():
        env_file = {"environment": parse_env_file(env_txt)}
    compose = copy.deepcopy(COMPOSE_BASE)
    build = {"build": {"context": str(path), "dockerfile": "Dockerfile"}}
    image = {"image": f"{config.DOCKER_REGISTRY}/{APP_NAME_PREFIX}{name}:{path.name}"}
    networks = {"networks": ["walkoff_default"]}
    deploy = {"deploy": {"mode": "replicated", "replicas": 0, "restart_policy": {"condition": "none"}}}
    config_mount = {"configs": ["common_env.yml"]}
    secret_mount = {"secrets": ["walkoff_encryption_key"]}
    shared_path = os.getcwd() + "/data/shared"
    final_mount = shared_path + ":/app/shared"
    volumes_mount = {"volumes": [final_mount]}
    compose["services"] = {name: {**build, **image, **networks, **deploy, **config_mount,
                                  **secret_mount, **volumes_mount, **env_file}}
    return compose


async def log_proc_output(proc, silent=False):
    stdout, stderr = await proc.communicate()
    if not silent:
        if proc.returncode:
            for line in stderr.decode().split('\n'):
                if line != '':
                    logger.error(line)
        else:
            for line in stdout.decode().split('\n'):
                if line != '':
                    logger.info(line)


def merge_composes(base, others):
    if not isinstance(base, dict):
        base = parse_yaml(base)
        if base.get("services") is None:
            base["services"] = {}
    if not isinstance(others[0], dict):
        others = [parse_yaml(o) for o in others]
    for o in others:
        base["services"].update(o.get("services", {}))
    return base


def generate_app_composes():
    # TODO: Probably find a way to incorporate the app repo in here as well to eliminate mounting files to umpire
    composes = []
    for app in pathlib.Path(config.APPS_PATH).iterdir():
        if not app.is_dir():
            try:
                zip_ref = zipfile.ZipFile(app, 'r')
                zip_ref.extractall(config.APPS_PATH)
                zip_ref.close()
                os.remove(app)
            except Exception as e:
                logger.error(f"Zip error: {e}")
                continue

    for app in pathlib.Path(config.APPS_PATH).iterdir():
        #  grabs only directories and ignores all __* directories i.e. __pycache__

        if app.is_dir() and not re.fullmatch(r"(__.*)", app.name):
            for version in app.iterdir():
                # grabs all valid version directories of form "v0.12.3.45..."
                if re.fullmatch(r"((\d\.?)+)", version.name):
                    composes.append(compose_from_app(version, f"app_{app.name}"))
                logger.info(f"Generated compose for {app.name} version: {version.name}")
    return composes


async def are_you_sure(prompt):
    try:
        resp = input(f"{prompt}\n\nAre you sure? (yes/no): ")
        while resp.lower() not in ("yes", "no"):
            resp = input("Please answer 'yes' or 'no': ")
    except KeyboardInterrupt:
        return False

    if resp.lower() == "yes":
        return True
    else:
        return False


async def create_encryption_key(docker_client, key_name, value=None):
    try:
        await get_secret(docker_client, key_name)
    except aiodocker.exceptions.DockerError:
        logger.info(f"Creating secret {key_name}...")
        try:
            key = value if value else base64.urlsafe_b64encode(os.urandom(32))
            await create_secret(docker_client, key_name, key)
            return key
        except aiodocker.exceptions.DockerError as e:
            logger.exception(f"Could not create Docker Secret {key_name}, exiting. Reason: {e}")
            os._exit(1)
    else:
        logger.info(f"Skipping secret {key_name} creation, it already exists.")
        return "Key already existed."


async def delete_encryption_key(docker_client, key_name):
    try:
        logger.info(f"Deleting secret {key_name}...")
        await delete_secret(docker_client, key_name)
    except aiodocker.exceptions.DockerError:
        logger.info(f"Skipping secret {key_name} deletion, it doesn't exist.")


async def check_for_network(docker_client):
    try:
        await get_network(docker_client, "walkoff_default")
        return True
    except aiodocker.exceptions.DockerError:
        return False


async def delete_dir_contents(path):
    logger.info(f"Deleting directory contents of {path}...")
    try:
        for root, dirs, files in os.walk(path):
            for f in files:
                os.unlink(os.path.join(root, f))
            for d in dirs:
                shutil.rmtree(os.path.join(root, d))
    except Exception as e:
        logger.exception(f"Could not remove contents in {path}. Reason: {e}")


@retry(stop=stop_after_attempt(10), wait=wait_exponential(min=1, max=10))
async def deploy_compose(compose):
    try:
        if not isinstance(compose, dict):
            compose = parse_yaml(compose)

        # Dump the compose to a temporary compose file and launch that. This is so we can amend the compose and update the
        # the stack without launching a new one
        dump_yaml(config.TMP_COMPOSE, compose)
        compose = config.TMP_COMPOSE

        proc = await asyncio.create_subprocess_exec("docker", "stack", "deploy", "--compose-file", compose, "walkoff",
                                                    stderr=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
        await log_proc_output(proc)

        if proc.returncode:
            raise OSError
        else:
            return True

    except Exception as e:
        logger.info("Failed deploying, waiting to try again...")
        raise e


@retry(stop=stop_after_attempt(10), wait=wait_exponential(min=1, max=10))
async def rm_stack(stack_name):
    try:
        logger.info("Removing Walkoff stack and related artifacts...")

        proc = await asyncio.create_subprocess_exec("docker", "stack", "rm", stack_name, stderr=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE)

        await log_proc_output(proc)

        if proc.returncode:
            raise OSError
        else:
            return proc

    except Exception as e:
        logger.info("Failed to fully remove stack, waiting to try again...")
        raise e


async def build_image(docker_client, repo, dockerfile, context_dir, dockerignore):
    logger.info(f"Building {repo} with {dockerfile} in {context_dir}")

    with docker_context(Path(context_dir), dockerignore=dockerignore) as context:
        log_stream = await docker_client.images.build(fileobj=context, tag=repo, rm=True,
                                                      forcerm=True, pull=True, stream=True,
                                                      path_dockerfile=dockerfile,
                                                      encoding="application/x-tar")

    await stream_docker_log(log_stream)


async def push_image(docker_client, repo):
    logger.info(f"Pushing image {repo}.")

    try:
        await docker_client.images.push(repo)
        # await stream_docker_log(log_stream)
        logger.info(f"Pushed image {repo}.")
        return True
    except aiodocker.exceptions.DockerError as e:
        logger.exception(f"Failed to push image: {e}")
        return False


async def force_service_update(docker_client, service_name, image):
    logger.info(f"Forcing service update for {service_name}...")
    await update_service(docker_client, service_name, image=image, force=True)

    await asyncio.sleep(3)

    logger.info(f"Scaling {service_name} to 0...")
    await update_service(docker_client, service_name, image=image, force=True, mode={"replicated": {"Replicas": 0}})

    replicas = 1
    while replicas > 0:
        replicas = (await get_replicas(docker_client, service_name))['running']
        await asyncio.sleep(2)

    logger.info(f"Scaling {service_name} back to 1...")
    await update_service(docker_client, service_name, image=image, force=True, mode={"replicated": {"Replicas": 1}})

    while replicas < 1:
        replicas = (await get_replicas(docker_client, service_name))['running']
        await asyncio.sleep(2)

    logger.info(f"Forced service update for {service_name}.")


class Bootloader:
    """ A class to hold the logic for each of the possible commands. This follows the dispatch pattern we us in app_base
        for calling actions in apps. The pattern as applied to the CLI follows close to this example:
        https://chase-seibert.github.io/blog/2014/03/21/python-multilevel-argparse.html#
    """

    def __init__(self, session=None, docker_client=None):
        self.session: aiohttp.ClientSession = session
        self.docker_client: aiodocker.Docker = docker_client
        with open(".dockerignore") as f:
            self.dockerignore = [line.strip() for line in f.readlines()]

    @staticmethod
    async def run():
        """ Landing pad to launch primary command and do whatever async init the bootloader needs. """
        # TODO: fill in the helps, and further develop cli with the end user in mind
        commands = {"up", "down", "refresh"}
        parser = argparse.ArgumentParser()
        parser.add_argument("command", choices=commands)
        parser.add_argument("args", nargs=argparse.REMAINDER)

        logger.setLevel("DEBUG")
        docker_logger.setLevel("DEBUG")

        # Parse out the command
        args = parser.parse_args(sys.argv[1:2])

        async with aiohttp.ClientSession() as session, connect_to_aiodocker() as docker_client:
            bootloader = Bootloader(session, docker_client)

            if hasattr(bootloader, args.command):
                await getattr(bootloader, args.command)()
            else:
                logger.error("Invalid command.")
                # TODO: Pipe this through the logger. print_help() accepts a file kwarg that we can use to do this
                parser.print_help()

    @retry(stop=stop_after_attempt(10), wait=wait_exponential(min=1, max=10))
    async def wait_for_registry(self):
        try:
            async with self.session.get(f"http://{DOCKER_HOST_IP}:5000") as resp:
                if resp.status == 200:
                    return True
                else:
                    raise ConnectionError
        except Exception as e:
            logger.info("Registry not available yet, waiting to try again...")
            raise e

    @retry(stop=stop_after_attempt(10), wait=wait_exponential(min=1, max=10))
    async def wait_for_minio(self):
        try:
            async with self.session.get(f"http://{config.MINIO}/minio/health/ready") as resp:
                if resp.status == 200:
                    return True
                else:
                    raise ConnectionError
        except Exception as e:
            logger.info("Minio not available yet, waiting to try again...")
            raise e

    async def up(self):
        data = {"name": "postgres-data"}
        # Create Postgres Volume
        await self.docker_client.volumes.create(data)

        # Create Walkoff encryption key
        wek = await create_encryption_key(self.docker_client, "walkoff_encryption_key")

        # Create internal user key
        wik = await create_encryption_key(self.docker_client, "walkoff_internal_key")

        # Create Postgres user password
        wpk = await create_encryption_key(self.docker_client, "walkoff_postgres_key")

        # Create Minio secret key
        wmak = await create_encryption_key(self.docker_client, "walkoff_minio_access_key", b"walkoff")
        wmsk = await create_encryption_key(self.docker_client, "walkoff_minio_secret_key")

        # Set up a subcommand parser
        parser = argparse.ArgumentParser(description="Bring the WALKOFF stack up and initialize it")
        parser.add_argument("-b", "--build", action="store_true",
                            help="Builds and pushes all WALKOFF components to local registry.")
        parser.add_argument("-d", "--debug", action="store_true",
                            help="Set log level to debug.")
        parser.add_argument("-k", "--keys", action="store_true",
                            help="Prints all keys to STDOUT (dangerous).")
        # Parse out the command
        args = parser.parse_args(sys.argv[2:])

        if args.debug:
            logger.setLevel("DEBUG")
            docker_logger.setLevel("DEBUG")

        logger.info("Creating persistent directories for registry, postgres, portainer...")
        os.makedirs(static.REGISTRY_DATA_PATH, exist_ok=True)
        os.makedirs(static.POSTGRES_DATA_PATH, exist_ok=True)
        os.makedirs(static.PORTAINER_DATA_PATH, exist_ok=True)
        os.makedirs(static.MINIO_DATA_PATH, exist_ok=True)

        # Bring up the base compose with the registry
        logger.info("Deploying base services (registry, postgres, portainer, redis)...")
        base_compose = parse_yaml(config.BASE_COMPOSE)

        await deploy_compose(base_compose)

        await self.wait_for_registry()

        # Merge the base, walkoff, and app composes
        app_composes = generate_app_composes()
        walkoff_compose = parse_yaml(config.WALKOFF_COMPOSE)
        merged_compose = merge_composes(walkoff_compose, app_composes)

        dump_yaml(config.TMP_COMPOSE, merged_compose)

        if args.build:
            walkoff_app_sdk = walkoff_compose["services"]["app_sdk"]
            await build_image(self.docker_client, walkoff_app_sdk["image"],
                              walkoff_app_sdk["build"]["dockerfile"],
                              walkoff_app_sdk["build"]["context"],
                              self.dockerignore)
            await push_image(self.docker_client, walkoff_app_sdk["image"])

            builders = []
            pushers = []

            for service_name, service in walkoff_compose["services"].items():
                if "build" in service:
                    build_func = build_image(self.docker_client, service["image"],
                                             service["build"]["dockerfile"],
                                             service["build"]["context"],
                                             self.dockerignore)
                    push_func = push_image(self.docker_client, service["image"])
                    if args.debug:
                        await build_func
                        await push_func
                    else:
                        builders.append(build_func)
                        pushers.append(push_func)

            if not args.debug:
                logger.info("Building Docker images asynchronously, this could take some time...")
                await asyncio.gather(*builders)
                logger.info("Build process complete.")
                logger.info("Pushing Docker images asynchronously, this could take some time...")
                await asyncio.gather(*pushers)
                logger.info("Push process complete.")

        await self.wait_for_minio()
        # await self.push_to_minio()

        logger.info("Deploying Walkoff stack...")

        return_code = await deploy_compose(merged_compose)

        if args.keys:
            if await are_you_sure("You specified -k/--keys, which will print all newly created keys to stdout."):
                print(f"walkoff_encryption_key:\t\t{wek.decode()}")
                print(f"walkoff_internal_key:\t\t{wik.decode()}")
                print(f"walkoff_postgres_key:\t\t{wpk.decode()}")
                print(f"walkoff_minio_access_key:\t{wmak.decode()}")
                print(f"walkoff_minio_secret_key:\t{wmsk.decode()}\n\n")

        logger.info("Walkoff stack deployed, it may take a little time to converge. \n"
                    "Use 'docker stack services walkoff' to check on Walkoff services. \n"
                    "Web interface should be available at 'https://127.0.0.1:8080' once walkoff_resource_nginx is up.")

        return return_code

    async def down(self):

        # Set up a subcommand parser
        parser = argparse.ArgumentParser(description="Remove the WALKOFF stack and optionally related artifacts.")
        parser.add_argument("-k", "--key", action="store_true",
                            help="Removes the walkoff_encryption_key secret.")
        parser.add_argument("-r", "--registry", action="store_true",
                            help="Clears the registry bind mount directory.")
        parser.add_argument("-v", "--volume", action="store_true", help="Clears the postgresql volume")
        parser.add_argument("-d", "--debug", action="store_true",
                            help="Set log level to debug.")

        # Parse out the command
        args = parser.parse_args(sys.argv[2:])

        if args.debug:
            logger.setLevel("DEBUG")
            docker_logger.setLevel("DEBUG")

        proc = await rm_stack("walkoff")

        # if not args.skipnetwork:
        #     logger.info("Waiting for containers to exit and network to be removed...")
        #     await exponential_wait(check_for_network, [self.docker_client], "Network walkoff_default still exists")

        if args.key:
            if await are_you_sure("Deleting encryption key will render database unreadable, so it will be cleared. "
                                  "This will delete all workflows, execution results, globals, users, roles, etc. "):
                await delete_encryption_key(self.docker_client, "walkoff_encryption_key")
                await delete_encryption_key(self.docker_client, "walkoff_internal_key")
                await delete_encryption_key(self.docker_client, "walkoff_postgres_key")
                await delete_dir_contents(static.POSTGRES_DATA_PATH)

        if args.registry:
            await delete_dir_contents(static.REGISTRY_DATA_PATH)
            await delete_dir_contents(static.MINIO_DATA_PATH)
            await delete_encryption_key(self.docker_client, "walkoff_minio_access_key")
            await delete_encryption_key(self.docker_client, "walkoff_minio_secret_key")

        if args.volume:
            await remove_volume("walkoff_postgres-data", wait=True)


        logger.info("Walkoff stack removed, it may take a little time to stop all services. "
                    "It is OK if the walkoff_default network is not fully removed.")

        return proc.returncode

    async def refresh(self):
        parser = argparse.ArgumentParser(description="Rebuild a specific service and force it to update.")
        parser.add_argument("-s", "--service",
                            help="Name of the service to rebuild and update. "
                                 "You can specify a prefix ('walkoff_app' or 'walkoff_core') "
                                 "to rebuild all in that category.")

        args = parser.parse_args(sys.argv[2:])

        compose = parse_yaml(config.TMP_COMPOSE)['services']
        service_yaml = compose.get(args.service)

        if service_yaml:
            if await are_you_sure("Forcing a service to update will disrupt any work it is currently doing. "
                                  "It is not yet guaranteed that a service will pick back up where it left off. "):
                if "build" in service_yaml:
                    await build_image(self.docker_client, service_yaml["image"],
                                      service_yaml["build"]["dockerfile"],
                                      service_yaml["build"]["context"],
                                      self.dockerignore)
                    await push_image(self.docker_client, service_yaml["image"])

                service_name = f"walkoff_{args.service}"
                await force_service_update(self.docker_client, service_name, service_yaml["image"])
        else:
            services = list(compose.keys())
            services.sort()
            logger.exception(f"No such service, valid services: {services}.")


if __name__ == "__main__":
    asyncio.run(Bootloader.run())
