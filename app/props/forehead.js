/**
 * 额头身份牌 · 单张牌 · ForeheadCard —— 可见性反转,只忠实渲染,不发牌。
 *
 * 【对齐引擎正解 · origin/master「额头牌状态化」】
 * 服务端 state.foreheads = {玩家: 身份};player_view 只给**别人**的、自己那张永远缺席。
 * 可见性反转在服务端焊死,客户端天然拿不到自己的词。
 *
 * ★ 心脏一行不动:身份由服务器发(view.foreheads[那个人]),本组件吃外部传入的 identity——
 *   组件内没有 dealForeheads 那套本地随机发牌(已废弃),不产出任何身份结果。
 *   - 别人的牌:点开即翻面亮出 identity(翻转 + 震动)
 *   - 自己的牌:锁着的牌背「你的牌你看不见」,翻不开
 */
import React, { useEffect, useRef } from 'react';
import { View, Text, StyleSheet, Animated, Easing } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import * as Haptics from 'expo-haptics';

/**
 * @param name     牌主名字(展示用)
 * @param identity 服务器发的身份串(view.foreheads[name]);isMe 时无视(自己看不到)
 * @param isMe     这张是不是"我自己"——是则永远牌背锁着
 */
export function ForeheadCard({ name, identity, isMe = false }) {
  const flip = useRef(new Animated.Value(0)).current;

  // 别人的牌:挂上来即翻面亮出身份(点开=看这个人的牌,揭晓编排放一次)。自己的牌不翻。
  useEffect(() => {
    if (isMe) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    const t = setTimeout(() => {
      Animated.timing(flip, { toValue: 1, duration: 460, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
    }, 60);
    return () => clearTimeout(t);
  }, [isMe, name]);

  const frontRotate = flip.interpolate({ inputRange: [0, 1], outputRange: ['180deg', '360deg'] });
  const backRotate = flip.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '180deg'] });
  const backOpacity = flip.interpolate({ inputRange: [0, 0.5, 0.5, 1], outputRange: [1, 1, 0, 0] });
  const frontOpacity = flip.interpolate({ inputRange: [0, 0.5, 0.5, 1], outputRange: [0, 0, 1, 1] });

  return (
    <View style={c.wrap}>
      {/* 牌背(扣着) */}
      <Animated.View style={[c.face, { opacity: backOpacity, transform: [{ perspective: 900 }, { rotateY: backRotate }] }]}>
        <LinearGradient colors={isMe ? ['#3a2a2a', '#241b1b'] : ['#3a3f6a', '#232a4a']} style={c.card}>
          <Text style={c.backName}>{name}</Text>
          <Text style={c.backMark}>{isMe ? '🙈' : '?'}</Text>
          <Text style={c.backHint}>{isMe ? '你的牌\n你看不见' : '翻开看他是谁'}</Text>
        </LinearGradient>
      </Animated.View>

      {/* 牌面(翻开后亮出服务器发的身份) */}
      <Animated.View style={[c.face, c.faceAbs, { opacity: frontOpacity, transform: [{ perspective: 900 }, { rotateY: frontRotate }] }]}>
        <LinearGradient colors={['#f4f1e8', '#ddd6c4']} style={c.card}>
          <Text style={c.frontName}>{name}</Text>
          <Text style={c.frontLabel} numberOfLines={3}>{identity || '—'}</Text>
        </LinearGradient>
      </Animated.View>
    </View>
  );
}

export default ForeheadCard;

const CARD_W = 150, CARD_H = 200;
const c = StyleSheet.create({
  wrap: { width: CARD_W, height: CARD_H },
  face: { width: CARD_W, height: CARD_H, backfaceVisibility: 'hidden' },
  faceAbs: { position: 'absolute', top: 0, left: 0 },
  card: { flex: 1, borderRadius: 16, alignItems: 'center', justifyContent: 'center', padding: 12,
    shadowColor: '#000', shadowOpacity: 0.4, shadowRadius: 10, shadowOffset: { width: 0, height: 5 } },
  backName: { position: 'absolute', top: 12, left: 14, color: '#fff', fontSize: 17, fontWeight: '800', opacity: 0.9 },
  backMark: { color: '#fff', fontSize: 52, opacity: 0.85 },
  backHint: { color: '#ffffffbb', fontSize: 13, textAlign: 'center', marginTop: 10, lineHeight: 18 },
  frontName: { position: 'absolute', top: 12, left: 14, color: '#8a7f68', fontSize: 16, fontWeight: '800' },
  frontLabel: { color: '#2c2618', fontSize: 24, fontWeight: '800', marginTop: 4, textAlign: 'center' },
});
