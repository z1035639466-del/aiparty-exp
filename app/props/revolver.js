/**
 * 虚拟左轮 · demo（零 3D 判定仪器）
 *
 * 「把手机变成枪」：真柯尔特单动竖着占满屏（缩 85%，不顶边）。
 * 交互直接在枪上，不用底部按钮：
 *   点【弹巢】→ 转膛（枪身晃 + 棘轮震动声，弹巢转一下）
 *   点【扳机】→ 扣扳机开火（枪口喷火 + 后坐 + 全屏闪白 + 手电爆闪 + 重震）
 *
 * ⚠️ 架构备忘（先不改）：概率规则 + 惩罚内容属游戏脚本层，正式接引擎时从道具拆出去。
 */
import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Pressable, Animated, Easing } from 'react-native';
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { RevolverFeel } from '../modules/propfeel';

const GUN = require('../assets/props/rev/gun_v.webp');

const PUNISH = [
  '用老板的语气念下一条规则', '给左边的人敬一句彩虹屁', '学一种动物叫，直到有人笑',
  '把手机给右边的人翻三秒相册', '模仿在座一个人，让大家猜', '接下来一轮只能用疑问句说话',
];

export default function Revolver() {
  const [phase, setPhase] = useState('idle');       // idle | armed | done
  const [remaining, setRemaining] = useState(6);
  const [loadedAt, setLoadedAt] = useState(-1);
  const [atHammer, setAtHammer] = useState(0);
  const [result, setResult] = useState(null);
  const [punish, setPunish] = useState('');
  const [torch, setTorch] = useState(false);

  const flash = useRef(new Animated.Value(0)).current;
  const dark = useRef(new Animated.Value(0)).current;   // 击发瞬间黑屏一顿
  const muzzle = useRef(new Animated.Value(0)).current;
  const recoil = useRef(new Animated.Value(0)).current;
  const wobble = useRef(new Animated.Value(0)).current;
  const spinArc = useRef(new Animated.Value(0)).current;   // 弹巢转的视觉指示

  useEffect(() => {
    try { setTorch(RevolverFeel.hasTorch()); } catch (e) {}
    RevolverFeel.start().catch(() => {});
    return () => { RevolverFeel.stop().catch(() => {}); };
  }, []);

  const doSpin = () => {
    RevolverFeel.spin().catch(() => {});
    setLoadedAt(Math.floor(Math.random() * 6));
    setAtHammer(0); setRemaining(6); setResult(null); setPhase('armed');
    wobble.setValue(0);
    Animated.sequence([
      Animated.timing(wobble, { toValue: 1, duration: 90, useNativeDriver: true }),
      Animated.timing(wobble, { toValue: -1, duration: 110, useNativeDriver: true }),
      Animated.timing(wobble, { toValue: 0.5, duration: 90, useNativeDriver: true }),
      Animated.timing(wobble, { toValue: 0, duration: 120, easing: Easing.out(Easing.quad), useNativeDriver: true }),
    ]).start();
    // 弹巢转的视觉:一圈快速旋转的弧线,压在弹巢位置
    spinArc.setValue(0);
    Animated.timing(spinArc, { toValue: 1, duration: 1000, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
  };

  const pullTrigger = () => {
    if (phase !== 'armed') { doSpin(); return; }   // 没装弹先转膛
    const hit = atHammer === loadedAt;
    if (hit) {
      RevolverFeel.bang().catch(() => {});
      muzzle.setValue(1);
      Animated.timing(muzzle, { toValue: 0, duration: 240, useNativeDriver: true }).start();
      recoil.setValue(1);
      Animated.spring(recoil, { toValue: 0, useNativeDriver: true, speed: 7, bounciness: 16 }).start();
      // 黑屏一顿 → 爆亮:先黑闪压一下,再白光炸开回落,冲击感更强
      dark.setValue(0.92);
      Animated.timing(dark, { toValue: 0, duration: 95, easing: Easing.out(Easing.quad), useNativeDriver: true }).start();
      flash.setValue(0);
      Animated.sequence([
        Animated.delay(55),
        Animated.timing(flash, { toValue: 1, duration: 45, useNativeDriver: true }),
        Animated.timing(flash, { toValue: 0, duration: 430, easing: Easing.out(Easing.quad), useNativeDriver: true }),
      ]).start();
      setResult('bang'); setPunish(PUNISH[Math.floor(Math.random() * PUNISH.length)]); setPhase('done');
    } else {
      RevolverFeel.click().catch(() => {});
      setAtHammer((h) => (h + 1) % 6);
      setRemaining((r) => Math.max(1, r - 1));
      setResult('click');
    }
  };

  return (
    <View style={s.root}>
      {/* 影棚渐变背景:上浅下深,深枪立刻看清(跟真枪实拍那种背景一样) */}
      <LinearGradient colors={['#6a6a74', '#43434c', '#22222a']} locations={[0, 0.5, 1]} style={StyleSheet.absoluteFill} />

      {/* 全屏竖枪(缩 85%)。后坐时枪口上跳,转膛时晃 */}
      <Animated.View style={[s.gunWrap, {
        transform: [
          { translateY: recoil.interpolate({ inputRange: [0, 1], outputRange: [22, -4] }) },
          { rotate: wobble.interpolate({ inputRange: [-1, 1], outputRange: ['-3deg', '3deg'] }) },
        ],
      }]}>
        <Image source={GUN} style={s.gun} contentFit="contain" />

        {/* 弹巢热区:点它转膛。带一圈可见的可点提示 + 转动弧线 */}
        <Pressable style={s.cylZone} onPress={doSpin}>
          <View style={s.cylRing}>
            <Animated.View style={[s.arc, { opacity: spinArc.interpolate({ inputRange: [0, 0.2, 1], outputRange: [0, 0.9, 0] }), transform: [{ rotate: spinArc.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '1080deg'] }) }] }]} />
            <Text style={s.zoneT}>转</Text>
          </View>
        </Pressable>

        {/* 扳机热区:点它开火 */}
        <Pressable style={s.trigZone} onPress={pullTrigger}>
          <View style={[s.trigRing, phase === 'armed' && s.trigArmed]}>
            <Text style={s.zoneT}>扣</Text>
          </View>
        </Pressable>
      </Animated.View>

      {/* 枪口火光:枪管在顶端偏右 */}
      <Animated.View pointerEvents="none" style={[s.muzzle, { opacity: muzzle, transform: [{ scale: muzzle.interpolate({ inputRange: [0, 1], outputRange: [0.4, 1.5] }) }] }]}>
        <Text style={s.muzzleGlyph}>💥</Text>
      </Animated.View>

      {/* 顶部:局长话 */}
      <LinearGradient colors={['#14141cdd', '#14141c00']} style={s.topScrim} pointerEvents="box-none">
        <Text style={s.hostText}>
          {phase === 'idle' && '🎩 六膛一响，谁扣谁认 —— 点弹巢转，点扳机扣。'}
          {phase === 'armed' && (result === 'click' ? '🎩 空膛……敢再扣一下吗？' : '🎩 装好了，点扳机。')}
          {phase === 'done' && '🎩 响了！这一膛是你的。'}
        </Text>
      </LinearGradient>

      {/* 底部:惩罚卡(响了才有) */}
      {phase === 'done' && (
        <LinearGradient colors={['#14141c00', '#14141cf5', '#14141c']} style={s.botScrim} pointerEvents="box-none">
          <View style={s.punishCard}>
            <Text style={s.punishK}>这一膛装的是</Text>
            <Text style={s.punishV}>「{punish}」</Text>
            <Text style={s.punishNote}>非饮酒替代 · 可跳过换一个</Text>
          </View>
          <View style={s.btnRow}>
            <Pressable style={[s.btn, s.spin]} onPress={doSpin}><Text style={s.btnT}>重装再来</Text></Pressable>
            <Pressable style={[s.btn, s.alt]} onPress={() => setPunish(PUNISH[Math.floor(Math.random() * PUNISH.length)])}><Text style={s.btnT}>换温和的</Text></Pressable>
          </View>
          <Text style={s.diag}>手电 {torch ? '可用（击发爆闪）' : '不可用（模拟器无手电）'}</Text>
        </LinearGradient>
      )}

      {/* 击发:黑屏一顿(下) → 白光爆闪(上) */}
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: '#000' }, { opacity: dark }]} />
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, s.flash, { opacity: flash }]} />
    </View>
  );
}

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#14141c' },
  gunWrap: { ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center' },
  gun: { width: '85%', height: '85%' },
  // 热区位置按截图校准(百分比,相对 gunWrap)
  cylZone: { position: 'absolute', top: '53%', left: '55%', width: 110, height: 110, alignItems: 'center', justifyContent: 'center' },
  cylRing: { width: 84, height: 84, borderRadius: 42, borderWidth: 2, borderColor: '#ffffff55', alignItems: 'center', justifyContent: 'center' },
  arc: { position: 'absolute', width: 84, height: 84, borderRadius: 42, borderWidth: 3, borderColor: '#e0b060', borderRightColor: 'transparent', borderBottomColor: 'transparent' },
  trigZone: { position: 'absolute', top: '58%', left: '33%', width: 96, height: 96, alignItems: 'center', justifyContent: 'center' },
  trigRing: { width: 62, height: 62, borderRadius: 31, borderWidth: 2, borderColor: '#ffffff44', alignItems: 'center', justifyContent: 'center' },
  trigArmed: { borderColor: '#ff6a6a', backgroundColor: '#8a2b2b55' },
  zoneT: { color: '#fff', fontSize: 15, fontWeight: '800', textShadowColor: '#000', textShadowRadius: 4 },
  muzzle: { position: 'absolute', top: 38, left: '64%', zIndex: 2 },
  muzzleGlyph: { fontSize: 90 },
  topScrim: { position: 'absolute', top: 0, left: 0, right: 0, paddingTop: 56, paddingBottom: 18, paddingHorizontal: 16 },
  hostText: { color: '#fff', fontSize: 15, fontWeight: '700', textShadowColor: '#000', textShadowRadius: 6 },
  botScrim: { position: 'absolute', bottom: 0, left: 0, right: 0, paddingHorizontal: 14, paddingBottom: 34, paddingTop: 40 },
  punishCard: { backgroundColor: '#1b1b28ee', borderRadius: 10, padding: 14, marginBottom: 10, borderWidth: 1, borderColor: '#5c4326' },
  punishK: { color: '#8b8b9e', fontSize: 12 },
  punishV: { color: '#fff', fontSize: 17, fontWeight: '700', marginTop: 4 },
  punishNote: { color: '#7a7a90', fontSize: 11, marginTop: 4 },
  btnRow: { flexDirection: 'row', gap: 8 },
  btn: { flex: 1, padding: 15, borderRadius: 11, alignItems: 'center' },
  spin: { backgroundColor: '#3a3a55' },
  alt: { backgroundColor: '#2a2a38' },
  btnT: { color: '#fff', fontSize: 16, fontWeight: '700' },
  diag: { color: '#7a7a90', fontSize: 10, marginTop: 10, fontFamily: 'Menlo', textAlign: 'center' },
  flash: { backgroundColor: '#fff' },
});
