/**
 * 快枪手对决 · 视听层 · DuelStage —— 把手机变成枪,只换皮肉不碰判定。
 *
 * ★ 心脏一行不动:对决状态(vs / drawn 枪响了没)、tap 上报、duel_result 胜负,全在
 *   服务端 + App.js 的 inDuel 逻辑里。本组件是**纯视听**,吃外部传入的状态:
 *     - 等待期(!drawn)  持枪待发,「手别碰屏幕」,碰=抢跑判负(判定在服务端)
 *     - 「拔!」亮起那一刻(drawn 从无到有)→ 枪口火光 + 枪响 + 后坐 + 全屏爆闪
 *     - dueled(我已开枪)→ 等局长宣布
 *   这里没有任何"自己 random 出结果"的路径——不再有转膛/中弹/惩罚那套本地随机,
 *   谁胜谁负只认服务器的 duel_result。
 *
 * 降级:枪响优先走原生 RevolverFeel(枪声+手电爆闪+重震);缺席则 expo-av 播枪声 +
 *   expo-haptics 重震 + 屏幕爆闪。Expo Go / 模拟器照样对得起来,一个 crash 都不许。
 */
import React, { useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Pressable, Animated } from 'react-native';
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { revStart, revStop, revBang, makePlayer, tapHeavy } from './feel';

const GUN = require('../assets/props/rev/gun_v.webp');
const BANG_WAV = require('../modules/propfeel/ios/rev_bang.wav');

/**
 * @param vs      对决双方名字数组(展示用)
 * @param drawn   服务端的"拔枪时刻到了"信号(枪响)
 * @param dueled  本机是否已开过枪(点过「拔!」)
 * @param onDraw  按下「拔!」的回调(sendEvent tap + 触感在 App 里,原样不动)
 */
export function DuelStage({ vs = [], drawn = false, dueled = false, onDraw }) {
  const flash = useRef(new Animated.Value(0)).current;   // 爆亮
  const dark = useRef(new Animated.Value(0)).current;    // 击发瞬间黑一顿
  const muzzle = useRef(new Animated.Value(0)).current;  // 枪口火光
  const recoil = useRef(new Animated.Value(0)).current;  // 后坐上跳
  const bangPlayer = useRef(null);
  const firedRef = useRef(false);

  // 原生手感会话(只备音频/手电,不碰动检——与对决逻辑零冲突);expo-av 兜底枪声预加载。
  useEffect(() => {
    revStart();
    bangPlayer.current = makePlayer(BANG_WAV);
    return () => {
      revStop();
      bangPlayer.current && bangPlayer.current.unload();
    };
  }, []);

  // —— 「拔!」亮起那一刻(drawn 从无到有):枪口火光 + 枪响 + 后坐 + 全屏爆闪,只放一次 ——
  useEffect(() => {
    if (drawn && !firedRef.current) {
      firedRef.current = true;
      // 声音+触感:原生接管了就不叠 expo-av;没接管走降级(枪声 + 重震)。
      const handled = revBang();
      if (!handled) { bangPlayer.current && bangPlayer.current.play(); tapHeavy(); }
      // 枪口火光
      muzzle.setValue(1);
      Animated.timing(muzzle, { toValue: 0, duration: 240, useNativeDriver: true }).start();
      // 后坐上跳
      recoil.setValue(1);
      Animated.spring(recoil, { toValue: 0, useNativeDriver: true, speed: 7, bounciness: 16 }).start();
      // 黑一顿 → 爆亮:先黑闪压一下,再白光炸开回落
      dark.setValue(0.9);
      Animated.timing(dark, { toValue: 0, duration: 95, useNativeDriver: true }).start();
      flash.setValue(0);
      Animated.sequence([
        Animated.delay(55),
        Animated.timing(flash, { toValue: 1, duration: 45, useNativeDriver: true }),
        Animated.timing(flash, { toValue: 0, duration: 430, useNativeDriver: true }),
      ]).start();
    } else if (!drawn) {
      firedRef.current = false; // 下一次对决重置
    }
  }, [drawn]);

  return (
    <View style={[s.root, { backgroundColor: drawn ? '#7a1010' : '#14141c' }]}>
      {/* 影棚渐变:深枪立刻看清 */}
      <LinearGradient colors={drawn ? ['#8a2020', '#4a1010', '#220808'] : ['#3a3a44', '#22222a', '#14141c']}
        locations={[0, 0.5, 1]} style={StyleSheet.absoluteFill} />

      {/* 顶部:对决双方 */}
      <View style={s.top} pointerEvents="none">
        <Text style={s.vs}>{vs.join('  ⚡  ')}</Text>
      </View>

      {/* 全屏竖枪:后坐时枪口上跳 */}
      <Animated.View style={[s.gunWrap, {
        transform: [{ translateY: recoil.interpolate({ inputRange: [0, 1], outputRange: [18, -6] }) }],
      }]} pointerEvents="none">
        <Image source={GUN} style={s.gun} contentFit="contain" />
      </Animated.View>

      {/* 枪口火光:枪管在顶端偏右 */}
      <Animated.View pointerEvents="none" style={[s.muzzle, {
        opacity: muzzle,
        transform: [{ scale: muzzle.interpolate({ inputRange: [0, 1], outputRange: [0.4, 1.5] }) }],
      }]}>
        <Text style={s.muzzleGlyph}>💥</Text>
      </Animated.View>

      {/* 底部:随对决状态切换的动作区 */}
      <View style={s.bottom}>
        {dueled ? (
          <Text style={s.wait}>已开枪,等局长宣布……</Text>
        ) : drawn ? (
          <Pressable style={s.drawBtn} onPress={onDraw}>
            <Text style={s.drawText}>拔!</Text>
          </Pressable>
        ) : (
          <>
            <Text style={s.wait}>对峙中……手别碰屏幕</Text>
            <Text style={s.hint}>枪响前碰 = 抢跑判负</Text>
          </>
        )}
      </View>

      {/* 击发:黑屏一顿(下) → 白光爆闪(上) */}
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: '#000', opacity: dark }]} />
      <Animated.View pointerEvents="none" style={[StyleSheet.absoluteFill, s.flash, { opacity: flash }]} />
    </View>
  );
}

export default DuelStage;

const s = StyleSheet.create({
  root: { flex: 1, alignItems: 'center', justifyContent: 'center' },
  top: { position: 'absolute', top: 70, left: 0, right: 0, alignItems: 'center' },
  vs: { color: '#fff', fontSize: 30, fontWeight: '800', textShadowColor: '#000', textShadowRadius: 6 },
  gunWrap: { ...StyleSheet.absoluteFillObject, alignItems: 'center', justifyContent: 'center' },
  gun: { width: '82%', height: '82%' },
  muzzle: { position: 'absolute', top: 60, left: '62%', zIndex: 2 },
  muzzleGlyph: { fontSize: 92 },
  bottom: { position: 'absolute', bottom: 90, left: 0, right: 0, alignItems: 'center' },
  wait: { color: '#fff', fontSize: 24, fontWeight: '700', marginBottom: 10, textShadowColor: '#000', textShadowRadius: 6 },
  hint: { color: '#f2d0d0', fontSize: 16, textShadowColor: '#000', textShadowRadius: 4 },
  drawBtn: { backgroundColor: '#ffd54a', width: 240, height: 240, borderRadius: 120,
    alignItems: 'center', justifyContent: 'center',
    shadowColor: '#000', shadowOpacity: 0.5, shadowRadius: 20, shadowOffset: { width: 0, height: 8 } },
  drawText: { fontSize: 78, fontWeight: '900', color: '#7a1010' },
  flash: { backgroundColor: '#fff' },
});
