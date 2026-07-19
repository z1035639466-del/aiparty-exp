"""M1 手拌佐料(立项原案:raw 直接进锅,10–20 条;M3 换 atoms.sqlite,接口不变)。

来源:xhs 采集件抽样(atoms-sample-v0)改写皮 + 扩编批已照准道具原语(#1/#9/#38/#42/#50 等)。
字段为原子 schema v0.1 子集;skill 型带 prop/uses/ritual(权力型惩罚)。
惩罚货币多元:喝/表演/怪造型/服务平级(验收单 frame 声明,不写死酒)。
"""
from __future__ import annotations

SEED_ATOMS: list[dict] = [
    {"id": "seed-01", "name": "冰块贴身", "type": "道具挑战", "text": "从杯里取一块冰,放在自己选定的位置坚持十秒", "wildness": 5, "props": ["冰块"], "safety": [], "currency": "表演"},
    {"id": "seed-02", "name": "行走的弹幕", "type": "任务内容", "text": "接下来两轮,你说的每句话都必须以「家人们」开头", "wildness": 2, "props": [], "safety": [], "currency": "表演"},
    {"id": "seed-03", "name": "分贝挑战", "type": "道具挑战", "text": "起立,用最大声喊出指定台词,分贝须超过上一位", "wildness": 4, "props": [], "safety": [], "currency": "表演"},
    {"id": "seed-04", "name": "三刀流", "type": "完整玩法", "text": "用三个瓶子摆出三刀流造型并说一句台词,全场投票像不像", "wildness": 4, "props": ["瓶子"], "safety": [], "currency": "喝"},
    {"id": "seed-05", "name": "逢七过", "type": "完整玩法", "text": "轮流报数,逢7与7的倍数敲杯代替,错者受罚", "wildness": 3, "props": ["杯子"], "safety": ["饮酒"], "currency": "喝"},
    {"id": "seed-06", "name": "语调模仿", "type": "问答题目", "text": "模仿指定歌手的语调说一句日常话,全场评谁最像", "wildness": 2, "props": [], "safety": [], "currency": "表演"},
    {"id": "seed-07", "name": "抢答歌词", "type": "完整玩法", "text": "报歌名,最快唱出下一句歌词者赢,最慢者受罚", "wildness": 3, "props": [], "safety": [], "currency": "喝"},
    {"id": "seed-08", "name": "定格十秒", "type": "条件点名", "text": "音乐停的瞬间全场定格十秒,先动者受罚", "wildness": 3, "props": [], "safety": [], "currency": "怪造型"},
    {"id": "seed-09", "name": "反差自拍", "type": "任务内容", "text": "摆出与你人设反差最大的表情拍一张,进本局相册", "wildness": 2, "props": ["手机"], "safety": [], "currency": "表演"},
    {"id": "seed-10", "name": "侍从十分钟", "type": "任务内容", "text": "接下来十分钟为左手边的人当侍从,负责递东西", "wildness": 2, "props": [], "safety": [], "currency": "服务"},
    # —— 通用游戏层(房主裁定 2026-07-18:挑战前先来通用局赌出输家;骰与拳走 random 工具保公平) ——
    {"id": "open-01", "name": "吹牛骰", "type": "完整玩法", "text": "每人用系统暗骰一把(random),轮流吹报全场总点数,被抓包或冤枉人者为输家,输家接惩罚", "wildness": 3, "props": [], "safety": [], "currency": "喝", "opener": True},
    {"id": "open-02", "name": "十五二十", "type": "完整玩法", "text": "两人对喊十五二十(出零五十配合喊数),喊中者胜,负者为输家接惩罚", "wildness": 3, "props": [], "safety": [], "currency": "喝", "opener": True},
    {"id": "open-03", "name": "石头剪刀布擂台", "type": "完整玩法", "text": "全员两两对拳打擂台,连败到底的为输家接惩罚", "wildness": 2, "props": [], "safety": [], "currency": "表演", "opener": True},
    {"id": "open-05", "name": "分队车轮战", "type": "完整玩法", "text": "两人石头剪刀布争队长,赢家先挑人轮流组队;两队轮流派人1v1对决(划拳/比拼皆可),败方受惩罚——合作结构,队友共担", "wildness": 3, "props": [], "safety": [], "currency": "喝", "opener": True},
    {"id": "open-04", "name": "抓手指", "type": "完整玩法", "text": "一人摊开手掌,其余人把食指放掌下;摊掌者报数或喊关键词后突然抓,被抓住的为输家接惩罚(xhs 语料本家玩法,房主口述规则)", "wildness": 2, "props": [], "safety": [], "currency": "表演", "opener": True},
    {"id": "open-06", "name": "快枪手对决", "type": "完整玩法", "text": "两人各持手机对峙:西部BGM响起,系统在随机鼓点亮出「拔!」,先拍屏者胜(系统按毫秒判先后,抢跑判负),慢者受罚(房主设计,手机原生通用游戏旗舰件)", "wildness": 2, "props": ["手机"], "safety": [], "currency": "表演", "opener": True},
    # —— 权力型(扩编批照准件,skill 授予) ——
    {"id": "prop-01", "name": "时间暂停器", "type": "技能授予", "text": "喊「时间停止!」全场定格十秒,动者受罚;发动者窗口内可自由行动一次", "wildness": 4, "props": ["遥控器", "打火机"], "safety": [], "currency": "怪造型", "skill": {"prop": "时间暂停器", "uses": 2, "ritual": "双手交叉猛地展开+高喊台词,缺一发动失败反罚自己"}},
    {"id": "prop-09", "name": "甩锅大法", "type": "技能授予", "text": "受罚时做华丽甩锅动作把惩罚转给邻座,每局限用", "wildness": 3, "props": [], "safety": [], "currency": "喝", "skill": {"prop": "甩锅大法", "uses": 1, "ritual": "双手作抛物线状甩向目标并喊「锅——飞——」"}},
    {"id": "prop-38", "name": "决斗手套", "type": "技能授予", "text": "掷「手套」向任意人宣战,对方必须应战划拳,输者受罚", "wildness": 3, "props": ["纸巾", "外套"], "safety": [], "currency": "喝", "skill": {"prop": "决斗手套", "uses": 3, "ritual": "将绑定实物郑重掷于对方面前并宣告「我要求决斗」"}},
    {"id": "prop-42", "name": "变身药水", "type": "技能授予", "text": "指定一人用现场实物完成一次扮相变身(发胶造型/丝袜劫匪),AI 验收", "wildness": 5, "props": ["发胶", "丝袜"], "safety": [], "currency": "怪造型", "skill": {"prop": "变身药水", "uses": 1, "ritual": "递上绑定实物并念「变!」"}},
    {"id": "prop-50", "name": "加冕礼", "type": "规则修饰", "text": "终局仪式:按今晚笑声账本加冕「今夜最靓」,全场摆合影梗", "wildness": 1, "props": [], "safety": [], "currency": "表演"},
]
