// ZAKZOK(原代号 Yappa)v0 · 两台手机的真人局客户端(Expo / React Native)
// 服务端 = 现有引擎 HTTP API(Mac 上 python -m modeb.simulator --lan)。
// 本客户端只消费 /api/view(自己那台手机该看的)与 /api/event(自己的动作)——
// 防偷看在服务端成立,客户端天然拿不到别人的底牌。
// 手机开局页(开工单欠账补):/api/start 也从手机发,不必回电脑驾驶舱;
// 判定=抽帧走照片通道:视频先在本机抽帧转 base64,仍是 /api/photo 那条口子。
import { useEffect, useRef, useState } from "react";
import {
  Alert, Animated, Image, KeyboardAvoidingView, Platform, Pressable, ScrollView,
  StyleSheet, Text, TextInput, View,
} from "react-native";
import { StatusBar } from "expo-status-bar";
import { Audio } from "expo-av";
// SDK 54 起 "expo-file-system" 主入口的 readAsStringAsync 等只是会抛异常的存根
// (官方迁移到 File/Directory 类)。录音判定与视频抽帧都靠它读 base64——走 legacy
// 入口才是真实现,EncodingType 也只在这条线上导出。
import * as FileSystem from "expo-file-system/legacy";
import * as Haptics from "expo-haptics";
import * as ImagePicker from "expo-image-picker";
import { useKeepAwake } from "expo-keep-awake";
import { Accelerometer } from "expo-sensors";
import * as VideoThumbnails from "expo-video-thumbnails";
// —— 道具实感层(局长派发流的皮肉)——
// 影棚质感组件,吃服务器传入的点数/状态,不产出任何游戏结果;原生手感缺席时无感降级
// (震动走 expo-haptics、音效走 expo-av、视觉照常),Expo Go/模拟器照常可玩。
import { DiceStage } from "./props/dice";
import { DuelStage } from "./props/revolver";
import { ForeheadCard } from "./props/forehead";

// 乐观回显上线后确认压力小了,轮询提到 600ms(慢不是病、没反馈才是病:节奏更跟手,
// 但别低于 500——再快只是空烧流量,乐观回显已经把"点了没反应"的死区堵死了)。
const POLL_MS = 600;
// —— 快捷回应条(输入侧去打字化,房主裁定 2026-07-24)——
// 社交局不许降维成打字游戏:最常说的几句做成 chips,单点即以桌上说话发出(配轻触感),
// 玩家少动手、只关注现实场。打字框保留但视觉降级(变矮变淡)。文案在此常量数组里改。
// 「完成了」已删(真机病历 2026-07-24:和绿色「完成」真按钮挨着,名字几乎一样,
// 玩家两个都试才分清);「人不在」给等人环节一个台阶(有人上厕所很正常,说一声局长好跳过)
const QUICK_CHIPS = ["好!", "过", "再来一局", "慢点等等", "人不在,先跳过吧"];
// 官方服务器(正式形态,2026-07-23 定稿):玩家从此只输房间码+座位名,
// 服务器输入框消失(长按标题下方空白 1.2 秒可唤回,开发调试用)。
const DEFAULT_SERVER = "https://play.zakzok.app";
// 上次入座用的服务器/座位/房间。局域网地址这种东西每开一次 App 重敲一遍没人受得了,
// 何况派对现场手忙脚乱。存本机,下次进来直接填好,改了照样能改。
const PREFS = FileSystem.documentDirectory + "yappa-last.json";

// —— 骰子回执识别(只认引擎防伪水印) ——
// 服务端 route_private 给 random.dice 的真结果打水印:"🔒🎲 [3, 1, 6]"(锁后紧跟骰、
// 无空格)。show 私发永远是 "🔒 {文案}" 带空格——局长在文案里自己写 🎲 也伪造不出
// 这个前缀。因此骰面**只**画水印件:真机实测抓到过局长用 show 编假骰子,从此封死。
const PURE_DICE_RE = /^[\[(]?\s*[1-6](?:\s*[,,、;\s]+\s*[1-6]){0,9}\s*[\])]?$/;
const parseDice = (item) => {
  const raw = String(item).trim();
  if (!raw.startsWith("🔒🎲")) return null; // 无水印=不是引擎摇的,按普通文字渲染
  const body = raw.replace(/^🔒🎲\s*/u, "").trim();
  if (PURE_DICE_RE.test(body)) return body.match(/[1-6]/g).map(Number);
  return null;
};

// 骰面用 View 点阵画(酒桌暗光下比 ⚀⚁ 字符放大清晰得多,字符骰在部分安卓字体上糊成一团)
const PIP_MAP = {
  1: [4], 2: [2, 6], 3: [2, 4, 6], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8],
};
function Die({ n, mini }) {
  const pips = PIP_MAP[n] || [];
  return (
    <View style={[s.die, mini && s.dieMini]}>
      {[0, 1, 2, 3, 4, 5, 6, 7, 8].map((i) => (
        <View key={i} style={s.pipCell}>
          {pips.includes(i) ? <View style={[s.pip, mini && s.pipMini]} /> : null}
        </View>
      ))}
    </View>
  );
}

// 叫价文案:{count:3, face:6} → 「(叫价:3个6)」;没带叫价就空串
const fmtBid = (bid) => (bid && bid.count && bid.face ? `(叫价:${bid.count}个${bid.face})` : "");

// 揭晓感:压 250ms 再弹开(spring 缩放),配一次触感——只在私件首次挂上来时放,
// 轮询重渲不重放(靠父级稳定 key 保证只挂载一次)。
function DiceReveal({ dice }) {
  const anim = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    const t = setTimeout(() => {
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium);
      Animated.spring(anim, { toValue: 1, friction: 5, tension: 80, useNativeDriver: true }).start();
    }, 250);
    return () => clearTimeout(t);
  }, []);
  const sum = dice.reduce((a, b) => a + b, 0);
  return (
    <View>
      <Text style={s.diceLabel}>🎲 你的暗骰</Text>
      <Animated.View style={[s.diceRow, {
        opacity: anim,
        transform: [{ scale: anim.interpolate({ inputRange: [0, 1], outputRange: [0.3, 1] }) }],
      }]}>
        {dice.map((n, i) => <Die key={i} n={n} />)}
        {dice.length > 1 ? <Text style={s.diceSum}>Σ{sum}</Text> : null}
      </Animated.View>
    </View>
  );
}

// —— 局长思考指示(真机节奏裁定 2026-07-24:慢不是病,没反馈才是病)——
// 服务端 run_turn 进行中 view.host_thinking=true,这里给一个呼吸的「🎩 …」——
// 局长在酝酿,不挡任何操作,只把"没反馈的死区"填上一口气。
// 语音开盅的叫价解析(房主 2026-07-24:「开盅也得弄个语音开,不选,说句话开了」):
// "三个六,开!" → {count:3, face:6}。中文/阿拉伯数字都认;"幺"=1;解析不出=不带
// 叫价开牌(走局长问一嘴的兜底路),话本身照常送到局长耳朵里(/api/stt 已入局)。
const ZH_N = { 一: 1, 二: 2, 两: 2, 三: 3, 四: 4, 五: 5, 六: 6, 七: 7, 八: 8, 九: 9 };
const zhNum = (s) => {
  if (/^\d+$/.test(s)) return parseInt(s, 10);
  if (s === "十") return 10;
  const m = /^([一二两三四五六七八九])?十([一二三四五六七八九])?$/.exec(s);
  if (m) return (m[1] ? ZH_N[m[1]] : 1) * 10 + (m[2] ? ZH_N[m[2]] : 0);
  return ZH_N[s] || null;
};
const parseSpokenBid = (text) => {
  const m = /(\d+|[一二两三四五六七八九十]{1,3})\s*个\s*(\d|[一二三四五六幺])/.exec(text || "");
  if (!m) return null;
  const count = zhNum(m[1]);
  const face = m[2] === "幺" ? 1 : zhNum(m[2]);
  if (!count || !face || face > 6) return null;
  return { count: Math.min(30, Math.max(1, count)), face };
};

function HostThinking() {
  const anim = useRef(new Animated.Value(0.35)).current;
  const [secs, setSecs] = useState(0); // 真机反馈 4/4:静止一两分钟像死机——计秒示活
  useEffect(() => {
    const loop = Animated.loop(Animated.sequence([
      Animated.timing(anim, { toValue: 1, duration: 650, useNativeDriver: true }),
      Animated.timing(anim, { toValue: 0.35, duration: 650, useNativeDriver: true }),
    ]));
    loop.start();
    const t = setInterval(() => setSecs((x) => x + 1), 1000);
    return () => { loop.stop(); clearInterval(t); };
  }, []);
  return (
    <View style={s.thinkingBar}>
      <Text style={s.thinkingHat}>🎩</Text>
      <Animated.Text style={[s.thinkingDots, { opacity: anim }]}>
        局长在酝酿{secs > 2 ? ` ${secs}s` : ""} …
      </Animated.Text>
    </View>
  );
}

// —— 骰盅道具(玩家自己摇)——
// 房主原则:局长不替玩家玩。盅由局长发(prop.dice_cup),点数由玩家在这儿自己摇出来。
// 摇一摇体感:Accelerometer 测晃动脉冲,摇够停下→扣盅→POST roll;点数由引擎 RNG 出
// (摇的时长/力度不影响点数),但手感让人觉得是自己摇出来的。传感器不可用有"摇!"按钮兜底。
const SHAKE_G = 1.28;      // 加速度模长(单位 g)离 1g 的偏移超此值算一次晃
const SHAKE_MIN = 3;       // 摇够几次脉冲才让扣盅(仪式对齐大话骰:摇几下再开)
const SHAKE_GAP_MS = 170;  // 两次脉冲最小间隔,防一次甩动被数成好几下

// —— 「开牌!」(challenge)——
// 宪法:叫价博弈永远留在嘴上;唯独开牌是判定时刻,做成按钮。手感选**长按 600ms**
// (不走二次确认弹窗):拍桌喊开是一个「按住发力」的动作,长按比对话框更像它,
// 且天然防误触——短按只轻震提示,不发事件。长按成立后重触感,弹出快速叫价
// (count 1-10 快拨、10+ 连点递增到 30;face 骰面点选;可跳过=不带 bid)。
function ChallengeControl({ onChallenge, others, onVoiceStart, onVoiceEnd }) {
  const [picking, setPicking] = useState(false); // 长按成立后进入叫价快选
  const [cnt, setCnt] = useState(null);
  const [face, setFace] = useState(null);
  const [bidder, setBidder] = useState(null); // 开的是谁(可选):不选=照旧局长点名
  const [tapHint, setTapHint] = useState(false); // 真机反馈:短按静默无反应像坏了——亮字提示
  if (!picking) {
    return (
      <Pressable style={s.challengeBtn} delayLongPress={600}
        onPress={() => {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);
          setTapHint(true); setTimeout(() => setTapHint(false), 1100);
        }}
        onLongPress={() => {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy); // 拍桌那一下
          setPicking(true);
        }}>
        <Text style={s.challengeBtnText}>⚡ 开牌!</Text>
        <Text style={[s.challengeBtnHint, tapHint && { color: "#ffb03a", fontWeight: "800" }]}>
          {tapHint ? "要按住 0.6 秒——拍桌喊开!" : "长按拍桌喊开"}
        </Text>
      </Pressable>
    );
  }
  const ready = cnt && face;
  return (
    <View style={s.bidBox}>
      <Text style={s.bidLabel}>被开的那口叫到几个几?(桌上喊过的;不记得可跳过)</Text>
      <View style={s.bidRow}>
        {[1, 2, 3, 4, 5, 6, 7, 8, 9, 10].map((n) => (
          <Pressable key={n} style={[s.bidChip, cnt === n && s.bidChipOn]} onPress={() => setCnt(n)}>
            <Text style={cnt === n ? s.bidChipTextOn : s.bidChipText}>{n}</Text>
          </Pressable>
        ))}
        <Pressable style={[s.bidChip, cnt > 10 && s.bidChipOn]}
          onPress={() => setCnt(cnt && cnt >= 10 ? Math.min(30, cnt + 1) : 11)}>
          <Text style={cnt > 10 ? s.bidChipTextOn : s.bidChipText}>{cnt > 10 ? cnt : "10+"}</Text>
        </Pressable>
      </View>
      <View style={s.bidRow}>
        {[1, 2, 3, 4, 5, 6].map((f) => (
          <Pressable key={f} style={[s.bidDieWrap, face === f && s.bidDieOn]} onPress={() => setFace(f)}>
            <Die n={f} mini />
          </Pressable>
        ))}
      </View>
      {/* 第三行「开的是谁?」:点了被开的叫价人,叫价人输时也能当庭报真名;
          可不选(再点一下取消)——照旧 loser=null 由局长点名。名单=除自己外的持盅玩家 */}
      {(others || []).length > 0 ? (
        <>
          <Text style={s.bidLabel}>开的是谁?(可不选——局长点名)</Text>
          <View style={s.bidRow}>
            {others.map((p) => (
              <Pressable key={p} style={[s.bidChip, bidder === p && s.bidChipOn]}
                onPress={() => setBidder(bidder === p ? null : p)}>
                <Text style={bidder === p ? s.bidChipTextOn : s.bidChipText}>{p}</Text>
              </Pressable>
            ))}
          </View>
        </>
      ) : null}
      {/* 语音开:按住说「三个六,开!」松手即开——不点选,嘴上开牌(房主 2026-07-24) */}
      {onVoiceStart ? (
        <Pressable style={s.voiceOpenBtn} onPressIn={onVoiceStart} onPressOut={onVoiceEnd}>
          <Text style={s.voiceOpenText}>🎙 按住说着开(如「三个六,开!」)</Text>
        </Pressable>
      ) : null}
      <Pressable style={[s.challengeGo, !ready && s.rollBtnDim]} disabled={!ready}
        onPress={() => onChallenge({ count: cnt, face }, bidder)}>
        <Text style={s.challengeGoText}>{ready ? `⚡ 开!(${cnt}个${face})` : "点选几个几,或跳过"}</Text>
      </Pressable>
      <View style={s.row}>
        <Pressable style={s.bidSkip} onPress={() => onChallenge(null)}>
          <Text style={s.bidSkipText}>跳过叫价直接开</Text>
        </Pressable>
        <Pressable style={s.bidSkip} onPress={() => { setPicking(false); setCnt(null); setFace(null); setBidder(null); }}>
          <Text style={s.bidSkipText}>算了,不开</Text>
        </Pressable>
      </View>
    </View>
  );
}

function DiceCup({ prop, rolling, onRoll, onChallenge, others, onVoiceStart, onVoiceEnd }) {
  const { count, rolled } = prop;
  const [shakes, setShakes] = useState(0);  // 摇动脉冲计数:既是仪式进度,也驱动 DiceStage 盅体抖
  const [sensorOk, setSensorOk] = useState(true);
  const lastPulse = useRef(0);
  const shakesRef = useRef(0);
  const settleTimer = useRef(null);
  const rollRef = useRef(onRoll);
  rollRef.current = onRoll;                 // 始终指向最新 onRoll,自动扣盅时不吃旧闭包
  const challengedBy = prop.challenged_by;  // 有人开牌:全桌盅锁定,摇的入口一并撤下
  const armed = !rolled && !rolling && !challengedBy; // 持未摇盅、没在扣、没被开:才订阅传感器

  // 传感器订阅:只在持未摇盅时挂,摇完/离屏即退订——别让轮询页背着传感器跑
  useEffect(() => {
    if (!armed) return;
    shakesRef.current = 0; setShakes(0);
    let sub = null, alive = true;
    (async () => {
      try {
        if (!(await Accelerometer.isAvailableAsync())) { if (alive) setSensorOk(false); return; }
      } catch (e) { if (alive) setSensorOk(false); return; }
      if (!alive) return;
      Accelerometer.setUpdateInterval(60);   // ~60ms 采样
      sub = Accelerometer.addListener(({ x, y, z }) => {
        const mag = Math.sqrt(x * x + y * y + z * z);     // 静止≈1g
        if (Math.abs(mag - 1) < SHAKE_G - 1) return;      // 偏移不够,不算晃
        const now = Date.now();
        if (now - lastPulse.current < SHAKE_GAP_MS) return;
        lastPulse.current = now;
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);  // 每次晃一次轻触感(全平台保底)
        shakesRef.current += 1;
        setShakes(shakesRef.current);   // 计数变化 → DiceStage 盅体抖一下 + 撞击音(降级层)
        // 摇够后骤停自动扣盅:每次脉冲重置 settle 计时,停手 ~420ms 没新脉冲就自动扣
        if (shakesRef.current >= SHAKE_MIN) {
          if (settleTimer.current) clearTimeout(settleTimer.current);
          settleTimer.current = setTimeout(() => rollRef.current(), 420);
        }
      });
    })();
    return () => {
      alive = false;
      if (sub) sub.remove();
      if (settleTimer.current) clearTimeout(settleTimer.current);
    };
  }, [armed, count]);

  // 摇过的盅:常驻显示自己的点数(大话骰全程要盯着自己的骰吹牛,不能摇完就没了)。
  // 骰面旁常驻「开牌!」入口(醒目但次于摇/扣主流程);被开后换成"等局长清算"。
  // 揭的必须是服务器回的那几颗点数(rolled=my_prop.rolled),DiceStage 只忠实揭盅。
  if (rolled) {
    const sum = rolled.reduce((a, b) => a + b, 0);
    return (
      <View style={s.cupBox}>
        <Text style={s.cupTitle}>🎲 你的骰盅 · {count}颗(只有你看得到)</Text>
        <DiceStage count={count} revealed={rolled} />
        {rolled.length > 1 ? <Text style={s.diceSum}>Σ{sum}</Text> : null}
        {challengedBy ? (
          <Text style={s.challengedNote}>⚡ {challengedBy} 开牌了{fmtBid(prop.bid)}——已开牌,等局长清算</Text>
        ) : (
          <ChallengeControl onChallenge={onChallenge} others={others}
            onVoiceStart={onVoiceStart} onVoiceEnd={onVoiceEnd} />
        )}
      </View>
    );
  }

  // 没摇就被开了牌(别人拍了开牌):盅锁定,摇的入口撤下,只留状态(盅盖着)
  if (challengedBy) {
    return (
      <View style={s.cupBox}>
        <Text style={s.cupTitle}>🎲 骰盅 · {count} 颗</Text>
        <DiceStage count={count} revealed={null} />
        <Text style={s.challengedNote}>⚡ {challengedBy} 开牌了{fmtBid(prop.bid)}——骰盅已锁,等局长清算</Text>
      </View>
    );
  }

  const enough = shakes >= SHAKE_MIN;
  // 未摇的活盅:主视觉是盖着的骰盅(摇时跟手抖、扣盅时哗啦哗啦),点数不出现在这里——
  // 盅盖着看不见骰,正是大话骰的产品事实。DiceStage 吃 pulse/rolling,不产出任何结果。
  return (
    <View style={s.cupBoxActive}>
      <Text style={s.cupTitle}>🎲 骰盅 · {count} 颗</Text>
      <DiceStage count={count} revealed={null} pulse={shakes} rolling={rolling} />
      {rolling ? (
        <Text style={s.cupHint}>哗啦哗啦……扣盅揭晓</Text>
      ) : sensorOk ? (
        // 传感器可用:主视觉是骰盅本体 +「摇一摇手机!」大字(体感是主交互),
        // 摇按钮缩小放底部当兜底——别让常驻大按钮把摇一摇的仪式感盖成毛坯点点点
        <>
          <Text style={s.shakeMain}>🎲 摇一摇手机!</Text>
          <Text style={s.cupHint}>
            {enough ? "摇够了!停手自动扣盅" : `摇 ${SHAKE_MIN} 下起扣(${shakes}/${SHAKE_MIN})`}
          </Text>
          <Pressable style={s.rollBtnSmall} onPress={onRoll}>
            <Text style={s.rollBtnSmallText}>{enough ? "扣盅!" : "摇不动?点这里替你摇"}</Text>
          </Pressable>
        </>
      ) : (
        // 传感器不可用/无权限/桌面平放:按钮放大回主交互位,点了同样扣盅,终点一致
        <>
          <Text style={s.cupHint}>这台手机摇不了——点按钮替你摇</Text>
          <Pressable style={s.rollBtn} onPress={onRoll}>
            <Text style={s.rollBtnText}>摇!</Text>
          </Pressable>
        </>
      )}
    </View>
  );
}

export default function App() {
  useKeepAwake(); // 快枪手对峙期间息屏=判负,整局常亮
  const [base, setBase] = useState(DEFAULT_SERVER);
  const [showServer, setShowServer] = useState(false); // 官方服域名焊死后,长按唤回调试入口
  const [me, setMe] = useState("");
  const [room, setRoom] = useState("");   // 房间码:多局并发时定位自己那一桌;留空=服务器唯一房间
  const [joined, setJoined] = useState(false);
  // —— 大厅态(开房改革 2026-07-24):房主空手开房拿码,朋友自己 join,人齐了锁定开打 ——
  const [inLobby, setInLobby] = useState(false);   // 停在大厅页(等人齐/等房主开打)
  const [isHost, setIsHost] = useState(false);     // 我是开房那台(能按开打)
  const [hostToken, setHostToken] = useState("");  // 开房返回的房主令牌(锁定时认人)
  const [seated, setSeated] = useState(false);     // 我已经报名入座(填了自己的名字)
  const [roster, setRoster] = useState([]);        // 大厅实时名单(轮询 /api/lobby)
  const [lobbyErr, setLobbyErr] = useState("");
  const [view, setView] = useState(null);
  const [err, setErr] = useState("");
  const [say, setSay] = useState("");
  const [dueled, setDueled] = useState(false); // 本次对决我开过枪了
  const [rolling, setRolling] = useState(false); // 骰盅扣盅动画进行中(挡重复扣)
  const [recording, setRecording] = useState(null); // 录音判定进行中的 Recording 对象
  // —— 按住说话(PTT,房主获批 2026-07-24)——与录音判定 recording 完全独立,互不干扰。
  // 显式动作:按住「局长」键才收音、松手即停即传;音频服务端转写完即弃,不留存。
  const [ptt, setPtt] = useState(false);        // 按住收音中(按钮变红大化 + 提示用)
  const [voiceMsgs, setVoiceMsgs] = useState([]); // 🎤 转写后已发给局长的话(本地回显)
  const pttRef = useRef(null);                  // 进行中的 PTT Recording 对象
  const pttHeldRef = useRef(false);             // 手指还按着吗(create 是异步的,防松手竞态)
  const pttModeRef = useRef("host");            // "host"=说给局长 | "challenge"=语音开盅
  const [challengeFlash, setChallengeFlash] = useState(null); // 「⚡ 开牌!」全屏横幅(1.6s 自动散)
  const [bellFlash, setBellFlash] = useState(null); // 系统级炸铃满屏大字(1.2s 自动散)
  // 额头牌查看:点桌面某人的 chip → 用 forehead 组件展示那个人的牌(自己的显示牌背)。
  // {name, identity, isMe};身份永远来自 view.foreheads(服务端发,自己那张缺席),不本地发牌。
  const [foreheadPeek, setForeheadPeek] = useState(null);
  // 乐观回显(真机节奏裁定 2026-07-24):任何动作发出的瞬间就在这条 outbox 里以"已发出"
  // 形态显示(淡色+小勾),HTTP 确认后短暂转正常再撤(真实 feed 行由轮询带出正常色),
  // 失败转红可重发。堵死"点了没反应等轮询"的死区。
  const [outbox, setOutbox] = useState([]); // [{id, label, status:"pending"|"ok"|"err", ev}]
  const outboxSeq = useRef(0);
  // 选择框即收:点了 open_ask 的选项瞬间本地收起换"✓ 已选 X",不等下一拍轮询撤 ask;
  // 轮询发现 ask 还开着(轮流模式还在别人/换了题)按服务器状态为准恢复。
  const [askPicked, setAskPicked] = useState(null); // {prompt, asked, choice}
  const [sceneBusy, setSceneBusy] = useState(false); // 场景侦察分析中(几秒,给反馈防"哑巴按钮")
  const prevRef = useRef({ inbox: 0, drawn: false, challenged: false, verdict: null });
  // 炸铃本地定时:记已排定那口铃的 at(去重,同一铃只触发一次)+ 定时器句柄(新铃覆盖旧铃时清掉)
  const bellRef = useRef({ at: null, timer: null });
  const feedRef = useRef(null); // 局长最新一句永远滚到眼前(手机举得远,不能靠手扒)

  // 设备匿名ID(用户数据层地基):首启生成、永久复用,随每个事件带给服务端,
  // episode 里落 device_bind 锚点——将来账号系统上线,历史局按它一键认领。
  const devRef = useRef(null);
  // 开 App 就把上次填过的回填进来(没存过=首次,静默略过)
  useEffect(() => {
    (async () => {
      let p = {};
      try {
        p = JSON.parse(await FileSystem.readAsStringAsync(PREFS));
        if (p.base) setBase(p.base);
        if (p.me) setMe(p.me);
        if (p.room) setRoom(p.room);
        if (p.skey) setStartKey(p.skey);  // 开局口令也要记住(真机病历:重载即忘,像被改了)
      } catch (e) { /* 首次开、或文件坏了:当没存过 */ }
      devRef.current = p.dev ||
        "d-" + Math.random().toString(36).slice(2, 10) + Date.now().toString(36);
      if (!p.dev)  // 新生成的立刻落盘,别等入座——ID 的稳定性就是它的全部价值
        FileSystem.writeAsStringAsync(PREFS, JSON.stringify({ ...p, dev: devRef.current }))
          .catch(() => {});
    })();
  }, []);
  // 只在真的入座/开局成功后才记——失败的地址记下来只会next次继续错
  const remember = (b, m, r) =>
    FileSystem.writeAsStringAsync(PREFS,
      JSON.stringify({ base: b, me: m, room: r, dev: devRef.current,
                       skey: startKey.trim() }))
      .catch(() => {});

  // 录音判定(judge.audio):按一下录、再按一下交卷;裁判在服务端(接 key 即通)
  const toggleRecord = async () => {
    try {
      if (recording) {
        setRecording(null);
        await recording.stopAndUnloadAsync();
        const uri = recording.getURI();
        const b64 = await FileSystem.readAsStringAsync(uri, { encoding: "base64" });
        const res = await api("/api/audio", { player: me, audio_b64: b64, format: "m4a" });
        Alert.alert("裁判", res.error || `${res.verdict}${res.reason ? ":" + res.reason : ""}`);
        return;
      }
      const perm = await Audio.requestPermissionsAsync();
      if (!perm.granted) { Alert.alert("需要麦克风权限", "录音判定要用麦克风"); return; }
      await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });
      const { recording: rec } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY);
      setRecording(rec);
    } catch (e) {
      setRecording(null);
      Alert.alert("录音失败", String(e.message));
    }
  };

  // —— 按住说话(PTT):长按「局长」键收音,松手停录→ /api/stt 转写→以 say(to=局长) 入局 ——
  // 姿势参照 toggleRecord(同一套 expo-av),但状态独立(pttRef),与录音判定互不干扰。
  const startPtt = async () => {
    try {
      if (pttRef.current) return;               // 已在录,别叠第二路
      const perm = await Audio.requestPermissionsAsync();
      if (!perm.granted) { Alert.alert("需要麦克风权限", "按住说话要用麦克风"); return; }
      await Audio.setAudioModeAsync({ allowsRecordingIOS: true, playsInSilentModeIOS: true });
      Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);   // 开录轻震:确认在收
      const { recording: rec } = await Audio.Recording.createAsync(
        Audio.RecordingOptionsPresets.HIGH_QUALITY);
      if (!pttHeldRef.current) {                // 手已经松了(create 是异步的):立即停掉弃录
        rec.stopAndUnloadAsync().catch(() => {});
        return;
      }
      pttRef.current = rec; setPtt(true);
    } catch (e) { pttRef.current = null; setPtt(false); Alert.alert("录音失败", String(e.message)); }
  };
  const stopPtt = async () => {
    const rec = pttRef.current;
    const mode = pttModeRef.current;            // 松手瞬间取走模式并复位,失败重试也不串台
    pttModeRef.current = "host";
    pttRef.current = null; setPtt(false);
    if (!rec) return;                           // 短按/没录起来:这里啥也不做
    try {
      await rec.stopAndUnloadAsync();           // 松手即停:PTT 之外零采集
      const b64 = await FileSystem.readAsStringAsync(rec.getURI(), { encoding: "base64" });
      uploadPtt(b64, false, mode);
    } catch (e) { Alert.alert("录音失败", String(e.message)); }
  };
  const uploadPtt = async (b64, retried, mode = "host") => {
    try {
      const res = await api("/api/stt", { player: me, audio_b64: b64, format: "m4a" });
      if (!res || res.error || !res.text) throw new Error((res && res.error) || "服务器没回转写");
      Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
      if (mode === "challenge") {
        // 语音开盅:话已进局长耳朵(/api/stt 入局),这里再解析叫价补一发结构化开牌。
        // 提到别的持盅玩家名=报了被开的人;解析不出叫价=不带 bid 开(局长兜底问)。
        const bid = parseSpokenBid(res.text);
        const names = ((view && view.cups) || []).map((c) => c.player).filter((p) => p && p !== me);
        const bidder = bid ? (names.find((n) => res.text.includes(n)) || null) : null;
        sendChallenge(bid, bidder);
        setVoiceMsgs((m) => [...m, res.text + (bid ? `(⚡开牌:${bid.count}个${bid.face}${bidder ? "·开" + bidder : ""})` : "(⚡开牌,叫价局长断)")]);
      } else {
        setVoiceMsgs((m) => [...m, res.text]);  // 转写文本以已发消息形态回显(标 🎤)
      }
    } catch (e) {
      if (!retried) {                           // 失败不丢录音:b64 还在手里,可再送一次
        Alert.alert("语音没送到局长", String(e.message), [
          { text: "算了" },
          { text: "重发这段", onPress: () => uploadPtt(b64, true, mode) },
        ]);
      } else {
        Alert.alert("还是没送到", String(e.message) + "(打字仍可用)");
      }
    }
  };
  // 语音开盅的按住/松手(房主 2026-07-24:「开盅也得弄个语音开,不选,说句话开了」)
  const startVoiceChallenge = () => { pttModeRef.current = "challenge"; pttHeldRef.current = true; startPtt(); };
  const endVoiceChallenge = () => { pttHeldRef.current = false; stopPtt(); };
  // 离场清资源:组件卸载时若还按着,停掉并弃录(音频不留存)
  useEffect(() => () => {
    pttHeldRef.current = false;
    if (pttRef.current) { pttRef.current.stopAndUnloadAsync().catch(() => {}); pttRef.current = null; }
  }, []);

  // —— 手机开局页(v0 欠账补):此前开局只能在电脑驾驶舱,手机只能入座 ——
  const [creating, setCreating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [minutes, setMinutes] = useState("30");
  const [wildness, setWildness] = useState("6");
  // 场合不再手填:开房那一拍拍张现场照,视觉链路自动认出场合/实物(想文字微调走驾驶舱)。
  const [lobbyScene, setLobbyScene] = useState(null); // 大厅拍照读场结果 {pending|error|occasion_guess,objects}
  const [playlist, setPlaylist] = useState("");
  const [botsText, setBotsText] = useState("");
  const [startKey, setStartKey] = useState(""); // 开局口令(服务器设了 ZAKZOK_START_KEY 才需要)

  // 多局并发:除 /api/start 外,所有请求带上房间码(query+body 都带,服务端两处都认);
  // room 留空则不带,服务器按唯一活跃房间默认命中(向后兼容单桌)。
  const api = async (path, body) => {
    let p = path;
    let b = body;
    if (room && path.startsWith("/api/") && !path.startsWith("/api/start")) {
      p += (path.includes("?") ? "&" : "?") + "room=" + encodeURIComponent(room);
      if (b) b = { room, ...b };
    }
    const r = await fetch(base.replace(/\/$/, "") + p, b ? {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(b),
    } : undefined);
    return r.json();
  };
  const sendEvent = (ev) =>
    api("/api/event", { ...ev, player: me, device_id: devRef.current })
      .catch(() => setErr("事件没发出去,再点一次"));

  // 乐观回显:动作发出的瞬间进 outbox("已发出"淡色+小勾),HTTP 确认转 ok(短暂后撤,
  // 真实 feed 行由轮询带出正常色),失败转 err 红色可重发。chips/say/done/forfeit/抢答
  // 一律走它——玩家点下去立刻有反馈,不再有"点了没反应"的死区。
  const fireOutbox = (id, ev) => {
    setOutbox((o) => o.map((x) => (x.id === id ? { ...x, status: "pending" } : x)));
    api("/api/event", { ...ev, player: me, device_id: devRef.current })
      .then((res) => {
        if (res && res.error) throw new Error(res.error);
        setOutbox((o) => o.map((x) => (x.id === id ? { ...x, status: "ok" } : x)));
        // 确认后短暂显示"已送达"再撤:真实事件此刻已由轮询进 feed(正常色)
        setTimeout(() => setOutbox((o) => o.filter((x) => x.id !== id)), 900);
      })
      .catch(() => setOutbox((o) => o.map((x) => (x.id === id ? { ...x, status: "err" } : x))));
  };
  const sendEventEcho = (ev, label) => {
    const id = ++outboxSeq.current;
    Haptics.selectionAsync();
    setOutbox((o) => [...o, { id, label, status: "pending", ev }]);
    fireOutbox(id, ev);
  };

  // 扣盅:玩家自己摇出点数的那一下(手真摇够/骤停自动扣,或按钮兜底都汇到这里)。
  // 引擎 RNG 出点数(摇的力度/时长不影响),点数经 🔒🎲 水印路回本人私件——App 只认水印
  // 画骰面,POST 只确认摇了,不信任响应里的点数(防伪架构一致)。结果由轮询 my_prop 揭晓。
  const rollCup = async () => {
    if (rolling) return;              // 已在扣盅动画里,别重复 POST
    setRolling(true);
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);   // 扣盅瞬间重触感
    const buzz = setInterval(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light), 70);
    setTimeout(() => clearInterval(buzz), 420);
    try {
      const res = await api("/api/event", { type: "roll", player: me });
      if (res && res.error) Alert.alert("摇不了", res.error);   // 没盅/已摇过:服务端驳回
    } catch (e) { setErr("摇盅没发出去,再点一次"); }
    // 点数经轮询回来(my_prop.rolled),给乱翻动画留够时间再解除 rolling
    setTimeout(() => setRolling(false), 520);
  };

  // 开牌:长按「开牌!」成立后到这儿发 challenge 事件(bid=被开那口叫价,可为 null;
  // bidder=开的是谁,可选——带上它叫价人输也能当庭报真名,不带照旧局长点名)。
  // 校验在服务端(持已摇盅、一局一开),驳回原话弹出来;全桌的感知走轮询里的 cups。
  const sendChallenge = async (bid, bidder) => {
    try {
      const res = await api("/api/event", { type: "challenge", player: me,
        ...(bid ? { bid } : {}), ...(bid && bidder ? { bidder } : {}) });
      if (res && res.error) Alert.alert("开不了牌", res.error);
    } catch (e) { setErr("开牌没发出去,再按一次"); }
  };

  // 炸铃音效挂接点:音效资产归道具官的音效包(别在这造资产)。接入后在此播放炸铃音。
  const playBellSound = () => { /* TODO: 接道具官音效包后在此播放"停!"炸铃音 */ };

  // 炸铃触发:到点那一下——三连重触感(Heavy ×3,间隔 120ms)+ 音效 + 满屏大字(1.2s 散)
  const triggerBell = (fx) => {
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
    setTimeout(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy), 120);
    setTimeout(() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy), 240);
    playBellSound();
    setBellFlash(fx || "停");
    setTimeout(() => setBellFlash(null), 1200);
  };

  // —— 轮询自己的视图 ——
  useEffect(() => {
    if (!joined) return;
    let alive = true;
    const tick = async () => {
      try {
        const v = await api("/api/view?player=" + encodeURIComponent(me));
        if (!alive) return;
        if (v.error) { setErr(v.error); return; }
        setErr("");
        const prev = prevRef.current;
        if ((v.inbox || []).length > prev.inbox)
          Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success); // 私件到了
        const inDuel = v.duel && v.duel.vs && v.duel.vs.includes(me);
        if (inDuel && v.duel.drawn && !prev.drawn)
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);             // 枪响!
        if (!v.duel) setDueled(false);
        // 系统级炸铃:见 bell → 用 server_now 算钟差,本地精确定时在换算后的时刻齐响。
        // 钟差 = 本地收包时刻 − 服务器当前时刻(server_now,含时钟偏移+单程网延的粗估);
        // 本地触发时刻 = bell.at 换算到本地钟 = bell.at*1000 + 钟差;距现在的延迟里 Date.now()
        // 抵消,净剩服务器视角的剩余时长——轮询何时到达(900ms 抖动)不影响触发点,全桌齐响。
        const bell = v.bell;
        if (bell && bell.at && v.server_now && bell.at !== bellRef.current.at) {
          const skewMs = Date.now() - v.server_now * 1000;      // 钟差
          const delay = bell.at * 1000 + skewMs - Date.now();   // = (bell.at − server_now)*1000
          if (bellRef.current.timer) clearTimeout(bellRef.current.timer); // 旧铃被覆盖:清旧定时
          bellRef.current.at = bell.at;                         // 记 at 去重:同一铃只触发一次
          bellRef.current.timer = setTimeout(() => triggerBell(bell.fx), Math.max(0, delay));
        }
        // 开牌感知(全桌):cups 里 challenged_by 从无到有 = 有人拍桌开牌——
        // 重触感一下 + 全屏「⚡ 开牌!」横幅短暂压场(1.6s 自动散,不拦操作)
        const chCup = (v.cups || []).find((c) => c.challenged_by);
        if (chCup && !prev.challenged) {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
          setChallengeFlash({ by: chCup.challenged_by, bid: chCup.bid });
          setTimeout(() => setChallengeFlash(null), 1600);
        }
        // 开牌即时清算结果卡:verdict 从无到有(带叫价的开牌当庭报数)→ 重震一下,
        // 结果卡由 v.challenge_verdict 常驻渲染(留到局长清算收盅才撤,全桌看得清)。
        const vd = v.challenge_verdict;
        const vdSig = vd ? `${vd.challenger}|${vd.face}|${vd.face_count}|${vd.bid && vd.bid.count}` : null;
        if (vdSig && vdSig !== prev.verdict)
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
        // 选择框即收的收尾:轮询发现 ask 已关(或换了题/换了被问的人)按服务器为准,
        // 撤掉本地"✓ 已选"回到服务器状态(下面渲染据 askPicked 与 open_ask 一致性判定)。
        if (!v.open_ask) setAskPicked(null);
        prevRef.current = { inbox: (v.inbox || []).length,
          drawn: !!(v.duel && v.duel.drawn), challenged: !!chCup, verdict: vdSig };
        setView(v);
      } catch (e) { if (alive) setErr("连不上服务器:" + e.message); }
    };
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(t);
      if (bellRef.current.timer) clearTimeout(bellRef.current.timer); }; // 离场清掉待触发的炸铃
  }, [joined]);

  // —— 大厅轮询:停在大厅页时拉实时名单;房主一开打(started)全场自动切游戏页 ——
  useEffect(() => {
    if (!inLobby || !room) return;
    let alive = true;
    const tick = async () => {
      try {
        const r = await fetch(base.replace(/\/$/, "") + "/api/lobby?room=" + encodeURIComponent(room));
        const j = await r.json();
        if (!alive) return;
        if (j.error) { setLobbyErr(j.error); return; }
        setLobbyErr("");
        if (j.started) { setInLobby(false); setJoined(true); return; } // 开打了→进游戏页
        setRoster(j.roster || []);
      } catch (e) { if (alive) setLobbyErr("连不上服务器:" + e.message); }
    };
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [inLobby, room, base]);

  // 报名入座(大厅页里房主/朋友都用它填自己的名字):成功后 seated,名字即 me
  const joinSeat = async (nm) => {
    const name = (nm || "").trim();
    if (!name) { Alert.alert("填个名字", "打上你自己的名字再入座"); return; }
    try {
      const res = await api("/api/join", { name, device_id: devRef.current });
      if (res.error) { Alert.alert("入座失败", res.error); return; }
      setMe(name); setSeated(true);
      if (res.roster) setRoster(res.roster);
      remember(base, name, room);
      if (res.started) { setInLobby(false); setJoined(true); } // 已开打的房:直接进游戏
    } catch (e) { Alert.alert("连不上", String(e.message)); }
  };

  // 房主开打:用最终名单锁定建引擎。成功后大厅轮询会发现 started 自动切页(这里也直接切)
  const lockAndStart = async () => {
    try {
      const res = await api("/api/lock", { host_token: hostToken, device_id: devRef.current });
      if (res.error) { Alert.alert("开打失败", res.error); return; }
      setInLobby(false); setJoined(true);
    } catch (e) { Alert.alert("连不上", String(e.message)); }
  };

  // 大厅里房主重拍场子(拍坏了/换了场地的低调补拍通道;首拍在开房那一拍自动走)。
  // 走既有 ImagePicker,识别结果照旧 lock 时并入 Session。
  const takeLobbyScene = async () => {
    try {
      const perm = await ImagePicker.requestCameraPermissionsAsync();
      if (!perm.granted) return;
      const shot = await ImagePicker.launchCameraAsync({ quality: 0.4, base64: true });
      if (shot.canceled) return;
      setLobbyScene({ pending: true });
      const sc = await api("/api/lobby_scene", {
        host_token: hostToken, device_id: devRef.current,
        image_b64: shot.assets[0].base64, media_type: "image/jpeg" });
      setLobbyScene(sc && sc.error ? { error: sc.error } : sc);
    } catch (e) { setLobbyScene({ error: String(e.message) }); }
  };

  // —— 大厅页:大字房间码 + 实时名单 + 开打大按钮(人未满 2 置灰) ——
  if (inLobby) {
    const enough = roster.length >= 2;
    return (
      <KeyboardAvoidingView style={s.page} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <StatusBar style="light" />
        <ScrollView contentContainerStyle={[s.center, { flexGrow: 1, paddingVertical: 44 }]}
          keyboardShouldPersistTaps="handled">
          <Text style={s.dim}>{isHost ? "你开的房 · 把房间码发给朋友" : "已进房 · 等房主开打"}</Text>
          <Text style={s.roomCode}>{room}</Text>
          {lobbyErr ? <Text style={s.err}>{lobbyErr}</Text> : null}
          <View style={s.rosterBox}>
            <Text style={s.rosterTitle}>已入座 {roster.length} 人</Text>
            {roster.length === 0 ? <Text style={s.rosterEmpty}>还没人入座…</Text> : null}
            {roster.map((n) => (
              <Text key={n} style={n === me ? s.rosterMe : s.rosterItem}>
                {n === me ? "🪑 " + n + "(你)" : "🪑 " + n}
              </Text>
            ))}
          </View>

          {/* 拍照读场:首拍已在开房那一拍自动走。这里只回显识别结果(场合猜测+实物,两行小字),
              外加一个低调的房主重拍通道(拍坏了/换场地用)——不放显眼大卡,拍照是增强不是门槛 */}
          {isHost ? (
            <>
              {lobbyScene ? (
                <View style={s.sceneMini}>
                  {lobbyScene.pending ? (
                    <Text style={s.sceneHint}>识别现场中…</Text>
                  ) : lobbyScene.error ? (
                    <Text style={s.sceneErr}>{lobbyScene.error}</Text>
                  ) : (
                    <>
                      <Text style={s.sceneLine}>🎯 {lobbyScene.occasion_guess || "没猜出场合(可到驾驶舱手填)"}</Text>
                      <Text style={s.sceneLine2}>认出:{(lobbyScene.objects || []).join("、") || "—"}</Text>
                    </>
                  )}
                </View>
              ) : null}
              <Pressable hitSlop={12} onPress={takeLobbyScene}>
                <Text style={s.optout}>📷 {lobbyScene && !lobbyScene.pending && !lobbyScene.error
                  ? `再拍一张(多角度,${lobbyScene.photos || 1}/5)` : "拍一下场子"}</Text>
              </Pressable>
            </>
          ) : null}

          {!seated ? (
            <>
              <Text style={s.dim}>{isHost ? "顺手给自己入个座:" : "打上你自己的名字:"}</Text>
              <TextInput style={s.input} placeholder="你的名字(如 疯子明)"
                placeholderTextColor="#667" autoCapitalize="none" autoCorrect={false}
                value={me} onChangeText={setMe} />
              <Pressable style={s.bigBtn} onPress={() => joinSeat(me)}>
                <Text style={s.bigBtnText}>我要入座</Text>
              </Pressable>
            </>
          ) : isHost ? (
            <>
              <Pressable style={[s.bigBtn, !enough && s.rollBtnDim]} disabled={!enough}
                onPress={lockAndStart}>
                <Text style={s.bigBtnText}>{enough ? "开打!" : "至少 2 人才能开打"}</Text>
              </Pressable>
              <Text style={s.dim}>人到齐点开打,座位就封了</Text>
            </>
          ) : (
            <Text style={s.dim}>🪑 你已入座,等房主开打…</Text>
          )}
          <Pressable hitSlop={14} onPress={() => {
            setInLobby(false); setSeated(false); setIsHost(false);
            setHostToken(""); setRoster([]); setRoom("");
          }}>
            <Text style={s.optout}>← 退出大厅</Text>
          </Pressable>
        </ScrollView>
      </KeyboardAvoidingView>
    );
  }

  if (!joined) {
    return (
      <KeyboardAvoidingView style={s.page} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <StatusBar style="light" />
        <ScrollView contentContainerStyle={[s.center, { flexGrow: 1, paddingVertical: 44 }]}
          keyboardShouldPersistTaps="handled">
          <Text style={s.logo}>ZAKZOK</Text>
          <Text style={s.dim}>{creating ? "开一局新的" : "局长在等你入座"}</Text>
          {(!DEFAULT_SERVER || showServer) ? (
            <TextInput style={s.input} placeholder="服务器,如 http://192.168.1.5:8747"
              placeholderTextColor="#667" autoCapitalize="none" autoCorrect={false}
              value={base} onChangeText={setBase} />
          ) : (
            <Pressable onLongPress={() => setShowServer(true)} delayLongPress={1200}>
              <Text style={s.dim}> </Text>
            </Pressable>
          )}

          {creating ? (
            <>
              <Text style={s.dim}>空手开房就行——朋友自己进来打名字,你不用替全桌打字</Text>
              <View style={s.row}>
                <TextInput style={[s.input, { flex: 1 }]} placeholder="时长(分钟)"
                  placeholderTextColor="#667" keyboardType="number-pad"
                  value={minutes} onChangeText={setMinutes} />
                <TextInput style={[s.input, { flex: 1 }]} placeholder="野度(1-10)"
                  placeholderTextColor="#667" keyboardType="number-pad"
                  value={wildness} onChangeText={setWildness} />
              </View>
              <Text style={s.dim}>📷 场合不用打字:点「开房拿码」会先拍张现场照,自动认出场合和实物(取消也能开)</Text>
              <TextInput style={s.input} placeholder="🔑 开局口令(服务器设了才要填,可选)"
                placeholderTextColor="#667" autoCapitalize="none" autoCorrect={false}
                value={startKey} onChangeText={setStartKey} />
              <TextInput style={s.input} placeholder="🎵 歌单(逗号分隔,可选)"
                placeholderTextColor="#667" value={playlist} onChangeText={setPlaylist} />
              <TextInput style={s.input} placeholder="🤖 bot 座位(可选,名:人设,逗号分隔)"
                placeholderTextColor="#667" value={botsText} onChangeText={setBotsText} />
              <Pressable style={s.bigBtn} disabled={starting} onPress={async () => {
                const bots = {};
                botsText.split(",").map((x) => x.trim()).filter(Boolean).forEach((x) => {
                  const [n, p] = x.split(/[:：]/);
                  if (n && n.trim()) bots[n.trim()] = (p || "").trim();
                });
                setStarting(true);
                // —— 拍照读场是开房流程的第一拍(输入侧去打字化)——
                // 先唤相机拍张场子;取消/失败都不挡开房(增强不是门槛)。拍到了就在
                // 开房拿码后紧接着 POST 到大厅拍照端点,进大厅时识别结果已在路上。
                let sceneShot = null;
                try {
                  const perm = await ImagePicker.requestCameraPermissionsAsync();
                  if (perm.granted) {
                    const shot = await ImagePicker.launchCameraAsync({ quality: 0.4, base64: true });
                    if (!shot.canceled) sceneShot = shot.assets[0].base64;
                  }
                } catch (e) { /* 拍照失败不挡开房 */ }
                try {
                  const res = await api("/api/start", {
                    // 开局口令:服务器设了 ZAKZOK_START_KEY 才需要(公网防白嫖开局);
                    // 没设则此字段被服务端忽略,留空即可
                    ...(startKey.trim() ? { key: startKey.trim() } : {}),
                    // players 不带 = 大厅态开房:立刻拿码,不建引擎不烧钱,朋友自己进来入座
                    device_id: devRef.current,   // 房主认人第二把钥匙(host_token 之外)
                    minutes: +minutes || 30,
                    wildness: +wildness || 6,
                    objects: [],
                    driver: "llm",
                    autoplay: true,   // 服务器自驱回合,手机可退到后台/锁屏也不停局
                    // 场合不再手填:拍照读场结果 lock 时并入(手填优先,拍照填空缺)
                    playlist: playlist.split(",").map((t) => t.trim()).filter(Boolean),
                    bots,   // bots 配置留在开房参数里,锁定开打时并入最终名单
                    // provider/模型一律不带:换家换模型是服务端 .env 的事(YAPPA_PROVIDER/
                    // YAPPA_MODEL),不该焊在客户端里——焊死过一次(qwen 一家),换中转站
                    // 就得改 App 重发包。手机只管开局,用哪家由开服务的人决定。
                  });
                  if (res.error) { Alert.alert("开房失败", res.error); return; }
                  if (!res.room_code) { Alert.alert("开房失败", "服务器没返回房间码"); return; }
                  // 拿码进大厅页:房主自己也要入座(开房≠入座),同页顺手填名字
                  setRoom(res.room_code);
                  setHostToken(res.host_token || "");
                  setIsHost(true); setSeated(false); setRoster([]);
                  setInLobby(true); setCreating(false);
                  remember(base, "", res.room_code);
                  // 拍了就紧接着把照片送去大厅拍照端点(带 room,此刻 room 状态还没刷新到闭包)
                  if (sceneShot) {
                    setLobbyScene({ pending: true });
                    api("/api/lobby_scene", {
                      room: res.room_code, host_token: res.host_token,
                      device_id: devRef.current, image_b64: sceneShot, media_type: "image/jpeg" })
                      .then((sc) => setLobbyScene(sc && sc.error ? { error: sc.error } : sc))
                      .catch((e) => setLobbyScene({ error: String(e.message) }));
                  } else {
                    setLobbyScene(null);
                  }
                } catch (e) {
                  Alert.alert("连不上", String(e.message));
                } finally {
                  setStarting(false);
                }
              }}>
                <Text style={s.bigBtnText}>{starting ? "开房中…" : "📷 开房拿码(先拍张场子)"}</Text>
              </Pressable>
              <Pressable hitSlop={14} onPress={() => setCreating(false)}>
                <Text style={s.optout}>← 返回入座</Text>
              </Pressable>
            </>
          ) : (
            <>
              {/* 座位名要跟服务端花名册逐字相等——自动更正/首字母大写会把 Jing 改成
                  King 这种,玩家看屏幕是对的却一直入座失败,必须关掉 */}
              <TextInput style={s.input} placeholder="你的座位名(自己起,朋友认得出就行)"
                placeholderTextColor="#667" autoCapitalize="none" autoCorrect={false}
                value={me} onChangeText={setMe} />
              <TextInput style={s.input} placeholder="房间码(如 A7QK;只有一桌可留空)"
                placeholderTextColor="#667" autoCapitalize="characters" autoCorrect={false}
                value={room} onChangeText={(t) => setRoom(t.trim().toUpperCase())} />
              <Pressable style={s.bigBtn} onPress={async () => {
                const nm = me.trim();
                if (!nm) { Alert.alert("填个名字", "打上你自己的名字再入座"); return; }
                try {
                  // room 已在 api() 里自动带上(query+body);留空则命中唯一房间。
                  // join 两态通吃:大厅房→进名单等开打;已开打的房→同名同机重连直接进游戏。
                  const res = await api("/api/join", { name: nm, device_id: devRef.current });
                  if (res.error) { Alert.alert("入座失败", res.error); return; }
                  setMe(nm); remember(base, nm, room);
                  if (res.started) { setJoined(true); }        // 局已开:直接进游戏页
                  else { setSeated(true); setIsHost(false); setInLobby(true); } // 大厅:等开打
                } catch (e) { Alert.alert("连不上", String(e.message)); }
              }}>
                <Text style={s.bigBtnText}>入座</Text>
              </Pressable>
              <Pressable style={[s.bigBtn, s.bigBtnAlt]} onPress={() => setCreating(true)}>
                <Text style={s.bigBtnAltText}>开新局</Text>
              </Pressable>
            </>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    );
  }

  const v = view || {};
  const inDuel = v.duel && v.duel.vs && v.duel.vs.includes(me);
  // 「现在该我干嘛」一眼锁定:醒目状态条(拔枪时刻是全屏对峙,不走这里)
  const askedMe = v.open_ask && v.open_ask.asked === me;
  // 定向题只有被问的人能答(真机病历 2026-07-24:「问 Lin 选对手」的按钮出现在 Jack
  // 手机上——服务端本就不收错人的答案,但按钮在就会被点,点了变成一条误导局长的私聊,
  // 一个人一次点击能把整局带偏)。问全场/没写对象才是人人可答。
  const askMine = v.open_ask && (askedMe
    || !v.open_ask.asked || ["全场", "all"].includes(v.open_ask.asked))
    && !((v.open_ask && v.open_ask.exclude) || []).includes(me); // 当事人回避:出题人不出按钮
  const myCue = v.photo_request ? "📸 轮到你:拍照判定,看下方题目"
    : v.audio_request ? "🎤 轮到你:录音判定,看下方题目"
    : askedMe ? "🫵 问到你了,往下答"
    : v.focus === me ? "🎯 焦点在你身上——做完按 ✅ 完成,不接按 🍺 认罚(自动扣分)" : null;

  const takePhoto = async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) { Alert.alert("需要相机权限", "拍照判定要用相机"); return; }
    const r = await ImagePicker.launchCameraAsync({ quality: 0.4, base64: true });
    if (r.canceled) return;
    try {
      const res = await api("/api/photo", {
        player: me, image_b64: r.assets[0].base64, media_type: "image/jpeg" });
      Alert.alert("裁判", res.error || `${res.verdict}${res.reason ? ":" + res.reason : ""}`);
    } catch (e) { Alert.alert("上传失败", String(e.message)); }
  };

  // 视频判定=抽帧走照片通道:显式判定时刻录段短视频,客户端抽 3 帧(开头/中段/尾段)
  // 转 base64,服务端 judge_photo 当"同一段动作的连续抽帧"多图送审,其余流程不变。
  const takeVideo = async () => {
    const perm = await ImagePicker.requestCameraPermissionsAsync();
    if (!perm.granted) { Alert.alert("需要相机权限", "拍视频判定要用相机"); return; }
    const r = await ImagePicker.launchCameraAsync({ mediaTypes: ["videos"], videoMaxDuration: 8 });
    if (r.canceled) return;
    try {
      const asset = r.assets[0];
      const durMs = asset.duration || 8000;
      // 0%/50%/95% 时点;起点用 1ms 而非 0——部分机型 time:0 抽不出帧
      const points = [1, Math.round(durMs * 0.5), Math.max(1, Math.round(durMs * 0.95))];
      const frames = [];
      for (const t of points) {
        const thumb = await VideoThumbnails.getThumbnailAsync(asset.uri, { time: t, quality: 0.4 });
        const b64 = await FileSystem.readAsStringAsync(thumb.uri,
          { encoding: FileSystem.EncodingType.Base64 });
        frames.push(b64);
      }
      const res = await api("/api/photo", { player: me, frames, media_type: "image/jpeg" });
      Alert.alert("裁判", res.error || `${res.verdict}${res.reason ? ":" + res.reason : ""}`);
    } catch (e) { Alert.alert("抽帧/上传失败", String(e.message)); }
  };

  // —— 快枪手全屏对峙 —— 视听层换成 props/revolver 的 DuelStage(持枪待发→拔枪火光枪响后坐)。
  // taps 判定 / duel_result 胜负逻辑零改动:onDraw 里做的事和原来那颗按钮一字不差
  // (setDueled + 重震 + sendEvent tap),胜负永远由服务端 duel_result 定。
  if (inDuel) {
    const drawn = v.duel.drawn;
    return (
      <>
        <StatusBar style="light" />
        <DuelStage
          vs={v.duel.vs}
          drawn={drawn}
          dueled={dueled}
          onDraw={() => {
            setDueled(true);
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
            sendEvent({ type: "tap" });
          }}
        />
      </>
    );
  }

  return (
    <KeyboardAvoidingView style={s.page} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <StatusBar style="light" />
      <View style={s.topbar}>
        <Text style={s.topText}>{me}{room ? ` · 🎫${room}` : ""} · 第{v.round ?? 0}轮 · 余{Math.max(0, Math.round(v.time_left_min ?? 0))}分</Text>
        {v.now_playing ? <Text style={s.topMusic}>🎵 {v.now_playing}</Text> : null}
      </View>
      {err ? <Text style={s.err}>{err}</Text> : null}
      {v.finished ? <Text style={s.finish}>🏁 本局已收</Text> : null}
      {myCue ? <View style={s.cueBar}><Text style={s.cueText}>{myCue}</Text></View> : null}
      {/* 局长思考指示:run_turn 进行中显示呼吸的「🎩 局长在酝酿 …」,不挡任何操作 */}
      {v.host_thinking ? <HostThinking /> : null}

      {/* 骰盅道具卡:局长发盅、玩家自己摇——常驻在 feed 之上,大话骰全程盯着自己的骰吹牛。
          others=除自己外的持盅玩家名(开牌面板「开的是谁?」的候选 chips) */}
      {v.my_prop ? <DiceCup prop={v.my_prop} rolling={rolling} onRoll={rollCup}
        onChallenge={sendChallenge} onVoiceStart={startVoiceChallenge} onVoiceEnd={endVoiceChallenge}
        others={(v.cups || []).map((c) => c.player).filter((p) => p && p !== me)} /> : null}
      {/* 在等谁摇(真机病历 2026-07-24:一只未摇的盅锁全桌,被锁的人干等8分钟以为
          App 坏了——只有去点开牌的人才撞到"还有人没摇完")。cups 数据早就在 view 里,
          摆到明面即可 */}
      {v.my_prop && (() => {
        const waiting = (v.cups || []).filter((c) => !c.rolled && c.player !== me).map((c) => c.player);
        return waiting.length ? (
          <Text style={s.cupWait}>⏳ 等 {waiting.join("、")} 摇盅(全摇才能开牌)</Text>
        ) : null;
      })()}

      {/* 桌面额头牌:局长发额头牌时,这一行 chips 显示桌上每个人,有牌的头上挂 🎴 角标。
          点带牌的人 → 弹出 forehead 组件亮出「view.foreheads 里那个人的牌」;点自己 →
          牌背「你的牌你看不见」(可见性反转在服务端焊死,自己那张永远缺席)。 */}
      {(() => {
        const fh = v.foreheads || {};                 // 别人的身份(服务端发,自己缺席)
        const active = Object.keys(fh).length > 0;    // 有额头牌在桌上
        if (!active) return null;
        const players = (v.players && v.players.length ? v.players
          : Object.keys(fh).concat(me)).filter((p, i, a) => p && a.indexOf(p) === i);
        return (
          <View style={s.foreheadRow}>
            <Text style={s.foreheadTitle}>🎴 桌面额头牌 · 点人看牌</Text>
            <View style={s.chipRow}>
              {players.map((p) => {
                const isMe = p === me;
                const has = isMe || Object.prototype.hasOwnProperty.call(fh, p);
                return (
                  <Pressable key={p} style={[s.fhChip, has && s.fhChipHas]}
                    onPress={() => has && setForeheadPeek({ name: p, identity: isMe ? null : fh[p], isMe })}>
                    <Text style={s.fhChipText}>{has ? "🎴 " : ""}{p}{isMe ? "(你)" : ""}</Text>
                  </Pressable>
                );
              })}
            </View>
          </View>
        );
      })()}

      {/* 「⚡ 开牌!」全屏横幅:短暂压场(1.6s),pointerEvents=none 不拦任何操作 */}
      {challengeFlash ? (
        <View pointerEvents="none" style={s.challengeFlash}>
          <Text style={s.challengeFlashText}>⚡ 开牌!</Text>
          <Text style={s.challengeFlashSub}>{challengeFlash.by} 开了{fmtBid(challengeFlash.bid)}</Text>
        </View>
      ) : null}

      {/* 开牌即时清算结果卡:开牌横幅之后紧接着的当庭报数结论(大字),留到局长清算收盅才撤。
          loser 有真名(开牌人输,或开牌时点了叫价人)直接报名;
          loser=null(叫价人输且没点人)报"叫价人输——局长点名!" */}
      {v.challenge_verdict ? (
        <View style={s.verdictCard}>
          <Text style={s.verdictHead}>
            ⚡ 叫{v.challenge_verdict.bid.count}个{v.challenge_verdict.bid.face}
            {" · "}实际{v.challenge_verdict.face_count}个
            {v.challenge_verdict.wild ? "(带赖子)" : ""}
          </Text>
          <Text style={s.verdictLoser}>
            {v.challenge_verdict.loser
              ? `${v.challenge_verdict.loser} 输!`
              : "叫价人输——局长点名!"}
          </Text>
        </View>
      ) : null}

      {/* 系统级炸铃:满屏大字那声「停!」(1.2s 自动散),pointerEvents=none 不拦操作。
          全桌手机在系统换算后的同一时刻齐响,比开牌横幅更大更炸——判定时刻要砸到桌上 */}
      {bellFlash ? (
        <View pointerEvents="none" style={s.bellFlash}>
          <Text style={s.bellFlashText}>{bellFlash}</Text>
        </View>
      ) : null}

      {/* 额头牌查看浮层:点桌面 chip 弹出,用 forehead 组件亮出那个人的牌;点空白收起。
          自己的牌只显示牌背——身份由服务端在 view 里就摘掉了,客户端天然拿不到。 */}
      {foreheadPeek ? (
        <Pressable style={s.foreheadPeek} onPress={() => setForeheadPeek(null)}>
          <ForeheadCard name={foreheadPeek.name} identity={foreheadPeek.identity} isMe={foreheadPeek.isMe} />
          <Text style={s.foreheadPeekHint}>{foreheadPeek.isMe ? "你的牌你看不见 · 靠别人给的提示猜" : "点空白处收起"}</Text>
        </Pressable>
      ) : null}

      <ScrollView style={s.feed} contentContainerStyle={{ paddingBottom: 12 }}
        ref={feedRef}
        onContentSizeChange={() => feedRef.current && feedRef.current.scrollToEnd({ animated: true })}>
        {(v.recent || []).map((t, i, arr) => {
          const latest = i === arr.length - 1; // 局长最新一句最显眼,历史缩小让路
          return (
            <View key={i} style={s.turn}>
              {t.host ? (
                <Text style={latest ? s.hostNow : s.host}>🎩 {t.host}</Text>
              ) : null}
              {(t.shown || []).map((c, j) => (
                <View key={j}>
                  <Text style={latest ? s.shownNow : s.shown}>📢 {c}</Text>
                  {/* 演示图最后一公里(2026-07-24):局长文字讲不清的手势/摆位,一张图秒懂 */}
                  {t.shown_demo && t.shown_demo[j] ? (
                    <Image source={{ uri: base + "/" + t.shown_demo[j] }}
                      style={s.demoImg} resizeMode="contain" />
                  ) : null}
                </View>
              ))}
              {(t.table || []).map((e, j) => {
                if (e.type === "duel_result" && e.winner)
                  return (
                    <Text key={j} style={s.duelResultEv}>
                      🔫 {e.winner} 快枪胜{e.loser ? ` ${e.loser}` : ""}{e.reason ? `(${e.reason})` : ""}
                    </Text>
                  );
                if (e.type === "roll")
                  return ( // 谁摇了骰盅:公开面只见动作、不见点数(点数只在本人手机+局长对账)
                    <Text key={j} style={s.rollEv}>🎲 {e.player} 摇了骰盅</Text>
                  );
                if (e.type === "challenge")
                  return ( // 开牌是全场判定时刻:谁开的+被开叫价公开(桌上喊出来的)
                    <Text key={j} style={s.challengeEv}>⚡ {e.player} 开牌!{fmtBid(e.bid)}</Text>
                  );
                if (e.value !== undefined && e.value !== null && e.type !== "say")
                  return ( // 公开随机(random.pick 之类)的结果:开签样式,不混进对话流
                    <View key={j} style={s.pickCard}>
                      <Text style={s.pickLabel}>🎲 当众开签</Text>
                      <Text style={s.pickValue}>{e.player} → {String(e.value)}</Text>
                    </View>
                  );
                return (
                  <Text key={j} style={s.tableEv}>
                    {e.player}{e.note ? ` ${e.note}` : e.text ? `:「${e.text}」` : e.value ? ` → ${e.value}` : ` · ${e.type}`}
                  </Text>
                );
              })}
            </View>
          );
        })}
        {/* 实时流(view.live=事件队列的遮蔽尾巴):桌上说话/信号不等局长回合跑完,
            立即以浅色即时区挂在局长最新话之后。同一事件被回合消化后自然从 live 消失、
            由 recent 正式行接棒(同一拍轮询里两边不并存,过渡不闪跳)。
            ①自己刚发且还挂在乐观回显 outbox 里的不重复显示;②同类连发折叠 ×N */}
        {(() => {
          const echoed = (it) => it.player === me && outbox.some((x) => {
            if (!x.ev) return false;
            if (it.type === "say") return x.ev.type === "say" && x.ev.text === it.text;
            if (it.type === "say_host") return x.ev.type === "say" && x.ev.to === "局长";
            return x.ev.type === it.type; // done/forfeit/tap 按事件类型对上即同一条
          });
          const folded = [];
          (v.live || []).filter((it) => it.player && !echoed(it)).forEach((it) => {
            const last = folded[folded.length - 1];
            if (last && last.player === it.player && last.type === it.type && last.text === it.text)
              last.n += 1;   // 相邻同人同类同文:折叠成一条 ×N
            else folded.push({ ...it, n: 1 });
          });
          const liveLabel = (it) => {
            const base = it.type === "say" ? `${it.player}:「${it.text}」`
              : it.type === "say_host" ? `${it.player} 跟局长说了句话` // 内容照旧只有局长看
              : it.type === "roll" ? `🎲 ${it.player} 摇了骰盅`
              : it.type === "done" ? `✅ ${it.player} 完成`
              : it.type === "forfeit" ? `🍺 ${it.player} 认罚`
              : it.type === "tap" ? `👏 ${it.player} 抢答`
              : it.type === "laugh" ? `😄 ${it.player} 笑场` : `${it.player} · ${it.type}`;
            return it.n > 1 ? `${base} ×${it.n}` : base;
          };
          return folded.length ? (
            <View style={s.liveZone}>
              {folded.map((it, i) => <Text key={i} style={s.liveLine}>{liveLabel(it)}</Text>)}
            </View>
          ) : null;
        })()}
        {/* 🎤 按住说话的本地回显:转写文本以已发消息形态挂在 feed 尾部(轮询稍后
            也会带回同一句——带 🎤 的这份标明它是语音转的) */}
        {voiceMsgs.map((t, i) => (
          <Text key={"v" + i} style={s.voiceMsg}>🎤 你对局长说:「{t}」</Text>
        ))}
      </ScrollView>

      {/* feed 以下的控制区自己可滚(真机病历 2026-07-24:骰盅面板+问询框一叠,
          页面超高又不能滑,底部按钮点不到)。flexShrink:1=平时按内容高,挤不下时
          内部滚动;feed 有 minHeight 兜底不至于被挤成零 */}
      <ScrollView style={{ flexShrink: 1, flexGrow: 0 }} keyboardShouldPersistTaps="handled"
        contentContainerStyle={{ paddingBottom: 2 }}>
      {v.photo_request && (
        <View style={s.photoBtn}>
          <Text style={s.photoText}>📸 {v.photo_request}</Text>
          <Text style={s.photoSub}>拍张照,或拍段≤8秒短视频,视觉裁判来判</Text>
          <View style={s.row}>
            <Pressable style={s.photoActionBtn} onPress={takePhoto}>
              <Text style={s.photoActionText}>📸 拍照</Text>
            </Pressable>
            <Pressable style={s.photoActionBtn} onPress={takeVideo}>
              <Text style={s.photoActionText}>🎥 拍视频</Text>
            </Pressable>
          </View>
        </View>
      )}

      {v.audio_request && (
        <View style={s.photoBtn}>
          <Text style={s.photoText}>🎤 {v.audio_request}</Text>
          <Text style={s.photoSub}>{recording ? "录音中…再按一下结束并交卷" : "按一下开录,听觉裁判来判"}</Text>
          <Pressable style={s.photoActionBtn} onPress={toggleRecord}>
            <Text style={s.photoActionText}>{recording ? "⏹ 停止并交卷" : "🎤 开始录音"}</Text>
          </Pressable>
        </View>
      )}

      {v.finished && (() => {
        // 皇冠只发给独一份的最高分(真机病历 2026-07-24:全员0分时稳定排序把皇冠
        // 戴给了座位表第一位——那不是冠军,是数组下标0,还和局长口头封的冠军打架)。
        // 没有分差就没有账面冠军,战报只列账,加冕以局长宣布为准。
        const entries = Object.entries(v.scores || {}).sort((a, b) => b[1] - a[1]);
        const crowned = entries.length > 0 && entries[0][1] > 0
          && (entries.length < 2 || entries[0][1] > entries[1][1]);
        return (
          <View style={s.settleBox}>
            <Text style={s.settleTitle}>🏁 终局战报</Text>
            {entries.map(([p, sc], i) => (
              <Text key={p} style={crowned && i === 0 ? s.settleTop : s.settleItem}>
                {crowned && i === 0 ? "👑 " : ""}{p}:{sc}
              </Text>
            ))}
            {!crowned ? <Text style={s.settleDim}>无分差,今晚的冠军以局长宣布为准</Text> : null}
          </View>
        );
      })()}

      {(v.inbox || []).length > 0 && (
        <View style={s.inboxBox}>
          <Text style={s.inboxTitle}>📬 只有你能看到</Text>
          {(() => { // key 用「内容+同文出现序号」:轮询窗口平移不重挂,骰子动画只放一次
            const seen = {};
            return v.inbox.slice(-3).map((x) => {
              seen[x] = (seen[x] || 0) + 1;
              const k = x + "#" + seen[x];
              const dice = parseDice(x);
              return dice ? <DiceReveal key={k} dice={dice} />
                : <Text key={k} style={s.inboxItem}>{x}</Text>;
            });
          })()}
        </View>
      )}

      {v.open_ask && (() => {
        // 选择框即收:选项被点的瞬间本地收起换"✓ 已选 X"(不等轮询撤 ask)。picked 只在
        // 同一题、同一被问对象下成立;轮询若 ask 换题/换人/关掉,picked 失配自动恢复选项框。
        const picked = askPicked && askPicked.prompt === v.open_ask.prompt
          && askPicked.asked === (v.open_ask.asked || "");
        return (
          <View style={[s.askBox, askedMe && s.askBoxMe]}>
            <Text style={askedMe ? s.askTextMe : s.askText}>🎤 {askedMe ? "问你" : `问${v.open_ask.asked}`}:{v.open_ask.prompt}</Text>
            {!askMine ? (
              <Text style={s.askPicked}>
                {(v.open_ask.exclude || []).includes(me)
                  ? "这题关于你——等大家猜…" : `等 ${v.open_ask.asked} 回答…`}
              </Text>
            ) : picked ? (
              <Text style={s.askPicked}>✓ 已选 {askPicked.choice}</Text>
            ) : (
              <View style={s.row}>
                {(v.open_ask.options || []).map((o, i) => (
                  <Pressable key={i} style={s.optBtn}
                    onPress={() => {
                      Haptics.selectionAsync();
                      const asked = v.open_ask.asked || "";
                      setAskPicked({ prompt: v.open_ask.prompt, asked, choice: o }); // 瞬间收起
                      api("/api/event", { type: "say", text: o, to: "局长", player: me, device_id: devRef.current })
                        .then((res) => { if (res && res.error) throw new Error(res.error); })
                        .catch(() => { setAskPicked(null); setErr("没选上,再点一次"); }); // 发失败恢复选项框
                    }}>
                    <Text style={s.optText}>{o}</Text>
                  </Pressable>
                ))}
              </View>
            )}
          </View>
        );
      })()}

      {/* 乐观回显条:任何动作发出的瞬间在这里"已发出"(淡色+小勾),确认后撤、失败转红可重发 */}
      {outbox.length > 0 && (
        <View style={s.outboxRow}>
          {outbox.map((x) => (
            <Pressable key={x.id} disabled={x.status !== "err"}
              onPress={() => x.status === "err" && fireOutbox(x.id, x.ev)}
              style={[s.echoPill, x.status === "err" && s.echoPillErr, x.status === "ok" && s.echoPillOk]}>
              <Text style={x.status === "err" ? s.echoTextErr : s.echoText}>
                {x.status === "err" ? `⚠ ${x.label} 没发出·重发` : x.status === "ok" ? `✓ ${x.label}` : `⌛ ${x.label} 已发出`}
              </Text>
            </Pressable>
          ))}
        </View>
      )}
      {/* 散场出口(真机病历 2026-07-24:「本局已收」后整个局面晾在那儿,按钮全都还能点,
          「再来一局」是聊天短语没人接——收局后动作区整体收起,只留一扇真门) */}
      {v.finished ? (
        <View style={[s.row, { justifyContent: "center" }]}>
          <Pressable style={[s.sigBtn, { backgroundColor: "#31506e" }]}
            onPress={() => { setJoined(false); setInLobby(false); setView(null); setAskPicked(null); }}>
            <Text style={s.sigText}>🚪 散场 · 回到首页</Text>
          </Pressable>
        </View>
      ) : (
      <>
      {/* 三信号(完成/认罚/抢答)是现实结果回流的唯一通道,砍不得——口头挑战时
          服务器无任何状态可依,靠它们收口(等待权裁定)。但平时亮着是噪音(房主
          观感 2026-07-24):默认调暗蛰伏,流程可见地轮到你/有钩子挂着时点亮 */}
      <View style={[s.row, !(v.timer_running || v.focus === me || askedMe
        || v.photo_request || v.audio_request) && { opacity: 0.45 }]}>
        {/* 点名派活时完成/认罚是当事人的活(真机病历:Ming 替 Lin 点了完成,全场跟按)
            ——不禁点(旁观确认也是信号,服务端已标 bystander),但视觉上让位当事人 */}
        <Pressable style={[s.sigBtn, { backgroundColor: "#2c5f3f" },
          v.focus && v.focus !== me && { opacity: 0.35 }]}
          onPress={() => sendEventEcho({ type: "done" }, "完成")}>
          <Text style={s.sigText}>✅ 完成{v.focus && v.focus !== me ? `(${v.focus}的活)` : ""}</Text>
        </Pressable>
        <Pressable style={[s.sigBtn, { backgroundColor: "#6b4a2b" },
          v.focus && v.focus !== me && { opacity: 0.35 }]}
          onPress={() => sendEventEcho({ type: "forfeit" }, "认罚")}>
          <Text style={s.sigText}>🍺 认罚跳过</Text>
        </Pressable>
        <Pressable style={[s.sigBtn, { backgroundColor: "#31506e", flex: 0.6 }]}
          onPress={() => sendEventEcho({ type: "tap" }, "抢答")}>
          <Text style={s.sigText}>👏 抢答</Text>
        </Pressable>
      </View>
      {/* 快捷回应条:最常说的几句单点即发(配轻触感),少动手、只关注现实场。
          发向哪儿看语境:问到你的窗口开着=刻意应答,发局长计票(真机病历:决赛投票
          点了「好!」发去桌上,引擎防截胡门槛只认 to=局长,票被静默丢弃);平时=桌上气氛 */}
      <View style={s.chipRow}>
        {QUICK_CHIPS.map((c) => (
          <Pressable key={c} style={s.chip}
            onPress={() => sendEventEcho({ type: "say", text: c, to: askMine ? "局长" : "桌上" }, c)}>
            <Text style={s.chipText}>{c}</Text>
          </Pressable>
        ))}
      </View>
      {/* 打字框保留但视觉降级(变矮变淡):chips 之外要打字的仍能打,但不再是主入口 */}
      <View style={s.row}>
        <TextInput style={[s.sayInputDim, { flex: 1 }]} placeholder="要打字再说…"
          placeholderTextColor="#556" value={say} onChangeText={setSay} />
        <Pressable style={s.sayBtnDim} onPress={() => { const t = say.trim(); if (t) { sendEventEcho({ type: "say", text: t, to: "桌上" }, t); setSay(""); } }}>
          <Text style={s.sayBtnDimText}>💬桌上</Text>
        </Pressable>
        {/* 「局长」键双态(PTT 获批 2026-07-24):短按=发输入框文字(原样);
            长按 350ms 起=按住说话(变红大化+轻震),松手停录→转写→say(to=局长)。
            onPressOut 每次松手都触发:短按时 pttRef 为空,stopPtt 直接返回,不误伤 */}
        <Pressable style={[s.sayBtnDim, ptt && s.pttBtnOn]} delayLongPress={350}
          onPress={() => { const t = say.trim(); if (t) { sendEventEcho({ type: "say", text: t, to: "局长" }, t); setSay(""); } }}
          onLongPress={() => { pttHeldRef.current = true; startPtt(); }}
          onPressOut={() => { pttHeldRef.current = false; stopPtt(); }}>
          {/* 能见度修复(房主 2026-07-24:「语音接口我没见到在哪」):没打字时明示按住说话 */}
          <Text style={ptt ? s.pttBtnOnText : s.sayBtnDimText}>{ptt ? "🎤 松手发给局长" : say.trim() ? "🎙发局长" : "🎙按住说话"}</Text>
        </Pressable>
      </View>
      <View style={[s.row, { justifyContent: "center" }]}>
        {/* 安全退出=离开房间退游戏(房主拍板 2026-07-24):不掺「跳过环节」二义
            ——跳过某轮的路早有(「过」短语/认罚跳过)。退回首页,随时可重新入座 */}
        <Pressable hitSlop={14} onPress={() => Alert.alert("安全退出",
          "离开房间,回到首页?(名字和房间码留着,随时能回来)", [
          { text: "再想想" },
          { text: "离开房间", style: "destructive", onPress: () => {
            setJoined(false); setInLobby(false); setSeated(false); setIsHost(false);
            setHostToken(""); setRoster([]); setView(null); setAskPicked(null);
          } },
        ])}>
          <Text style={s.optout}>安全退出</Text>
        </Pressable>
        <Pressable hitSlop={14} disabled={sceneBusy} onPress={async () => {   // 拍一张现场:实物清单+场景速写自动进局
          const perm = await ImagePicker.requestCameraPermissionsAsync();
          if (!perm.granted) return;
          const r = await ImagePicker.launchCameraAsync({ quality: 0.4, base64: true });
          if (r.canceled) return;
          setSceneBusy(true);   // 视觉分析要几秒,没反馈会被当成哑巴按钮(真机病历)
          const res = await api("/api/scene", { image_b64: r.assets[0].base64, media_type: "image/jpeg" })
            .catch(e => ({ error: e.message }));
          setSceneBusy(false);
          Alert.alert("场景侦察", res.error || `${res.brief || ""}\n实物:${(res.objects || []).join("、")}`);
        }}>
          <Text style={s.optout}>{sceneBusy ? "📷 侦察中…" : "📷 场景侦察"}</Text>
        </Pressable>
      </View>
      </>
      )}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: "#14141c", paddingTop: 54, paddingHorizontal: 14 },
  center: { alignItems: "center", justifyContent: "center", paddingTop: 0 },
  logo: { color: "#ffd54a", fontSize: 44, fontWeight: "900", marginBottom: 4 },
  dim: { color: "#889", fontSize: 14, marginBottom: 18 },
  input: { backgroundColor: "#20202c", color: "#eee", borderRadius: 10, padding: 12,
    width: "100%", marginVertical: 6, fontSize: 16 },
  bigBtn: { backgroundColor: "#ffd54a", borderRadius: 14, paddingVertical: 14,
    paddingHorizontal: 60, marginTop: 16 },
  bigBtnText: { fontSize: 20, fontWeight: "800", color: "#222" },
  bigBtnAlt: { backgroundColor: "#31506e", marginTop: 10 },
  bigBtnAltText: { fontSize: 18, fontWeight: "800", color: "#fff" },
  // 大厅页:大字房间码 + 实时名单
  roomCode: { color: "#ffd54a", fontSize: 64, fontWeight: "900", letterSpacing: 8,
    marginVertical: 10 },
  rosterBox: { backgroundColor: "#20202c", borderRadius: 12, padding: 14, width: "100%",
    marginVertical: 12, borderWidth: 1, borderColor: "#3a3350" },
  rosterTitle: { color: "#c9b8ff", fontSize: 14, fontWeight: "700", marginBottom: 8 },
  rosterEmpty: { color: "#667", fontSize: 14 },
  rosterItem: { color: "#eee", fontSize: 18, lineHeight: 26 },
  rosterMe: { color: "#ffd54a", fontSize: 18, lineHeight: 26, fontWeight: "800" },
  topbar: { flexDirection: "row", justifyContent: "space-between", marginBottom: 6 },
  topText: { color: "#aab", fontSize: 13 },
  topMusic: { color: "#8fb", fontSize: 13 },
  err: { color: "#f66", fontSize: 13, marginBottom: 4 },
  finish: { color: "#ffd54a", fontSize: 16, fontWeight: "700", marginVertical: 6 },
  feed: { flex: 1, minHeight: 60 },  // 控制区可滚后仍给公屏留一口气,不被挤成零
  turn: { marginBottom: 10 },
  // 历史缩小让路;局长最新一句是屏幕上最大的字(暗光+距离,一眼要能读到)
  host: { color: "#aab", fontSize: 14, lineHeight: 20, marginBottom: 2 },
  hostNow: { color: "#fff", fontSize: 23, lineHeight: 32, fontWeight: "700", marginBottom: 2 },
  shown: { color: "#c9a93e", fontSize: 14, marginVertical: 2 },
  shownNow: { color: "#ffd54a", fontSize: 18, fontWeight: "600", marginVertical: 2 },
  // 演示图(局长讲玩法时垫的手势/摆位图,2026-07-24):撑满气泡可用宽,原图比例 800:500
  demoImg: { width: "100%", aspectRatio: 800 / 500, borderRadius: 10, marginVertical: 4 },
  tableEv: { color: "#99a", fontSize: 13, marginLeft: 8 },
  // 实时流即时区(feed 最底部):浅色降一档,与 recent 正式行区分——落地转正后自然消失
  liveZone: { marginTop: 2, marginBottom: 4, paddingLeft: 8, borderLeftWidth: 2,
    borderLeftColor: "#3a3a4e" },
  liveLine: { color: "#778", fontSize: 14, lineHeight: 20, marginVertical: 1 },
  duelResultEv: { color: "#ff9a8a", fontSize: 16, fontWeight: "700", marginLeft: 8, marginVertical: 2 },
  // 「现在该我干嘛」状态条:全屏最亮的一块,抬眼即中
  cueBar: { backgroundColor: "#ffd54a", borderRadius: 12, paddingVertical: 12,
    paddingHorizontal: 14, marginVertical: 6 },
  cueText: { color: "#1a1408", fontSize: 19, fontWeight: "800" },
  // 公开随机结果的「开签」卡:不混进对话流
  pickCard: { backgroundColor: "#33290e", borderColor: "#ffd54a", borderWidth: 1,
    borderRadius: 10, paddingVertical: 8, paddingHorizontal: 12, marginVertical: 4, marginLeft: 4 },
  pickLabel: { color: "#c9a93e", fontSize: 12, marginBottom: 2 },
  pickValue: { color: "#ffd54a", fontSize: 22, fontWeight: "800" },
  // 骰面点阵(私件暗骰揭晓)
  diceLabel: { color: "#c9b8ff", fontSize: 13, marginTop: 4 },
  diceRow: { flexDirection: "row", alignItems: "center", gap: 10, marginVertical: 6 },
  die: { width: 56, height: 56, borderRadius: 12, backgroundColor: "#f4f1e6",
    flexDirection: "row", flexWrap: "wrap", padding: 6 },
  pipCell: { width: "33.33%", height: "33.33%", alignItems: "center", justifyContent: "center" },
  pip: { width: 10, height: 10, borderRadius: 5, backgroundColor: "#1c1c24" },
  diceSum: { color: "#fff", fontSize: 24, fontWeight: "800" },
  // 骰盅道具卡:未摇(醒目、可摇)/ 已摇(常驻显示自己的点数)
  cupBoxActive: { backgroundColor: "#2a2438", borderColor: "#c9b8ff", borderWidth: 2,
    borderRadius: 14, padding: 14, marginVertical: 8, alignItems: "center" },
  cupBox: { backgroundColor: "#241f30", borderColor: "#5a4a8a", borderWidth: 1,
    borderRadius: 12, padding: 12, marginVertical: 8, alignItems: "center" },
  cupTitle: { color: "#c9b8ff", fontSize: 15, fontWeight: "700", marginBottom: 8 },
  cupHint: { color: "#bbade0", fontSize: 14, marginTop: 8, marginBottom: 2, textAlign: "center" },
  rollBtn: { backgroundColor: "#ffd54a", borderRadius: 16, paddingVertical: 14,
    paddingHorizontal: 56, marginTop: 10, minHeight: 56, justifyContent: "center" },
  rollBtnDim: { backgroundColor: "#6a5f3a" },
  rollBtnText: { color: "#241a05", fontSize: 24, fontWeight: "900" },
  // 传感器可用时的主视觉:「摇一摇手机!」大字(体感是主交互,按钮只是兜底)
  shakeMain: { color: "#ffd54a", fontSize: 26, fontWeight: "900", marginTop: 6 },
  // 摇按钮的兜底小样(缩小放底部):传感器可用时不抢主视觉
  rollBtnSmall: { backgroundColor: "#3a3350", borderRadius: 10, paddingVertical: 8,
    paddingHorizontal: 18, marginTop: 10, minHeight: 40, justifyContent: "center" },
  rollBtnSmallText: { color: "#bbade0", fontSize: 13, fontWeight: "700" },
  rollEv: { color: "#c9b8ff", fontSize: 14, fontWeight: "600", marginLeft: 8, marginVertical: 2 },
  // 「开牌!」:醒目但次于摇/扣主流程(描边幽灵款,不与黄色主按钮抢);长按 600ms 触发
  challengeBtn: { borderColor: "#ff9a5a", borderWidth: 2, borderRadius: 14,
    paddingVertical: 10, paddingHorizontal: 36, marginTop: 10, alignItems: "center",
    minHeight: 52, justifyContent: "center" },
  challengeBtnText: { color: "#ff9a5a", fontSize: 19, fontWeight: "900" },
  challengeBtnHint: { color: "#a97b5a", fontSize: 11, marginTop: 1 },
  challengedNote: { color: "#ff9a5a", fontSize: 15, fontWeight: "700", marginTop: 10,
    textAlign: "center" },
  challengeEv: { color: "#ff9a5a", fontSize: 16, fontWeight: "800", marginLeft: 8, marginVertical: 2 },
  // 开牌后的快速叫价面板(count 快拨 + face 骰面点选,可跳过)
  bidBox: { marginTop: 10, alignSelf: "stretch", alignItems: "center" },
  bidLabel: { color: "#bbade0", fontSize: 13, marginBottom: 6, textAlign: "center" },
  bidRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, justifyContent: "center",
    marginVertical: 4 },
  bidChip: { backgroundColor: "#3a3350", borderRadius: 8, minWidth: 36, minHeight: 36,
    alignItems: "center", justifyContent: "center", paddingHorizontal: 8 },
  bidChipOn: { backgroundColor: "#ffd54a" },
  bidChipText: { color: "#cbc0e8", fontSize: 16, fontWeight: "700" },
  bidChipTextOn: { color: "#241a05", fontSize: 16, fontWeight: "900" },
  bidDieWrap: { borderRadius: 10, padding: 3, borderWidth: 2, borderColor: "transparent" },
  bidDieOn: { borderColor: "#ffd54a" },
  challengeGo: { backgroundColor: "#ff9a5a", borderRadius: 14, paddingVertical: 12,
    paddingHorizontal: 32, marginTop: 8, minHeight: 48, justifyContent: "center" },
  challengeGoText: { color: "#2a1405", fontSize: 18, fontWeight: "900" },
  bidSkip: { flex: 1, alignItems: "center", paddingVertical: 8, minHeight: 40,
    justifyContent: "center" },
  bidSkipText: { color: "#889", fontSize: 13 },
  // 全屏「⚡ 开牌!」横幅:压场不拦操作(pointerEvents=none),1.6s 自动散
  challengeFlash: { position: "absolute", left: 0, right: 0, top: "34%", zIndex: 99,
    alignItems: "center", backgroundColor: "rgba(20,10,4,0.88)", paddingVertical: 26 },
  challengeFlashText: { color: "#ff9a5a", fontSize: 52, fontWeight: "900" },
  challengeFlashSub: { color: "#ffd54a", fontSize: 18, fontWeight: "700", marginTop: 6 },
  // 系统级炸铃:满屏大字压全场(比开牌横幅更大更炸),压场不拦操作(pointerEvents=none),1.2s 散
  bellFlash: { position: "absolute", left: 0, right: 0, top: 0, bottom: 0, zIndex: 200,
    alignItems: "center", justifyContent: "center", backgroundColor: "rgba(176,20,20,0.94)" },
  bellFlashText: { color: "#fff", fontSize: 120, fontWeight: "900", letterSpacing: 10 },
  // 桌面额头牌:一行玩家 chips(有牌的挂 🎴 角标,点开看牌)
  foreheadRow: { backgroundColor: "#241f30", borderColor: "#5a4a8a", borderWidth: 1,
    borderRadius: 12, padding: 10, marginVertical: 6 },
  foreheadTitle: { color: "#c9b8ff", fontSize: 13, fontWeight: "700", marginBottom: 6 },
  fhChip: { backgroundColor: "#2b2b3a", borderRadius: 16, paddingVertical: 8, paddingHorizontal: 14,
    minHeight: 40, justifyContent: "center", opacity: 0.6 },
  fhChipHas: { backgroundColor: "#3a3f6a", opacity: 1 },
  fhChipText: { color: "#e6e0ff", fontSize: 15, fontWeight: "700" },
  // 额头牌查看浮层:半透明压场 + 居中大牌 + 收起提示
  foreheadPeek: { position: "absolute", left: 0, right: 0, top: 0, bottom: 0, zIndex: 150,
    alignItems: "center", justifyContent: "center", backgroundColor: "rgba(10,8,20,0.92)" },
  foreheadPeekHint: { color: "#bbade0", fontSize: 15, marginTop: 22 },
  dieMini: { width: 34, height: 34, borderRadius: 7, padding: 4 },
  pipMini: { width: 6, height: 6, borderRadius: 3 },
  inboxBox: { backgroundColor: "#2a2438", borderRadius: 12, padding: 10, marginVertical: 6,
    borderWidth: 1, borderColor: "#5a4a8a" },
  inboxTitle: { color: "#c9b8ff", fontSize: 12, marginBottom: 4 },
  inboxItem: { color: "#fff", fontSize: 17, lineHeight: 23, marginVertical: 1 },
  askBox: { backgroundColor: "#1e2a38", borderRadius: 12, padding: 10, marginVertical: 6 },
  askBoxMe: { borderWidth: 2, borderColor: "#ffd54a" },
  askText: { color: "#cde", fontSize: 15, marginBottom: 6 },
  askTextMe: { color: "#fff", fontSize: 18, fontWeight: "700", marginBottom: 6 },
  // 选择框即收后的一行"✓ 已选 X"
  askPicked: { color: "#8fd0a8", fontSize: 17, fontWeight: "800", marginTop: 2 },
  settleDim: { color: "#99a", fontSize: 13, marginTop: 4 },
  cupWait: { color: "#c9a93e", fontSize: 13, textAlign: "center", marginBottom: 4 },
  voiceOpenBtn: { borderWidth: 1, borderColor: "#31506e", borderRadius: 10,
    paddingVertical: 8, alignItems: "center", marginTop: 6 },
  voiceOpenText: { color: "#9cc4f0", fontSize: 14, fontWeight: "600" },
  // 局长思考指示:呼吸的「🎩 局长在酝酿 …」
  thinkingBar: { flexDirection: "row", alignItems: "center", gap: 8, marginVertical: 4,
    paddingHorizontal: 4 },
  thinkingHat: { fontSize: 18 },
  thinkingDots: { color: "#c9b8ff", fontSize: 15, fontWeight: "600" },
  // 开牌即时清算结果卡:开牌横幅后的当庭报数结论(大字)
  verdictCard: { backgroundColor: "#3a1410", borderColor: "#ff6a4a", borderWidth: 2,
    borderRadius: 14, paddingVertical: 12, paddingHorizontal: 14, marginVertical: 8,
    alignItems: "center" },
  verdictHead: { color: "#ffcaa8", fontSize: 15, fontWeight: "700", marginBottom: 4 },
  verdictLoser: { color: "#ff7a5a", fontSize: 28, fontWeight: "900" },
  // 乐观回显条:已发出(淡)/ 已送达(绿勾)/ 没发出(红,可重发)
  outboxRow: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 4, marginBottom: 2 },
  echoPill: { backgroundColor: "#232432", borderRadius: 14, paddingVertical: 5,
    paddingHorizontal: 12, opacity: 0.7 },
  echoPillOk: { opacity: 0.85 },
  echoPillErr: { backgroundColor: "#4a1414", opacity: 1 },
  echoText: { color: "#9aa", fontSize: 13, fontWeight: "600" },
  echoTextErr: { color: "#ff8a7a", fontSize: 13, fontWeight: "800" },
  row: { flexDirection: "row", gap: 8, marginVertical: 5, alignItems: "center" },
  optBtn: { backgroundColor: "#31506e", borderRadius: 10, paddingVertical: 8,
    paddingHorizontal: 14, minHeight: 44, justifyContent: "center" },
  optText: { color: "#fff", fontSize: 16 },
  sigBtn: { flex: 1, borderRadius: 12, paddingVertical: 14, alignItems: "center",
    minHeight: 48, justifyContent: "center" },
  sigText: { color: "#fff", fontSize: 16, fontWeight: "700" },
  sayBtn: { backgroundColor: "#31506e", borderRadius: 10, padding: 12,
    minHeight: 48, justifyContent: "center" },
  // 快捷回应条:单点即发的 chips(主入口)——高对比、易点,配轻触感
  chipRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 6, marginBottom: 2 },
  chip: { backgroundColor: "#2c3a52", borderRadius: 18, paddingVertical: 9,
    paddingHorizontal: 16, minHeight: 40, justifyContent: "center" },
  chipText: { color: "#dce6f5", fontSize: 16, fontWeight: "700" },
  // 打字框视觉降级(变矮变淡):保留但不再抢主入口
  sayInputDim: { backgroundColor: "#191922", color: "#aab", borderRadius: 8,
    paddingVertical: 7, paddingHorizontal: 10, fontSize: 13, minHeight: 34 },
  sayBtnDim: { backgroundColor: "#242c3a", borderRadius: 8, paddingVertical: 7,
    paddingHorizontal: 10, minHeight: 34, justifyContent: "center" },
  sayBtnDimText: { color: "#8a97a8", fontSize: 12, fontWeight: "600" },
  // 按住说话(PTT):按住期间「局长」键变红大化——收音中一眼可见,松手即停
  pttBtnOn: { backgroundColor: "#b02020", borderRadius: 12, paddingVertical: 14,
    paddingHorizontal: 18, minHeight: 48 },
  pttBtnOnText: { color: "#fff", fontSize: 15, fontWeight: "800" },
  voiceMsg: { color: "#8fd0a8", fontSize: 15, marginVertical: 2, textAlign: "right" },
  // 大厅拍照读场:两行小字回显(场合猜测+认出实物)+ 低调重拍链接
  sceneMini: { backgroundColor: "#20202c", borderRadius: 10, paddingVertical: 8,
    paddingHorizontal: 12, width: "100%", marginTop: 2, marginBottom: 2,
    borderWidth: 1, borderColor: "#2f3a30" },
  sceneHint: { color: "#8a9", fontSize: 13 },
  sceneErr: { color: "#c99", fontSize: 12 },
  sceneLine: { color: "#9d8", fontSize: 14, fontWeight: "700" },
  sceneLine2: { color: "#889", fontSize: 12, marginTop: 2 },
  optout: { color: "#667", fontSize: 13, textAlign: "center", marginVertical: 8 },
  photoBtn: { backgroundColor: "#4a3a10", borderColor: "#ffd54a", borderWidth: 1,
    borderRadius: 12, padding: 12, marginVertical: 6 },
  photoText: { color: "#ffd54a", fontSize: 17, fontWeight: "700" },
  photoSub: { color: "#bb9", fontSize: 12, marginTop: 2 },
  photoActionBtn: { flex: 1, backgroundColor: "#ffd54a", borderRadius: 10,
    paddingVertical: 10, alignItems: "center", minHeight: 44, justifyContent: "center" },
  photoActionText: { color: "#222", fontSize: 15, fontWeight: "700" },
  settleBox: { backgroundColor: "#20242c", borderRadius: 12, padding: 12, marginVertical: 6,
    borderWidth: 1, borderColor: "#ffd54a" },
  settleTitle: { color: "#ffd54a", fontSize: 18, fontWeight: "800", marginBottom: 6 },
  settleTop: { color: "#ffd54a", fontSize: 24, fontWeight: "800", marginVertical: 2 },
  settleItem: { color: "#fff", fontSize: 19, marginVertical: 2 },
  duelVs: { color: "#fff", fontSize: 30, fontWeight: "800", marginBottom: 30 },
  duelWait: { color: "#fff", fontSize: 24, fontWeight: "700", marginBottom: 10 },
  duelHint: { color: "#c7ccda", fontSize: 16 },
  drawBtn: { backgroundColor: "#ffd54a", width: 260, height: 260, borderRadius: 130,
    alignItems: "center", justifyContent: "center" },
  drawText: { fontSize: 80, fontWeight: "900", color: "#7a1010" },
});
