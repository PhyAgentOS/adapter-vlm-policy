"""Run the actual PAOS CLI with a separate instance config/data directory."""

import os
from pathlib import Path

from PhyAgentOS.config.loader import set_config_path

set_config_path(Path(os.environ["OPERATOR_PAOS_CONFIG"]).resolve())
from PhyAgentOS.cli.commands import app  # noqa: E402 -- bind instance before CLI imports

app()
