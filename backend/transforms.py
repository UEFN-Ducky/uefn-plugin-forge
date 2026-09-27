"""Affine transforms matching the kernel's Rz * Ry * Rx convention."""
import math

from .kernel.mesh import _euler


def multiply(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def world(scene, nid):
    out = [[float(i == j) for j in range(4)] for i in range(4)]
    chain, seen = [], set()
    while nid:
        if nid in seen:
            raise ValueError("Scene hierarchy contains a cycle")
        seen.add(nid)
        node = scene.node(nid)
        if not node:
            raise ValueError("Missing parent in scene hierarchy")
        chain.append(node)
        nid = node.get("parent")
    for node in reversed(chain):
        t = node["transform"]
        r = _euler(t["rot"])
        local = [[r[i][j] * t["scale"][j] for j in range(3)] + [t["pos"][i]] for i in range(3)]
        out = multiply(out, local + [[0, 0, 0, 1]])
    return out


def inverse(m):
    a = [row[:] + [float(i == j) for j in range(4)] for i, row in enumerate(m)]
    for col in range(4):
        pivot = max(range(col, 4), key=lambda i: abs(a[i][col]))
        if abs(a[pivot][col]) < 1e-10:
            raise ValueError("Cannot reparent through a group with zero scale")
        a[col], a[pivot] = a[pivot], a[col]
        v = a[col][col]
        a[col] = [x / v for x in a[col]]
        for row in range(4):
            if row != col:
                v = a[row][col]
                a[row] = [x - v * y for x, y in zip(a[row], a[col])]
    return [row[4:] for row in a]


def decompose(m):
    scale = [math.sqrt(sum(m[i][j] ** 2 for i in range(3))) for j in range(3)]
    if min(scale) < 1e-10:
        raise ValueError("Cannot reparent an object with zero scale")
    det = sum(m[0][i] * (m[1][(i+1)%3] * m[2][(i+2)%3] - m[1][(i+2)%3] * m[2][(i+1)%3]) for i in range(3))
    if det < 0:
        scale[0] *= -1
    r = [[m[i][j] / scale[j] for j in range(3)] for i in range(3)]
    if any(abs(sum(r[k][i] * r[k][j] for k in range(3))) > 1e-6 for i, j in ((0, 1), (0, 2), (1, 2))):
        raise ValueError("Reparenting would shear this object; use uniform group scale first")
    y = math.asin(max(-1, min(1, -r[2][0])))
    if abs(r[2][0]) < 0.9999999:
        x, z = math.atan2(r[2][1], r[2][2]), math.atan2(r[1][0], r[0][0])
    else:
        x, z = 0, math.atan2(-r[0][1], r[1][1])
    return {"pos": [m[i][3] for i in range(3)], "rot": [math.degrees(v) for v in (x, y, z)], "scale": scale}
