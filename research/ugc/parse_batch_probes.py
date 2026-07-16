"""Parse the bounded Track-B probe cache into safe, attributed mechanism atoms.

The cache contains two different things: discovery metadata for large corpora and
bounded editorial listicles.  Metadata proves that a corpus exists; it does not
prove a game mechanic.  This parser therefore emits atoms only when a configured
content page contains the short heading/action markers needed to support the
rewritten mechanism.  It never copies source paragraphs into committed output.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import run_ugc_collection as ugc  # noqa: E402


CAPTURED_AT = "2026-07-16T00:00:00+08:00"
CONTENT_KIND = "url_list"
MAX_PROBE_BYTES = 8 * 1024 * 1024


class BatchParseError(RuntimeError):
    """Raised when a probe is missing, malformed, or no longer matches evidence guards."""


@dataclass(frozen=True)
class SourceProfile:
    language: str
    region: str
    creator: str
    query: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Recipe:
    key: str
    title: str
    atom_type: str
    source_markers: Mapping[str, tuple[str, ...]]
    mechanic: Mapping[str, str]
    safety: Mapping[str, Any]
    evidence_note: str
    tags: tuple[str, ...]
    role_terms_by_source: Mapping[str, tuple[Mapping[str, str], ...]] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class Snapshot:
    source_id: str
    visible_text: str
    headings: tuple[str, ...]
    byte_count: int


class _VisibleHTMLParser(HTMLParser):
    _SKIP = {"script", "style", "svg", "noscript", "nav", "footer", "header", "aside"}
    _HEADINGS = {"h1", "h2", "h3", "h4"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._heading_tag: str | None = None
        self._heading_parts: list[str] = []
        self.text_parts: list[str] = []
        self.headings: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag in self._HEADINGS and self._heading_tag is None:
            self._heading_tag = tag
            self._heading_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if self._heading_tag == tag:
            heading = _clean_text(" ".join(self._heading_parts))
            if heading:
                self.headings.append(heading)
            self._heading_tag = None
            self._heading_parts = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        self.text_parts.append(data)
        if self._heading_tag is not None:
            self._heading_parts.append(data)


def _clean_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _search_text(value: str) -> str:
    return _clean_text(value).casefold()


def parse_visible_html(source_id: str, payload: bytes) -> Snapshot:
    if not payload:
        raise BatchParseError(f"{source_id}: empty HTML probe")
    try:
        html = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BatchParseError(f"{source_id}: HTML is not valid UTF-8") from exc
    parser = _VisibleHTMLParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # HTMLParser can surface malformed entity errors.
        raise BatchParseError(f"{source_id}: cannot parse HTML: {exc}") from exc
    visible = _search_text(" ".join(parser.text_parts))
    headings = tuple(_search_text(value) for value in parser.headings)
    if not visible or not headings:
        raise BatchParseError(f"{source_id}: no visible article text/headings found")
    return Snapshot(source_id, visible, headings, len(payload))


def _safe_none(*, refusal_guard: str = "") -> dict[str, Any]:
    return {
        "forced_drinking": False,
        "non_alcohol_alternative": "",
        "adult_level": "none",
        "refusal_guard": refusal_guard,
    }


def _safe_drinking(alternative: str, *, refusal_guard: str = "") -> dict[str, Any]:
    return {
        "forced_drinking": True,
        "non_alcohol_alternative": alternative,
        "adult_level": "none",
        "refusal_guard": refusal_guard,
    }


def _safe_yellow(alternative: str, refusal_guard: str) -> dict[str, Any]:
    return {
        "forced_drinking": True,
        "non_alcohol_alternative": alternative,
        "adult_level": "yellow",
        "refusal_guard": refusal_guard,
    }


PSYCAT_SOURCES = (
    "listicle_psycat_en",
    "listicle_psycat_de",
    "listicle_psycat_es",
    "listicle_psycat_fr",
    "listicle_psycat_it",
    "listicle_psycat_pt",
    "listicle_psycat_pl",
)


def _psycat_markers(headings: Sequence[str], english_detail: str) -> dict[str, tuple[str, ...]]:
    if len(headings) != len(PSYCAT_SOURCES):
        raise AssertionError("one localized heading is required for every PsyCat source")
    result = {
        source_id: (heading,) for source_id, heading in zip(PSYCAT_SOURCES, headings)
    }
    result["listicle_psycat_en"] = (headings[0], english_detail)
    return result


TASK_ROLES = {
    "listicle_psycat_en": ({"term": "Task Master", "role": "host", "status": "verified"},),
    "listicle_psycat_de": ({"term": "Task Master", "role": "host", "status": "verified"},),
    "listicle_psycat_es": ({"term": "Task Master", "role": "host", "status": "verified"},),
    "listicle_psycat_fr": ({"term": "Maître du Jeu", "role": "host", "status": "verified"},),
    "listicle_psycat_it": ({"term": "Task Master", "role": "host", "status": "verified"},),
    "listicle_psycat_pt": ({"term": "Mestre das Tarefas", "role": "host", "status": "verified"},),
    "listicle_psycat_pl": ({"term": "Mistrz zadań", "role": "host", "status": "verified"},),
}


RECIPES: tuple[Recipe, ...] = (
    Recipe(
        key="psycat_friends_enemies",
        title="同点牌救援与目标转移",
        atom_type="drinking_overlay",
        source_markers=_psycat_markers(
            (
                "Friends and Enemies",
                "Freunde und Feinde",
                "Amigos y Enemigos",
                "Amis et Ennemis",
                "Amici e Nemici",
                "Amigos e Inimigos",
                "Przyjaciele i wrogowie",
            ),
            "same number",
        ),
        mechanic={
            "trigger": "主持人把普通扑克牌平均发给所有人；每人只看自己的牌，并事先设定积分上限。",
            "action": "出牌者翻出一张牌并指定一名已同意参与的玩家；若别人持有同点数牌，可打出来替其转移目标，连续同点数可以叠加。",
            "resolution": "最终承接者获得不超过上限的积分；同一人不能被连续点名，达到上限后立即重置。",
        },
        safety=_safe_drinking(
            "完全改用积分或无现金筹码，不按牌面秒数饮酒。",
            refusal_guard="参与者可拒绝被点名；禁止围攻、连续针对或用现实关系施压。",
        ),
        evidence_note="多语版公开规则页共同列出同点牌可救援并转移目标的结构；这里只保留限额积分版。",
        tags=("cards", "retarget", "stacking", "multilingual", "safe-rewrite"),
    ),
    Recipe(
        key="psycat_jenga_prompts",
        title="积木抽取触发任务",
        atom_type="drinking_overlay",
        source_markers=_psycat_markers(
            (
                "Drinking Jenga",
                "Trink-Jenga",
                "Jenga para Beber",
                "Jenga à boire",
                "Jenga Alcolico",
                "Jenga de Beber",
                "Pijane Jenga",
            ),
            "command on the block",
        ),
        mechanic={
            "trigger": "搭好积木塔，并在每块写入轻量、可跳过的动作或问答。",
            "action": "玩家轮流抽块并执行该块提示；不愿完成时可直接换题而不解释。",
            "resolution": "塔倒者负责重搭或记一分；不设置饮酒、亲密、羞辱或危险动作。",
        },
        safety=_safe_drinking(
            "任务只使用积分、表演或问答；饮品可完全不入局。",
            refusal_guard="每张任务卡都可无条件跳过，且不以额外处罚代替拒绝权。",
        ),
        evidence_note="七种语言版本都把抽积木与块上任务绑定；入库版本删除了强制饮酒和冒犯任务。",
        tags=("blocks", "prompt", "multilingual", "consent", "safe-rewrite"),
    ),
    Recipe(
        key="psycat_four_card_prediction",
        title="四步翻牌预测",
        atom_type="drinking_overlay",
        source_markers=_psycat_markers(
            (
                "Around the World (The Four Card Game)",
                "Around the World (Das Vier-Karten-Spiel)",
                "Alrededor del Mundo (El Juego de las Cuatro Cartas)",
                "Autour du Monde (Le Jeu des Quatre Cartes)",
                "Giro del Mondo (Il Gioco delle Quattro Carte)",
                "Volta ao Mundo (O Jogo das Quatro Cartas)",
                "Dookoła świata (Gra na cztery karty)",
            ),
            "red or black",
        ),
        mechanic={
            "trigger": "庄家在玩家面前依次扣放四张牌。",
            "action": "翻牌前依次猜颜色、相对大小、是否夹在前两张之间，以及最后一张的花色。",
            "resolution": "每猜对一项得一枚筹码，猜错不受罚；四项结束后轮换庄家。",
        },
        safety=_safe_drinking("全部用积分或筹码结算，猜错不饮酒。"),
        evidence_note="多语规则页重复出现颜色、大小、区间和花色四级预测；结果已改为无酒精计分。",
        tags=("cards", "prediction", "four-stage", "multilingual", "no-penalty"),
    ),
    Recipe(
        key="psycat_suit_letter_sprint",
        title="花色字母抢词",
        atom_type="parlor_game",
        source_markers=_psycat_markers(
            (
                "Loose Tongues",
                "Lose Zungen",
                "Lenguas Sueltas",
                "Langues Sciées",
                "Lingue Sciolte",
                "Línguas Soltas",
                "Rozluźnione języki",
            ),
            "same letter as the suit",
        ),
        mechanic={
            "trigger": "两名玩家同时看到一张牌，并以花色名称的首字母作为本轮字母。",
            "action": "双方尽快说出以该字母开头且符合预设类别的词；进阶轮可改用第二个字母。",
            "resolution": "先说出有效且未重复词的人得一分；重复、无效或超时不计分。",
        },
        safety=_safe_drinking("只记分，不把反应速度与饮酒挂钩。"),
        evidence_note="多语版规则页把牌的花色字母与快速造词结合；本条保留语言反应机制并取消饮酒。",
        tags=("word-game", "cards", "reaction", "multilingual", "category-variant"),
    ),
    Recipe(
        key="psycat_rank_snap",
        title="循环报点与匹配拍标",
        atom_type="parlor_game",
        source_markers=_psycat_markers(
            (
                "Irish Snap",
                "Irish Snap",
                "Irish Snap",
                "Snap Irlandais",
                "Irish Snap",
                "Snap Irlandês",
                "Irlandzki Snap",
            ),
            "matches the number",
        ),
        mechanic={
            "trigger": "牌均分且保持背面朝上，玩家轮流出牌，同时按A到K循环报点。",
            "action": "当报出的点数与翻出的牌相同，所有人拍自己的桌面标记，而不是争抢中央牌堆。",
            "resolution": "最慢者收走中央牌堆；最先清空手牌者获胜。",
        },
        safety=_safe_drinking(
            "慢者只收牌，不饮酒。",
            refusal_guard="为避免手部碰撞，各人只拍自己面前的标记。",
        ),
        evidence_note="七语页面均收录报点与翻牌匹配后的抢拍结构；安全版把中央抢拍改成个人标记。",
        tags=("cards", "chant", "reaction", "multilingual", "collision-guard"),
    ),
    Recipe(
        key="psycat_task_master",
        title="自愿任务主持轮换",
        atom_type="parlor_game",
        source_markers=_psycat_markers(
            (
                "Task Master",
                "Task Master",
                "Task Master",
                "Maître du Jeu",
                "Task Master",
                "Mestre das Tarefas",
                "Mistrz zadań",
            ),
            "appointed the first Task Master",
        ),
        mechanic={
            "trigger": "用石头剪刀布选出首位任务主持，并只使用预先审核、限于自愿参与者的任务卡。",
            "action": "主持人给组内玩家发一个可拒绝任务；完成后由该玩家接任主持。",
            "resolution": "拒绝或失败只换卡或记分；禁止接触陌生人、索取私人物品，或让第三方在不知情时入局。",
        },
        safety=_safe_yellow(
            "完全采用积分与换卡，不以整杯或半杯饮酒惩罚拒绝和失败。",
            "任务仅限知情同意的组内成员；任何人可跳过且无需说明原因。",
        ),
        evidence_note="原页的主持角色会分配任务并在成功后轮换，但含不知情第三方风险；本条只保留同意制主持机制。",
        tags=("host-role", "task-cards", "rotation", "multilingual", "third-party-guard"),
        role_terms_by_source=TASK_ROLES,
    ),
    Recipe(
        key="psycat_safe_roulette",
        title="公开配料口味盲猜",
        atom_type="drinking_overlay",
        source_markers=_psycat_markers(
            (
                "Shot Roulette",
                "Shot Roulette",
                "Ruleta de Chupitos",
                "Roulette Russe à Shots",
                "Roulette di Shot",
                "Roleta de Shots",
                "Ruletka shotów",
            ),
            "half of them with water",
        ),
        mechanic={
            "trigger": "将外观相同的小杯全部装入无酒精且已公开配料的不同口味饮品或清水。",
            "action": "玩家随机选一杯，通过味道猜口味；过敏者可以只观察或担任记录员。",
            "resolution": "猜对得一分，猜错不处罚；禁止把酒精或未公开成分混入未知杯。",
        },
        safety=_safe_drinking(
            "所有杯都必须是无酒精饮品或清水，且配料、过敏原提前公开。",
            refusal_guard="任何人可不入口并改为看颜色猜、闻香猜或记录。",
        ),
        evidence_note="多语页面列出外观相同杯子的随机选择；本条将隐藏酒精彻底替换为公开配料的无酒精口味盲猜。",
        tags=("roulette", "taste", "multilingual", "allergen-guard", "zero-alcohol"),
    ),
    Recipe(
        key="ph_pinoy_henyo",
        title="受限回复猜词",
        atom_type="parlor_game",
        source_markers={
            "listicle_philippine_primer_party_games": ("Pinoy Henyo", "yes-or-no questions"),
            "listicle_ph_staycations_pinoy_parlor": ("Pinoy Henyo", "limited time"),
        },
        mechanic={
            "trigger": "两人一组，一人看不到自己对应的词卡，类别可以是地点、食物、歌曲等。",
            "action": "猜词者提出封闭式问题，队友只能用预先约定的简短回复给线索。",
            "resolution": "限时内猜中计一分，换组继续；用时最短或积分最高的组胜出。",
        },
        safety=_safe_none(),
        evidence_note="两家菲律宾公开规则页都把 Pinoy Henyo 描述为限时双人猜词；回复方式按较严格的封闭问答改写。",
        tags=("philippines", "pinoy-henyo", "guessing", "paired", "timed"),
    ),
    Recipe(
        key="ph_bring_me",
        title="安全清单寻物",
        atom_type="parlor_game",
        source_markers={
            "listicle_philippine_primer_party_games": ("Bring Me", "game master will shout"),
            "listicle_ph_staycations_pinoy_parlor": ("Bring Me", "wins a point"),
        },
        mechanic={
            "trigger": "主持人只从事先公布的安全公共道具清单中喊出一种物品。",
            "action": "玩家在限定区域内找到对应道具并带回主持台，禁止翻找他人物品或奔跑。",
            "resolution": "最先带回正确道具者得一分；无道具可选的人可担任裁判。",
        },
        safety=_safe_none(refusal_guard="不得索取头发、现金、证件、手机或其他私人物品。"),
        evidence_note="两家菲律宾页面都记录由 game master 叫物、玩家找回的结构；敏感物品与抢跑已移除。",
        tags=("philippines", "bring-me", "host-role", "scavenger", "privacy-guard"),
        role_terms_by_source={
            "listicle_philippine_primer_party_games": (
                {"term": "game master", "role": "host", "status": "verified"},
            ),
            "listicle_ph_staycations_pinoy_parlor": (
                {"term": "game master", "role": "host", "status": "verified"},
            ),
        },
    ),
    Recipe(
        key="ph_paper_dance",
        title="逐轮缩小舞蹈站位",
        atom_type="parlor_game",
        source_markers={
            "listicle_philippine_primer_party_games": ("Paper Dance", "folded in half"),
            "listicle_ph_staycations_pinoy_parlor": ("Newspaper Dance", "smaller surface"),
        },
        mechanic={
            "trigger": "两人一组，各有一块防滑地垫；音乐播放时在垫旁活动。",
            "action": "音乐暂停时两人要同时站到垫上；每轮将可站立区域折小或更换更小垫。",
            "resolution": "无法安全站稳可主动退出，不做抱举或强迫身体接触；坚持到最后的组获胜。",
        },
        safety=_safe_none(refusal_guard="参与者可选择同意的搭档或单人版；禁止抱举、推挤和强迫接触。"),
        evidence_note="两家菲律宾页面都描述音乐暂停后站回逐轮缩小纸面的双人玩法；本条改用防滑垫并加入接触边界。",
        tags=("philippines", "music", "shrinking-space", "pairs", "contact-guard"),
    ),
    Recipe(
        key="ph_stop_dance",
        title="音乐停顿定格",
        atom_type="parlor_game",
        source_markers={
            "listicle_philippine_primer_party_games": ("Stop Dance", "must freeze"),
        },
        mechanic={
            "trigger": "主持人播放可合法使用的音乐，玩家在安全间距内自由动作。",
            "action": "音乐突然停止时所有人保持当前姿势，音乐恢复后继续。",
            "resolution": "明显移动者记一分而非淘汰；若地面拥挤或身体不适可随时旁观。",
        },
        safety=_safe_none(),
        evidence_note="菲律宾规则页把 Stop Dance 描述为音乐停止即定格；本条用记分替代淘汰并补充场地安全。",
        tags=("philippines", "music", "freeze", "reaction", "inclusive"),
    ),
    Recipe(
        key="ph_boat_grouping",
        title="口令人数快速成组",
        atom_type="parlor_game",
        source_markers={
            "listicle_ph_staycations_pinoy_parlor": ("The Boat is Sinking", "specific number"),
        },
        mechanic={
            "trigger": "主持人喊出“船要沉了”和一个人数。",
            "action": "玩家在不拉扯的前提下迅速组成相应人数的小组。",
            "resolution": "未成组者记一分并加入下一轮，不淘汰、不孤立固定玩家；主持人轮换人数。",
        },
        safety=_safe_none(refusal_guard="不得推拉、抱举或固定排斥同一参与者。"),
        evidence_note="菲律宾聚会页记录主持人口令人数、玩家迅速成组的机制；本条取消淘汰并加入反孤立规则。",
        tags=("philippines", "the-boat-is-sinking", "grouping", "host-role", "anti-exclusion"),
        role_terms_by_source={
            "listicle_ph_staycations_pinoy_parlor": (
                {"term": "game master", "role": "host", "status": "verified"},
            ),
        },
    ),
    Recipe(
        key="ph_calamansi_relay",
        title="手持勺青柠接力",
        atom_type="parlor_game",
        source_markers={
            "listicle_ph_staycations_pinoy_parlor": ("Calamansi Relay", "using only a spoon"),
        },
        mechanic={
            "trigger": "每队排成一列，准备小青柠、独立托盘和每人一把勺。",
            "action": "队员只用手握勺，把青柠从自己的勺滚到下一人的干净勺，不把器具含入口中。",
            "resolution": "最先完成全队传递且不落地的队伍得分；掉落后从当前队员重来。",
        },
        safety=_safe_none(refusal_guard="为卫生与窒息安全，勺子只用手握，不入口。"),
        evidence_note="菲律宾页面记录用勺传递 calamansi 的接力；入库版本把口含勺改成手持勺。",
        tags=("philippines", "calamansi", "relay", "hygiene-guard", "team"),
    ),
    Recipe(
        key="ph_pinoy_bingo",
        title="本地文化图标宾果",
        atom_type="parlor_game",
        source_markers={
            "listicle_ph_staycations_pinoy_parlor": ("Pinoy Bingo", "complete a row or column"),
        },
        mechanic={
            "trigger": "制作包含菲律宾食物、地点或流行文化图标的宾果卡，并准备对应叫号牌。",
            "action": "主持人随机叫出图标，玩家在自己的卡上标记。",
            "resolution": "最先完成预定行、列或图案者喊停，经核验后获胜。",
        },
        safety=_safe_none(),
        evidence_note="菲律宾页面给出以本地文化词图替换普通数字的宾果变体；这里只保留图标叫号机制。",
        tags=("philippines", "bingo", "localization", "visual", "host-role"),
    ),
    Recipe(
        key="ph_indoor_patintero",
        title="胶带网格室内穿越",
        atom_type="parlor_game",
        source_markers={
            "listicle_ph_staycations_pinoy_parlor": ("Patintero with a Twist", "masking tape"),
        },
        mechanic={
            "trigger": "用可移除胶带在室内划出网格，两队分别担任穿越者和守线者。",
            "action": "穿越者尝试从一端到另一端；守线者只能沿指定线移动并以轻触完成拦截。",
            "resolution": "成功往返得分，被触碰后交换角色；禁止推拉和冲撞。",
        },
        safety=_safe_none(refusal_guard="参与者可选择步行速度；禁止冲刺、推拉和封堵出口。"),
        evidence_note="菲律宾页面明确提出用胶带网格把 Patintero 改成室内版；本条补充轻触和防冲撞边界。",
        tags=("philippines", "patintero", "indoor", "grid", "movement"),
    ),
    Recipe(
        key="ph_safe_pabitin",
        title="低位奖券绳格",
        atom_type="parlor_game",
        source_markers={
            "listicle_philippine_primer_party_games": ("Agaw Bitin", "lowers or raises"),
            "listicle_ph_staycations_pinoy_parlor": ("Pabitin for Birthdays", "lowered and raised"),
        },
        mechanic={
            "trigger": "把轻质奖券或任务卡系在桌面高度的软绳格上，由主持人缓慢移动绳格。",
            "action": "玩家每轮只能伸手取一张，不跳跃、不争抢。",
            "resolution": "拿到卡后退出该轮，直到人人都有一张；不使用硬框、钱币或高处悬挂。",
        },
        safety=_safe_none(refusal_guard="仅用软绳、纸卡和低位操作，禁止跳跃、硬物坠落与抢夺。"),
        evidence_note="两家菲律宾页面分别以 Agaw Bitin/Pabitin 记录升降奖品格；本条改成低位软绳与人人一张。",
        tags=("philippines", "pabitin", "agaw-bitin", "prize-grid", "safe-rewrite"),
    ),
    Recipe(
        key="india_mafia_narrator",
        title="叙事主持切换昼夜阶段",
        atom_type="parlor_game",
        source_markers={
            "listicle_india_partie_adult_party_games": ("Mafia (The GOAT)", "experienced narrator"),
        },
        mechanic={
            "trigger": "一名叙事主持私下分配少量阵营角色，并向所有人说明白天讨论、夜间闭眼的边界。",
            "action": "主持人按固定顺序切换昼夜阶段、收集秘密选择并公布不带身份信息的结果。",
            "resolution": "白天限时讨论并投票；一局结束后公开角色并轮换主持，避免主持暗示。",
        },
        safety=_safe_none(refusal_guard="不得把游戏指控延伸为对现实身份或品格的评价。"),
        evidence_note="印度聚会页强调有经验的 narrator 会决定该游戏体验；本条抽出阶段控制与中立主持机制。",
        tags=("india", "narrator", "host-role", "hidden-role", "phase-control"),
        role_terms_by_source={
            "listicle_india_partie_adult_party_games": (
                {"term": "narrator", "role": "host", "status": "verified"},
            ),
        },
    ),
    Recipe(
        key="india_antakshari_variant",
        title="结尾音节歌曲接龙",
        atom_type="song_chain",
        source_markers={
            "listicle_india_partie_adult_party_games": ("Antakshari", "last letter"),
        },
        mechanic={
            "trigger": "分队后由首队唱一小段自选歌曲，主持记录结尾音节或字母。",
            "action": "下一队必须在倒计时内用该结尾开头唱另一首歌；可增加年代、语言或曲风限定轮。",
            "resolution": "有效接龙得分，重复或超时让下一队接棒；只唱短片段并尊重版权。",
        },
        safety=_safe_none(),
        evidence_note="印度聚会页描述 Antakshari 的尾字接唱，并给出倒计时与曲风轮变体；这里只留机制摘要。",
        tags=("india", "antakshari", "song-chain", "timer", "genre-round"),
    ),
    Recipe(
        key="india_bollywood_charades",
        title="无声表演猜电影",
        atom_type="parlor_game",
        source_markers={
            "listicle_india_partie_adult_party_games": ("Dumb Charades", "without speaking"),
        },
        mechanic={
            "trigger": "主持准备经审核的电影名卡，两队轮流派一人抽卡。",
            "action": "表演者不能说话或拼写，只用动作让本队猜电影名。",
            "resolution": "限时猜中得分；敏感或不会演的卡可无条件更换一次。",
        },
        safety=_safe_none(refusal_guard="卡片可无条件换一次，禁止模仿族群、残障或现实参与者。"),
        evidence_note="印度聚会页把 Dumb Charades 放在 Bollywood 电影语境；本条保留无声动作猜题。",
        tags=("india", "bollywood", "charades", "silent", "teams"),
    ),
    Recipe(
        key="india_bollywood_taboo",
        title="宝莱坞禁词描述",
        atom_type="parlor_game",
        source_markers={
            "listicle_india_partie_adult_party_games": ("Taboo (Bollywood Edition)", "forbidden words"),
        },
        mechanic={
            "trigger": "每张卡写一个印度电影、演员或歌曲目标词，并列出若干禁用提示词。",
            "action": "提示者描述目标，但不能说目标词、禁词或直接拼写，队友限时猜。",
            "resolution": "猜中得分，说出禁词则跳过；不使用针对现实参与者的冒犯描述。",
        },
        safety=_safe_none(),
        evidence_note="印度聚会页给出以电影、演员和歌曲为题面的 Bollywood Taboo；这里只记录禁词提示结构。",
        tags=("india", "bollywood", "taboo", "word-game", "teams"),
    ),
    Recipe(
        key="india_bluff_challenge",
        title="盖牌声明与质疑",
        atom_type="parlor_game",
        source_markers={
            "listicle_india_partie_adult_party_games": ("Bluff (I Doubt It)", "challenger is wrong"),
        },
        mechanic={
            "trigger": "玩家把牌背面朝上打到中央，并口头声明牌面点数。",
            "action": "下一位可以继续出牌，也可以质疑前一人的声明。",
            "resolution": "若声明为假，出牌者收走牌堆；若声明为真，质疑者收走，先清空手牌者胜。",
        },
        safety=_safe_none(),
        evidence_note="印度聚会页说明盖牌、声明和挑战者判定真假的闭环；本条为短机制改写。",
        tags=("india", "cards", "bluff", "challenge", "deduction"),
    ),
    Recipe(
        key="india_story_builder",
        title="逐句共创故事",
        atom_type="parlor_game",
        source_markers={
            "listicle_india_partie_adult_party_games": ("Story Builder", "adds the next sentence"),
        },
        mechanic={
            "trigger": "首位玩家用一句中性开场建立故事。",
            "action": "其余人按顺序各补一句，必须承接前句，且不替现实参与者编造隐私。",
            "resolution": "完成约定轮数后共同给故事命名；不设输家，任何人可跳过一次。",
        },
        safety=_safe_none(refusal_guard="不得把现实隐私、创伤或未经同意的亲密关系写入故事。"),
        evidence_note="印度聚会页将 Story Builder 描述为每人续写一句的共创结构；本条加入隐私和跳过边界。",
        tags=("india", "story", "improv", "turn-taking", "privacy-guard"),
    ),
    Recipe(
        key="india_specific_word_songs",
        title="指定词歌曲清单",
        atom_type="song_chain",
        source_markers={
            "listicle_india_firstcry_kitty_party_games": ("Songs with a Particular Word", "host assigns"),
        },
        mechanic={
            "trigger": "主持人给出一个常见且中性的目标词。",
            "action": "玩家在限时内写出包含或以该词开头的歌曲名；团队版可以轮流唱极短片段核验。",
            "resolution": "去重后每个有效歌名得一分，最高分获胜。",
        },
        safety=_safe_none(),
        evidence_note="印度 kitty party 页面记录主持人指定词、玩家列举相关歌曲的玩法；仅保留短机制。",
        tags=("india", "kitty-party", "songs", "keyword", "host-role"),
        role_terms_by_source={
            "listicle_india_firstcry_kitty_party_games": (
                {"term": "host", "role": "host", "status": "verified"},
            ),
        },
    ),
    Recipe(
        key="india_memory_tray",
        title="托盘物件限时回忆",
        atom_type="parlor_game",
        source_markers={
            "listicle_india_firstcry_kitty_party_games": ("Challenge Your Memory", "hide the tray"),
        },
        mechanic={
            "trigger": "主持人在托盘上摆放一组清晰可见的普通物件，并短暂展示。",
            "action": "托盘遮住后，玩家在一分钟内独立写下记得的物件。",
            "resolution": "按正确且不重复的物件数计分；最多者获胜。",
        },
        safety=_safe_none(),
        evidence_note="印度 kitty party 页面给出展示托盘、隐藏后限时回忆物件的完整闭环。",
        tags=("india", "kitty-party", "memory", "tray", "one-minute"),
    ),
    Recipe(
        key="india_song_question_rounds",
        title="歌曲片段后定向问答",
        atom_type="bgm_chant",
        source_markers={
            "listicle_india_firstcry_kitty_party_games": ("Musical Puzzle Game", "questions after every song"),
        },
        mechanic={
            "trigger": "主持人为每段有权使用的音乐准备一道相关问题，并给每人纸笔。",
            "action": "逐段播放短音频，每段结束后提问，玩家在十几秒内独立写答案。",
            "resolution": "全部片段结束后统一核对，正确答案最多者获胜。",
        },
        safety=_safe_none(),
        evidence_note="印度 kitty party 页面把逐首播放与每首一题结合；本条强调短片段和使用权。",
        tags=("india", "kitty-party", "music", "quiz", "written-answer"),
    ),
    Recipe(
        key="india_hinglish_song_decode",
        title="英译歌名反向识别",
        atom_type="bgm_chant",
        source_markers={
            "listicle_india_firstcry_kitty_party_games": ("Guess the Hinglish Song Game", "translated songs"),
        },
        mechanic={
            "trigger": "主持人把若干熟悉的印地语歌名自行改写成简短英文线索，每人拿到同一份题单。",
            "action": "玩家在一分钟内把英文线索还原为原歌名。",
            "resolution": "每个正确且拼写可辨的歌名得一分，最高分获胜。",
        },
        safety=_safe_none(),
        evidence_note="印度 kitty party 页面记录把 Hindi 歌曲翻成英文再猜回原名的玩法；不保存页面例题。",
        tags=("india", "kitty-party", "hinglish", "song-title", "translation"),
    ),
    Recipe(
        key="india_card_piece_match",
        title="牌面碎片限时配对",
        atom_type="parlor_game",
        source_markers={
            "listicle_india_firstcry_kitty_party_games": ("Card Puzzle Game", "match the correct cards"),
        },
        mechanic={
            "trigger": "主持人用自制牌面复印件制作若干颜色和点数不同的三片拼图，不剪真实牌组。",
            "action": "玩家在一分钟内把同一牌面的颜色、点数和图案碎片拼回一组。",
            "resolution": "每个完整正确组合得一分，组合最多者获胜。",
        },
        safety=_safe_none(),
        evidence_note="印度 kitty party 页面给出把牌面分片再按颜色与点数匹配的结构；本条改用自制复印件。",
        tags=("india", "kitty-party", "cards", "puzzle", "one-minute"),
    ),
    Recipe(
        key="india_bowl_of_fame",
        title="名人纸条双人表演猜词",
        atom_type="parlor_game",
        source_markers={
            "listicle_india_firstcry_kitty_party_games": ("The Bowl of Fame", "one person will act"),
        },
        mechanic={
            "trigger": "把经审核的公众人物或虚构角色名写在纸条上放入碗中，两人一组。",
            "action": "一人抽纸条后只用动作提示，搭档在一分钟内猜名字。",
            "resolution": "限时猜对数量最多的组合获胜；不会或不愿表演的题可换一次。",
        },
        safety=_safe_none(refusal_guard="不使用现实参与者、争议身份或带歧视性的模仿题。"),
        evidence_note="印度 kitty party 页面记录从碗中抽名人纸条、双人表演猜名的限时机制。",
        tags=("india", "kitty-party", "charades", "celebrity", "pairs"),
    ),
    Recipe(
        key="india_back_to_back_describe",
        title="背对背禁名描述",
        atom_type="parlor_game",
        source_markers={
            "listicle_india_firstcry_kitty_party_games": ("Partners in Pen", "without giving out the exact name"),
        },
        mechanic={
            "trigger": "两人背对背坐，一人从袋中抽取普通物件，另一人拿纸笔。",
            "action": "抽物者描述特征但不能说出物件名称，搭档写下猜测。",
            "resolution": "限时内猜对最多的组合获胜；随后交换描述与猜测角色。",
        },
        safety=_safe_none(),
        evidence_note="印度 kitty party 页面完整描述背对背、抽物、禁说名称和限时猜测的双人结构。",
        tags=("india", "kitty-party", "description", "paired", "forbidden-name"),
    ),
)


SOURCE_PROFILES: dict[str, SourceProfile] = {
    "listicle_psycat_en": SourceProfile("mul", "global", "PsyCat Games", "student drinking games multilingual editions", ("language:en",)),
    "listicle_psycat_de": SourceProfile("mul", "global", "PsyCat Games", "student drinking games multilingual editions", ("language:de",)),
    "listicle_psycat_es": SourceProfile("mul", "global", "PsyCat Games", "student drinking games multilingual editions", ("language:es",)),
    "listicle_psycat_fr": SourceProfile("mul", "global", "PsyCat Games", "student drinking games multilingual editions", ("language:fr",)),
    "listicle_psycat_it": SourceProfile("mul", "global", "PsyCat Games", "student drinking games multilingual editions", ("language:it",)),
    "listicle_psycat_pt": SourceProfile("mul", "global", "PsyCat Games", "student drinking games multilingual editions", ("language:pt",)),
    "listicle_psycat_pl": SourceProfile("mul", "global", "PsyCat Games", "student drinking games multilingual editions", ("language:pl",)),
    "listicle_philippine_primer_party_games": SourceProfile("en-PH", "PH", "Philippine Primer", "common Filipino party games", ("language:en-PH",)),
    "listicle_ph_staycations_pinoy_parlor": SourceProfile("en-PH", "PH", "Staycations.ph", "Pinoy parlor games", ("language:en-PH",)),
    "listicle_india_partie_adult_party_games": SourceProfile("en-IN", "IN", "Partie", "party games for adults India", ("language:en-IN",)),
    "listicle_india_firstcry_kitty_party_games": SourceProfile("en-IN", "IN", "FirstCry Parenting", "kitty party games India", ("language:en-IN",)),
}


METADATA_REASONS = {
    "hf_namuwiki_size_probe": "仅为 Hugging Face 数据集体量与行数元数据，未下载正文。",
    "wikimedia_kowiki_checksum_probe": "仅为 Wikimedia 校验和清单，不含百科正文。",
    "commoncrawl_catalog_probe": "仅为 Common Crawl 集合目录，不含目标网页 WARC 内容。",
    "arctic_shift_subreddit_probe": "仅返回子版块发现元数据，不含帖子或评论机制。",
    "arctic_shift_download_index_probe": "仅为存档下载索引，不含 Reddit 帖子或评论正文。",
    "academic_torrents_2026_06_details_probe": "仅为 70.38 GiB 月度数据集详情页，未打开 torrent payload。",
}


def _read_probe(path: Path) -> bytes:
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise BatchParseError(f"missing probe file: {path}") from exc
    if not resolved.is_file():
        raise BatchParseError(f"probe is not a regular file: {path}")
    size = resolved.stat().st_size
    if size > MAX_PROBE_BYTES:
        raise BatchParseError(f"probe exceeds {MAX_PROBE_BYTES} bytes: {path}")
    return resolved.read_bytes()


def _validate_metadata_payload(source: Mapping[str, Any], payload: bytes) -> None:
    source_id = str(source["id"])
    if not payload.strip():
        raise BatchParseError(f"{source_id}: empty metadata probe")
    filename = str(source["filename"])
    if filename.endswith(".json"):
        try:
            json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BatchParseError(f"{source_id}: malformed JSON metadata") from exc
    if source_id not in METADATA_REASONS:
        raise BatchParseError(f"{source_id}: metadata-only reason is not documented")


def load_manifest(path: Path) -> list[dict[str, Any]]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BatchParseError(f"cannot load manifest {path}: {exc}") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("sources"), list):
        raise BatchParseError(f"{path}: manifest must contain a sources list")
    sources = [source for source in manifest["sources"] if source.get("enabled") is True]
    ids = [source.get("id") for source in sources]
    if len(ids) != len(set(ids)):
        raise BatchParseError(f"{path}: enabled source ids must be unique")
    return sources


def validate_recipe_catalog(sources: Sequence[Mapping[str, Any]]) -> None:
    by_id = {str(source["id"]): source for source in sources}
    content_ids = {source_id for source_id, source in by_id.items() if source.get("kind") == CONTENT_KIND}
    configured_ids = {
        source_id for recipe in RECIPES for source_id in recipe.source_markers
    }
    if configured_ids != content_ids:
        missing = sorted(content_ids - configured_ids)
        unexpected = sorted(configured_ids - content_ids)
        raise BatchParseError(
            f"recipe/source coverage mismatch; missing={missing}, unexpected={unexpected}"
        )
    if content_ids != set(SOURCE_PROFILES):
        raise BatchParseError("SOURCE_PROFILES must cover every enabled content source exactly")
    keys = [recipe.key for recipe in RECIPES]
    if len(keys) != len(set(keys)):
        raise BatchParseError("recipe keys must be unique")
    for recipe in RECIPES:
        if not recipe.source_markers:
            raise BatchParseError(f"{recipe.key}: no evidence source configured")
        for source_id, markers in recipe.source_markers.items():
            if source_id not in by_id:
                raise BatchParseError(f"{recipe.key}: unknown source {source_id}")
            if not markers or any(not _clean_text(marker) for marker in markers):
                raise BatchParseError(f"{recipe.key}/{source_id}: empty evidence marker")


def load_snapshots(
    sources: Sequence[Mapping[str, Any]], cache_dir: Path
) -> tuple[dict[str, Snapshot], dict[str, int]]:
    snapshots: dict[str, Snapshot] = {}
    byte_counts: dict[str, int] = {}
    for source in sources:
        source_id = str(source["id"])
        filename = str(source["filename"])
        payload = _read_probe(cache_dir / filename)
        byte_counts[source_id] = len(payload)
        if source.get("kind") == CONTENT_KIND:
            snapshots[source_id] = parse_visible_html(source_id, payload)
        else:
            _validate_metadata_payload(source, payload)
    return snapshots, byte_counts


def _require_markers(snapshot: Snapshot, markers: Iterable[str], recipe_key: str) -> None:
    haystack = snapshot.visible_text
    missing = [marker for marker in markers if _search_text(marker) not in haystack]
    if missing:
        raise BatchParseError(
            f"{recipe_key}/{snapshot.source_id}: missing evidence marker(s): {missing}"
        )


def _candidate(
    recipe: Recipe, source: Mapping[str, Any], profile: SourceProfile
) -> dict[str, Any]:
    source_id = str(source["id"])
    roles = [copy.deepcopy(role) for role in recipe.role_terms_by_source.get(source_id, ())]
    return {
        "track": "batch",
        "platform": "public_web_listicle",
        "source_url": str(source["url"]),
        "captured_at": CAPTURED_AT,
        "language": profile.language,
        "region": profile.region,
        "creator": profile.creator,
        "query": profile.query,
        "role_terms": roles,
        "atom_type": recipe.atom_type,
        "title": recipe.title,
        "mechanic": copy.deepcopy(dict(recipe.mechanic)),
        "safety": copy.deepcopy(dict(recipe.safety)),
        "license": str(source["license"]),
        "evidence_note": recipe.evidence_note,
        "tags": [*recipe.tags, *profile.tags, f"source:{source_id}", "track-b"],
    }


def build_candidates(
    sources: Sequence[Mapping[str, Any]], snapshots: Mapping[str, Snapshot]
) -> list[dict[str, Any]]:
    by_id = {str(source["id"]): source for source in sources}
    candidates: list[dict[str, Any]] = []
    for recipe in RECIPES:
        for source_id, markers in recipe.source_markers.items():
            snapshot = snapshots.get(source_id)
            if snapshot is None:
                raise BatchParseError(f"{recipe.key}: missing content snapshot {source_id}")
            _require_markers(snapshot, markers, recipe.key)
            candidates.append(_candidate(recipe, by_id[source_id], SOURCE_PROFILES[source_id]))
    return candidates


def _source_ids_for_atom(atom: Mapping[str, Any]) -> list[str]:
    prefix = "source:"
    return sorted(
        tag[len(prefix) :]
        for tag in atom.get("tags", [])
        if isinstance(tag, str) and tag.startswith(prefix)
    )


def build_parse_report(
    sources: Sequence[Mapping[str, Any]],
    byte_counts: Mapping[str, int],
    candidates: Sequence[Mapping[str, Any]],
    atoms: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    atom_ids_by_source: dict[str, list[str]] = {str(source["id"]): [] for source in sources}
    for atom in atoms:
        for source_id in _source_ids_for_atom(atom):
            atom_ids_by_source[source_id].append(str(atom["id"]))
    source_rows = []
    for source in sources:
        source_id = str(source["id"])
        is_content = source.get("kind") == CONTENT_KIND
        atom_ids = sorted(set(atom_ids_by_source[source_id]))
        source_rows.append(
            {
                "id": source_id,
                "kind": source.get("kind"),
                "status": "content_parsed" if is_content else "metadata_only",
                "bytes": byte_counts[source_id],
                "source_url": ugc.normalize_url(str(source["url"])),
                "license": source["license"],
                "atom_count": len(atom_ids),
                "atom_ids": atom_ids,
                "note": (
                    "短标记核验通过；只提交改写后的机制事实。"
                    if is_content
                    else METADATA_REASONS[source_id]
                ),
            }
        )
    source_urls = {
        url
        for atom in atoms
        for url in [atom["source_url"], *atom.get("source_urls", [])]
    }
    return {
        "schema_version": 1,
        "generated_at": CAPTURED_AT,
        "inputs": {
            "manifest": "research/ugc/batch_sources.json",
            "cache": ".ugc-cache (gitignored)",
        },
        "summary": {
            "enabled_probe_count": len(sources),
            "downloaded_probe_count": len(byte_counts),
            "content_source_count": sum(row["status"] == "content_parsed" for row in source_rows),
            "metadata_only_source_count": sum(row["status"] == "metadata_only" for row in source_rows),
            "raw_candidate_record_count": len(candidates),
            "deduplicated_atom_count": len(atoms),
            "duplicates_collapsed": len(candidates) - len(atoms),
            "retained_source_url_count": len(source_urls),
            "forced_drinking_atom_count": sum(bool(atom["safety"]["forced_drinking"]) for atom in atoms),
            "yellow_or_adult_atom_count": sum(
                atom["safety"]["adult_level"] in {"yellow", "adult"} for atom in atoms
            ),
        },
        "sources": source_rows,
        "atoms": [
            {
                "id": atom["id"],
                "title": atom["title"],
                "atom_type": atom["atom_type"],
                "source_ids": _source_ids_for_atom(atom),
                "forced_drinking": atom["safety"]["forced_drinking"],
                "adult_level": atom["safety"]["adult_level"],
            }
            for atom in atoms
        ],
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Track B 小探针解析报告（2026-07-16）",
        "",
        "## 结论",
        "",
        f"- 已核对启用并下载的探针：**{summary['downloaded_probe_count']}/{summary['enabled_probe_count']}**。",
        f"- 可支持机制的内容页：**{summary['content_source_count']}**；仅含入口/体量/校验元数据：**{summary['metadata_only_source_count']}**。",
        f"- 原始候选：**{summary['raw_candidate_record_count']}**；按机制去重后：**{summary['deduplicated_atom_count']}**；合并重复：**{summary['duplicates_collapsed']}**。",
        f"- 去重后仍保留 **{summary['retained_source_url_count']}** 个来源 URL。",
        f"- 涉及原始饮酒规则并已加无酒精替代的原子：**{summary['forced_drinking_atom_count']}**；黄/成人分级：**{summary['yellow_or_adult_atom_count']}**。",
        "",
        "六个语料入口探针没有正文，因此没有被包装成玩法证据。所有内容页只用短标记核验，提交内容均为中文机制改写，不保存网页段落、例题或字幕。",
        "",
        "## 来源覆盖",
        "",
        "| 探针 | 类型 | 状态 | 字节 | 支持原子 | 说明 |",
        "|---|---|---|---:|---:|---|",
    ]
    for row in report["sources"]:
        note = str(row["note"]).replace("|", "\\|")
        lines.append(
            f"| [{row['id']}]({row['source_url']}) | {row['kind']} | {row['status']} | "
            f"{row['bytes']} | {row['atom_count']} | {note} |"
        )
    lines.extend(
        [
            "",
            "## 去重后机制",
            "",
            "| ID | 标题 | 类型 | 来源数 | 饮酒护栏 | 分级 |",
            "|---|---|---|---:|---|---|",
        ]
    )
    for atom in report["atoms"]:
        lines.append(
            f"| `{atom['id']}` | {atom['title']} | {atom['atom_type']} | "
            f"{len(atom['source_ids'])} | {'是' if atom['forced_drinking'] else '否'} | {atom['adult_level']} |"
        )
    lines.extend(
        [
            "",
            "## 权利与安全边界",
            "",
            "- 原始 HTML 只留在 `.ugc-cache`，不会提交到仓库。",
            "- 公开可访问不等于开放许可；每个原子保留清单中的 URL 与 license/rights 说明。",
            "- 多语镜像及跨站同机制会合并，但 `source_urls` 与来源标签不会丢失。",
            "- 原规则若把饮酒当惩罚，记录仍标记 `forced_drinking=true`，同时强制提供积分、无酒精或跳过方案。",
            "- 涉及陌生人任务、抢夺、口含器具、隐藏酒精、抱举或强迫接触的原结构均被拒绝或安全重写。",
            "",
            "机器可读审计见 `batch_parse_2026-07-16.json`；机制库见 `../batch_atoms.jsonl`。",
            "",
        ]
    )
    return "\n".join(lines)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def run_parse(
    manifest_path: Path,
    cache_dir: Path,
    atoms_path: Path,
    json_report_path: Path,
    markdown_report_path: Path,
) -> dict[str, Any]:
    sources = load_manifest(manifest_path)
    validate_recipe_catalog(sources)
    snapshots, byte_counts = load_snapshots(sources, cache_dir)
    candidates = build_candidates(sources, snapshots)
    atoms = ugc.merge_records(candidates)
    ugc.validate_located_records([ugc.LocatedRecord(atom) for atom in atoms])
    report = build_parse_report(sources, byte_counts, candidates, atoms)
    ugc.write_jsonl(atoms_path, atoms)
    _atomic_write_text(
        json_report_path,
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    _atomic_write_text(markdown_report_path, render_markdown(report))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=ROOT / "research/ugc/batch_sources.json"
    )
    parser.add_argument("--cache", type=Path, default=ROOT / ".ugc-cache")
    parser.add_argument(
        "--atoms", type=Path, default=ROOT / "research/ugc/batch_atoms.jsonl"
    )
    parser.add_argument(
        "--json-report",
        type=Path,
        default=ROOT / "research/ugc/live/batch_parse_2026-07-16.json",
    )
    parser.add_argument(
        "--markdown-report",
        type=Path,
        default=ROOT / "research/ugc/live/batch_parse_2026-07-16.md",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = run_parse(
            args.manifest,
            args.cache,
            args.atoms,
            args.json_report,
            args.markdown_report,
        )
    except (BatchParseError, ugc.UGCCollectionError, OSError, ValueError) as exc:
        print(f"batch parse failed: {exc}", file=sys.stderr)
        return 1
    summary = report["summary"]
    print(
        "batch parse complete: "
        f"{summary['downloaded_probe_count']} probes, "
        f"{summary['deduplicated_atom_count']} atoms, "
        f"{summary['duplicates_collapsed']} duplicates collapsed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
