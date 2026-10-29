import os
from pathlib import Path

import yaml

DEPLOYMENT_ENV = {
    env["name"]: env["value"]
    for env in yaml.safe_load((Path(__file__).parent / "deployment.yaml").read_text())["spec"]["template"]["spec"]["containers"][0]["env"]
    if "value" in env
}

# Tests run against the shipped exec argv allowlists.
os.environ.setdefault(
    "KUBECTL_PREAPPROVED_EXEC_ARGV_ALLOWLISTS", DEPLOYMENT_ENV["KUBECTL_PREAPPROVED_EXEC_ARGV_ALLOWLISTS"]
)
