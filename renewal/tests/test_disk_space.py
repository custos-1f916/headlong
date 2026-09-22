from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from custos_disk_space import observe, sample
from custos_observe import Observer
from custos_square import Store


class Native:
    def __init__(self):
        self.messages = {}

    def append(self, identity, envelope):
        self.messages[identity] = envelope
        return identity


class DiskTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.db.close)
        self.native = Native()

    def check(self, percent, budget=6):
        owner = Observer({}, self.store, None, self.native, now=1000)
        owner.remaining = budget
        with patch('custos_disk_space.os.statvfs', return_value=SimpleNamespace(
                f_blocks=1000, f_bfree=1000-percent*10,
                f_bavail=1000-percent*10, f_frsize=1024**2)):
            observe(owner)
        return owner

    def test_healthy_is_quiet_and_records_sample(self):
        self.check(79)
        self.assertEqual(self.native.messages, {})
        self.assertEqual(self.store.get('disk-space:latest')['used_percent'], 79)

    def test_threshold_escalation_and_quiet_repeats(self):
        for value in [80, 80, 85, 79, 81, 90, 89, 91, 95, 100]:
            self.check(value)
        self.assertEqual([x['threshold'] for x in self.native.messages.values()], [80, 90, 95])

    def test_initial_critical_emits_only_one_observation(self):
        self.check(100)
        self.assertEqual(len(self.native.messages), 1)
        self.assertEqual(next(iter(self.native.messages.values()))['threshold'], 95)

    def test_hysteresis_recovery_and_new_episode(self):
        for value in [80, 79, 75, 74, 74, 80]:
            self.check(value)
        self.assertEqual([x['threshold'] for x in self.native.messages.values()], [80, 0, 80])

    def test_observation_budget_failure_retries(self):
        self.check(80, budget=0)
        self.assertIsNone(self.store.get('disk-space:alert'))
        self.check(80)
        self.assertEqual(len(self.native.messages), 1)

    def test_recovery_budget_failure_retries(self):
        self.check(80)
        self.check(70, budget=0)
        self.assertEqual(self.store.get('disk-space:alert')['peak'], 80)
        self.check(70)
        self.assertEqual(self.store.get('disk-space:alert')['peak'], 0)

    def test_receipt_replay_does_not_duplicate_after_state_write_failure(self):
        original = self.store.put
        def fail(key, value):
            if key == 'disk-space:alert':
                raise OSError('interrupted')
            return original(key, value)
        with patch.object(self.store, 'put', side_effect=fail):
            with self.assertRaises(OSError):
                self.check(80)
        self.check(80)
        self.assertEqual(len(self.native.messages), 1)
        self.assertEqual(self.store.get('disk-space:alert')['sequence'], 1)

    def test_reserved_blocks_match_df_denominator(self):
        with patch('custos_disk_space.os.statvfs', return_value=SimpleNamespace(
                f_blocks=100, f_bfree=24, f_bavail=19, f_frsize=4096)):
            self.assertEqual(sample(1000)['used_percent'], 80)

    def test_stat_failure_is_not_a_healthy_reading(self):
        with patch('custos_disk_space.os.statvfs', side_effect=OSError('unavailable')):
            with self.assertRaises(OSError):
                observe(Observer({}, self.store, None, self.native, now=1000))
        self.assertIsNone(self.store.get('disk-space:latest'))


if __name__ == '__main__':
    unittest.main()
