/**
 * 骰盅影棚质感层 · DiceStage —— 只画皮肉,不碰心脏。
 *
 * 这一屏建立在一个产品事实上:大话骰摇盅时骰子盖着,玩家全程看不见。
 * 所以摇的那几秒屏幕上没有值得渲染的骰面——手感来自盅体跟手抖 + 震动 + 音效,
 * 揭盅那一下才是落差所在。
 *
 * ★ 心脏一行不动:摇动脉冲计数、扣盅→POST roll、开牌锁定、点数来源,全在 App.js 的
 *   DiceCup 状态机里。本组件是**纯展示**:
 *     - pulse    每次摇动脉冲自增一下 → 盅体抖 + 撞击音(降级层)
 *     - rolling  扣盅动画进行中 → 盅体持续抖(哗啦哗啦)
 *     - revealed **服务器回的那五颗点数**(my_prop.rolled)→ 揭盅 + 骰面朝上散落 + 揭盅音
 *   revealed 永远从外部传入。组件内没有任何"自己 random 出点数"的路径——
 *   落点/旋转是确定性伪散布(只是摆位,不是结果),连 Math.random 都不用,杜绝嫌疑。
 */
import React, { useEffect, useRef } from 'react';
import { View, StyleSheet, Animated, Easing } from 'react-native';
import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { makePlayer } from './feel';

// Blender 渲的象牙白树脂骰(中式红/黑点)+ 骰盅 + 对称柔影。require 必须字面量(Metro 静态解析)。
const DIE_IMG = {
  1: require('../assets/props/dice/die_1.webp'), 2: require('../assets/props/dice/die_2.webp'),
  3: require('../assets/props/dice/die_3.webp'), 4: require('../assets/props/dice/die_4.webp'),
  5: require('../assets/props/dice/die_5.webp'), 6: require('../assets/props/dice/die_6.webp'),
};
const CUP_IMG = require('../assets/props/dice/cup.webp');
const SHADOW_IMG = require('../assets/props/dice/die_shadow.webp');
// 音效降级层的两颗 wav(原生 DiceFeel 缺席时用 expo-av 播):撞击 + 揭盅。
const CLICK_WAV = require('../modules/propfeel/ios/dice_click_1.wav');
const REVEAL_WAV = require('../modules/propfeel/ios/dice_reveal.wav');

// —— 确定性伪散布 —— 落点/旋转只是"视觉摆位",不是游戏结果。用 (index, value) 哈希算出,
// 稳定不随重渲抖动(骰子不会每帧乱跳),更从根上避免任何 Math.random 出结果的活路径。
const RX = 128, RY = 42;
function frac(a, b) {
  let h = (Math.imul(a + 1, 73856093) ^ Math.imul(b + 3, 19349663) ^ 0x9e3779b9) >>> 0;
  h ^= h >>> 13; h = Math.imul(h, 0x5bd1e995) >>> 0; h ^= h >>> 15;
  return (h >>> 0) / 4294967295;
}
function layout(values) {
  // 横向均匀铺开(小颗数天然不叠)+ 哈希纵向抖动 + 哈希旋转/微缩。
  const n = values.length;
  const arr = [];
  for (let i = 0; i < n; i++) {
    const t = n === 1 ? 0.5 : i / (n - 1);
    const v = values[i] || 1;
    arr.push({
      x: (t - 0.5) * 2 * RX,
      y: (frac(i, v) - 0.5) * 2 * RY,
      rot: frac(i + 7, v) * 360,
      s: 0.94 + frac(i + 13, v) * 0.14,
    });
  }
  return arr;
}

/**
 * @param count    盅里几颗(摇动阶段只画盅,不需要真点数)
 * @param revealed 服务器回的点数数组(揭盅);null/undefined = 盅还盖着
 * @param pulse    摇动脉冲计数(每次摇一下自增)——驱动盅体抖 + 撞击音
 * @param rolling  扣盅动画进行中——盅体持续抖
 */
export function DiceStage({ count = 5, revealed = null, pulse = 0, rolling = false }) {
  const lift = useRef(new Animated.Value(0)).current;    // 0 盅盖着 → 1 盅揭起
  const shake = useRef(new Animated.Value(0)).current;   // 盅体左右抖(跟手/脉冲)
  const wasRevealed = useRef(false);
  const scatterRef = useRef([]);
  const clickPlayer = useRef(null);
  const revealPlayer = useRef(null);
  const rollLoop = useRef(null);

  // 音效降级层:预加载一次,组件走时卸载。原生缺席时这是唯一声音来源。
  useEffect(() => {
    clickPlayer.current = makePlayer(CLICK_WAV);
    revealPlayer.current = makePlayer(REVEAL_WAV);
    return () => {
      clickPlayer.current && clickPlayer.current.unload();
      revealPlayer.current && revealPlayer.current.unload();
    };
  }, []);

  // —— 揭盅编排 —— revealed 从无到有:锁定散布(用服务器点数算摆位)、升盅、放揭盅音。
  useEffect(() => {
    const has = Array.isArray(revealed) && revealed.length > 0;
    if (has && !wasRevealed.current) {
      wasRevealed.current = true;
      scatterRef.current = layout(revealed);
      revealPlayer.current && revealPlayer.current.play();
      Animated.timing(lift, {
        toValue: 1, duration: 440, easing: Easing.out(Easing.cubic), useNativeDriver: true,
      }).start();
    } else if (!has && wasRevealed.current) {
      // 新一局重开:盅盖回去
      wasRevealed.current = false;
      lift.setValue(0);
    }
  }, [revealed]);

  // —— 摇动脉冲 —— 每次 pulse 自增:盅体抖一下 + 撞击音(仅盅盖着时)。
  useEffect(() => {
    if (pulse <= 0 || wasRevealed.current) return;
    clickPlayer.current && clickPlayer.current.play();
    Animated.sequence([
      Animated.timing(shake, { toValue: 12, duration: 40, useNativeDriver: true }),
      Animated.timing(shake, { toValue: -8, duration: 50, useNativeDriver: true }),
      Animated.timing(shake, { toValue: 0, duration: 60, useNativeDriver: true }),
    ]).start();
  }, [pulse]);

  // —— 扣盅动画 —— rolling 期间盅体持续小幅抖(哗啦哗啦),结束归位。
  useEffect(() => {
    if (rolling && !wasRevealed.current) {
      rollLoop.current = Animated.loop(Animated.sequence([
        Animated.timing(shake, { toValue: 7, duration: 60, useNativeDriver: true }),
        Animated.timing(shake, { toValue: -7, duration: 70, useNativeDriver: true }),
      ]));
      rollLoop.current.start();
    } else {
      if (rollLoop.current) { rollLoop.current.stop(); rollLoop.current = null; }
      if (!wasRevealed.current) Animated.timing(shake, { toValue: 0, duration: 90, useNativeDriver: true }).start();
    }
    return () => { if (rollLoop.current) { rollLoop.current.stop(); rollLoop.current = null; } };
  }, [rolling]);

  const dice = Array.isArray(revealed) ? revealed : [];

  return (
    <View style={s.stage}>
      {/* 绿绒托盘:骰子落定的地方(.felt 配色 #1D5A3D→#0F3826→#082418) */}
      <LinearGradient
        colors={['#20604233', '#1D5A3D', '#0F3826', '#082418']}
        locations={[0, 0.3, 0.7, 1]}
        style={s.tray}
      >
        <Animated.View style={[StyleSheet.absoluteFill, s.trayInner, { opacity: lift }]}>
          {/* 先铺柔影(只平移,不转) */}
          {dice.map((n, i) => {
            const sc = scatterRef.current[i] || { x: 0, y: 0, s: 1 };
            return (
              <Image key={'sh' + i} source={SHADOW_IMG} contentFit="contain"
                style={[s.dieShadowImg, { transform: [{ translateX: sc.x }, { translateY: sc.y + 5 }, { scale: sc.s }] }]} />
            );
          })}
          {/* 再叠骰子(平移 + 旋转),点数面朝上——画的就是服务器回的那几颗 */}
          {dice.map((n, i) => {
            const sc = scatterRef.current[i] || { x: 0, y: 0, rot: 0, s: 1 };
            return (
              <Image key={'d' + i} source={DIE_IMG[n] || DIE_IMG[1]} contentFit="contain"
                style={[s.dieImg, { transform: [{ translateX: sc.x }, { translateY: sc.y }, { rotate: `${sc.rot}deg` }, { scale: sc.s }] }]} />
            );
          })}
        </Animated.View>
      </LinearGradient>

      {/* 骰盅:摇时跟着抖,揭盅时上移露出托盘里的骰子 */}
      <Animated.View style={[s.cup, {
        transform: [
          { translateX: shake },
          { rotate: shake.interpolate({ inputRange: [-14, 14], outputRange: ['-5deg', '5deg'] }) },
          { translateY: lift.interpolate({ inputRange: [0, 1], outputRange: [0, -196] }) },
        ],
        opacity: lift.interpolate({ inputRange: [0, 1], outputRange: [1, 0] }),
      }]}>
        <Image source={CUP_IMG} style={s.cupImg} contentFit="contain" />
      </Animated.View>
    </View>
  );
}

export default DiceStage;

const s = StyleSheet.create({
  stage: { height: 220, alignItems: 'center', justifyContent: 'center', alignSelf: 'stretch' },
  tray: { position: 'absolute', bottom: 6, width: '96%', height: 120, borderRadius: 16,
    alignItems: 'center', justifyContent: 'center', borderWidth: 1, borderColor: '#0a2a1c' },
  trayInner: { alignItems: 'center', justifyContent: 'center' },
  cup: { position: 'absolute', zIndex: 3, bottom: 30 },
  cupImg: { width: 176, height: 176 },
  dieImg: { position: 'absolute', width: 50, height: 50 },
  dieShadowImg: { position: 'absolute', width: 48, height: 48 },
});
