/**
 * 额头身份牌 —— 可见性反转（别人看得到、你自己看不到）
 *
 * 【对齐引擎正解 · origin/master 8c2d18d「额头牌状态化」】
 * 服务端 state.foreheads = {玩家: 身份}；player_view 给别人的、自己那张永远缺席。
 * 可见性反转在服务端焊死,客户端只忠实渲染。
 *
 * 【额头档 = 猜自己式】每人一个不同身份,没有卧底。别人看得到你、你看不到自己,
 * 你靠别人的提示猜自己是谁。看得到别人是玩法不是泄密。
 * (谁是卧底属于「自己看」档,别混。)
 *
 * 交互:每个人一张扣着的牌,点一下翻开看身份(3D 翻转 + 震动);自己那张锁着翻不开。
 */
import React, { useRef, useState } from 'react';
import { View, Text, StyleSheet, Pressable, Animated, Easing } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import * as Haptics from 'expo-haptics';

const IDENTITIES = [
  '🐘 大象', '🦁 狮子', '🐧 企鹅', '🦒 长颈鹿', '🐙 章鱼', '🦊 狐狸',
  '🦖 恐龙', '🐳 鲸鱼', '🦩 火烈鸟', '🐨 考拉', '🦉 猫头鹰', '🦔 刺猬',
];
const NAMES = ['甲', '乙', '丙', '丁', '戊', '己'];

function dealForeheads(players) {
  const pool = [...IDENTITIES].sort(() => Math.random() - 0.5);
  const f = {};
  players.forEach((p, i) => (f[p] = pool[i]));
  return f;
}
function playerView(foreheads, me) {
  const out = {};
  for (const p of Object.keys(foreheads)) if (p !== me) out[p] = foreheads[p];
  return out; // me 缺席
}

// ---- 单张牌:扣着 → 点翻开 ----
function BadgeCard({ name, identity, isMe, revealed, onFlip }) {
  const flip = useRef(new Animated.Value(0)).current;

  const doFlip = () => {
    if (isMe || revealed) return;
    Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Medium).catch(() => {});
    onFlip();
    Animated.timing(flip, { toValue: 1, duration: 460, easing: Easing.out(Easing.cubic), useNativeDriver: true }).start();
  };

  const frontRotate = flip.interpolate({ inputRange: [0, 1], outputRange: ['180deg', '360deg'] });
  const backRotate = flip.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '180deg'] });
  const backOpacity = flip.interpolate({ inputRange: [0, 0.5, 0.5, 1], outputRange: [1, 1, 0, 0] });
  const frontOpacity = flip.interpolate({ inputRange: [0, 0.5, 0.5, 1], outputRange: [0, 0, 1, 1] });

  const [emoji, ...rest] = (identity || '  ').split(' ');
  const label = rest.join(' ');

  return (
    <Pressable style={c.wrap} onPress={doFlip}>
      {/* 牌背(扣着) */}
      <Animated.View style={[c.face, { opacity: backOpacity, transform: [{ perspective: 800 }, { rotateY: backRotate }] }]}>
        <LinearGradient
          colors={isMe ? ['#3a2a2a', '#241b1b'] : ['#3a3f6a', '#232a4a']}
          style={c.card}
        >
          <Text style={c.backName}>{name}</Text>
          <Text style={c.backMark}>{isMe ? '🙈' : '?'}</Text>
          <Text style={c.backHint}>{isMe ? '你自己的\n看不到' : '点一下翻开'}</Text>
        </LinearGradient>
      </Animated.View>

      {/* 牌面(翻开后) */}
      <Animated.View style={[c.face, c.faceAbs, { opacity: frontOpacity, transform: [{ perspective: 800 }, { rotateY: frontRotate }] }]}>
        <LinearGradient colors={['#f4f1e8', '#ddd6c4']} style={c.card}>
          <Text style={c.frontName}>{name}</Text>
          <Text style={c.frontEmoji}>{emoji}</Text>
          <Text style={c.frontLabel}>{label}</Text>
        </LinearGradient>
      </Animated.View>
    </Pressable>
  );
}

export default function ForeheadBadge() {
  const [phase, setPhase] = useState('setup');
  const [players] = useState(NAMES.slice(0, 5));
  const [me, setMe] = useState('甲');
  const [view, setView] = useState({});
  const [opened, setOpened] = useState({});

  const start = (seat) => {
    setMe(seat);
    setView(playerView(dealForeheads(players), seat));
    setOpened({});
    setPhase('play');
  };

  if (phase === 'setup') {
    return (
      <View style={s.root}>
        <Text style={s.h1}>额头身份牌</Text>
        <Text style={s.sub}>猜猜我是谁 · 别人看得到、你自己看不到</Text>
        <View style={s.card}>
          <Text style={s.cardT}>
            每人额头上一个身份，各不相同。翻开别人的牌看他是谁，
            <Text style={s.hl}> 唯独自己那张翻不开</Text>——
            你是谁，靠别人给你的提示去猜。
          </Text>
        </View>
        <Text style={s.pick}>选你的座位（本设备）：</Text>
        <View style={s.seatRow}>
          {players.map((p) => (
            <Pressable key={p} style={s.seatBtn} onPress={() => start(p)}>
              <Text style={s.seatT}>{p}</Text>
            </Pressable>
          ))}
        </View>
      </View>
    );
  }

  return (
    <View style={s.root}>
      <Text style={s.h1}>你是 {me}</Text>
      <Text style={s.sub}>翻开别人看他是谁 · 你自己的靠猜</Text>

      <View style={s.grid}>
        {players.map((p) => (
          <BadgeCard
            key={p}
            name={p + (p === me ? '（你）' : '')}
            identity={view[p]}
            isMe={p === me}
            revealed={!!opened[p]}
            onFlip={() => setOpened((o) => ({ ...o, [p]: true }))}
          />
        ))}
      </View>

      <View style={s.foot}>
        <Text style={s.footT}>
          可见性反转：你翻得开别人全部的牌，唯独自己那张锁着。
          看得到别人不是泄密——这是玩法，别人靠你看不到的那张给你提示。
        </Text>
        <Pressable style={[s.btn, s.again]} onPress={() => setPhase('setup')}>
          <Text style={s.btnT}>重发一局</Text>
        </Pressable>
      </View>
    </View>
  );
}

const CARD_W = 104, CARD_H = 140;
const c = StyleSheet.create({
  wrap: { width: CARD_W, height: CARD_H, margin: 6 },
  face: { width: CARD_W, height: CARD_H, backfaceVisibility: 'hidden' },
  faceAbs: { position: 'absolute', top: 0, left: 0 },
  card: { flex: 1, borderRadius: 14, alignItems: 'center', justifyContent: 'center', padding: 8,
    shadowColor: '#000', shadowOpacity: 0.4, shadowRadius: 8, shadowOffset: { width: 0, height: 4 } },
  backName: { position: 'absolute', top: 10, left: 12, color: '#fff', fontSize: 16, fontWeight: '800', opacity: 0.9 },
  backMark: { color: '#fff', fontSize: 40, opacity: 0.85 },
  backHint: { color: '#ffffffaa', fontSize: 11, textAlign: 'center', marginTop: 8, lineHeight: 15 },
  frontName: { position: 'absolute', top: 10, left: 12, color: '#8a7f68', fontSize: 15, fontWeight: '800' },
  frontEmoji: { fontSize: 52 },
  frontLabel: { color: '#3a3324', fontSize: 17, fontWeight: '800', marginTop: 4 },
});

const s = StyleSheet.create({
  root: { flex: 1, backgroundColor: '#14141c', paddingHorizontal: 18, paddingTop: 70 },
  h1: { color: '#fff', fontSize: 26, fontWeight: '800' },
  sub: { color: '#8b8b9e', fontSize: 13, marginTop: 6, marginBottom: 20 },
  card: { backgroundColor: '#1b1b28', borderRadius: 12, padding: 16, marginBottom: 22 },
  cardT: { color: '#c9c9d6', fontSize: 15, lineHeight: 24 },
  hl: { color: '#c9a86e', fontWeight: '700' },
  pick: { color: '#a99bd8', fontSize: 14, marginBottom: 12 },
  seatRow: { flexDirection: 'row', gap: 10, flexWrap: 'wrap' },
  seatBtn: { width: 60, height: 60, borderRadius: 12, backgroundColor: '#2b3f56', alignItems: 'center', justifyContent: 'center' },
  seatT: { color: '#fff', fontSize: 22, fontWeight: '700' },
  grid: { flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center' },
  foot: { marginTop: 20 },
  footT: { color: '#7a7a90', fontSize: 12, lineHeight: 19 },
  btn: { paddingVertical: 15, borderRadius: 11, alignItems: 'center', marginTop: 16 },
  again: { backgroundColor: '#3a3a55' },
  btnT: { color: '#fff', fontSize: 16, fontWeight: '700' },
});
