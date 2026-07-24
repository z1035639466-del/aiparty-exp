"""演示图最后一公里(房主裁定 2026-07-24)。

上游一直是通的:patterns-v0.jsonl 里每张模式卡登记 demo_ref,draw_atom 把它带回 demo.ref,
主持 show(demo=...) 校验后落进公屏行,driver_llm 的工具声明与铁律都写了要配图——唯独最后
一公里断着:服务器没有伺服 demo/ 目录下图片文件的路由,demo_ref 是个死链接。

本测试只打这最后一公里:GET /demo/... 能不能把 demo/ 下的图原样(或 svg→png 换伺服)吐出来,
以及路径穿越挡不挡得住。风格随 test_modeb_lobby_scene 的最后一个测试:make_server 起真 HTTP。
"""
from __future__ import annotations

import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modeb.simulator import make_server  # noqa: E402

PNG_MAGIC = b"\x89PNG"


def _server(tmp_path):
    """起一台真服务器,返回 (base_url, srv)。demo/ 目录是仓库根现成资产,不必造 fixture。"""
    srv = make_server(0, tmp_path)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


def _get(base: str, path: str):
    """GET 一次,返回 (status, body_bytes, content_type)。用 urllib 而非 json——图片是二进制。"""
    req = urllib.request.Request(base + path, method="GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers.get("Content-Type", "")
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers.get("Content-Type", "")


def test_svg_ref_serves_prerendered_png(tmp_path):
    """demo_ref 登记的是 .svg(手绘原稿名),实际伺服的是 png/ 目录下同名预渲染 png——
    App 端拿到 demo_ref 原样拼 URL,零改写,路由自己把 .svg 换成 .png 伺服。"""
    base, _srv = _server(tmp_path)
    status, body, ctype = _get(base, "/demo/t1/card-stack-corners.svg")
    assert status == 200
    assert ctype.startswith("image/png")
    assert body[:4] == PNG_MAGIC, "svg 请求换伺服的应是真 png(PNG 魔数开头)"


def test_ai_png_ref_serves_directly(tmp_path):
    """pat-v1-03.png 这类 AI 图本来就是 png,直接原样伺服(不经过 svg→png 换伺服那步)。"""
    base, _srv = _server(tmp_path)
    status, body, ctype = _get(base, "/demo/t1/pat-v1-03.png")
    assert status == 200
    assert ctype.startswith("image/png")
    assert body[:4] == PNG_MAGIC


def test_t2_png_ref_serves(tmp_path):
    """demo/t2 目录同一套路由,不是 t1 专属特判。"""
    base, _srv = _server(tmp_path)
    status, body, ctype = _get(base, "/demo/t2/pat-v1-08.png")
    assert status == 200 and body[:4] == PNG_MAGIC


def test_path_traversal_dotdot_blocked(tmp_path):
    """`..` 穿越必须挡住:demo/../modeb/simulator.py 这种试图跳出 demo/ 目录读源码的
    请求一律 404,不许真读到仓库其他文件。"""
    base, _srv = _server(tmp_path)
    status, _body, _ct = _get(base, "/demo/../modeb/simulator.py")
    assert status == 404


def test_path_traversal_encoded_dotdot_blocked(tmp_path):
    """URL 编码穿越(%2e%2e)同样要挡住,不能靠字面 `..` 字符串匹配蒙混过关。"""
    base, _srv = _server(tmp_path)
    status, _body, _ct = _get(base, "/demo/%2e%2e/modeb/simulator.py")
    assert status == 404


def test_path_traversal_absolute_path_blocked(tmp_path):
    """伪装成绝对路径穿越(pathlib 的经典坑:Path(root)/'/etc/passwd' 会整个跳过 root)
    也要挡住,不许把 demo/ 之外的任意文件系统路径读出来。"""
    base, _srv = _server(tmp_path)
    status, _body, _ct = _get(base, "/demo//etc/passwd")
    assert status == 404


def test_missing_file_404(tmp_path):
    """资产册里没有、磁盘上也不存在的文件,老老实实 404,不报 500。"""
    base, _srv = _server(tmp_path)
    status, _body, _ct = _get(base, "/demo/t1/no-such-pattern.svg")
    assert status == 404
