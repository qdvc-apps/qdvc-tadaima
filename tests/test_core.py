"""Display-free tests for config, index sync, and derivative generation."""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_config_roundtrip_and_validation():
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_CONFIG_HOME"] = tmp
        # Fresh import so it picks up the env.
        import importlib

        import qdvc.config as cfg_mod

        importlib.reload(cfg_mod)
        cfg = cfg_mod.Config()

        assert cfg.density == "compact"  # default
        cfg.density = "banana"           # invalid -> compact
        assert cfg.density == "compact"
        cfg.density = "relaxed"
        assert cfg.density == "relaxed"

        assert cfg.add_scanned_folder("/tmp/photos") is True
        assert cfg.add_scanned_folder("/tmp/photos") is False  # dedupe
        assert "/tmp/photos" in cfg.scanned_folders

        assert cfg.ui_backend == "gtk4"

        # Persisted across reloads.
        cfg2 = cfg_mod.Config()
        assert cfg2.density == "relaxed"
        assert "/tmp/photos" in cfg2.scanned_folders


def test_index_sync_and_thumbnail():
    from PIL import Image

    import qdvc.cache as cache_mod

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_CACHE_HOME"] = tmp
        import importlib

        importlib.reload(cache_mod)

        photos = os.path.join(tmp, "photos")
        os.makedirs(photos)
        img_path = os.path.join(photos, "one.png")
        Image.new("RGB", (1200, 800), (10, 120, 200)).save(img_path)

        index = cache_mod.Index()
        new, removed = cache_mod.sync_index(index, [photos])
        assert len(new) == 1
        assert not removed
        assert img_path in index.records

        rec = index.records[img_path]
        ok = cache_mod.generate_derivatives(rec, want_square=True)
        assert ok
        assert rec.thumb_path and os.path.exists(rec.thumb_path)
        assert rec.screen_path and os.path.exists(rec.screen_path)
        assert rec.square_path and os.path.exists(rec.square_path)

        # Square is exactly SQUARE_PX.
        with Image.open(rec.square_path) as sq:
            assert sq.size == (cache_mod.SQUARE_PX, cache_mod.SQUARE_PX)
        # Thumb within bounds.
        with Image.open(rec.thumb_path) as th:
            assert max(th.size) <= cache_mod.THUMB_MAX_PX

        # Persist + reload.
        index.save()
        index2 = cache_mod.Index()
        index2.load()
        assert img_path in index2.records

        # Removal cleans up.
        os.remove(img_path)
        new2, removed2 = cache_mod.sync_index(index2, [photos])
        assert img_path in removed2


if __name__ == "__main__":
    test_config_roundtrip_and_validation()
    test_index_sync_and_thumbnail()
    print("ALL CORE TESTS PASSED")
