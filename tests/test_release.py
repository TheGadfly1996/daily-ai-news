"""Exercise release guards against temporary local Git remotes; no external pushes."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.remote = root / 'remote.git'
        self.repo = root / 'repo'
        self.other = root / 'other'
        self.env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_AUTHOR_NAME': 'test', 'GIT_AUTHOR_EMAIL': 'test@example.org',
                    'GIT_COMMITTER_NAME': 'test', 'GIT_COMMITTER_EMAIL': 'test@example.org'}
        self.run_command(['git', 'init', '--bare', '--initial-branch=main', str(self.remote)], root)
        self.run_command(['git', 'clone', str(self.remote), str(self.repo)], root)
        (self.repo / 'data/digests').mkdir(parents=True)
        (self.repo / 'data/digests/.gitkeep').write_text('')
        (self.repo / 'src').mkdir()
        (self.repo / 'src/example.txt').write_text('base')
        self.git(self.repo, 'add', '.')
        self.git(self.repo, 'commit', '-m', 'initial')
        self.git(self.repo, 'push', 'origin', 'main')
        self.run_command(['git', 'clone', str(self.remote), str(self.other)], root)
        (self.repo / 'dist').mkdir()

    def run_command(self, args, cwd, check=True):
        return subprocess.run(args, cwd=cwd, env=self.env, text=True, capture_output=True, check=check)

    def git(self, cwd, *args):
        return self.run_command(['git', *args], cwd).stdout.strip()

    def candidate(self):
        (self.repo / 'data/digests/2026-10-12.json').write_text(json.dumps({'date': '2026-10-12'}))
        (self.repo / 'data/runs').mkdir()
        (self.repo / 'data/runs/example.json').write_text('{}')

    def advance_main(self):
        (self.other / 'src/example.txt').write_text('new main')
        self.git(self.other, 'add', '.')
        self.git(self.other, 'commit', '-m', 'new code')
        self.git(self.other, 'push', 'origin', 'main')
        return self.git(self.other, 'rev-parse', 'HEAD')

    def test_validated_candidate_push_and_release_revision(self):
        self.candidate()
        self.run_command(['bash', str(SCRIPTS/'publish-history.sh')], self.repo)
        self.run_command(['bash', str(SCRIPTS/'check-release.sh')], self.repo)
        head = self.git(self.repo, 'rev-parse', 'HEAD')
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), head)
        self.assertEqual((self.repo/'dist/revision.txt').read_text().strip(), head)

    def test_concurrent_main_change_rejects_push_and_preserves_candidate(self):
        self.candidate()
        newer = self.advance_main()
        result = self.run_command(['bash', str(SCRIPTS/'publish-history.sh')], self.repo, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('main changed', result.stdout)
        self.assertEqual(self.git(self.remote, 'rev-parse', 'main'), newer)
        self.assertTrue((self.repo/'data/runs/example.json').exists())

    def test_superseded_or_dirty_build_cannot_deploy(self):
        (self.repo/'src/example.txt').write_text('unvalidated')
        dirty = self.run_command(['bash', str(SCRIPTS/'check-release.sh')], self.repo, check=False)
        self.assertNotEqual(dirty.returncode, 0)
        self.assertIn('Build inputs changed', dirty.stdout)
        self.advance_main()
        stale = self.run_command(['bash', str(SCRIPTS/'check-release.sh')], self.repo, check=False)
        self.assertNotEqual(stale.returncode, 0)
        self.assertIn('superseded', stale.stdout)
        self.assertFalse((self.repo/'dist/revision.txt').exists())


if __name__ == '__main__':
    unittest.main()
