"""Indexed timeline queries used by the editor view.

The timeline model intentionally remains a simple, serializable collection of
tracks.  This module is the derived view index: it can be rebuilt after a model
edit and answers visible-range, hit-test, snap-boundary, and keyframe queries
without forcing the painter to scan every clip on every frame.
"""

from bisect import bisect_left, bisect_right


class TimelineIndex:
    """Sorted per-track index for time-based timeline queries."""

    def __init__(self, timeline):
        self.timeline = timeline
        self._tracks = {}
        self._starts = {}
        self._ends = {}
        self._prefix_max_ends = {}
        self._model_order = {}
        self._track_boundaries = {}
        self._boundaries = []
        self._keyframes = []
        self.total_duration = 0.0
        self.rebuild()

    @staticmethod
    def _end(item) -> float:
        return float(getattr(item, "timeline_end",
                            item.timeline_start + item.duration))

    def rebuild(self):
        self._tracks.clear()
        self._starts.clear()
        self._ends.clear()
        self._prefix_max_ends.clear()
        self._model_order.clear()
        self._track_boundaries.clear()
        boundaries = []
        keyframes = []
        total_duration = 0.0
        for kind, tracks in (("video", self.timeline.video_tracks),
                             ("audio", self.timeline.audio_tracks),
                             ("subtitle", self.timeline.subtitle_tracks)):
            for track_idx, track in enumerate(tracks):
                items = sorted(track, key=lambda item: item.timeline_start)
                key = (kind, track_idx)
                self._tracks[key] = items
                self._starts[key] = [float(item.timeline_start) for item in items]
                self._model_order[key] = {id(item): pos for pos, item in enumerate(track)}
                ends = [self._end(item) for item in items]
                self._ends[key] = ends
                prefix_max = []
                latest_end = float("-inf")
                for end in ends:
                    latest_end = max(latest_end, end)
                    prefix_max.append(latest_end)
                self._prefix_max_ends[key] = prefix_max
                track_boundaries = []
                for item in items:
                    item_id = id(item)
                    start = float(item.timeline_start)
                    end = self._end(item)
                    total_duration = max(total_duration, end)
                    track_boundaries.extend((start, end))
                    boundaries.extend(((start, item_id), (end, item_id)))
                    for values in (getattr(item, "keyframes", {}) or {}).values():
                        for rel_time, _value in values:
                            keyframes.append((start + float(rel_time), item_id))
                self._track_boundaries[key] = sorted(track_boundaries)
        self._boundaries = sorted(boundaries)
        self._keyframes = sorted(keyframes)
        self.total_duration = total_duration

    def has_boundary_between(self, kind: str, track_idx: int,
                             previous: float, current: float) -> bool:
        """Whether a clip on this track starts or ends in (previous, current]."""
        if current <= previous:
            return False
        values = self._track_boundaries.get((kind, int(track_idx)), ())
        return bisect_right(values, previous) < bisect_right(values, current)

    def clips(self, kind: str, track_idx: int, start=None, end=None) -> list:
        """Return clips intersecting [start, end], in timeline order."""
        key = (kind, int(track_idx))
        items = self._tracks.get(key, [])
        if start is None or end is None or not items:
            return list(items)
        lo, hi = sorted((float(start), float(end)))
        starts = self._starts[key]
        begin = max(0, bisect_left(starts, lo) - 1)
        result = []
        for item in items[begin:]:
            item_start = float(item.timeline_start)
            if item_start > hi:
                break
            if self._end(item) >= lo:
                result.append(item)
        return result

    def hit_test(self, kind: str, track_idx: int, sec: float) -> list:
        """Return all clips covering sec, topmost/latest first."""
        key = (kind, int(track_idx))
        items = self._tracks.get(key, [])
        if not items:
            return []
        starts = self._starts[key]
        pos = bisect_right(starts, float(sec))
        result = []
        for item in reversed(items[:pos]):
            if float(item.timeline_start) <= sec <= self._end(item):
                result.append(item)
        return result

    def active_at(self, kind: str, track_idx: int, sec: float) -> list:
        """Visible items active at sec, preserving their order in the model track.

        A prefix maximum lets the search stop as soon as all earlier clips
        have already ended. Long nonoverlapping tracks cost O(log n) per frame.
        """
        key = (kind, int(track_idx))
        items = self._tracks.get(key, ())
        if not items:
            return []
        position = bisect_right(self._starts[key], float(sec)) - 1
        max_ends = self._prefix_max_ends[key]
        ends = self._ends[key]
        matches = []
        while position >= 0 and max_ends[position] > sec:
            item = items[position]
            if ends[position] > sec and getattr(item, "visible", True):
                matches.append(item)
            position -= 1
        if len(matches) > 1:
            order = self._model_order[key]
            matches.sort(key=lambda item: order[id(item)])
        return matches

    @property
    def boundaries(self):
        return self._boundaries

    @property
    def keyframes(self):
        return self._keyframes

