import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from frame_retention import enforce_image_limit


class ImageLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, size=10, age=1, body=None):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body if body is not None else b'x' * size)
        os.utime(path, (age, age))
        return path

    def test_oldest_images_only_and_durable_error_event(self):
        oldest = self.write('frames/one/frame.jpg', age=1)
        middle = self.write('camera-review/two/clothing.png', age=2)
        newest = self.write('test-evidence/three/frame.jpg', age=3)
        metadata = self.write('frames/one/observation.json', size=1000)
        queue = self.write('outbox/pending.json', size=1000)
        with self.assertLogs(level='ERROR') as logs:
            result = enforce_image_limit(self.root, 20)
        self.assertFalse(oldest.exists())
        self.assertTrue(middle.exists() and newest.exists() and metadata.exists() and queue.exists())
        self.assertEqual(result['after_bytes'], 20)
        self.assertIn('image_storage_limit_exceeded', logs.output[0])
        with sqlite3.connect(self.root/'frame-retention.sqlite3') as conn:
            saved = json.loads(conn.execute('SELECT details FROM image_storage_events').fetchone()[0])
        self.assertEqual(saved['deleted_files'], ['frames/one/frame.jpg'])
        self.assertEqual(saved['event'], 'image_storage_trimmed')

    def test_under_or_exact_limit_does_not_delete_or_log(self):
        image = self.write('frame.jpg', 10)
        with self.assertNoLogs(level='ERROR'):
            result = enforce_image_limit(self.root, 10)
        self.assertTrue(image.exists())
        self.assertEqual(result['deleted_files'], [])

    def test_extensionless_archived_images_count(self):
        image = self.write('official/blobs/abcd', body=b'\x89PNG\r\n\x1a\nxxxx')
        html = self.write('official/blobs/efgh', body=b'<html>archive</html>')
        with self.assertLogs(level='ERROR'):
            result = enforce_image_limit(self.root, 1)
        self.assertFalse(image.exists())
        self.assertTrue(html.exists())
        self.assertEqual(result['after_bytes'], 0)

    def test_symlinks_and_external_regression_data_excluded(self):
        external = self.root.parent / (self.root.name + '-external')
        external.mkdir()
        self.addCleanup(lambda: external.rmdir())
        image = external/'fixture.jpg'
        image.write_bytes(b'x'*100)
        self.addCleanup(image.unlink)
        (self.root/'linked-directory').symlink_to(external, target_is_directory=True)
        (self.root/'linked.jpg').symlink_to(image)
        with self.assertNoLogs(level='ERROR'):
            result = enforce_image_limit(self.root, 1)
        self.assertEqual(result['before_bytes'], 0)
        self.assertTrue(image.exists())

    def test_failure_logs_unmet_limit_without_deleting_metadata(self):
        image = self.write('frame.jpg')
        metadata = self.write('observation.json')
        with patch.object(Path, 'unlink', side_effect=PermissionError('denied')), \
                self.assertLogs(level='ERROR') as logs:
            result = enforce_image_limit(self.root, 1)
        self.assertEqual(result['event'], 'image_storage_limit_unmet')
        self.assertEqual(len(result['errors']), 1)
        self.assertIn('image_storage_limit_unmet', logs.output[-1])
        self.assertTrue(image.exists() and metadata.exists())

    def test_restart_rechecks_remaining_images(self):
        self.write('old.jpg', 10, age=1)
        self.write('new.jpg', 10, age=2)
        with self.assertLogs(level='ERROR'):
            enforce_image_limit(self.root, 10)
        with self.assertNoLogs(level='ERROR'):
            self.assertEqual(enforce_image_limit(self.root, 10)['before_bytes'], 10)


if __name__ == '__main__':
    unittest.main()
