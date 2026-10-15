import os
import subprocess
import sys
import allo_upload


def test_tests_use_an_empty_config_dir():
    assert "allo-config" in os.environ["ALLO_ROUTER_CONFIG_DIR"]
    assert allo_upload.load_state() is None



def test_subprocesses_inherit_the_empty_config_dir():
    out = subprocess.run([sys.executable, "-c", "import os;print(os.environ['ALLO_ROUTER_CONFIG_DIR'])"],
                         capture_output=True, text=True).stdout.strip()
    assert out == os.environ["ALLO_ROUTER_CONFIG_DIR"]
