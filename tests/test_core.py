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
        ok = cache_mod.generate_derivatives(rec)  # square always generated now
        assert ok
        assert rec.thumb_path and os.path.exists(rec.thumb_path)
        assert rec.screen_path and os.path.exists(rec.screen_path)
        assert rec.square_path and os.path.exists(rec.square_path)

        # Thumbnail dimensions are recorded so the UI can hug the image.
        assert rec.thumb_w > 0 and rec.thumb_h > 0

        # Small derivatives are low-quality JPEGs to save disk space.
        assert rec.thumb_path.endswith(".jpg")
        assert rec.square_path.endswith(".jpg")
        with Image.open(rec.thumb_path) as th:
            assert th.format == "JPEG"
        with Image.open(rec.square_path) as sqi:
            assert sqi.format == "JPEG"

        # Square is exactly SQUARE_PX (now 64).
        with Image.open(rec.square_path) as sq:
            assert sq.size == (cache_mod.SQUARE_PX, cache_mod.SQUARE_PX)
        assert cache_mod.SQUARE_PX == 64
        # Thumb within bounds and aspect-preserving (not cropped square).
        with Image.open(rec.thumb_path) as th:
            assert max(th.size) <= cache_mod.THUMB_MAX_PX
            assert th.size[0] != th.size[1]  # source was 1200x800

        # Persist + reload.
        index.save()
        index2 = cache_mod.Index()
        index2.load()
        assert img_path in index2.records

        # Removal cleans up.
        os.remove(img_path)
        new2, removed2 = cache_mod.sync_index(index2, [photos])
        assert img_path in removed2


def test_absorption_normalization():
    from qdvc.config import normalize_scanned_folders

    # Child absorbed by parent.
    out = normalize_scanned_folders(
        ["/home/james/photos", "/home/james", "/other"]
    )
    assert "/home/james" in out
    assert "/home/james/photos" not in out
    assert "/other" in out
    # Order preserved by first appearance among survivors.
    assert out == ["/home/james", "/other"]
    # Dedup.
    assert normalize_scanned_folders(["/a", "/a"]) == ["/a"]
    # Sibling-prefix is NOT absorption (/home/jamesX not under /home/james).
    out2 = normalize_scanned_folders(["/home/james", "/home/jamesX"])
    assert set(out2) == {"/home/james", "/home/jamesX"}


def test_content_sniffing_rejects_non_images():
    import importlib
    import tempfile

    import qdvc.cache as cache_mod

    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_CACHE_HOME"] = tmp
        importlib.reload(cache_mod)
        photos = os.path.join(tmp, "p")
        os.makedirs(photos)

        # A real JPEG.
        real = os.path.join(photos, "real.jpg")
        Image.new("RGB", (10, 10), (1, 2, 3)).save(real)
        # An AppleDouble sidecar with a .jpg name but non-image content.
        fake = os.path.join(photos, "._real.jpg")
        with open(fake, "wb") as fh:
            fh.write(b"\x00\x05\x16\x07" + b"\x00" * 40)
        # A text file renamed to .png.
        fake2 = os.path.join(photos, "notes.png")
        with open(fake2, "wb") as fh:
            fh.write(b"hello this is not an image")

        found = cache_mod.scan_images([photos])
        assert real in found
        assert fake not in found
        assert fake2 not in found
        assert cache_mod.looks_like_image(real) is True
        assert cache_mod.looks_like_image(fake) is False


def test_has_all_derivatives_and_dir_name():
    import importlib
    import tempfile

    import qdvc.cache as cache_mod

    from PIL import Image

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["XDG_CACHE_HOME"] = tmp
        importlib.reload(cache_mod)
        # Derivatives live under a folder named exactly "derivatives".
        assert cache_mod.derivatives_dir().name == "derivatives"

        photos = os.path.join(tmp, "p")
        os.makedirs(photos)
        p = os.path.join(photos, "one.jpg")
        Image.new("RGB", (40, 30), (9, 9, 9)).save(p)
        idx = cache_mod.Index()
        cache_mod.sync_index(idx, [photos])
        rec = idx.records[p]
        assert cache_mod.has_all_derivatives(rec) is False  # not generated yet
        cache_mod.generate_derivatives(rec)
        assert cache_mod.has_all_derivatives(rec) is True


if __name__ == "__main__":
    test_config_roundtrip_and_validation()
    test_index_sync_and_thumbnail()
    test_absorption_normalization()
    test_content_sniffing_rejects_non_images()
    test_has_all_derivatives_and_dir_name()
    print("ALL CORE TESTS PASSED")
