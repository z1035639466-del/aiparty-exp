"""脚本驱动器:确定性走完一局,验证引擎与钳制层(M1 验收用,非产品)。

覆盖:开局→抽原子→挑战→本地计票入事件→写分→技能授予与发动→「过」短路→
越界写分(钳制演示)→终局加冕礼。台词仅为占位,人格层归 prompt v0。
"""
from __future__ import annotations


class ScriptedDriver:
    def __init__(self) -> None:
        self.step = 0

    def decide(self, digest: dict, events: list[dict]) -> dict:
        s = self.step
        self.step += 1
        players = list(digest["scores"].keys())
        p1, p2, p3 = players[0], players[1], players[2]

        script = [
            # 0 开局:定焦点+开轮
            {"text": "各位,今晚的第一位幸运儿让骰子决定。",
             "tool_use": [{"name": "state.next_round", "input": {}},
                          {"name": "random.pick", "input": {"from": "players"}}]},
            # 1 抽原子给焦点人
            {"text": f"{p1},你的命运来了。",
             "tool_use": [{"name": "state.set_focus", "input": {"player": p1}},
                          {"name": "draw_atom", "input": {"野度": 6}}]},
            # 2 下发挑战+计时
            {"text": "二十秒,全场见证。",
             "tool_use": [{"name": "show", "input": {"content": "挑战开始", "visibility": "全场公开"}},
                          {"name": "timer", "input": {"seconds": 20, "label": "挑战"}}]},
            # 3 计票结果已在事件窗(本地计好),写分——外加一次越界写分演示钳制
            {"text": f"二比一,{p1} 过了!",
             "tool_use": [{"name": "state.add_score", "input": {"player": p1, "delta": 1, "reason": "挑战过"}},
                          {"name": "state.add_score", "input": {"player": p2, "delta": 5, "reason": "越界演示"}}]},
            # 4 技能授予:抽技能原子给 p2
            {"text": f"{p2},接住这件神器。",
             "tool_use": [{"name": "draw_atom", "input": {"atom_type": "技能授予", "grant_to": p2, "野度": 6}}]},
            # 5 技能发动:从 digest.grants 读实际授予件(协议正确用法,不写死)
            {"text": "仪式完整,发动生效!",
             "tool_use": ([{"name": "state.use_grant",
                            "input": {"prop": digest["grants"][-1]["prop"],
                                      "holder": digest["grants"][-1]["holder"]}}]
                          if digest.get("grants") else [])
                         + [{"name": "fx", "input": {"effect": "发动号角"}}]},
            # 6 p3 喊「过」→ 无代价跳过,换下一位
            {"text": f"{p3} 说过就过,规矩就是规矩,不追问不起哄。下一位!",
             "tool_use": [{"name": "state.next_round", "input": {}},
                          {"name": "random.pick", "input": {"from": "players", "exclude": [p3]}}]},
            # 7 再抽一条低野度原子
            {"text": "来个温柔的。",
             "tool_use": [{"name": "draw_atom", "input": {"野度": 3}}]},
            # 8 写分并收尾进终局
            {"text": "好,时间差不多,准备收尾。",
             "tool_use": [{"name": "state.add_score", "input": {"player": p3, "delta": 1, "reason": "温柔轮"}},
                          {"name": "show", "input": {"content": "收尾:今晚名场面回放", "visibility": "全场公开"}}]},
            # 9 终局:仪式不竞技(过程>结果),收局
            {"text": "今晚各位都很灵——收工!",
             "tool_use": [{"name": "fx", "input": {"effect": "彩带+闪光"}},
                          {"name": "state.finish", "input": {}}]},
        ]
        return script[min(s, len(script) - 1)]
