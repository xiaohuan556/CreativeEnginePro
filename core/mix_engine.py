"""
mix_engine.py — 视频混剪核心引擎
负责：素材管理、排列组合计算、FFmpeg 调用、防重复算法
"""

import os
import math
import random
import subprocess
import tempfile
from datetime import datetime


# ==================== 素材数据结构 ====================

class ClipMaterial:
    def __init__(self, path: str, start: float = 0.0, end: float = None):
        self.path = path
        self.name = os.path.basename(path)
        self.start = start
        self.end = end
        self._duration = None

    @property
    def usable_duration(self):
        if self.end is None:
            return max(0.0, (self._duration or 0) - self.start)
        return max(0.0, self.end - self.start)


# ==================== 五种混剪模式 ====================

class MixMode:
    MODE_A = "模式A：固定开头结尾，随机中间"
    MODE_B = "模式B：固定中间，随机开头结尾"
    MODE_C = "模式C：随机拼接达到目标时长"
    MODE_D = "模式D：固定开头，随机后面"
    MODE_E = "模式E：固定结尾，随机前面"

    @staticmethod
    def get_slots(mode: str, total: float = 15.0,
                  seg_a: tuple = (3.0, 7.0, 5.0),
                  seg_b: tuple = (3.0, 7.0, 5.0)) -> list:
        if mode == MixMode.MODE_A:
            head, mid, tail = seg_a
            return [
                {'role': 'fixed',  'duration': head, 'label': f'开头({head}s)'},
                {'role': 'random', 'duration': mid,  'label': f'中间({mid}s)'},
                {'role': 'fixed',  'duration': tail, 'label': f'结尾({tail}s)'},
            ]
        elif mode == MixMode.MODE_B:
            head, mid, tail = seg_b
            return [
                {'role': 'random', 'duration': head, 'label': f'开头({head}s)'},
                {'role': 'fixed',  'duration': mid,  'label': f'中间({mid}s)'},
                {'role': 'random', 'duration': tail, 'label': f'结尾({tail}s)'},
            ]
        elif mode == MixMode.MODE_C:
            return [
                {'role': 'random', 'duration': total, 'label': f'随机拼接({total}s)'},
            ]
        elif mode == MixMode.MODE_D:
            # seg_a[0] 作为固定开头时长，随机段 = total - head（total 由用户在UI填写）
            head = seg_a[0]
            rest = round(max(0.5, total - head), 4)
            return [
                {'role': 'fixed',  'duration': head, 'label': f'开头({head}s)'},
                {'role': 'random', 'duration': rest, 'label': f'后段({rest}s)'},
            ]
        elif mode == MixMode.MODE_E:
            # seg_a[2] 作为固定结尾时长，随机段 = total - tail（total 由用户在UI填写）
            tail = seg_a[2]
            rest = round(max(0.5, total - tail), 4)
            return [
                {'role': 'random', 'duration': rest, 'label': f'前段({rest}s)'},
                {'role': 'fixed',  'duration': tail, 'label': f'结尾({tail}s)'},
            ]
        return []


# ==================== 排列组合计算器 ====================

class ComboCalculator:

    @staticmethod
    def min_materials_needed(target_count: int, random_slot_count: int) -> int:
        if random_slot_count <= 0:
            return 0
        raw = math.ceil(target_count ** (1.0 / random_slot_count))
        return max(raw, int(raw * 1.5))

    @staticmethod
    def estimate_info(mode: str, fixed_materials: dict,
                      random_materials: list, target_count: int,
                      seg_a=(3.0, 7.0, 5.0), seg_b=(3.0, 7.0, 5.0),
                      total=15.0, max_reuse=2) -> dict:
        slots = MixMode.get_slots(mode, total, seg_a, seg_b)
        random_slots   = [s for s in slots if s['role'] == 'random']
        n_random_slots = len(random_slots)
        n_random_mats  = len(random_materials)

        if n_random_slots == 0:
            return {
                'max_unique': 1,
                'need_random_mats': 0,
                'is_feasible': target_count <= 1,
                'message': '该模式无随机槽，只能生成1条视频。'
            }

        
        segments_per_mat = 3
        effective_pool   = n_random_mats * min(max_reuse, segments_per_mat)
        max_unique       = effective_pool ** n_random_slots
        need             = ComboCalculator.min_materials_needed(target_count, n_random_slots)

        is_feasible = max_unique >= target_count
        if is_feasible:
            msg = (f"{n_random_mats} 条随机素材（每条最多用 {max_reuse} 次），"
                   f"理论最多 {max_unique} 条不重复视频，满足目标 {target_count} 条。")
        else:
            msg = (f"素材不足！理论最多 {max_unique} 条，"
                   f"建议至少准备 {need} 条随机素材。")

        return {
            'max_unique': max_unique,
            'need_random_mats': need,
            'is_feasible': is_feasible,
            'message': msg,
        }


# ==================== 任务生成器 ====================

class TaskGenerator:

    def __init__(self, mode: str, fixed_materials: dict, random_materials: list,
                 seg_a=(3.0, 7.0, 5.0), seg_b=(3.0, 7.0, 5.0), total=15.0):
        self.mode      = mode
        self.fixed     = fixed_materials
        self.randoms   = random_materials
        self.seg_a     = seg_a
        self.seg_b     = seg_b
        self.total     = total
        self.slots_def = MixMode.get_slots(mode, total, seg_a, seg_b)

    def _get_fixed_mat(self, slot_label: str):
        label = slot_label.lower()
        if '开头' in label:
            return self.fixed.get('head')
        elif '中间' in label:
            return self.fixed.get('mid')
        elif '结尾' in label:
            return self.fixed.get('tail')
        return None

    def _pick_segment(self, mat: ClipMaterial, duration: float,
                      used_segments: set, offset_step: float = 1.0) -> tuple:
        usable           = mat.usable_duration
        max_start_offset = max(0.0, usable - duration)

        candidates = []
        t = 0.0
        while t <= max_start_offset + 0.01:
            candidates.append(round(t, 2))
            t += offset_step
        random.shuffle(candidates)

        for offset in candidates:
            seg_key = f"{mat.path}@{offset:.2f}"
            if seg_key not in used_segments:
                return (mat.start + offset, mat.start + offset + duration, seg_key)

        # 兜底：强制选一个
        offset    = random.uniform(0, max_start_offset)
        seg_key   = f"{mat.path}@{offset:.2f}_forced"
        return (mat.start + offset, mat.start + offset + duration, seg_key)

    def generate(self, count: int, max_reuse: int = 2) -> list:
        tasks         = []
        global_used   = set()
        mat_use_count = {id(m): 0 for m in self.randoms}

        for i in range(count):
            task_slots = []
            video_used = set()

            for slot in self.slots_def:
                role     = slot['role']
                duration = slot['duration']
                label    = slot['label']

                if role == 'fixed':
                    mat = self._get_fixed_mat(label)
                    if mat is None:
                        task_slots.append(None)
                        continue
                    abs_start = mat.start
                    task_slots.append({
                        'mat': mat,
                        'start': abs_start,
                        'end': abs_start + duration,
                        'duration': duration,
                        'role': 'fixed',
                        'label': label,
                    })

                else:
                    if not self.randoms:
                        task_slots.append(None)
                        continue

                    shuffled = self.randoms[:]
                    random.shuffle(shuffled)

                    chosen_mat = chosen_start = chosen_end = chosen_key = None

                    # 第一轮：次数未超限 + 时间段全局未用
                    for mat in shuffled:
                        if mat_use_count[id(mat)] >= max_reuse:
                            continue
                        s, e, key = self._pick_segment(
                            mat, duration, global_used | video_used)
                        if key not in video_used:
                            chosen_mat, chosen_start, chosen_end, chosen_key = mat, s, e, key
                            break

                    # 第二轮：次数未超限，放宽时间段限制
                    if chosen_mat is None:
                        for mat in shuffled:
                            if mat_use_count[id(mat)] >= max_reuse:
                                continue
                            s, e, key = self._pick_segment(mat, duration, video_used)
                            chosen_mat, chosen_start, chosen_end, chosen_key = mat, s, e, key
                            break

                    # 兜底：所有素材都超限，选使用次数最少的
                    if chosen_mat is None:
                        least = min(shuffled, key=lambda m: mat_use_count[id(m)])
                        s, e, key = self._pick_segment(least, duration, video_used)
                        chosen_mat, chosen_start, chosen_end, chosen_key = least, s, e, key

                    video_used.add(chosen_key)
                    global_used.add(chosen_key)
                    mat_use_count[id(chosen_mat)] += 1

                    task_slots.append({
                        'mat': chosen_mat,
                        'start': chosen_start,
                        'end': chosen_end,
                        'duration': duration,
                        'role': 'random',
                        'label': label,
                    })

            tasks.append(task_slots)

        return tasks


# ==================== FFmpeg 执行器 ====================

class FFmpegMixer:

    def __init__(self, ffmpeg_path: str = 'ffmpeg', log_callback=None):
        self.ffmpeg = ffmpeg_path
        self.log    = log_callback or (lambda msg: None)

    def check_ffmpeg(self) -> bool:
        try:
            r = subprocess.run([self.ffmpeg, '-version'],
                               capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            import logging; logging.getLogger("CreativeEnginePro").debug("check_ffmpeg failed", exc_info=True)
            return False

    def render_task(self, task_slots: list, audio_path: str,
                    out_path: str, total_duration: float = 15.0,
                    target_w: int = 1080, target_h: int = 1920) -> bool:
        if not task_slots or any(s is None for s in task_slots):
            self.log(f"任务包含空槽，跳过: {out_path}")
            return False

        tmpdir        = tempfile.mkdtemp()
        segment_files = []

        try:
            # 步骤1：截取各段
            for idx, slot in enumerate(task_slots):
                mat       = slot['mat']
                seg_start = slot['start']
                seg_dur   = slot['end'] - slot['start']
                seg_out   = os.path.join(tmpdir, f"seg_{idx:02d}.mp4")

                vf = (
                    f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                    f"crop={target_w}:{target_h},fps=30"
                )
                cmd = [
                    self.ffmpeg, '-y',
                    '-ss', str(seg_start),
                    '-i', mat.path,
                    '-t', str(seg_dur),
                    '-vf', vf,
                    '-an',
                    '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                    '-pix_fmt', 'yuv420p',
                    seg_out
                ]
                ret = subprocess.run(cmd, capture_output=True, timeout=120,
                     env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
                if ret.returncode != 0:
                    self.log(f"截取失败: {mat.name} [{seg_start:.1f}s]")
                    self.log(ret.stderr.decode('utf-8', errors='ignore')[-300:])
                    return False
                segment_files.append(seg_out)

            # 步骤2：concat 拼接
            concat_list = os.path.join(tmpdir, "concat.txt")
            with open(concat_list, 'w', encoding='utf-8') as f:
                for sf in segment_files:
                    f.write(f"file '{sf}'\n")

            merged = os.path.join(tmpdir, "merged.mp4")
            ret = subprocess.run([
                self.ffmpeg, '-y', '-f', 'concat', '-safe', '0',
                '-i', concat_list, '-c', 'copy', merged
            ], capture_output=True, timeout=120)
            if ret.returncode != 0:
                self.log("拼接失败")
                return False

            # 步骤3：替换音频
            if audio_path and os.path.exists(audio_path):
                cmd_audio = [
                    self.ffmpeg, '-y',
                    '-i', merged, '-i', audio_path,
                    '-map', '0:v:0', '-map', '1:a:0',
                    '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k',
                    '-t', str(total_duration), '-shortest',
                    out_path
                ]
            else:
                cmd_audio = [
                    self.ffmpeg, '-y',
                    '-i', merged,
                    '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
                    '-map', '0:v:0', '-map', '1:a:0',
                    '-c:v', 'copy', '-c:a', 'aac', '-b:a', '128k',
                    '-t', str(total_duration), '-shortest',
                    out_path
                ]
            ret = subprocess.run(cmd_audio, capture_output=True, timeout=120)
            if ret.returncode != 0:
                self.log("音频合并失败")
                return False

            return True

        except subprocess.TimeoutExpired:
            self.log(f"FFmpeg 超时: {out_path}")
            return False
        except Exception as e:
            self.log(f"渲染异常: {e}")
            return False
        finally:
            for sf in segment_files:
                try: os.remove(sf)
                except Exception: import logging; logging.getLogger("CreativeEnginePro").debug("segment cleanup failed", exc_info=True)
            for tmp in ['merged.mp4', 'concat.txt']:
                try: os.remove(os.path.join(tmpdir, tmp))
                except Exception: import logging; logging.getLogger("CreativeEnginePro").debug("merged/concat cleanup failed", exc_info=True)
            try: os.rmdir(tmpdir)
            except Exception: import logging; logging.getLogger("CreativeEnginePro").debug("tmpdir rmdir failed", exc_info=True)
