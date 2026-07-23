/**
 * 道具箱 · 四件已验证的道具原语(对应 spec-prop-library #1-#4)
 *
 * 单机试玩入口:不用开局、不连引擎,现场直接掏出来用。
 * 正式接引擎后,这些屏由局长通过 props_dealt/state.foreheads 派发,
 * 这里的菜单只是"手动抽屉"——道具本身的行为两边完全一致。
 *
 * 合宪边界:手机=私密信道+判定仪器,不是游戏屏幕;
 * 传递/摆桌上这类物理动作 app 一律不感知,判定走全场共识。
 */
import React, { useState } from 'react';
import { View, Text, StyleSheet, Pressable, ScrollView } from 'react-native';
import Dice from './dice';
import Bomb from './bomb';
import Revolver from './revolver';
import Forehead from './forehead';

const PROPS = [
  { key: 'dice', title: '🎲 电子骰盅', sub: '隐藏·随机 · 自己看|摇了自己偷看,别人看不见', C: Dice },
  { key: 'bomb', title: '💣 定时炸弹', sub: '隐藏·随机 · 全场|倒计时只有持机人震动里知道', C: Bomb },
  { key: 'revolver', title: '🔫 虚拟左轮', sub: '随机 · 全场公开|转膛,扣扳机,听天由命', C: Revolver },
  { key: 'forehead', title: '🎴 额头身份牌', sub: '隐藏(反转) · 额头|别人看得到你,你看不到自己', C: Forehead },
];

export default function PropsBox({ onClose }) {
  const [cur, setCur] = useState(null);

  if (cur) {
    const P = PROPS.find((p) => p.key === cur).C;
    return (
      <View style={st.full}>
        <P />
        {/* 返回浮在道具屏之上;道具屏自己占满全屏 */}
        <Pressable style={st.back} hitSlop={12} onPress={() => setCur(null)}>
          <Text style={st.backT}>✕</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <View style={st.full}>
      <ScrollView contentContainerStyle={st.menu}>
        <Text style={st.logo}>🧰 道具箱</Text>
        <Text style={st.dim}>不用开局,单机就能玩。接引擎后这些由局长派发。</Text>
        {PROPS.map((p) => (
          <Pressable key={p.key} style={st.card} onPress={() => setCur(p.key)}>
            <Text style={st.cardT}>{p.title}</Text>
            <Text style={st.cardSub}>{p.sub}</Text>
          </Pressable>
        ))}
        <Pressable hitSlop={14} onPress={onClose}>
          <Text style={st.close}>← 返回</Text>
        </Pressable>
      </ScrollView>
    </View>
  );
}

const st = StyleSheet.create({
  full: { flex: 1, backgroundColor: '#14141c' },
  menu: { flexGrow: 1, justifyContent: 'center', padding: 24, gap: 12 },
  logo: { color: '#fff', fontSize: 30, fontWeight: '800', textAlign: 'center' },
  dim: { color: '#889', fontSize: 13, textAlign: 'center', marginBottom: 10 },
  card: { backgroundColor: '#1b1b28', borderRadius: 12, padding: 16 },
  cardT: { color: '#fff', fontSize: 19, fontWeight: '700' },
  cardSub: { color: '#98a', fontSize: 12, marginTop: 4 },
  close: { color: '#c9a86e', fontSize: 15, textAlign: 'center', padding: 14 },
  back: {
    position: 'absolute', top: 54, right: 14, width: 36, height: 36, borderRadius: 18,
    backgroundColor: '#00000088', alignItems: 'center', justifyContent: 'center',
  },
  backT: { color: '#fff', fontSize: 16, fontWeight: '700' },
});
