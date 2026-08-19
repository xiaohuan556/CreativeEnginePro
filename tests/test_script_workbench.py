import unittest

from ai.script_workbench import (
    previous_script_version, save_script_version, script_metrics,
)


class ScriptWorkbenchContractTest(unittest.TestCase):
    def test_versions_are_deduplicated_and_recoverable(self):
        record = {}
        save_script_version(record, "第一稿")
        save_script_version(record, "第一稿")
        save_script_version(record, "第二稿", "改写")
        self.assertEqual(2, len(record["script_versions"]))
        self.assertEqual("第一稿", previous_script_version(record)["content"])
        self.assertEqual(2, record["script_version"])

    def test_metrics_make_workbench_state_visible(self):
        metrics = script_metrics("场景一：车站\n阿青：别回头。\n外景 雨夜")
        self.assertEqual(2, metrics["scenes"])
        self.assertEqual(2, metrics["dialogue_lines"])
        self.assertGreater(metrics["characters"], 0)


if __name__ == "__main__":
    unittest.main()
