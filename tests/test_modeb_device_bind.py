"""设备匿名ID↔座位绑定:用户数据层的地基(房主裁定 2026-07-22)。

产品要走账号/生态/画像,但账号层等首批真人局后再建;唯一"现在不做将来补不回"
的是给每一局的数据打上可回溯的设备锚点。App 首启生成永久 device_id,随事件
上报;服务端首见即绑、episode 落 device_bind 元信息行——账号上线按它认领历史局。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _session(tmp_path, names=("甲", "乙")):
    from modeb.simulator import Session
    return Session(list(names), 30, 6, [], "manual", tmp_path)


def test_bind_once_and_logged_to_episode(tmp_path):
    s = _session(tmp_path)
    s.bind_device("甲", "d-abc123")
    s.bind_device("甲", "d-abc123")      # 幂等:同设备重报不重写
    s.bind_device("甲", "d-other")       # 换设备不覆盖:归属争议留给账号层
    s.bind_device("路人", "d-zzz")       # 不在座:忽略
    s.bind_device("乙", "")              # 空 ID:忽略
    assert s.device_map == {"甲": "d-abc123"}
    lines = [json.loads(x) for x in s.episode_path.read_text(encoding="utf-8").splitlines()]
    binds = [x for x in lines if x.get("meta") == "device_bind"]
    assert len(binds) == 1 and binds[0]["player"] == "甲" and binds[0]["device_id"] == "d-abc123"


def test_device_id_stripped_from_event_stream(tmp_path):
    """device_id 是元信息:进绑定表,不进事件流(主持不该看见设备号)。"""
    s = _session(tmp_path)
    # 模拟 /api/event 分支的处理次序:先剥 device_id 绑定,再入队
    ev = {"type": "say", "player": "甲", "text": "来了", "device_id": "d-abc123"}
    dev = ev.pop("device_id", None)
    s.bind_device(ev.get("player"), dev)
    s.engine.push_event(ev)
    assert s.device_map["甲"] == "d-abc123"
    assert all("device_id" not in json.dumps(e) for e in s.engine.event_queue)
