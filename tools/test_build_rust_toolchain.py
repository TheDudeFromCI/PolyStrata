"""Test toolchain selection with CLI fixtures; no Rust downloads or model access."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

HELPER = Path(__file__).with_name('build_rust_toolchain.sh').resolve()


class RustSelection(unittest.TestCase):
    def select(self, rust='1.98.0', cargo=None, rustup=False, fallback=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for command, version in [('rustc', rust), ('cargo', cargo or rust)]:
                (root / command).write_text('#!/bin/sh\n' +
                    ('if [ "$RUSTUP_TOOLCHAIN" = 1.95.0 ]; then echo "' + command + ' 1.95.0 (fixture)"; exit; fi\n' if fallback else '') +
                    'echo "' + command + ' ' + version + ' (fixture)"\n')
                (root / command).chmod(0o755)
            if rustup:
                (root / 'rustup').write_text('#!/bin/sh\n'
                    'echo "$*" >> "$RUST_TEST_LOG"\n'
                    'if [ "$1" = show ]; then echo "1.98.0-x86_64-unknown-linux-gnu (default)"; fi\n')
                (root / 'rustup').chmod(0o755)
            environment = dict(os.environ, PATH=str(root), RUST_TEST_LOG=str(root/'calls'))
            environment.pop('RUSTUP_TOOLCHAIN', None)
            result = subprocess.run(['/bin/bash','-c','set -eu; source "$1"; select_build_rust_toolchain 1.95.0; printf "selected=%s\\n" "${RUSTUP_TOOLCHAIN:-system}"',
                                     'test',str(HELPER)], env=environment, capture_output=True, text=True)
            calls = (root/'calls').read_text() if (root/'calls').exists() else ''
            return result, calls

    def test_newer_system_rust_is_accepted(self):
        result, calls = self.select()
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('rustc 1.98.0',result.stdout)
        self.assertIn('selected=system',result.stdout)
        self.assertEqual(calls,'')

    def test_minimum_system_rust_is_accepted(self):
        result,_ = self.select(rust='1.95.0')
        self.assertEqual(result.returncode,0,result.stderr)

    def test_newer_rustup_toolchain_is_preserved_without_install(self):
        result,calls = self.select(rustup=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('selected=1.98.0-x86_64-unknown-linux-gnu',result.stdout)
        self.assertNotIn('install',calls)

    def test_old_compiler_falls_back_via_rustup(self):
        result,calls = self.select(rust='1.94.0',rustup=True,fallback=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('toolchain install 1.95.0 --profile minimal',calls)
        self.assertIn('selected=1.95.0',result.stdout)

    def test_old_compiler_or_cargo_without_rustup_fails(self):
        for rust,cargo in [('1.94.0','1.98.0'),('1.98.0','1.94.0')]:
            with self.subTest(rust=rust,cargo=cargo):
                result,_ = self.select(rust=rust,cargo=cargo)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('or newer',result.stderr)


if __name__ == '__main__':
    unittest.main()
