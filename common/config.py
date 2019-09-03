import logging
from pathlib import Path
import os

import yaml

logging.basicConfig(level=logging.INFO, format="{asctime} - {name} - {levelname}:{message}", style='{')
logger = logging.getLogger("WALKOFF")
CONFIG_PATH = os.getenv("CONFIG_PATH", "/common_env.yml")


def sint(value, default):
    if not isinstance(default, int):
        raise TypeError("Default value must be of integer type")
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def sfloat(value, default):
    if not isinstance(default, int):
        raise TypeError("Default value must be of float type")
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Static:
    """Common location for static values"""

    # Common statics
    CONTAINER_ID = os.getenv("HOSTNAME")

    # Service names
    CORE_PREFIX = "walkoff_core"
    RESOURCE_PREFIX = "walkoff_resource"
    APP_PREFIX = "walkoff_app"

    API_GATEWAY_SERVICE = f"{CORE_PREFIX}_api_gateway"
    UMPIRE_SERVICE = f"{CORE_PREFIX}_umpire"
    WORKER_SERVICE = f"{CORE_PREFIX}_worker"

    REDIS_SERVICE = f"{RESOURCE_PREFIX}_redis"
    POSTGRES_SERVICE = f"{RESOURCE_PREFIX}_postgres"
    NGINX_SERVICE = f"{RESOURCE_PREFIX}_nginx"
    PORTAINER_SERVICE = f"{RESOURCE_PREFIX}_portainer"
    REGISTRY_SERVICE = f"{RESOURCE_PREFIX}_registry"
    MINIO_SERVICE = f"{RESOURCE_PREFIX}_minio"

    # Redis options
    REDIS_EXECUTING_WORKFLOWS = "executing-workflows"
    REDIS_PENDING_WORKFLOWS = "pending-workflows"
    REDIS_ABORTING_WORKFLOWS = "aborting-workflows"
    REDIS_ACTIONS_IN_PROCESS = "actions-in-process"
    REDIS_WORKFLOW_QUEUE = "workflow-queue"
    REDIS_WORKFLOWS_IN_PROCESS = "workflows-in-process"
    REDIS_WORKFLOW_GROUP = "workflow-group"
    REDIS_ACTION_RESULTS_GROUP = "action-results-group"
    REDIS_WORKFLOW_TRIGGERS_GROUP = "workflow-triggers-group"
    REDIS_WORKFLOW_CONTROL = "workflow-control"
    REDIS_WORKFLOW_CONTROL_GROUP = "workflow-control-group"

    # File paths
    API_PATH = Path("api_gateway") / "api"
    CLIENT_PATH = Path("api_gateway") / "client"

    REDIS_DATA_PATH = Path("data") / "redis" / "red_data"
    POSTGRES_DATA_PATH = Path("data") / "postgres" / "pg_data"
    PORTAINER_DATA_PATH = Path("data") / "portainer" / "prt_data"
    REGISTRY_DATA_PATH = Path("data") / "registry" / "reg_data"
    MINIO_DATA_PATH = Path("data") / "minio" / "min_data"

    SECRET_BASE_PATH = Path("/") / "run" / "secrets"

    SWAGGER_URL = "/walkoff/api/docs"

    def set_local_hostname(self, hostname):
        if not self.CONTAINER_ID:
            self.CONTAINER_ID = hostname


class Config:
    """Common location for configurable values.
    Precedence:
    1. Environment Variables
    2. Config File
    3. Defaults defined here
    """

    # Common options
    API_GATEWAY_URI = os.getenv("API_GATEWAY_URI", f"http://{Static.API_GATEWAY_SERVICE}:8080")
    REDIS_URI = os.getenv("REDIS_URI", f"redis://{Static.REDIS_SERVICE}:6379")
    MINIO = os.getenv("MINIO", f"{Static.MINIO_SERVICE}:9000")

    # Key locations
    ENCRYPTION_KEY_PATH = os.getenv("ENCRYPTION_KEY_PATH", Static.SECRET_BASE_PATH / "walkoff_encryption_key")
    INTERNAL_KEY_PATH = os.getenv("INTERNAL_KEY_PATH", Static.SECRET_BASE_PATH / "walkoff_internal_key")
    POSTGRES_KEY_PATH = os.getenv("POSTGRES_KEY_PATH", Static.SECRET_BASE_PATH / "walkoff_postgres_key")
    MINIO_ACCESS_KEY_PATH = os.getenv("MINIO_SECRET_KEY_PATH", Static.SECRET_BASE_PATH / "walkoff_minio_access_key")
    MINIO_SECRET_KEY_PATH = os.getenv("MINIO_SECRET_KEY_PATH", Static.SECRET_BASE_PATH / "walkoff_minio_secret_key")

    # Worker options
    MAX_WORKER_REPLICAS = os.getenv("MAX_WORKER_REPLICAS", "10")
    WORKER_TIMEOUT = os.getenv("WORKER_TIMEOUT", "30")
    WALKOFF_USERNAME = os.getenv("WALKOFF_USERNAME", '')

    # Umpire options
    APPS_PATH = os.getenv("APPS_PATH", "./apps")
    APP_REFRESH = os.getenv("APP_REFRESH", "60")
    SWARM_NETWORK = os.getenv("SWARM_NETWORK", "walkoff_default")
    DOCKER_REGISTRY = os.getenv("DOCKER_REGISTRY", "127.0.0.1:5000")
    UMPIRE_HEARTBEAT = os.getenv("UMPIRE_HEARTBEAT", "1")

    # API Gateway options
    DB_TYPE = os.getenv("DB_TYPE", "postgres")
    DB_HOST = os.getenv("DB_HOST", Static.POSTGRES_SERVICE)
    SERVER_DB_NAME = os.getenv("SERVER_DB", "walkoff")
    EXECUTION_DB_NAME = os.getenv("EXECUTION_DB", "execution")
    DB_USERNAME = os.getenv("DB_USERNAME", "")

    # Bootloader options
    BASE_COMPOSE = os.getenv("BASE_COMPOSE", "./bootloader/base-compose.yml")
    WALKOFF_COMPOSE = os.getenv("WALKOFF_COMPOSE", "./bootloader/walkoff-compose.yml")
    TMP_COMPOSE = os.getenv("TMP_COMPOSE", "./tmp-compose.yml")

    # App options
    MAX_APP_REPLICAS = os.getenv("MAX_APP_REPLICAS", "10")
    APP_TIMEOUT = os.getenv("APP_TIMEOUT", "30")  # ??

    # Overrides the environment variables for docker-compose and docker commands on the docker machine at 'DOCKER_HOST'
    # See: https://docs.docker.com/compose/reference/envvars/ for more information.
    # DOCKER_HOST = os.getenv("DOCKER_HOST", "tcp://ip_of_docker_swarm_manager:2376")
    # DOCKER_HOST = os.getenv("DOCKER_HOST", "unix:///var/run/docker.sock")
    # DOCKER_TLS_VERIFY = os.getenv("DOCKER_TLS_VERIFY", "1")
    # DOCKER_CERT_PATH = os.getenv("DOCKER_CERT_PATH", "/Path/to/certs/for/remote/docker/daemon")

    def get_int(self, key, default):
        return sint(getattr(self, key), default)

    def get_float(self, key, default):
        return sfloat(getattr(self, key), default)

    def load_config(self):
        with open(CONFIG_PATH) as f:
            y = yaml.safe_load(f)

        for key, value in y.items():
            if hasattr(self, key.upper()) and not os.getenv(key.upper()):
                setattr(self, key.upper(), value)

    def dump_config(self, file):
        with open(file, 'w') as f:
            yaml.safe_dump(vars(self), f)

    @staticmethod
    def get_from_file(file_path, mode='r'):
        with open(file_path, mode) as f:
            s = f.read().strip()

        return s


config = Config()
config.load_config()

static = Static()
