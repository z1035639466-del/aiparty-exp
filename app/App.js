// Yappa v0 · 两台手机的真人局客户端(Expo / React Native)
// 服务端 = 现有引擎 HTTP API(Mac 上 python -m modeb.simulator --lan)。
// 本客户端只消费 /api/view(自己那台手机该看的)与 /api/event(自己的动作)——
// 防偷看在服务端成立,客户端天然拿不到别人的底牌。
// 手机开局页(开工单欠账补):/api/start 也从手机发,不必回电脑驾驶舱;
// 判定=抽帧走照片通道:视频先在本机抽帧转 base64,仍是 /api/photo 那条口子。
import { useEffect, useRef, useState } from "react";
import {
  Alert, Animated, KeyboardAvoidingView, Platform, Pressable, ScrollView,
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

const POLL_MS = 900;
// 官方服务器(正式形态):域名定了填在这里,玩家从此只输房间码+座位名,
// 服务器输入框整个消失(长按标题下方空白 1.2 秒可唤回,开发调试用)。
// 留空 = 开发期行为:显示服务器输入框(自架地址没法未卜先知)。
const DEFAULT_SERVER = "";
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
function ChallengeControl({ onChallenge }) {
  const [picking, setPicking] = useState(false); // 长按成立后进入叫价快选
  const [cnt, setCnt] = useState(null);
  const [face, setFace] = useState(null);
  if (!picking) {
    return (
      <Pressable style={s.challengeBtn} delayLongPress={600}
        onPress={() => Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light)}
        onLongPress={() => {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy); // 拍桌那一下
          setPicking(true);
        }}>
        <Text style={s.challengeBtnText}>⚡ 开牌!</Text>
        <Text style={s.challengeBtnHint}>长按拍桌喊开</Text>
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
      <Pressable style={[s.challengeGo, !ready && s.rollBtnDim]} disabled={!ready}
        onPress={() => onChallenge({ count: cnt, face })}>
        <Text style={s.challengeGoText}>{ready ? `⚡ 开!(${cnt}个${face})` : "点选几个几,或跳过"}</Text>
      </Pressable>
      <View style={s.row}>
        <Pressable style={s.bidSkip} onPress={() => onChallenge(null)}>
          <Text style={s.bidSkipText}>跳过叫价直接开</Text>
        </Pressable>
        <Pressable style={s.bidSkip} onPress={() => { setPicking(false); setCnt(null); setFace(null); }}>
          <Text style={s.bidSkipText}>算了,不开</Text>
        </Pressable>
      </View>
    </View>
  );
}

function DiceCup({ prop, rolling, onRoll, onChallenge }) {
  const { count, rolled } = prop;
  const [faces, setFaces] = useState(() => Array.from({ length: count }, () => 1));
  const [shakes, setShakes] = useState(0);
  const [sensorOk, setSensorOk] = useState(true);
  const lastPulse = useRef(0);
  const shakesRef = useRef(0);
  const settleTimer = useRef(null);
  const rollRef = useRef(onRoll);
  rollRef.current = onRoll;                 // 始终指向最新 onRoll,自动扣盅时不吃旧闭包
  const challengedBy = prop.challenged_by;  // 有人开牌:全桌盅锁定,摇的入口一并撤下
  const armed = !rolled && !rolling && !challengedBy; // 持未摇盅、没在扣、没被开:才订阅传感器

  // 骰面快速乱翻(哗啦哗啦的感觉):摇动脉冲时、脚本兜底 rolling 时都用它
  const tumble = () => setFaces(Array.from({ length: count }, () => 1 + Math.floor(Math.random() * 6)));

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
        Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);  // 每次晃一次轻触感
        tumble();
        shakesRef.current += 1;
        setShakes(shakesRef.current);
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

  // 脚本兜底 / 扣盅瞬间:rolling 期间骰面持续乱翻,直到点数经轮询回来
  useEffect(() => {
    if (!rolling) return;
    const iv = setInterval(tumble, 70);
    return () => clearInterval(iv);
  }, [rolling, count]);

  // 摇过的盅:常驻显示自己的点数(大话骰全程要盯着自己的骰吹牛,不能摇完就没了)。
  // 骰面旁常驻「开牌!」入口(醒目但次于摇/扣主流程);被开后换成"等局长清算"。
  if (rolled) {
    const sum = rolled.reduce((a, b) => a + b, 0);
    return (
      <View style={s.cupBox}>
        <Text style={s.cupTitle}>🎲 你的骰盅 · {count}颗(只有你看得到)</Text>
        <View style={s.diceRow}>
          {rolled.map((n, i) => <Die key={i} n={n} />)}
          {rolled.length > 1 ? <Text style={s.diceSum}>Σ{sum}</Text> : null}
        </View>
        {challengedBy ? (
          <Text style={s.challengedNote}>⚡ {challengedBy} 开牌了{fmtBid(prop.bid)}——已开牌,等局长清算</Text>
        ) : (
          <ChallengeControl onChallenge={onChallenge} />
        )}
      </View>
    );
  }

  // 没摇就被开了牌(别人拍了开牌):盅锁定,摇的入口撤下,只留状态
  if (challengedBy) {
    return (
      <View style={s.cupBox}>
        <Text style={s.cupTitle}>🎲 骰盅 · {count} 颗</Text>
        <Text style={s.challengedNote}>⚡ {challengedBy} 开牌了{fmtBid(prop.bid)}——骰盅已锁,等局长清算</Text>
      </View>
    );
  }

  const enough = shakes >= SHAKE_MIN;
  return (
    <View style={s.cupBoxActive}>
      <Text style={s.cupTitle}>🎲 骰盅 · {count} 颗</Text>
      {rolling ? (
        <>
          <View style={s.diceRow}>{faces.map((n, i) => <Die key={i} n={n} />)}</View>
          <Text style={s.cupHint}>哗啦哗啦……扣盅揭晓</Text>
        </>
      ) : sensorOk ? (
        <>
          <View style={s.diceRow}>{faces.map((n, i) => <Die key={i} n={n} />)}</View>
          <Text style={s.cupHint}>
            {enough ? "摇够了!停手自动扣,或点👇" : `摇一摇手机…(${shakes}/${SHAKE_MIN})`}
          </Text>
          <Pressable style={[s.rollBtn, !enough && s.rollBtnDim]} onPress={onRoll}>
            <Text style={s.rollBtnText}>{enough ? "扣盅!" : "摇!"}</Text>
          </Pressable>
        </>
      ) : (
        // 传感器不可用/无权限/桌面平放:按钮兜底,点了走脚本化震动+乱翻后同样扣盅,终点一致
        <>
          <Text style={s.cupHint}>摇不动?点这里替你摇</Text>
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
  const [view, setView] = useState(null);
  const [err, setErr] = useState("");
  const [say, setSay] = useState("");
  const [dueled, setDueled] = useState(false); // 本次对决我开过枪了
  const [rolling, setRolling] = useState(false); // 骰盅扣盅动画进行中(挡重复扣)
  const [recording, setRecording] = useState(null); // 录音判定进行中的 Recording 对象
  const [challengeFlash, setChallengeFlash] = useState(null); // 「⚡ 开牌!」全屏横幅(1.6s 自动散)
  const prevRef = useRef({ inbox: 0, drawn: false, challenged: false });
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
      JSON.stringify({ base: b, me: m, room: r, dev: devRef.current }))
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

  // —— 手机开局页(v0 欠账补):此前开局只能在电脑驾驶舱,手机只能入座 ——
  const [creating, setCreating] = useState(false);
  const [starting, setStarting] = useState(false);
  const [seats, setSeats] = useState("");
  const [minutes, setMinutes] = useState("30");
  const [wildness, setWildness] = useState("6");
  const [occasion, setOccasion] = useState("");
  const [playlist, setPlaylist] = useState("");
  const [botsText, setBotsText] = useState("");

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

  // 开牌:长按「开牌!」成立后到这儿发 challenge 事件(bid=被开那口叫价,可为 null)。
  // 校验在服务端(持已摇盅、一局一开),驳回原话弹出来;全桌的感知走轮询里的 cups。
  const sendChallenge = async (bid) => {
    try {
      const res = await api("/api/event", { type: "challenge", player: me, ...(bid ? { bid } : {}) });
      if (res && res.error) Alert.alert("开不了牌", res.error);
    } catch (e) { setErr("开牌没发出去,再按一次"); }
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
        // 开牌感知(全桌):cups 里 challenged_by 从无到有 = 有人拍桌开牌——
        // 重触感一下 + 全屏「⚡ 开牌!」横幅短暂压场(1.6s 自动散,不拦操作)
        const chCup = (v.cups || []).find((c) => c.challenged_by);
        if (chCup && !prev.challenged) {
          Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
          setChallengeFlash({ by: chCup.challenged_by, bid: chCup.bid });
          setTimeout(() => setChallengeFlash(null), 1600);
        }
        prevRef.current = { inbox: (v.inbox || []).length,
          drawn: !!(v.duel && v.duel.drawn), challenged: !!chCup };
        setView(v);
      } catch (e) { if (alive) setErr("连不上服务器:" + e.message); }
    };
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [joined]);

  if (!joined) {
    return (
      <KeyboardAvoidingView style={s.page} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <StatusBar style="light" />
        <ScrollView contentContainerStyle={[s.center, { flexGrow: 1, paddingVertical: 44 }]}
          keyboardShouldPersistTaps="handled">
          <Text style={s.logo}>Yappa</Text>
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
              <TextInput style={s.input} placeholder="座位名(逗号分隔,至少2个,如 疯子明,小静)"
                placeholderTextColor="#667" autoCapitalize="none" autoCorrect={false}
                value={seats} onChangeText={setSeats} />
              <View style={s.row}>
                <TextInput style={[s.input, { flex: 1 }]} placeholder="时长(分钟)"
                  placeholderTextColor="#667" keyboardType="number-pad"
                  value={minutes} onChangeText={setMinutes} />
                <TextInput style={[s.input, { flex: 1 }]} placeholder="野度(1-10)"
                  placeholderTextColor="#667" keyboardType="number-pad"
                  value={wildness} onChangeText={setWildness} />
              </View>
              <TextInput style={s.input} placeholder="场合一句话(如 老友重聚/生日局,可选)"
                placeholderTextColor="#667" value={occasion} onChangeText={setOccasion} />
              <TextInput style={s.input} placeholder="🎵 歌单(逗号分隔,可选)"
                placeholderTextColor="#667" value={playlist} onChangeText={setPlaylist} />
              <TextInput style={s.input} placeholder="🤖 bot 座位(可选,名:人设,逗号分隔)"
                placeholderTextColor="#667" value={botsText} onChangeText={setBotsText} />
              <Pressable style={s.bigBtn} disabled={starting} onPress={async () => {
                const seatList = seats.split(",").map((x) => x.trim()).filter(Boolean);
                if (seatList.length < 2) {
                  Alert.alert("座位不够", "至少填两个座位名(逗号分隔)"); return;
                }
                const bots = {};
                botsText.split(",").map((x) => x.trim()).filter(Boolean).forEach((x) => {
                  const [n, p] = x.split(/[:：]/);
                  if (n && n.trim()) bots[n.trim()] = (p || "").trim();
                });
                setStarting(true);
                try {
                  const res = await api("/api/start", {
                    players: seatList,
                    minutes: +minutes || 30,
                    wildness: +wildness || 6,
                    objects: [],
                    driver: "llm",
                    autoplay: true,   // 服务器自驱回合,手机可退到后台/锁屏也不停局
                    occasion: occasion.trim(),
                    playlist: playlist.split(",").map((t) => t.trim()).filter(Boolean),
                    bots,
                    // provider/模型一律不带:换家换模型是服务端 .env 的事(YAPPA_PROVIDER/
                    // YAPPA_MODEL),不该焊在客户端里——焊死过一次(qwen 一家),换中转站
                    // 就得改 App 重发包。手机只管开局,用哪家由开服务的人决定。
                  });
                  if (res.error) { Alert.alert("开局失败", res.error); return; }
                  if (res.room_code) {
                    setRoom(res.room_code);
                    Alert.alert("房间码", `本桌房间码:${res.room_code}\n发给其他人,入座时填这个`);
                  }
                  setMe(seatList[0]); setJoined(true); setCreating(false);
                  remember(base, seatList[0], res.room_code || "");
                } catch (e) {
                  Alert.alert("连不上", String(e.message));
                } finally {
                  setStarting(false);
                }
              }}>
                <Text style={s.bigBtnText}>{starting ? "开局中…" : "开新局"}</Text>
              </Pressable>
              <Pressable hitSlop={14} onPress={() => setCreating(false)}>
                <Text style={s.optout}>← 返回入座</Text>
              </Pressable>
            </>
          ) : (
            <>
              {/* 座位名要跟服务端花名册逐字相等——自动更正/首字母大写会把 Jing 改成
                  King 这种,玩家看屏幕是对的却一直入座失败,必须关掉 */}
              <TextInput style={s.input} placeholder="你的座位名(开局时定的)"
                placeholderTextColor="#667" autoCapitalize="none" autoCorrect={false}
                value={me} onChangeText={setMe} />
              <TextInput style={s.input} placeholder="房间码(如 A7QK;只有一桌可留空)"
                placeholderTextColor="#667" autoCapitalize="characters" autoCorrect={false}
                value={room} onChangeText={(t) => setRoom(t.trim().toUpperCase())} />
              <Pressable style={s.bigBtn} onPress={async () => {
                try {
                  // room 已在 api() 里自动带上(query+body);留空则命中唯一房间
                  const v = await api("/api/view?player=" + encodeURIComponent(me.trim()));
                  if (v.error) { Alert.alert("入座失败", v.error); return; }
                  setMe(me.trim()); setJoined(true);
                  remember(base, me.trim(), room);
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
  const myCue = v.photo_request ? "📸 轮到你:拍照判定,看下方题目"
    : v.audio_request ? "🎤 轮到你:录音判定,看下方题目"
    : askedMe ? "🫵 问到你了,往下答"
    : v.focus === me ? "🎯 焦点在你身上" : null;

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

  // —— 快枪手全屏对峙 ——
  if (inDuel) {
    const drawn = v.duel.drawn;
    return (
      <View style={[s.page, s.center, { backgroundColor: drawn ? "#7a1010" : "#14141c" }]}>
        <StatusBar style="light" />
        <Text style={s.duelVs}>{v.duel.vs.join("  ⚡  ")}</Text>
        {dueled ? (
          <Text style={s.duelWait}>已开枪,等局长宣布……</Text>
        ) : drawn ? (
          <Pressable style={s.drawBtn} onPress={() => {
            setDueled(true);
            Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Heavy);
            sendEvent({ type: "tap" });
          }}>
            <Text style={s.drawText}>拔!</Text>
          </Pressable>
        ) : (
          <>
            <Text style={s.duelWait}>对峙中……手别碰屏幕</Text>
            <Text style={s.duelHint}>枪响前碰 = 抢跑判负</Text>
          </>
        )}
      </View>
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

      {/* 骰盅道具卡:局长发盅、玩家自己摇——常驻在 feed 之上,大话骰全程盯着自己的骰吹牛 */}
      {v.my_prop ? <DiceCup prop={v.my_prop} rolling={rolling} onRoll={rollCup}
        onChallenge={sendChallenge} /> : null}

      {/* 「⚡ 开牌!」全屏横幅:短暂压场(1.6s),pointerEvents=none 不拦任何操作 */}
      {challengeFlash ? (
        <View pointerEvents="none" style={s.challengeFlash}>
          <Text style={s.challengeFlashText}>⚡ 开牌!</Text>
          <Text style={s.challengeFlashSub}>{challengeFlash.by} 开了{fmtBid(challengeFlash.bid)}</Text>
        </View>
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
                <Text key={j} style={latest ? s.shownNow : s.shown}>📢 {c}</Text>
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
      </ScrollView>

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

      {v.finished && (
        <View style={s.settleBox}>
          <Text style={s.settleTitle}>🏁 终局战报</Text>
          {Object.entries(v.scores || {}).sort((a, b) => b[1] - a[1]).map(([p, sc], i) => (
            <Text key={p} style={i === 0 ? s.settleTop : s.settleItem}>
              {i === 0 ? "👑 " : ""}{p}:{sc}
            </Text>
          ))}
        </View>
      )}

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

      {v.open_ask && (
        <View style={[s.askBox, askedMe && s.askBoxMe]}>
          <Text style={askedMe ? s.askTextMe : s.askText}>🎤 {askedMe ? "问你" : `问${v.open_ask.asked}`}:{v.open_ask.prompt}</Text>
          <View style={s.row}>
            {(v.open_ask.options || []).map((o, i) => (
              <Pressable key={i} style={s.optBtn}
                onPress={() => sendEvent({ type: "say", text: o, to: "局长" })}>
                <Text style={s.optText}>{o}</Text>
              </Pressable>
            ))}
          </View>
        </View>
      )}

      <View style={s.row}>
        <Pressable style={[s.sigBtn, { backgroundColor: "#2c5f3f" }]}
          onPress={() => sendEvent({ type: "done" })}>
          <Text style={s.sigText}>✅ 完成</Text>
        </Pressable>
        <Pressable style={[s.sigBtn, { backgroundColor: "#6b4a2b" }]}
          onPress={() => sendEvent({ type: "forfeit" })}>
          <Text style={s.sigText}>🍺 认罚跳过</Text>
        </Pressable>
        <Pressable style={[s.sigBtn, { backgroundColor: "#31506e", flex: 0.6 }]}
          onPress={() => { Haptics.selectionAsync(); sendEvent({ type: "tap" }); }}>
          <Text style={s.sigText}>👏 抢答</Text>
        </Pressable>
      </View>
      <View style={s.row}>
        <TextInput style={[s.input, { flex: 1, marginVertical: 0 }]} placeholder="说点什么…"
          placeholderTextColor="#667" value={say} onChangeText={setSay} />
        <Pressable style={s.sayBtn} onPress={() => { if (say.trim()) { sendEvent({ type: "say", text: say.trim(), to: "桌上" }); setSay(""); } }}>
          <Text style={s.sigText}>💬桌上</Text>
        </Pressable>
        <Pressable style={s.sayBtn} onPress={() => { if (say.trim()) { sendEvent({ type: "say", text: say.trim(), to: "局长" }); setSay(""); } }}>
          <Text style={s.sigText}>🎙局长</Text>
        </Pressable>
      </View>
      <View style={[s.row, { justifyContent: "center" }]}>
        <Pressable hitSlop={14} onPress={() => Alert.alert("安全退出", "零代价退出当前环节,确定?", [
          { text: "再想想" },
          { text: "退出这轮", onPress: () => sendEvent({ type: "optout" }) },
        ])}>
          <Text style={s.optout}>安全退出</Text>
        </Pressable>
        <Pressable hitSlop={14} onPress={async () => {   // 开局拍一张现场:实物清单+场景速写自动进局
          const perm = await ImagePicker.requestCameraPermissionsAsync();
          if (!perm.granted) return;
          const r = await ImagePicker.launchCameraAsync({ quality: 0.4, base64: true });
          if (r.canceled) return;
          const res = await api("/api/scene", { image_b64: r.assets[0].base64, media_type: "image/jpeg" })
            .catch(e => ({ error: e.message }));
          Alert.alert("场景侦察", res.error || `${res.brief || ""}\n实物:${(res.objects || []).join("、")}`);
        }}>
          <Text style={s.optout}>📷 场景侦察</Text>
        </Pressable>
      </View>
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
  topbar: { flexDirection: "row", justifyContent: "space-between", marginBottom: 6 },
  topText: { color: "#aab", fontSize: 13 },
  topMusic: { color: "#8fb", fontSize: 13 },
  err: { color: "#f66", fontSize: 13, marginBottom: 4 },
  finish: { color: "#ffd54a", fontSize: 16, fontWeight: "700", marginVertical: 6 },
  feed: { flex: 1 },
  turn: { marginBottom: 10 },
  // 历史缩小让路;局长最新一句是屏幕上最大的字(暗光+距离,一眼要能读到)
  host: { color: "#aab", fontSize: 14, lineHeight: 20, marginBottom: 2 },
  hostNow: { color: "#fff", fontSize: 23, lineHeight: 32, fontWeight: "700", marginBottom: 2 },
  shown: { color: "#c9a93e", fontSize: 14, marginVertical: 2 },
  shownNow: { color: "#ffd54a", fontSize: 18, fontWeight: "600", marginVertical: 2 },
  tableEv: { color: "#99a", fontSize: 13, marginLeft: 8 },
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
  row: { flexDirection: "row", gap: 8, marginVertical: 5, alignItems: "center" },
  optBtn: { backgroundColor: "#31506e", borderRadius: 10, paddingVertical: 8,
    paddingHorizontal: 14, minHeight: 44, justifyContent: "center" },
  optText: { color: "#fff", fontSize: 16 },
  sigBtn: { flex: 1, borderRadius: 12, paddingVertical: 14, alignItems: "center",
    minHeight: 48, justifyContent: "center" },
  sigText: { color: "#fff", fontSize: 16, fontWeight: "700" },
  sayBtn: { backgroundColor: "#31506e", borderRadius: 10, padding: 12,
    minHeight: 48, justifyContent: "center" },
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
