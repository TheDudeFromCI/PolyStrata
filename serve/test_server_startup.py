"""Entry-point smoke tests: --help exits before engine startup or model access."""
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ServerStartup(unittest.TestCase):
    def assert_help(self, args, cwd):
        environment = dict(os.environ)
        environment.pop("PYTHONPATH", None)
        result = subprocess.run([sys.executable, *args, "--help"], cwd=cwd, env=environment,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--engine", result.stdout)

    def test_direct_script_from_repository(self):
        self.assert_help(["-I", str(ROOT / "serve/server.py")], ROOT)

    def test_direct_script_from_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assert_help(["-I", str(ROOT / "serve/server.py")], directory)

    def test_module_from_repository(self):
        self.assert_help(["-m", "serve.server"], ROOT)


if __name__ == "__main__":
    unittest.main()
