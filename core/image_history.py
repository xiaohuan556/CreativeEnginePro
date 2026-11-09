"""Immutable, shared compressed tiles for image history and save snapshots."""
import hashlib
import weakref
import zlib
import numpy as np


class Tile:
    def __init__(self, raw):
        sample = raw[:1024] + raw[-1024:]
        packed = zlib.compress(raw, 1) if len(zlib.compress(sample, 1)) < len(sample)*.65 else raw
        self.compressed = len(packed) < len(raw)
        self.data = packed if self.compressed else raw

    def unpack(self):
        return zlib.decompress(self.data) if self.compressed else self.data


class FrozenArray:
    def __init__(self, shape, dtype, tiles, source=None, store=None):
        self.shape, self.dtype, self._tiles = tuple(shape), np.dtype(dtype), tuple(tiles)
        self._source, self._store = source, store
        self.ndim = len(self.shape)
        self.nbytes = int(np.prod(shape)) * self.dtype.itemsize

    def copy(self, order='C'):
        if self._source is not None:
            return self._source.copy(order=order)
        result = np.empty(self.shape, self.dtype)
        for y, x, height, width, tile in self.tiles:
            shape = (height, width) + self.shape[2:]
            result[y:y+height, x:x+width] = np.frombuffer(tile.unpack(), self.dtype).reshape(shape)
        return result

    @property
    def tiles(self):
        self.freeze()
        return self._tiles

    def freeze(self):
        if self._source is not None:
            frozen = self._store.capture(self._source)
            self._tiles = frozen._tiles
            self._source = self._store = None

    def __array__(self, dtype=None, copy=None):
        result = self.copy()
        return result if dtype is None else result.astype(dtype, copy=False)

    def __getitem__(self, key):
        return self.copy()[key]

    def astype(self, dtype, **kwargs):
        return self.copy().astype(dtype, **kwargs)

    def __deepcopy__(self, memo):
        return self


class ImageHistoryStore:
    def __init__(self):
        self.tiles = weakref.WeakValueDictionary()
        self.pending = weakref.WeakSet()

    def defer(self, array):
        if isinstance(array, FrozenArray):
            return array
        result = FrozenArray(array.shape, array.dtype, (), source=array, store=self)
        self.pending.add(result)
        return result

    def freeze_pending(self):
        frozen = {}
        for item in tuple(self.pending):
            source = item._source
            if source is not None:
                prior = frozen.get(id(source))
                if prior is not None and prior[0] is source:
                    item._tiles = prior[1]._tiles
                    item._source = item._store = None
                else:
                    item.freeze()
                    frozen[id(source)] = (source, item)
        self.pending.clear()
        return frozen

    def capture(self, array):
        if array is None or isinstance(array, FrozenArray):
            return array
        array = np.asarray(array)
        parts = []
        for y in range(0, array.shape[0], 128):
            for x in range(0, array.shape[1], 128):
                region = array[y:y+128, x:x+128]
                raw = region.tobytes()
                key = (len(raw), hashlib.blake2b(raw, digest_size=16).digest())
                tile = self.tiles.get(key)
                if tile is None:
                    tile = Tile(raw)
                    self.tiles[key] = tile
                parts.append((y, x, region.shape[0], region.shape[1], tile))
        return FrozenArray(array.shape, array.dtype, parts)


def retained_bytes(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    if isinstance(value, FrozenArray):
        if value._source is not None:
            return retained_bytes(value._source, seen)
        return sum(retained_bytes(part[-1], seen) for part in value.tiles)
    if isinstance(value, Tile):
        return len(value.data)
    if isinstance(value, np.ndarray):
        return value.nbytes
    if isinstance(value, dict):
        return sum(retained_bytes(item, seen) for item in value.values())
    if isinstance(value, (list, tuple)):
        return sum(retained_bytes(item, seen) for item in value)
    return 0


class ImageHistoryOperations:
    def _snapshot_array(self, array):
        if array is None:
            return None
        if not getattr(self, '_snapshot_capturing', False):
            return array.copy()
        if not hasattr(self, '_history_store'):
            self._history_store = ImageHistoryStore()
        if not hasattr(self, '_metadata_array_cache'):
            self._metadata_array_cache = {}
        key = id(array)
        cached = self._metadata_array_cache.get(key)
        if getattr(self, '_snapshot_metadata_only', False) and cached and cached[0]() is array:
            return cached[1]
        if getattr(self, '_snapshot_metadata_only', False):
            frozen = (self._history_store.capture(array) if array.ndim == 2 or array.dtype.kind in 'bf'
                      else self._history_store.defer(array))
            self._metadata_array_cache[key] = (weakref.ref(array), frozen)
        else:
            prior = getattr(self, '_snapshot_frozen_arrays', {}).get(key)
            frozen = prior[1] if prior is not None and prior[0] is array else self._history_store.capture(array)
        return frozen

    def _snapshot(self, metadata_only=False):
        if not metadata_only:
            self._metadata_array_cache = {}
            if hasattr(self, '_history_store'):
                self._snapshot_frozen_arrays = self._history_store.freeze_pending()
        self._snapshot_metadata_only = metadata_only
        self._snapshot_capturing = True
        try:
            return self._capture_document_snapshot()
        finally:
            self._snapshot_capturing = False
            self._snapshot_metadata_only = False
            self._snapshot_frozen_arrays = {}

    def _snapshot_nested(self, value):
        if isinstance(value, (np.ndarray, FrozenArray)):
            return self._snapshot_array(value)
        if isinstance(value, dict):
            return {key: self._snapshot_nested(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._snapshot_nested(item) for item in value]
        import copy
        return copy.deepcopy(value)

