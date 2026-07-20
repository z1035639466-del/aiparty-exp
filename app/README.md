# Yappa App v0(Expo / React Native)

两台手机的真人局客户端。服务端就是现有引擎——不重写、不另起后端。

## 跑起来(Mac)

```bash
# 1. 服务端(引擎 + 活局长),同一 Wi-Fi
export ANTHROPIC_API_KEY=...      # 或国产家的 key
python3 -m modeb.simulator --lan   # 记下打印的局域网地址

# 2. 驾驶舱开局(电脑浏览器,v0 开局仍在电脑;手机只入座)
#    llm 驱动、按钮档、填好两个座位名、✔勾上「自动回合」再开局
#    ——回合发动机在服务器里自驱,开完局这个页面可以关、电脑可以合盖

# 3. App(首次)
cd app && npm install
npx expo install --fix   # 对齐本机 SDK 版本(版本漂移就跑这条)
npx expo start           # 出二维码

# 4. 两台 iPhone 装 Expo Go(App Store 免费)→ 相机扫码 → 输入
#    服务器地址(--lan 打印的那个)和自己的座位名 → 入座
```

## v0 已有

轮询自己的 `/api/view`(看不到别人的底牌,服务端保证)/ 主持词与公开卡 /
📬 私密收件(到件震动)/ 🎤 问询选项一键答 / ✅完成 🍺认罚 大按钮 /
💬桌上 🎙局长 两路发言 / 快枪手全屏对峙(枪响重震 + 大按钮,息屏常亮防判负)/
低调的安全退出。

## 走向正式包

Expo Go 是开发期形态;出真安装包:`npx eas build`(开发者账户已备)→
TestFlight / APK。到那一步再迁,客户端代码不用改。
