import os

DEBUG = True

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SECRET_KEY = "django-treenode"

ALLOWED_HOSTS = ["*"]

database_engine = os.environ.get("DATABASE_ENGINE", "sqlite")
sqlite_test_path = os.environ.get("TREENODE_TEST_SQLITE_PATH")
database_config = {
    "sqlite": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("TREENODE_TEST_SQLITE_PATH", ":memory:"),
    },
    # 'mysql': {
    #     'ENGINE': 'django.db.backends.mysql',
    #     'NAME': 'treenode',
    #     'USER': 'mysql',
    #     'PASSWORD': 'mysql',
    #     'HOST': '',
    #     'PORT': '',
    # },
    "postgres": {
        "ENGINE": "django.db.backends.postgresql_psycopg2",
        "NAME": "treenode",
        "USER": "postgres",
        "PASSWORD": "postgres",
        "HOST": "",
        "PORT": "",
    },
}

if sqlite_test_path:
    database_config["sqlite"]["TEST"] = {"NAME": sqlite_test_path}

github_workflow = os.environ.get("GITHUB_WORKFLOW")
if github_workflow:
    database_config["postgres"]["NAME"] = "postgres"
    database_config["postgres"]["HOST"] = "127.0.0.1"
    database_config["postgres"]["PORT"] = "5432"

postgres_test_name = os.environ.get("TREENODE_TEST_POSTGRES_NAME")
if postgres_test_name:
    database_config["postgres"]["NAME"] = postgres_test_name
    database_config["postgres"]["TEST"] = {"NAME": postgres_test_name}

DATABASES = {
    "default": database_config.get(database_engine),
}

if os.environ.get("TREENODE_TEST_ROUTER_ALIASES"):
    if database_engine != "sqlite":
        raise RuntimeError("TREENODE_TEST_ROUTER_ALIASES currently requires SQLite")
    DATABASES["revision"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.messages",
    "django.contrib.sessions",
    "tests",
    "treenode",
]

DEFAULT_AUTO_FIELD = "django.db.models.AutoField"

MIDDLEWARE = [
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.contrib.auth.context_processors.auth",
                "django.template.context_processors.request",
                "django.contrib.messages.context_processors.messages",
            ],
            "debug": DEBUG,
        },
    },
]

MEDIA_ROOT = os.path.join(BASE_DIR, "treenode/public/media/")
MEDIA_URL = "/media/"

STATIC_ROOT = os.path.join(BASE_DIR, "treenode/public/static/")
STATIC_URL = "/static/"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "django-treenode-default",
        "TIMEOUT": 60,
    },
    "treenode": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "django-treenode",
        "TIMEOUT": 60,
    },
}

if os.environ.get("TREENODE_TEST_REDIS_URL"):
    CACHES["safe_memory"] = {
        "BACKEND": "treenode.backends.memory.SafeMemoryCache",
        "LOCATION": "safe-memory-tests",
        "KEY_PREFIX": "safe-memory-tests",
        "OPTIONS": {"NAMESPACE": "safe-memory-tests"},
    }
    CACHES["safe_redis"] = {
        "BACKEND": "treenode.backends.redis.SafeRedisCache",
        "LOCATION": os.environ["TREENODE_TEST_REDIS_URL"],
        "KEY_PREFIX": "safe-redis-tests",
        "OPTIONS": {"NAMESPACE": "safe-redis-tests", "socket_connect_timeout": 1},
    }
    CACHES["safe_redis_other"] = {
        "BACKEND": "treenode.backends.redis.SafeRedisCache",
        "LOCATION": os.environ["TREENODE_TEST_REDIS_URL"],
        "KEY_PREFIX": "safe-redis-other-tests",
        "OPTIONS": {"NAMESPACE": "safe-redis-other-tests", "socket_connect_timeout": 1},
    }
