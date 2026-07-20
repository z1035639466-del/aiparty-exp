// Yappa v0 · 两台手机的真人局客户端(Expo / React Native)
// 服务端 = 现有引擎 HTTP API(Mac 上 python -m modeb.simulator --lan)。
// 本客户端只消费 /api/view(自己那台手机该看的)与 /api/event(自己的动作)——
// 防偷看在服务端成立,客户端天然拿不到别人的底牌。
// 手机开局页(开工单欠账补):/api/start 也从手机发,不必回电脑驾驶舱;
// 判定=抽帧走照片通道:视频先在本机抽帧转 base64,仍是 /api/photo 那条口子。
import { useEffect, useRef, useState } from "react";
import {
  Alert, KeyboardAvoidingView, Platform, Pressable, ScrollView,
  StyleSheet, Text, TextInput, View,
} from "react-native";
import { StatusBar } from "expo-status-bar";
import { Audio } from "expo-av";
import * as FileSystem from "expo-file-system";
import * as Haptics from "expo-haptics";
import * as ImagePicker from "expo-image-picker";
import { useKeepAwake } from "expo-keep-awake";
import * as VideoThumbnails from "expo-video-thumbnails";

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
  const [recording, setRecording] = useState(null); // 录音判定进行中的 Recording 对象
  const prevRef = useRef({ inbox: 0, drawn: false });

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
      <KeyboardAvoidingView style={s.page} behavior={Platform.OS === "ios" ? "padding" : undefined}>
        <StatusBar style="light" />
        <ScrollView contentContainerStyle={[s.center, { flexGrow: 1, paddingVertical: 44 }]}
          keyboardShouldPersistTaps="handled">
          <Text style={s.logo}>Yappa</Text>
          <Text style={s.dim}>{creating ? "开一局新的" : "局长在等你入座"}</Text>
          <TextInput style={s.input} placeholder="服务器,如 http://192.168.1.5:8747"
            placeholderTextColor="#667" autoCapitalize="none" autoCorrect={false}
            value={base} onChangeText={setBase} />

          {creating ? (
            <>
              <TextInput style={s.input} placeholder="座位名(逗号分隔,至少2个,如 疯子明,小静)"
                placeholderTextColor="#667" value={seats} onChangeText={setSeats} />
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
                    // 千问一家默认(房主定案):一把 DASHSCOPE key 全通,手机开局零输入
                    provider: "qwen", host_model: "qwen3.7-max", seat_model: "qwen3.6-flash",
                    occasion: occasion.trim(),
                    playlist: playlist.split(",").map((t) => t.trim()).filter(Boolean),
                    bots,
                    // 主持模型/provider 不填:沿用服务端默认(Hub.start 的 anthropic/sonnet)
                  });
                  if (res.error) { Alert.alert("开局失败", res.error); return; }
                  setMe(seatList[0]); setJoined(true); setCreating(false);
                } catch (e) {
                  Alert.alert("连不上", String(e.message));
                } finally {
                  setStarting(false);
                }
              }}>
                <Text style={s.bigBtnText}>{starting ? "开局中…" : "开新局"}</Text>
              </Pressable>
              <Pressable onPress={() => setCreating(false)}>
                <Text style={s.optout}>← 返回入座</Text>
              </Pressable>
            </>
          ) : (
            <>
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
          <Text style={s.inboxTitle}>🏁 终局战报</Text>
          {Object.entries(v.scores || {}).sort((a, b) => b[1] - a[1]).map(([p, sc]) => (
            <Text key={p} style={s.inboxItem}>{p}:{sc}</Text>
          ))}
        </View>
      )}

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
        <Pressable onPress={() => Alert.alert("安全退出", "零代价退出当前环节,确定?", [
          { text: "再想想" },
          { text: "退出这轮", onPress: () => sendEvent({ type: "optout" }) },
        ])}>
          <Text style={s.optout}>安全退出</Text>
        </Pressable>
        <Pressable onPress={async () => {   // 开局拍一张现场:实物清单+场景速写自动进局
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
  photoBtn: { backgroundColor: "#4a3a10", borderColor: "#ffd54a", borderWidth: 1,
    borderRadius: 12, padding: 12, marginVertical: 6 },
  photoText: { color: "#ffd54a", fontSize: 16, fontWeight: "700" },
  photoSub: { color: "#bb9", fontSize: 12, marginTop: 2 },
  photoActionBtn: { flex: 1, backgroundColor: "#ffd54a", borderRadius: 10,
    paddingVertical: 10, alignItems: "center" },
  photoActionText: { color: "#222", fontSize: 15, fontWeight: "700" },
  settleBox: { backgroundColor: "#20242c", borderRadius: 12, padding: 10, marginVertical: 6,
    borderWidth: 1, borderColor: "#ffd54a" },
  duelVs: { color: "#fff", fontSize: 26, fontWeight: "800", marginBottom: 30 },
  duelWait: { color: "#eee", fontSize: 20, marginBottom: 8 },
  drawBtn: { backgroundColor: "#ffd54a", width: 260, height: 260, borderRadius: 130,
    alignItems: "center", justifyContent: "center" },
  drawText: { fontSize: 80, fontWeight: "900", color: "#7a1010" },
});
