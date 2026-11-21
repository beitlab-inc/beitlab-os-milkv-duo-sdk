"""Exercise fresh and interrupted sparse dependency checkouts without a network."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[2]


@unittest.skipUnless(all(shutil.which(tool) for tool in ('git', 'cmake', 'sha256sum')), 'needs dependency-fetch host tools')
class FetchTest(unittest.TestCase):
    def git(self, path, *arguments):
        return subprocess.check_output([
            'git', '-C', str(path), '-c', 'user.name=Dependency fixture',
            '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
            *arguments], text=True, stderr=subprocess.PIPE).strip()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project'
        scripts = self.project / 'scripts'
        scripts.mkdir(parents=True)
        shutil.copyfile(REPO / 'scripts/fetch-freertos.sh', scripts / 'fetch-freertos.sh')
        stub = scripts / 'build-rtos-libs.sh'
        stub.write_text('#!/bin/sh\nset -eu\n'
                        '[ -f "$PLAYGROUND_SDK/freertos/cvitek/scripts/toolchain-riscv64-elf.cmake" ]\n'
                        '"$PLAYGROUND_RISCV_TOOLCHAIN/riscv64-unknown-elf-gcc" --version >/dev/null\n'
                        'touch "$PLAYGROUND_SDK/.fixture-libraries-built"\n')
        stub.chmod(0o755)
        self.sdk_source = self.root / 'sdk-origin'
        self.tools_source = self.root / 'tools-origin'
        toolchain = 'freertos/cvitek/scripts/toolchain-riscv64-elf.cmake'
        header = 'freertos/cvitek/driver/rtos_cmdqu/include/rtos_cmdqu.h'
        for repo, files in ((self.sdk_source, {toolchain: 'fixture toolchain\n', header: 'fixture ABI\n',
                                              'unrelated-linux/do-not-fetch': 'outside FreeRTOS\n'}),
                            (self.tools_source, {'gcc/riscv64-elf-x86_64/bin/riscv64-unknown-elf-gcc':
                                                 '#!/bin/sh\necho fixture-C906-compiler\n',
                                                 'other-compiler/do-not-fetch': 'not needed\n'})):
            repo.mkdir()
            self.git(repo, 'init', '--initial-branch=main')
            for name, contents in files.items():
                path = repo / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
                if path.name == 'riscv64-unknown-elf-gcc':
                    path.chmod(0o755)
            self.git(repo, 'add', '.')
            self.git(repo, 'commit', '-m', 'Dependency fixture')
        self.sdk_commit = self.git(self.sdk_source, 'rev-parse', 'HEAD')
        self.tools_commit = self.git(self.tools_source, 'rev-parse', 'HEAD')
        lock = {'sdk_url': self.sdk_source, 'sdk_commit': self.sdk_commit,
                'host_tools_url': self.tools_source, 'host_tools_commit': self.tools_commit,
                'host_tools_riscv64_elf': 'gcc/riscv64-elf-x86_64', 'toolchain_file': toolchain,
                'toolchain_file_sha256': hashlib.sha256((self.sdk_source / toolchain).read_bytes()).hexdigest(),
                'cmdqu_header': header,
                'cmdqu_header_sha256': hashlib.sha256((self.sdk_source / header).read_bytes()).hexdigest()}
        (self.project / 'sdk.lock').write_text(''.join(f'{key} = {value}\n' for key, value in lock.items()))
        self.sdk = self.root / 'external-cache/sdk'
        self.tools = self.sdk / 'host-tools'

    def fetch(self):
        result = subprocess.run(['bash', str(self.project / 'scripts/fetch-freertos.sh')],
                                env=dict(os.environ, BEITLAB_WORK_DIR=str(self.sdk.parent),
                                         PLAYGROUND_SDK=str(self.sdk)),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.sdk / '.fixture-libraries-built').is_file())
        self.assertEqual(self.git(self.sdk, 'rev-parse', 'HEAD'), self.sdk_commit)
        self.assertEqual(self.git(self.tools, 'rev-parse', 'HEAD'), self.tools_commit)
        self.assertFalse((self.sdk / 'unrelated-linux').exists())
        self.assertFalse((self.tools / 'other-compiler').exists())
        self.assertFalse((self.project / 'sdk').exists())

    def test_fresh_clone_at_pinned_head_and_repeat_fetch(self):
        self.fetch()
        note = self.sdk / 'local-note'
        note.write_text('keep local cache files\n')
        self.fetch()
        self.assertEqual(note.read_text(), 'keep local cache files\n')

    def test_resume_no_checkout_at_pinned_head_without_index(self):
        self.sdk.parent.mkdir(parents=True)
        for source, destination, pattern in ((self.sdk_source, self.sdk, '/freertos/\n'),
                                             (self.tools_source, self.tools, '/gcc/riscv64-elf-x86_64/\n')):
            self.git(self.root, 'clone', '--no-checkout', str(source), str(destination))
            self.git(destination, 'config', 'core.sparseCheckout', 'true')
            (destination / '.git/info/sparse-checkout').write_text(pattern)
            self.assertFalse((destination / '.git/index').exists())
        self.fetch()


if __name__ == '__main__':
    unittest.main()
