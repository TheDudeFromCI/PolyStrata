"""Model-free CLI/preservation checks for build.sh; no network or CUDA required."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'build.sh'


class BuildScript(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        shutil.copy2(SCRIPT, self.root / 'build.sh')

    def run_script(self, *args):
        return subprocess.run(['bash', str(self.root / 'build.sh'), *args], cwd='/tmp',
                              capture_output=True, text=True, timeout=15)

    def test_help_from_another_directory_does_not_install(self):
        result = self.run_script('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--fetch-only', result.stdout)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ['build.sh'])

    def test_bad_arguments_fail_before_fetch(self):
        for args in [('--jobs', '0'), ('--jobs',), ('--arch', '86 120'), ('--unknown',),
                     ('--test', '--fetch-only')]:
            result = self.run_script(*args)
            self.assertNotEqual(result.returncode, 0, (args, result.stdout))
            self.assertFalse((self.root / 'third_party').exists())

    def test_existing_non_git_sources_are_preserved(self):
        dependency = self.root / 'third_party/llguidance'
        dependency.mkdir(parents=True)
        sentinel = dependency / 'my-work.txt'
        sentinel.write_text('preserve this')
        result = self.run_script('--fetch-only')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not a Git checkout', result.stderr)
        self.assertEqual(sentinel.read_text(), 'preserve this')

    def test_wrong_revision_is_not_reset(self):
        dependency = self.root / 'third_party/llguidance'
        dependency.mkdir(parents=True)
        subprocess.run(['git', 'init', '--quiet', str(dependency)], check=True)
        sentinel = dependency / 'my-work.txt'
        sentinel.write_text('local revision')
        subprocess.run(['git', '-C', str(dependency), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(dependency), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid', 'commit', '--quiet', '-m', 'local'], check=True)
        before = subprocess.check_output(['git', '-C', str(dependency), 'rev-parse', 'HEAD'])
        result = self.run_script('--fetch-only')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not pinned at', result.stderr)
        self.assertEqual(subprocess.check_output(['git', '-C', str(dependency), 'rev-parse', 'HEAD']), before)
        self.assertEqual(sentinel.read_text(), 'local revision')


if __name__ == '__main__':
    unittest.main()
