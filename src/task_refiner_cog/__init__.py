"""task-groomer-cog: reads a task and the code it names, and proposes what it should say.

Install name:  task-groomer-cog
Import name:   task_groomer_cog
"""

from mini_app_polis import load_secrets

from ._version import __version__ as __version__

# Before anything else in the package: a Lambda worker's secrets arrive from
# SSM Parameter Store here, at the first import of the package, so they are
# in the environment before any module that reads it at import: the logger,
# Sentry's init in the worker. A no-op wherever SSM_PARAMETERS is unset (local
# runs under `doppler run`, tests). See mini_app_polis.ssm_secrets.
load_secrets()
