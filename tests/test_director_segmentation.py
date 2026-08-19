import json
import unittest
from types import SimpleNamespace

try:
    from ui.script_workbench import ScriptWorkbench, _DirectorWorker
    UI_AVAILABLE = True
except ModuleNotFoundError:
    UI_AVAILABLE = False


class _FakeCompletions:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        payload = self.payloads.pop(0)
        message = SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeClient:
    def __init__(self, payloads):
        self.chat = SimpleNamespace(completions=_FakeCompletions(payloads))


def _batch(prefix):
    return {
        "asset_inventory": {"used": {}, "not_ready": [], "missing": []},
        "characters": [],
        "shots": [
            {"duration": 7, "scene": f"{prefix}镜头一", "scene_name": "测试场景"},
            {"duration": 7, "scene": f"{prefix}镜头二", "scene_name": "测试场景"},
            {"duration": 7, "scene": f"{prefix}镜头三", "scene_name": "测试场景"},
        ],
    }


@unittest.skipUnless(UI_AVAILABLE, "script workbench dependencies are unavailable")
class DirectorSegmentationTests(unittest.TestCase):
    def test_duration_is_inferred_from_text_without_ui_setting(self):
        self.assertEqual(42, ScriptWorkbench._infer_director_duration("做一个42秒短剧"))
        self.assertEqual(90, ScriptWorkbench._infer_director_duration("总长1分30秒"))
        self.assertEqual(120, ScriptWorkbench._infer_director_duration("制作2 minutes视频"))
        self.assertEqual(30, ScriptWorkbench._infer_director_duration("一只机器人回到家"))

    def test_42_seconds_is_split_and_incrementally_saved(self):
        plan = {
            "title": "测试长分镜", "summary": "连续测试",
            "production_bible": {
                "logline": "机器人必须在雨停前找到回家的门。",
                "continuity_rules": ["白色外壳不得变化"],
            },
            "screenplay": {
                "hook": "雨水从室内天花板落下。",
                "beats": [{
                    "id": "beat_01", "start": 0, "end": 21,
                    "purpose": "建立目标", "summary": "机器人寻找出口",
                }],
            },
            "asset_inventory": {"used": {}, "not_ready": [], "missing": []},
            "characters": [],
            "segments": [
                {"index": 1, "summary": "前半段", "ending_state": "走到门口"},
                {"index": 2, "summary": "后半段", "ending_state": "故事结束"},
            ],
        }
        worker = _DirectorWorker("测试故事", "9:16", 42, "标准", "")
        partials = []
        finals = []
        worker.partial.connect(lambda board, _message: partials.append(board))
        worker.finished.connect(finals.append)
        client = _FakeClient([plan, _batch("前"), _batch("后")])

        worker._run_segmented(client, "fake-model", "")

        self.assertEqual(3, client.chat.completions.calls)
        self.assertEqual(1, len(partials))
        self.assertEqual("partial", partials[0]["_director_generation"]["status"])
        self.assertEqual(3, len(partials[0]["shots"]))
        self.assertEqual(1, len(finals))
        board = finals[0]
        self.assertEqual(42.0, board["duration"])
        self.assertEqual(6, len(board["shots"]))
        self.assertEqual(
            [0.0, 7.0, 14.0, 21.0, 28.0, 35.0],
            [shot["start"] for shot in board["shots"]])
        self.assertEqual("complete", board["_director_generation"]["status"])
        self.assertEqual(
            "机器人必须在雨停前找到回家的门。",
            board["production_bible"]["logline"])
        self.assertEqual("雨水从室内天花板落下。", board["screenplay"]["hook"])

    def test_resume_only_requests_remaining_segment(self):
        schedule = _DirectorWorker._segment_schedule(42)
        plan = {
            "title": "断点测试", "summary": "连续测试", "segments": schedule,
            "asset_inventory": {"used": {}, "not_ready": [], "missing": []},
            "characters": [],
        }
        first_worker = _DirectorWorker("测试故事", "9:16", 42, "标准", "")
        first_shots = _DirectorWorker._fit_segment_shots(
            _batch("前")["shots"], 0, 21)
        partial_board = first_worker._build_partial_board(
            plan, schedule, first_shots,
            _DirectorWorker._merge_inventory(plan["asset_inventory"]), [], 1, "partial")

        resumed = _DirectorWorker(
            "测试故事", "9:16", 42, "标准", "",
            resume_board=partial_board)
        finals = []
        resumed.finished.connect(finals.append)
        client = _FakeClient([_batch("后")])

        resumed._run_segmented(client, "fake-model", "")

        self.assertEqual(1, client.chat.completions.calls)
        self.assertEqual(6, len(finals[0]["shots"]))
        self.assertEqual("complete", finals[0]["_director_generation"]["status"])


if __name__ == "__main__":
    unittest.main()
