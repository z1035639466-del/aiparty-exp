// 道具动画路线 A/B 同屏对比(技术验证件,不进正式产品路径)
//
//   A 预渲染:Blender/Cycles 离线出图 → 手机只管播。画质=离线渲染上限,
//            但轨迹写死、玩家动不了,且带透明通道的高清序列帧很吃包体。
//   B 实时3D:three.js 跑在 expo-gl 上,手机每帧现算。可以甩、可以转、
//            骰子点数能真的随机停,代价是画质掉一档(无全局光、无路径追踪)。
//
// 这一屏把两者放同一台手机上,同样的骰子,让画质差距可以直接用眼睛量。
import { useRef, useState } from "react";
import {
  Image, PanResponder, Pressable, ScrollView, StyleSheet, Text, View,
} from "react-native";
import { GLView } from "expo-gl";
import * as THREE from "three";

// —— 骰面点数贴图:程序化画,不占素材 ——
// RN 里没有 canvas,直接往像素数组里涂圆点再交给 DataTexture。
const FACE = 128;
const PIPS = {
  1: [[0.5, 0.5]],
  2: [[0.27, 0.27], [0.73, 0.73]],
  3: [[0.25, 0.25], [0.5, 0.5], [0.75, 0.75]],
  4: [[0.28, 0.28], [0.72, 0.28], [0.28, 0.72], [0.72, 0.72]],
  5: [[0.26, 0.26], [0.74, 0.26], [0.5, 0.5], [0.26, 0.74], [0.74, 0.74]],
  6: [[0.28, 0.22], [0.28, 0.5], [0.28, 0.78], [0.72, 0.22], [0.72, 0.5], [0.72, 0.78]],
};

function faceTexture(n) {
  const data = new Uint8Array(FACE * FACE * 4);
  const pips = PIPS[n];
  // 幺点大红,其余黑 —— 跟 Cycles 那版同一套配色,方便对比
  const ink = n === 1 ? [176, 26, 26] : [26, 26, 26];
  const r = n === 1 ? 0.17 : 0.1;   // 幺点更大,骰子的老规矩
  for (let y = 0; y < FACE; y++) {
    for (let x = 0; x < FACE; x++) {
      const u = x / FACE, v = y / FACE;
      let hit = 0;
      for (const [px, py] of pips) {
        const d = Math.hypot(u - px, v - py) / r;
        if (d < 1) hit = Math.max(hit, Math.min(1, (1 - d) * 6)); // 边缘软化,免锯齿
      }
      const i = (y * FACE + x) * 4;
      // 白瓷底
      data[i] = 240 - (240 - ink[0]) * hit;
      data[i + 1] = 238 - (238 - ink[1]) * hit;
      data[i + 2] = 232 - (232 - ink[2]) * hit;
      data[i + 3] = 255;
    }
  }
  const t = new THREE.DataTexture(data, FACE, FACE, THREE.RGBAFormat);
  // expo-gl 对 DataTexture 的自动 mipmap 与 pixelStorei 支持不全,关掉免得整帧渲染抛错
  t.generateMipmaps = false;
  t.minFilter = THREE.LinearFilter;
  t.magFilter = THREE.LinearFilter;
  t.flipY = false;
  t.needsUpdate = true;
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

// Box 的六面顺序:+X -X +Y -Y +Z -Z;对面点数相加=7
const FACE_ORDER = [1, 6, 2, 5, 3, 4];

export default function AB({ onBack }) {
  const [fps, setFps] = useState(0);
  const spin = useRef({ x: 0.006, y: 0.011 });      // 当前角速度
  const drag = useRef({ on: false });

  // 甩骰子:拖拽给角速度,松手让它自己转 —— B 路线独有的东西
  const pan = useRef(
    PanResponder.create({
      onStartShouldSetPanResponder: () => true,
      onPanResponderGrant: () => { drag.current.on = true; },
      onPanResponderMove: (_e, g) => {
        spin.current = { x: g.dy * 0.0004, y: g.dx * 0.0004 };
      },
      onPanResponderRelease: () => { drag.current.on = false; },
    })
  ).current;

  const onContextCreate = async (gl) => {
    const w = gl.drawingBufferWidth, h = gl.drawingBufferHeight;
    // three 要一个类 canvas 的壳;expo-gl 只给 context,按惯例喂一个假的
    const renderer = new THREE.WebGLRenderer({
      canvas: {
        width: w, height: h, style: {}, clientWidth: w, clientHeight: h,
        addEventListener() {}, removeEventListener() {},
        getContext: () => gl,
      },
      context: gl,
      antialias: true,
    });
    renderer.setSize(w, h);
    renderer.setClearColor(0x1b2420, 1);   // 跟 Cycles 那版一样的墨绿绒布底
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;

    const scene = new THREE.Scene();
    const cam = new THREE.PerspectiveCamera(38, w / h, 0.1, 100);
    cam.position.set(0, 2.4, 4.2);
    cam.lookAt(0, 0, 0);

    // 打光:一盏主光投影 + 环境光提暗部。离线渲染那边是面光源+全局光,
    // 这里只能拿这套凑 —— 画质差距主要就差在这。
    scene.add(new THREE.AmbientLight(0xffffff, 0.55));
    const key = new THREE.DirectionalLight(0xffffff, 1.6);
    key.position.set(3, 6, 4);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.radius = 4;
    scene.add(key);
    const rim = new THREE.DirectionalLight(0x88aaff, 0.35);
    rim.position.set(-4, 2, -3);
    scene.add(rim);

    // 绒布桌面(接阴影)
    const felt = new THREE.Mesh(
      new THREE.PlaneGeometry(20, 20),
      new THREE.MeshStandardMaterial({ color: 0x24302a, roughness: 0.95 })
    );
    felt.rotation.x = -Math.PI / 2;
    felt.position.y = -0.85;
    felt.receiveShadow = true;
    scene.add(felt);

    const mats = FACE_ORDER.map((n) => new THREE.MeshStandardMaterial({
      map: faceTexture(n), roughness: 0.32, metalness: 0.0,
    }));
    // 诊断:先用不依赖光照与贴图的基础材质确认几何体到底有没有画出来
    const die = new THREE.Mesh(new THREE.BoxGeometry(1.5, 1.5, 1.5),
      new THREE.MeshBasicMaterial({ color: 0xffffff }));
    die.castShadow = true;
    scene.add(die);

    let last = Date.now(), frames = 0, acc = 0;
    const loop = () => {
      requestAnimationFrame(loop);
      const now = Date.now(), dt = now - last; last = now;
      acc += dt; frames++;
      if (acc > 500) { setFps(Math.round((frames * 1000) / acc)); frames = 0; acc = 0; }

      die.rotation.x += spin.current.x;
      die.rotation.y += spin.current.y;
      if (!drag.current.on) {   // 松手后缓慢衰减,像真骰子停下来
        spin.current.x *= 0.995;
        spin.current.y *= 0.995;
      }
      try {
        renderer.render(scene, cam);
      } catch (e) {
        if (!loop.reported) { loop.reported = true; console.log("RENDER-ERR:", String(e)); }
      }
      gl.endFrameEXP();
    };
    loop();
  };

  return (
    <ScrollView style={s.page} contentContainerStyle={{ paddingBottom: 40 }}>
      <Pressable onPress={onBack}><Text style={s.back}>← 回入座页</Text></Pressable>
      <Text style={s.h1}>道具路线 A / B 同屏对比</Text>
      <Text style={s.sub}>同一颗骰子,同样的墨绿绒布底,同一块屏</Text>

      <Text style={s.label}>A · 预渲染(Blender Cycles 离线出图)</Text>
      <Image source={require("./assets/dice_cycles.jpg")} style={s.shot} resizeMode="cover" />
      <Text style={s.note}>
        128 spp 路径追踪 + 降噪。看凹点里的暗部渐变、瓷面的高光过渡、
        三颗骰子之间互相投的软阴影 —— 这些是全局光算出来的,实时渲染给不了。
        代价:这是一张死图,玩家转不动它。
      </Text>

      <Text style={s.label}>B · 实时 3D(three.js on expo-gl)</Text>
      <View style={s.glWrap} {...pan.panHandlers}>
        <GLView style={{ flex: 1 }} onContextCreate={onContextCreate} />
        <View style={s.fps}><Text style={s.fpsText}>{fps} fps</Text></View>
      </View>
      <Text style={s.note}>
        ☝️ 用手指甩它。这就是 B 的全部意义:能转、能停、点数能真的随机。
        但看阴影边缘的硬度、暗部一片死黑没有反弹光、瓷面高光是一块糊的 ——
        差距就在这里。
      </Text>
    </ScrollView>
  );
}

const s = StyleSheet.create({
  page: { flex: 1, backgroundColor: "#14141c", paddingTop: 54, paddingHorizontal: 14 },
  back: { color: "#7788aa", fontSize: 14, marginBottom: 10 },
  h1: { color: "#ffd54a", fontSize: 22, fontWeight: "800" },
  sub: { color: "#889", fontSize: 13, marginBottom: 16 },
  label: { color: "#fff", fontSize: 16, fontWeight: "700", marginTop: 18, marginBottom: 6 },
  shot: { width: "100%", height: 220, borderRadius: 12 },
  glWrap: { width: "100%", height: 300, borderRadius: 12, overflow: "hidden",
    backgroundColor: "#1b2420" },
  fps: { position: "absolute", top: 8, right: 10, backgroundColor: "#0008",
    paddingHorizontal: 8, paddingVertical: 3, borderRadius: 8 },
  fpsText: { color: "#8fb", fontSize: 12, fontWeight: "700" },
  note: { color: "#99a", fontSize: 13, lineHeight: 19, marginTop: 8 },
});
