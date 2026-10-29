"""Binary .fmesh blobs: exact round trip, old JSON blobs still load, size/speed vs JSON, lazy scene header.

Run: py -3 -m backend.test_storage_binary
"""
import json
import tempfile
import time
import unittest
from pathlib import Path

from . import storage


def grid_mesh(n_tris=200_000):
    """Triangle soup on a grid; coords are multiples of 1/64 so float32 holds them exactly."""
    w = int((n_tris / 2) ** 0.5)
    verts = [[x / 64, y / 64, ((x * y) % 17) / 64] for y in range(w + 1) for x in range(w + 1)]
    faces, uvs = [], []
    for y in range(w):
        for x in range(w):
            a = y * (w + 1) + x
            b, c, d = a + 1, a + w + 1, a + w + 2
            for tri in ([a, b, d], [a, d, c]):
                faces.append(tri)
                uvs.append([[verts[i][0], verts[i][1]] for i in tri])
    return {"verts": verts, "faces": faces, "face_mat": ["skin" if i % 3 else "default" for i in range(len(faces))],
            "uvs": uvs, "smooth": True, "attrs": {"w": [float(i % 5) / 4 for i in range(len(verts))],
                                                  "c": [[0.5, 0.25, 1.0] for _ in verts]}}


class StorageBinary(unittest.TestCase):
    def test_binary_roundtrip_exact(self):
        mesh = grid_mesh(2000)
        mesh["faces"].append([0, 1, 2, 3])  # mixed ngon
        mesh["face_mat"].append("skin")
        mesh["uvs"].append([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
        self.assertEqual(storage.decode(storage.encode(mesh)), mesh)
        self.assertEqual(storage.decode(storage.encode({"verts": [], "faces": [], "face_mat": [], "uvs": [], "smooth": False})),
                         {"verts": [], "faces": [], "face_mat": [], "uvs": [], "smooth": False})

    def test_reads_old_json_blob(self):
        mesh = grid_mesh(200)
        with tempfile.TemporaryDirectory() as d:
            raw = json.dumps(mesh, separators=(",", ":")).encode()
            import hashlib
            key = hashlib.sha256(raw).hexdigest()
            (Path(d) / "mesh-blobs").mkdir()
            (Path(d) / "mesh-blobs" / (key + ".json")).write_bytes(raw)
            data = storage.unpack({"nodes": [{"mesh": {"_forge_mesh_blob": key}}]}, d)
            self.assertEqual(data["nodes"][0]["mesh"], mesh)
            # next save migrates it to .fmesh
            packed = storage.pack(data, d)
            new_key = packed["nodes"][0]["mesh"]["_forge_mesh_blob"]
            self.assertTrue((Path(d) / "mesh-blobs" / (new_key + ".fmesh")).is_file())

    def test_lazy_header_and_repack(self):
        mesh = grid_mesh(200)
        with tempfile.TemporaryDirectory() as d:
            packed = storage.pack({"nodes": [{"mesh": mesh}], "prototypes": []}, d)
            key = packed["nodes"][0]["mesh"]["_forge_mesh_blob"]
            storage._refs.clear()
            data = storage.unpack(json.loads(json.dumps(packed)), d, lazy=True)
            lazy = data["nodes"][0]["mesh"]
            self.assertFalse(lazy.loaded)
            self.assertEqual(storage.pack(data, d)["nodes"][0]["mesh"]["_forge_mesh_blob"], key)
            self.assertFalse(lazy.loaded)  # repack of an untouched mesh never read it
            self.assertEqual(len(lazy["faces"]), len(mesh["faces"]))
            self.assertTrue(lazy.loaded)
            self.assertEqual(dict(lazy), mesh)

    def test_size_and_speed(self):
        mesh = grid_mesh(200_000)
        as_json = json.dumps(mesh, separators=(",", ":")).encode()
        blob = storage.encode(mesh)
        t = time.perf_counter(); json.loads(as_json); t_json = time.perf_counter() - t
        t = time.perf_counter(); storage.decode(blob); t_bin = time.perf_counter() - t
        with tempfile.TemporaryDirectory() as d:
            packed = storage.pack({"nodes": [{"mesh": mesh}]}, d)
            storage._refs.clear()
            t = time.perf_counter(); storage.unpack(packed, d, lazy=True); t_open = time.perf_counter() - t
        print(f"\n  200k tris: json {len(as_json)/1e6:.1f} MB {t_json*1000:.0f} ms | fmesh {len(blob)/1e6:.1f} MB "
              f"{t_bin*1000:.0f} ms full decode, {t_open*1000:.2f} ms scene open")
        self.assertGreaterEqual(len(as_json), 5 * len(blob))
        # Full decode still builds ~1.8M python floats, so it only beats json.loads by a margin; the 5x is scene open
        # (lazy: no mesh bytes touched until an object is used).
        self.assertGreaterEqual(t_json, 1.3 * t_bin)
        self.assertGreaterEqual(t_json, 5 * t_open)


if __name__ == "__main__":
    unittest.main()
