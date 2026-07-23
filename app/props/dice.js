/**
 * 摇骰子 · 手感版（无 3D）
 *
 * 这一屏建立在一个产品事实上:
 *   大话骰摇盅的时候骰子是盖着的 —— 玩家全程看不见。
 *
 * 所以摇的那几秒屏幕上没有任何值得渲染的东西。手感 100% 来自:
 *   1. 震动（Core Haptics 逐颗合成撞击，不是罐头波形）
 *   2. 盅体的视觉反馈（跟着手抖，暗示里面在翻）
 *   3. 揭盅那一下的落差
 *
 * 前面那些 RealityKit 物理 / Unity 全屏 / 透明合成，
 * 解的都是"摇的时候要看什么"——而这个问题根本不存在。
 */
import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Pressable, Animated, Easing } from 'react-native';
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { DiceFeel } from '../modules/propfeel';

// Blender 渲的象牙白树脂骰(中式红/黑点) + 半透明盅。require 必须字面量。
const DIE_IMG = {
  1: require('../assets/props/dice/die_1.webp'), 2: require('../assets/props/dice/die_2.webp'),
  3: require('../assets/props/dice/die_3.webp'), 4: require('../assets/props/dice/die_4.webp'),
  5: require('../assets/props/dice/die_5.webp'), 6: require('../assets/props/dice/die_6.webp'),
};
const CUP_IMG = require('../assets/props/dice/cup.webp');
const SHADOW_IMG = require('../assets/props/dice/die_shadow.webp');   // Blender 渲的对称柔影

export default function Feel() {
  const [phase, setPhase] = useState('idle');   // idle | shaking | ready | revealed
  const [dice, setDice] = useState([1, 1, 1, 1, 1]);
  const [scatter, setScatter] = useState([]);   // 每颗骰子的随机落点/旋转
  const [ticks, setTicks] = useState(0);
  const [supported, setSupported] = useState(null);
  const [hapticErr, setHapticErr] = useState(null);
  const [audioStatus, setAudioStatus] = useState(null);
  const [tier, setTier] = useState(-1);
  const [maxG, setMaxG] = useState(0);
  const startedAt = useRef(0);

  // 盅体抖动：摇的时候跟着手的力度晃，这是"里面有东西在翻"的唯一视觉暗示
  const shake = useRef(new Animated.Value(0)).current;
  const lift = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    try { setSupported(DiceFeel.isSupported()); } catch (e) { setSupported(false); }
    const t = DiceFeel.addListener('onShakeTick', (e) => {
      setTicks((n) => n + 1);
      setTier(e.tier);
      setMaxG((m) => Math.max(m, e.magnitude));
      // 每次撞击给盅一个小位移，幅度跟力度走
      const amp = Math.min(e.magnitude * 6, 14);
      Animated.sequence([
        Animated.timing(shake, { toValue: amp, duration: 40, useNativeDriver: true }),
        Animated.timing(shake, { toValue: -amp * 0.7, duration: 50, useNativeDriver: true }),
        Animated.timing(shake, { toValue: 0, duration: 60, useNativeDriver: true }),
      ]).start();
    });
    const s = DiceFeel.addListener('onSettled', () => setPhase('ready'));
    // 震动出错必须显出来 —— 上一版"摇六十次就没反应"正是因为错误被静默吞了
    const err = DiceFeel.addListener('onHapticError', (e) => setHapticErr(e.message));
    // 音频状态:session/engine/buffersLoaded —— 上一版没声,用这个定位到底哪层断了
    const aud = DiceFeel.addListener('onAudioStatus', (e) => setAudioStatus(e));
    return () => { t.remove(); s.remove(); err.remove(); aud.remove(); DiceFeel.stop(); };
  }, []);

  const beginShake = async () => {
    setPhase('shaking');
    setTicks(0);
    setTier(-1);
    setMaxG(0);
    setDice([1, 1, 1, 1, 1]);
    lift.setValue(0);
    startedAt.current = Date.now();
    await DiceFeel.start(5);
    // 状态走同步轮询(事件通路两版都没走通,不再赌它)
    setTimeout(() => {
      try { setAudioStatus(DiceFeel.audioStatusSync()); } catch (e) { setAudioStatus({ err: String(e) }); }
    }, 600);
  };

  const doReveal = async () => {
    await DiceFeel.stop();
    await DiceFeel.reveal();
    // 真实产品里这五个数来自服务端引擎（random.dice 工具），不是客户端摇出来的
    setDice(Array.from({ length: 5 }, () => 1 + Math.floor(Math.random() * 6)));
    // 随机散落 + 避让:骰子 58px,中心距 ≥56px 才不叠。
    // 拒绝采样:每颗随机试位置,跟已放的都够远才落;试 60 次还不行就取"离最近邻最远"的那个。
    const RX = 150, RY = 54, MIN = 56;   // 落点范围 + 最小中心距
    const placed = [];
    for (let k = 0; k < 5; k++) {
      let best = null, bestD = -1;
      for (let t = 0; t < 60; t++) {
        const cand = { x: (Math.random() - 0.5) * 2 * RX, y: (Math.random() - 0.5) * 2 * RY };
        let nearest = Infinity;
        for (const p of placed) {
          const d = Math.hypot(cand.x - p.x, cand.y - p.y);
          if (d < nearest) nearest = d;
        }
        if (nearest >= MIN) { best = cand; break; }
        if (nearest > bestD) { bestD = nearest; best = cand; }
      }
      placed.push({ ...best, rot: Math.random() * 360, s: 0.92 + Math.random() * 0.18 });
    }
    setScatter(placed);
    setPhase('revealed');
    Animated.timing(lift, {
      toValue: 1, duration: 420, easing: Easing.out(Easing.cubic), useNativeDriver: true,
    }).start();
  };

  return (
    <View style={s.root}>
      <Pressable onLongPress={doReveal} style={s.hostCard}>
        <Text style={s.hostText}>
          {phase === 'idle' && '🎩 该你摇了。'}
          {phase === 'shaking' && '🎩 摇着，别停。'}
          {phase === 'ready' && '🎩 差不多了 —— 自己看，别出声。'}
          {phase === 'revealed' && '🎩 记住你的点数，盖回去。'}
        </Text>
      </Pressable>

      <View style={s.stage}>
        {/* 绿绒托盘：骰子落定的地方(照设计稿 .felt 配色 #1D5A3D→#0F3826→#082418) */}
        <LinearGradient
          colors={['#20604233', '#1D5A3D', '#0F3826', '#082418']}
          locations={[0, 0.3, 0.7, 1]}
          style={s.tray}
        >
          {/* 骰子：揭盅后随机散落,点数面朝上。影子层不旋转、骰子层旋转,分两层叠 */}
          <Animated.View style={[StyleSheet.absoluteFill, s.trayInner, { opacity: lift }]}>
            {/* 先铺所有柔影(只随落点平移,不旋转) */}
            {dice.map((n, i) => {
              const sc = scatter[i] || { x: 0, y: 0, s: 1 };
              return (
                <Image
                  key={'sh' + i}
                  source={SHADOW_IMG}
                  style={[s.dieShadowImg, { transform: [{ translateX: sc.x }, { translateY: sc.y + 5 }, { scale: sc.s }] }]}
                  contentFit="contain"
                />
              );
            })}
            {/* 再叠骰子(平移 + 随机旋转),盖在影子上 */}
            {dice.map((n, i) => {
              const sc = scatter[i] || { x: 0, y: 0, rot: 0, s: 1 };
              return (
                <Image
                  key={'d' + i}
                  source={DIE_IMG[n]}
                  style={[s.dieImg, { transform: [{ translateX: sc.x }, { translateY: sc.y }, { rotate: `${sc.rot}deg` }, { scale: sc.s }] }]}
                  contentFit="contain"
                />
              );
            })}
          </Animated.View>
        </LinearGradient>

        {/* 骰盅：摇的时候跟着抖，揭盅时上移露出托盘里的骰子 */}
        <Animated.View
          style={[
            s.cup,
            {
              transform: [
                { translateX: shake },
                { rotate: shake.interpolate({ inputRange: [-14, 14], outputRange: ['-5deg', '5deg'] }) },
                { translateY: lift.interpolate({ inputRange: [0, 1], outputRange: [0, -210] }) },
              ],
              opacity: lift.interpolate({ inputRange: [0, 1], outputRange: [1, 0] }),
            },
          ]}
        >
          <Image source={CUP_IMG} style={s.cupImg} contentFit="contain" />
        </Animated.View>
      </View>

      {phase === 'revealed' && (
        <Text style={s.sum}>Σ {dice.reduce((a, b) => a + b, 0)}</Text>
      )}

      <View style={s.btnRow}>
        {phase === 'idle' || phase === 'revealed' ? (
          <Pressable style={[s.btn, s.btnGo]} onPress={beginShake}>
            <Text style={s.btnT}>开始摇（真的把手机摇起来）</Text>
          </Pressable>
        ) : (
          <Pressable
            style={[s.btn, phase === 'ready' ? s.btnReady : s.btnWait]}
            onPress={doReveal}
          >
            <Text style={s.btnT}>{phase === 'ready' ? '揭盅' : '摇够了再揭…'}</Text>
          </Pressable>
        )}
      </View>

      <View style={s.info}>
        <Text style={s.infoT}>撞击反馈 {ticks} 次（摇多久都不该停）</Text>
        <Text style={s.infoT}>
          当前档位 {tier < 0 ? '—' : ['轻摇 2颗', '中等 3颗', '用力 5颗+1重击', '猛摇 5颗+3重击'][tier]}
          {'  '}峰值 {maxG.toFixed(2)}G
        </Text>
        <View style={s.bar}>
          {[0, 1, 2, 3].map((k) => (
            <View key={k} style={[s.seg, tier >= k && s.segOn, tier === k && s.segNow]} />
          ))}
        </View>
        {hapticErr && <Text style={s.err}>震动异常: {hapticErr.slice(0, 90)}</Text>}
        <Text style={s.infoT}>
          Core Haptics {supported === null ? '检测中' : supported ? '可用' : '不可用（模拟器无震动）'}
        </Text>
        <Text style={[s.infoT, audioStatus && audioStatus.session && audioStatus.session !== 'ok' && s.err]}>
          音频 {audioStatus ? JSON.stringify(audioStatus) : '未启动（点开始摇后出）'}
        </Text>
        <Text style={s.note}>
          基线=音效+视觉(全平台保底)，震动是增强层。
          关掉震动/静音键挡住时，声音和盅体抖动仍在——道具不锁在单一感官后面。
        </Text>
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#14141c', paddingHorizontal: 12, paddingTop: 60 },
  hostCard: { backgroundColor: '#1b1b28', borderRadius: 10, padding: 12, marginTop: 8 },
  hostText: { color: '#fff', fontSize: 16, fontWeight: '600' },
  stage: { height: 340, alignItems: 'center', justifyContent: 'center', marginTop: 6 },
  tray: { position: 'absolute', bottom: 10, width: '96%', height: 150, borderRadius: 18, alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: '#0a2a1c' },
  cup: { position: 'absolute', zIndex: 3, bottom: 40 },
  cupImg: { width: 210, height: 210 },
  trayInner: { alignItems: 'center', justifyContent: 'center' },
  dieImg: { position: 'absolute', width: 58, height: 58 },
  dieShadowImg: { position: 'absolute', width: 56, height: 56 },
  sum: { color: '#fff', fontSize: 22, fontWeight: '700', textAlign: 'center' },
  btnRow: { marginTop: 10 },
  btn: { padding: 16, borderRadius: 11, alignItems: 'center' },
  btnGo: { backgroundColor: '#3a3a55' },
  btnWait: { backgroundColor: '#2a2a38' },
  btnReady: { backgroundColor: '#7a5a2a' },
  btnT: { color: '#fff', fontSize: 16, fontWeight: '700' },
  info: { marginTop: 14 },
  infoT: { color: '#8b8b9e', fontSize: 12, fontFamily: 'Menlo', marginBottom: 3 },
  note: { color: '#c9a86e', fontSize: 12, lineHeight: 19, marginTop: 8 },
  err: { color: '#ff7a7a', fontSize: 11, fontFamily: 'Menlo', marginTop: 4 },
  bar: { flexDirection: 'row', gap: 4, marginTop: 6, marginBottom: 2 },
  seg: { flex: 1, height: 6, borderRadius: 3, backgroundColor: '#26263a' },
  segOn: { backgroundColor: '#5a5a80' },
  segNow: { backgroundColor: '#c9a86e' },
});
