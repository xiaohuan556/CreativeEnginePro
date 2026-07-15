"""
GlobalFlux AI - 视频矩阵去重引擎
核心：感知哈希（pHash/dHash）+ 关键帧采样 + 变体策略
负责：检测批量视频中的相似内容，确保矩阵视频之间有足够差异
"""
import io
import json
import random
import subprocess
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from config import FFMPEG_BIN, FFPROBE_BIN


@dataclass
class VideoFingerprint:
    """视频指纹"""
    path: Path
    duration: float
    phash_list: list[str]      # 关键帧感知哈希列表
    file_hash: str              # 文件整体 MD5
    aspect_ratio: str = ""


@dataclass
class SimilarityResult:
    """相似度比对结果"""
    path_a: Path
    path_b: Path
    similarity: float           # 0.0 - 1.0
    identical_frames: int       # 完全相同的帧数


@dataclass
class DedupGroup:
    """去重分组"""
    group_id: int
    representative: Path        # 代表视频（用于保留）
    variants: list[Path]        # 变体视频列表
    similarity: float           # 组内最大相似度


class DedupEngine:
    """
    矩阵去重引擎

    使用感知哈希（dHash）提取关键帧视觉特征，
    计算视频间的汉明距离，支持不同去重策略。
    """

    def __init__(self, threshold: float = 0.95, sample_count: int = 5):
        """
        Args:
            threshold: 相似度阈值，超过此值认为重复（默认 0.95）
            sample_count: 每视频采样关键帧数量（默认 5）
        """
        self.threshold = threshold
        self.sample_count = sample_count

    # ── 公开 API ──────────────────────────────────────────────────────────────

    def scan_directory(
        self,
        directory: Path,
        extensions: tuple = (".mp4", ".mov", ".avi", ".mkv"),
    ) -> list[DedupGroup]:
        """
        扫描目录，返回相似视频分组。

        Args:
            directory: 视频目录
            extensions: 扫描的文件扩展名

        Returns:
            DedupGroup 列表，每组包含一个代表视频和若干变体
        """
        videos = []
        for ext in extensions:
            videos.extend(directory.glob(f"*{ext}"))
            videos.extend(directory.glob(f"*{ext.upper()}"))
        videos = sorted(set(videos))

        if len(videos) < 2:
            return []

        # 1. 提取指纹
        fingerprints = {}
        for v in videos:
            fp = self._extract_fingerprint(v)
            if fp:
                fingerprints[v] = fp

        # 2. 构建相似度矩阵，找出所有超过阈值的配对
        edges = []  # (path_a, path_b, similarity)
        keys = list(fingerprints.keys())
        for i in range(len(keys)):
            for j in range(i + 1, len(keys)):
                sim = self._compute_similarity(
                    fingerprints[keys[i]], fingerprints[keys[j]]
                )
                if sim >= self.threshold:
                    edges.append((keys[i], keys[j], sim))

        # 3. Union-Find 聚类
        groups = self._union_find_groups(keys, edges)
        return groups

    def generate_variant(
        self,
        source_video: Path,
        output_path: Optional[Path] = None,
        dedup_seed: Optional[int] = None,
    ) -> Path:
        """
        对单个视频应用防重复变体处理。

        Args:
            source_video: 源视频路径
            output_path: 输出路径
            dedup_seed: 随机种子

        Returns:
            变体视频路径
        """
        from core.mixer import VideoMixer

        if output_path is None:
            output_path = source_video.with_stem(source_video.stem + "_variant")

        seed = dedup_seed if dedup_seed is not None else random.randint(1, 999999)

        mixer = VideoMixer()
        return mixer.render_final_video(
            source_video=source_video,
            mixed_audio=source_video,  # 保持原音轨不变
            output_path=output_path,
            target_aspect="original",
            apply_dedup=True,
            dedup_seed=seed,
        )

    def batch_generate_variants(
        self,
        videos: list[Path],
        output_dir: Optional[Path] = None,
        variant_count: int = 5,
    ) -> list[Path]:
        """
        批量生成去重变体视频。

        Args:
            videos: 视频路径列表
            output_dir: 输出目录
            variant_count: 每个视频生成多少个变体

        Returns:
            所有变体视频路径列表
        """
        if output_dir is None:
            output_dir = Path("work_output/dedup_variants")
        output_dir.mkdir(parents=True, exist_ok=True)

        results = []
        for video in videos:
            for i in range(variant_count):
                seed = hash(video.name.encode()).__and__(0xFFFFFF) ^ (i * 17)
                out = output_dir / f"{video.stem}_v{i + 1:02d}{video.suffix}"
                try:
                    path = self.generate_variant(video, out, seed)
                    results.append(path)
                except Exception as e:
                    print(f"  ✗ {video.name} 变体 {i + 1} 生成失败: {e}")
        return results

    # ── 内部实现 ──────────────────────────────────────────────────────────────

    def _extract_fingerprint(self, video_path: Path) -> Optional[VideoFingerprint]:
        """提取视频指纹"""
        try:
            duration = self._get_duration(video_path)
            if duration <= 0:
                return None

            phash_list = []
            interval = duration / (self.sample_count + 1)
            for i in range(1, self.sample_count + 1):
                t = round(interval * i, 2)
                frame_hash = self._extract_frame_hash(video_path, t)
                if frame_hash:
                    phash_list.append(frame_hash)

            file_hash = self._file_md5(video_path)

            return VideoFingerprint(
                path=video_path,
                duration=duration,
                phash_list=phash_list,
                file_hash=file_hash,
            )
        except Exception as e:
            import logging; logging.getLogger("CreativeEnginePro").warning(f"  ⚠ 提取指纹失败 {video_path.name}: {e}")
            return None

    def _get_duration(self, video_path: Path) -> float:
        """获取视频时长"""
        try:
            result = subprocess.run(
                [
                    FFPROBE_BIN, "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    str(video_path),
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return float(result.stdout.strip())
        except Exception:
            import logging; logging.getLogger("CreativeEnginePro").debug("get_duration failed", exc_info=True)
            return 0.0

    def _extract_frame_hash(self, video_path: Path, timestamp: float) -> Optional[str]:
        """提取单帧画面并计算 dHash"""
        try:
            result = subprocess.run(
                [
                    FFMPEG_BIN, "-y",
                    "-ss", str(timestamp),
                    "-i", str(video_path),
                    "-vframes", "1",
                    "-f", "image2pipe",
                    "-vcodec", "png",
                    "-",
                ],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0 or not result.stdout:
                return None
            return self._dhash_bytes(result.stdout)
        except Exception:
            import logging; logging.getLogger("CreativeEnginePro").debug("extract_frame_hash failed", exc_info=True)
            return None

    def _dhash_bytes(self, image_data: bytes, size: int = 8) -> str:
        """
        差异哈希（dHash）— 纯 Python 实现，无需 PIL 依赖。
        通过比较相邻像素明暗关系生成哈希。
        """
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(image_data))
            img = img.convert("L").resize((size + 1, size))
            pixels = list(img.getdata())
            bits = []
            for row in range(size):
                for col in range(size):
                    left = pixels[row * (size + 1) + col]
                    right = pixels[row * (size + 1) + col + 1]
                    bits.append('1' if left > right else '0')
            return ''.join(bits)
        except ImportError:
            # 无 PIL，使用简化 MD5 哈希（精度降低但可用）
            return hashlib.md5(image_data[:4096]).hexdigest()[:size * size]
        except Exception:
            import logging; logging.getLogger("CreativeEnginePro").debug("_dhash_bytes failed", exc_info=True)
            return ""

    @staticmethod
    def _file_md5(path: Path) -> str:
        """计算文件 MD5（只读前 1MB+后 1MB）"""
        try:
            size = path.stat().st_size
            h = hashlib.md5()
            with open(path, "rb") as f:
                h.update(f.read(min(1 << 20, size)))
                if size > 1 << 21:
                    f.seek(size - (1 << 20))
                    h.update(f.read(1 << 20))
            return h.hexdigest()
        except Exception:
            import logging; logging.getLogger("CreativeEnginePro").debug("_file_md5 failed", exc_info=True)
            return ""

    def _compute_similarity(
        self,
        fp1: VideoFingerprint,
        fp2: VideoFingerprint,
    ) -> float:
        """计算两视频指纹的相似度（0.0-1.0）"""
        if not fp1.phash_list or not fp2.phash_list:
            return 0.0

        min_len = min(len(fp1.phash_list), len(fp2.phash_list))
        if min_len == 0:
            return 0.0

        total_sim = 0.0
        for i in range(min_len):
            h1, h2 = fp1.phash_list[i], fp2.phash_list[i]
            if len(h1) != len(h2) or len(h1) == 0:
                continue
            hamming = sum(a != b for a, b in zip(h1, h2))
            total_sim += 1.0 - hamming / len(h1)

        return total_sim / min_len

    def _union_find_groups(
        self,
        paths: list[Path],
        edges: list,
    ) -> list[DedupGroup]:
        """用 Union-Find 算法聚类相似视频"""
        parent = {p: p for p in paths}

        def find(x):
            if parent[x] != x:
                parent[x] = find(parent[x])
            return parent[x]

        def union(x, y):
            px, py = find(x), find(y)
            if px != py:
                parent[px] = py

        for a, b, _ in edges:
            union(a, b)

        # 收集组
        clusters = {}
        for p in paths:
            root = find(p)
            if root not in clusters:
                clusters[root] = []
            clusters[root].append(p)

        groups = []
        for i, (root, members) in enumerate(clusters.items()):
            if len(members) < 2:
                continue
            # 以文件最小的作为代表
            rep = min(members, key=lambda p: p.stat().st_size)
            groups.append(DedupGroup(
                group_id=i + 1,
                representative=rep,
                variants=members,
                similarity=1.0,
            ))

        return groups


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python dedup.py <视频目录> [--threshold 0.95]")
        sys.exit(1)

    directory = Path(sys.argv[1])
    threshold = 0.95
    if "--threshold" in sys.argv:
        idx = sys.argv.index("--threshold")
        threshold = float(sys.argv[idx + 1])

    engine = DedupEngine(threshold=threshold)
    groups = engine.scan_directory(directory)

    if not groups:
        print("未发现相似视频组。")
    else:
        print(f"发现 {len(groups)} 组相似视频：\n")
        for g in groups:
            print(f"组 #{g.group_id}（{len(g.variants)} 个视频）：")
            print(f"  代表视频: {g.representative.name}")
            print(f"  变体:")
            for v in g.variants:
                if v != g.representative:
                    print(f"    └── {v.name}")
            print()
