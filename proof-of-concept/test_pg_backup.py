"""Backup failure handling and cron edits, with Docker/crontab commands mocked."""
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]


class PgBackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.bin=self.root/'bin';self.bin.mkdir()
        self.backups=self.root/'private backups'
        self.env=dict(os.environ,PATH=str(self.bin)+os.pathsep+os.environ['PATH'],
                      PG_BACKUP_DIR=str(self.backups),CRON_MAKE='/usr/bin/make',
                      FAKE_CRONTAB=str(self.root/'crontab'),FAKE_CRON_WRITES=str(self.root/'writes'))
        self.command('docker',"printf '%s\\n' '-- fixture SQL'\nexit \"${FAKE_DUMP_EXIT:-0}\"\n")
        self.command('crontab', '''if [ "$1" = '-l' ]; then
if [ "${FAKE_CRON_DENIED:-0}" = 1 ]; then echo 'permission denied' >&2; exit 1; fi
if [ -f "$FAKE_CRONTAB" ]; then cat "$FAKE_CRONTAB"; else echo 'no crontab for test' >&2; exit 1; fi
else cp "$1" "$FAKE_CRONTAB"; echo write >> "$FAKE_CRON_WRITES"; fi
''')

    def command(self,name,body):
        p=self.bin/name;p.write_text('#!/bin/sh\n'+body);p.chmod(0o700)

    def run_script(self,name,*args,ok=True):
        result=subprocess.run(['bash',str(ROOT/'utilities'/name),*args],env=self.env,capture_output=True,text=True)
        if ok:self.assertEqual(result.returncode,0,result.stderr+result.stdout)
        else:self.assertNotEqual(result.returncode,0)
        return result

    def test_backup_is_complete_zip_private_and_without_plaintext_residue(self):
        self.run_script('docker-pg-backup.sh')
        files=list(self.backups.iterdir());self.assertEqual(len(files),1)
        self.assertEqual(files[0].suffix,'.zip')
        with zipfile.ZipFile(files[0]) as archive:
            self.assertEqual(len(archive.namelist()),1)
            self.assertEqual(archive.read(archive.namelist()[0]),b'-- fixture SQL\n')
        self.assertEqual(stat.S_IMODE(self.backups.stat().st_mode),0o700)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode),0o600)

    def test_failed_dump_never_leaves_successful_backup_or_plaintext(self):
        self.env['FAKE_DUMP_EXIT']='9'
        result=self.run_script('docker-pg-backup.sh',ok=False)
        self.assertNotIn('Backup written',result.stdout)
        self.assertEqual(list(self.backups.iterdir()),[])

    def test_failed_zip_never_publishes_backup(self):
        self.command('zip','exit 7\n')
        self.run_script('docker-pg-backup.sh',ok=False)
        self.assertEqual(list(self.backups.iterdir()),[])

    def test_cron_preserves_other_jobs_and_ensure_is_idempotent(self):
        existing='0 2 * * * echo source # cozadzban-docker-pg-backup\n5 4 * * * echo other\n'
        cron=Path(self.env['FAKE_CRONTAB']);cron.write_text(existing)
        self.run_script('install-pg-backup-cron.sh','install')
        first=cron.read_text();self.assertTrue(first.startswith(existing))
        self.assertEqual(first.count('# fold-counter-docker-pg-backup'),1)
        self.assertIn('private\\ backups/cron.log',first)
        self.run_script('install-pg-backup-cron.sh','ensure')
        self.assertEqual(Path(self.env['FAKE_CRON_WRITES']).read_text().count('write'),1)
        self.run_script('install-pg-backup-cron.sh','install')
        self.assertEqual(cron.read_text(),first)

    def test_cron_read_errors_do_not_overwrite_jobs(self):
        cron=Path(self.env['FAKE_CRONTAB']);cron.write_text('other job\n')
        self.env['FAKE_CRON_DENIED']='1'
        self.run_script('install-pg-backup-cron.sh','install',ok=False)
        self.assertEqual(cron.read_text(),'other job\n')
        self.assertFalse(Path(self.env['FAKE_CRON_WRITES']).exists())

    def test_cron_rejects_injected_schedule(self):
        self.env['CRON_SCHEDULE']='0 2 * * *; echo bad'
        self.run_script('install-pg-backup-cron.sh','install',ok=False)
        self.assertFalse(Path(self.env['FAKE_CRONTAB']).exists())


if __name__=='__main__':unittest.main()
