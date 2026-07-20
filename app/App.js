// Yappa v0 · 两台手机的真人局客户端(Expo / React Native)
// 服务端 = 现有引擎 HTTP API(Mac 上 python -m modeb.simulator --lan)。
// 本客户端只消费 /api/view(自己那台手机该看的)与 /api/event(自己的动作)——
// 防偷看在服务端成立,客户端天然拿不到别人的底牌。
import { useEffect, useRef, useState } from "react";
import {
  Alert, KeyboardAvoidingView, Platform, Pressable, ScrollView,
  StyleSheet, Text, TextInput, View,
} from "react-native";
import { StatusBar } from "expo-status-bar";
import * as Haptics from "expo-haptics";
import { useKeepAwake } from "expo-keep-awake";

const POLL_MS = 900;

export default function App() {
  useKeepAwake(); // 快枪手对峙期间息屏=判负,整局常亮
  const [base, setBase] = useState("");
  const [me, setMe] = useState("");
  const [joined, setJoined] = useState(false);
  const [view, setView] = useState(null);
  const [err, setErr] = useState("");
  const [say, setSay] = useState("");
  const [dueled, setDueled] = useState(false); // 本次对决我开过枪了
  const prevRef = useRef({ inbox: 0, drawn: false });

  const api = async (path, body) => {
    const r = await fetch(base.replace(/\/$/, "") + path, body ? {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    } : undefined);
    return r.json();
  };
  const sendEvent = (ev) =>
    api("/api/event", { ...ev, player: me }).catch(() => setErr("事件没发出去,再点一次"));

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
        prevRef.current = { inbox: (v.inbox || []).length, drawn: !!(v.duel && v.duel.drawn) };
        setView(v);
      } catch (e) { if (alive) setErr("连不上服务器:" + e.message); }
    };
    tick();
    const t = setInterval(tick, POLL_MS);
    return () => { alive = false; clearInterval(t); };
  }, [joined]);

  if (!joined) {
    return (
      <View style={[s.page, s.center]}>
        <StatusBar style="light" />
        <Text style={s.logo}>Yappa</Text>
        <Text style={s.dim}>局长在等你入座</Text>
        <TextInput style={s.input} placeholder="服务器,如 http://192.168.1.5:8747"
          placeholderTextColor="#667" autoCapitalize="none" autoCorrect={false}
          value={base} onChangeText={setBase} />
        <TextInput style={s.input} placeholder="你的座位名(开局时定的)"
          placeholderTextColor="#667" value={me} onChangeText={setMe} />
        <Pressable style={s.bigBtn} onPress={async () => {
          try {
            const v = await api("/api/view?player=" + encodeURIComponent(me.trim()));
            if (v.error) { Alert.alert("入座失败", v.error); return; }
            setMe(me.trim()); setJoined(true);
          } catch (e) { Alert.alert("连不上", String(e.message)); }
        }}>
          <Text style={s.bigBtnText}>入座</Text>
        </Pressable>
      </View>
    );
  }

  const v = view || {};
  const inDuel = v.duel && v.duel.vs && v.duel.vs.includes(me);

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
            <Text style={s.dim}>枪响前碰 = 抢跑判负</Text>
          </>
        )}
      </View>
    );
  }

  return (
    <KeyboardAvoidingView style={s.page} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <StatusBar style="light" />
      <View style={s.topbar}>
        <Text style={s.topText}>{me} · 第{v.round ?? 0}轮 · 余{Math.max(0, Math.round(v.time_left_min ?? 0))}分</Text>
        {v.now_playing ? <Text style={s.topMusic}>🎵 {v.now_playing}</Text> : null}
      </View>
      {err ? <Text style={s.err}>{err}</Text> : null}
      {v.finished ? <Text style={s.finish}>🏁 本局已收</Text> : null}

      <ScrollView style={s.feed} contentContainerStyle={{ paddingBottom: 12 }}>
        {(v.recent || []).map((t, i) => (
          <View key={i} style={s.turn}>
            {t.host ? <Text style={s.host}>🎩 {t.host}</Text> : null}
            {(t.shown || []).map((c, j) => <Text key={j} style={s.shown}>📢 {c}</Text>)}
            {(t.table || []).map((e, j) => (
              <Text key={j} style={s.tableEv}>
                {e.player}{e.note ? ` ${e.note}` : e.text ? `:「${e.text}」` : e.value ? ` → ${e.value}` : ` · ${e.type}`}
              </Text>
            ))}
          </View>
        ))}
      </ScrollView>

      {(v.inbox || []).length > 0 && (
        <View style={s.inboxBox}>
          <Text style={s.inboxTitle}>📬 只有你能看到</Text>
          {v.inbox.slice(-3).map((x, i) => <Text key={i} style={s.inboxItem}>{x}</Text>)}
        </View>
      )}

      {v.open_ask && (
        <View style={s.askBox}>
          <Text style={s.askText}>🎤 {v.open_ask.asked === me ? "问你" : `问${v.open_ask.asked}`}:{v.open_ask.prompt}</Text>
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
      <Pressable onPress={() => Alert.alert("安全退出", "零代价退出当前环节,确定?", [
        { text: "再想想" },
        { text: "退出这轮", onPress: () => sendEvent({ type: "optout" }) },
      ])}>
        <Text style={s.optout}>安全退出</Text>
      </Pressable>
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
  topbar: { flexDirection: "row", justifyContent: "space-between", marginBottom: 6 },
  topText: { color: "#aab", fontSize: 13 },
  topMusic: { color: "#8fb", fontSize: 13 },
  err: { color: "#f66", fontSize: 13, marginBottom: 4 },
  finish: { color: "#ffd54a", fontSize: 16, fontWeight: "700", marginVertical: 6 },
  feed: { flex: 1 },
  turn: { marginBottom: 10 },
  host: { color: "#fff", fontSize: 17, lineHeight: 24, marginBottom: 2 },
  shown: { color: "#ffd54a", fontSize: 16, marginVertical: 2 },
  tableEv: { color: "#99a", fontSize: 13, marginLeft: 8 },
  inboxBox: { backgroundColor: "#2a2438", borderRadius: 12, padding: 10, marginVertical: 6,
    borderWidth: 1, borderColor: "#5a4a8a" },
  inboxTitle: { color: "#c9b8ff", fontSize: 12, marginBottom: 4 },
  inboxItem: { color: "#fff", fontSize: 16, marginVertical: 1 },
  askBox: { backgroundColor: "#1e2a38", borderRadius: 12, padding: 10, marginVertical: 6 },
  askText: { color: "#cde", fontSize: 15, marginBottom: 6 },
  row: { flexDirection: "row", gap: 8, marginVertical: 5, alignItems: "center" },
  optBtn: { backgroundColor: "#31506e", borderRadius: 10, paddingVertical: 8, paddingHorizontal: 14 },
  optText: { color: "#fff", fontSize: 15 },
  sigBtn: { flex: 1, borderRadius: 12, paddingVertical: 14, alignItems: "center" },
  sigText: { color: "#fff", fontSize: 16, fontWeight: "700" },
  sayBtn: { backgroundColor: "#31506e", borderRadius: 10, padding: 12 },
  optout: { color: "#556", fontSize: 12, textAlign: "center", marginVertical: 8 },
  duelVs: { color: "#fff", fontSize: 26, fontWeight: "800", marginBottom: 30 },
  duelWait: { color: "#eee", fontSize: 20, marginBottom: 8 },
  drawBtn: { backgroundColor: "#ffd54a", width: 260, height: 260, borderRadius: 130,
    alignItems: "center", justifyContent: "center" },
  drawText: { fontSize: 80, fontWeight: "900", color: "#7a1010" },
});
