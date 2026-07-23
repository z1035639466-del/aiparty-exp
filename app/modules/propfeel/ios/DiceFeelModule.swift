import AVFoundation
import CoreHaptics
import CoreMotion
import ExpoModulesCore
import QuartzCore

/**
 摇骰子的「手感」—— 不含任何 3D。

 大话骰摇盅时骰子是盖着的,玩家全程看不见。所以手感 100% 由震动构成,
 渲染什么都不重要。

 关键在于不能用罐头震动。expo-haptics 那种 impactLight/Medium 是固定波形,
 连打几下就露馅,像手机在响而不像骰子在滚。
 Core Haptics 可以逐颗合成瞬态事件,每颗骰子撞壁都是独立的一击,
 强度、锐度、时间间隔全都带随机 —— 这才是"一把骰子"而不是"一个震动"。

 【播放器必须复用】
 早期版本每次撞击都 makePlayer 新建一个播放器。Core Haptics 对同时存活的
 pattern player 有硬上限,连摇六十来次就再也建不出来,start() 开始抛错 ——
 表现就是"摇着摇着突然没反应了,也不报错"。
 现在改成开摇时预建一组播放器循环复用,力度用 hapticIntensityControl
 动态参数实时调,播放器总数恒定,摇多久都不会耗尽。
 */
public class DiceFeelModule: Module {
  private var engine: CHHapticEngine?
  private let motion = CMMotionManager()

  // ---- 音频:这是基线,不是增强 ----
  // 规格 §9「基线 = 点击+视觉+音效,任何道具仅靠基线即完整可玩」。
  // 震动是增强层 —— 安卓没有 Core Haptics、低电量会关震动、用户可能关了触感反馈,
  // 那些情况下【音效必须照常响】。所以音频引擎完全独立于 haptic,各走各的。
  // 【为什么不用 AVAudioEngine】早期版本用 AVAudioEngine + PlayerNode 节点图,
  // 连报废两次(引擎起了不出声 + 状态事件不达)。短音效用不上节点图 ——
  // 换成 AVAudioPlayer 池:预加载、prepareToPlay、直接 play(),最稳。
  // 一个 player 正在放时再 play 会重头开始(不叠),所以用 8 个实例轮流放,
  // 相邻两次撞击落在不同实例上,自然重叠(真骰子就是叠着响的)。
  private var clickPlayers: [AVAudioPlayer] = []
  private var settlePlayer2: AVAudioPlayer?
  private var revealPlayer2: AVAudioPlayer?
  private var clickPlayerIdx = 0
  private var audioReady = false
  // 状态走同步轮询,不走事件 —— onAudioStatus 事件两版都没到过 JS,
  // 而同步 Function(isSupported)一直好用。不再赌事件,JS 直接拉这个字典。
  private var lastAudioStatus: [String: Any] = ["stage": "not-started"]

  // 预建的撞击播放器,按[强度档][变体]组织。
  // 分档是因为光靠 hapticIntensityControl 调强度,动态范围只有 0~1 一点点头顶,
  // 手上分辨不出来。真正能被感知的是"撞击颗数"变化 ——
  // 轻摇两三颗、猛摇五颗加重击,这个差别比调强度明显得多。
  // 4 档 × 3 变体 = 12 个播放器,总数恒定,不会像上一版那样耗尽。
  private var rattleTiers: [[CHHapticAdvancedPatternPlayer]] = []
  private static let tierCount = 4
  private static let variantCount = 3
  private var settlePlayer: CHHapticAdvancedPatternPlayer?
  private var revealPlayer: CHHapticAdvancedPatternPlayer?
  private var rattleIdx = 0
  private var currentDiceCount = 5

  private var energy: Double = 0
  private var lastRattle: CFTimeInterval = 0
  private var running = false
  private var rattleCount = 0
  private var lastTier = 0
  private var armed = false            // 摇够了(揭盅已解锁)
  private var settleSoundPlayed = false // 落定音只播一次
  private var quietFrames = 0          // 连续低幅度帧数,判定停手

  public func definition() -> ModuleDefinition {
    Name("DiceFeel")

    Events("onShakeTick", "onSettled", "onHapticError", "onAudioStatus")

    Function("isSupported") { () -> Bool in
      CHHapticEngine.capabilitiesForHardware().supportsHaptics
    }

    // JS 轮询这个拿音频状态(同步,绕开事件通路)
    Function("audioStatusSync") { () -> [String: Any] in
      self.lastAudioStatus
    }

    AsyncFunction("start") { (diceCount: Int) in
      // ★ 必须回主线程:AsyncFunction 默认在后台线程执行,而
      //   ① AVAudioEngine 在非主线程启动 = 经典的"引擎起了却不出声"
      //   ② sendEvent 从后台线程发不保证送达 JS(这就是状态一直显"未启动"的原因)
      //   之前能工作的震动事件都是从 CMMotionManager 的 .main 闭包里发的,所以没露这个坑。
      DispatchQueue.main.async {
        self.currentDiceCount = max(1, min(diceCount, 8))
        self.setupAudio()                 // 基线,先起
        try? self.startEngine()           // 增强,失败不影响基线
        try? self.buildPlayers()
        self.startMotion()
      }
    }

    AsyncFunction("stop") {
      DispatchQueue.main.async {
        self.stopMotion()
        self.engine?.stop()
        self.clickPlayers.forEach { $0.stop() }
      }
    }

    AsyncFunction("rattle") { (intensity: Double, diceCount: Int) in
      DispatchQueue.main.async { self.playRattle(norm: intensity) }
    }

    AsyncFunction("settle") {
      DispatchQueue.main.async {
        self.fire(self.settlePlayer, intensity: 1.0)
        self.playSettleSound()
      }
    }
    AsyncFunction("reveal") {
      DispatchQueue.main.async {
        self.fire(self.revealPlayer, intensity: 1.0)
        self.playRevealSound()
      }
    }

    /// 诊断用:摇了多少次、播放器还在不在
    Function("stats") { () -> [String: Any] in
      [
        "rattleCount": self.rattleCount,
        "players": self.rattleTiers.reduce(0) { $0 + $1.count },
        "engineAlive": self.engine != nil,
      ]
    }

    OnDestroy {
      self.stopMotion()
      self.engine?.stop()
    }
  }

  // MARK: - 音频（基线）

  private func setupAudio() {
    lastAudioStatus["stage"] = "entered"
    guard !audioReady else { return }

    var status: [String: Any] = ["stage": "done"]

    // 裸 .playback:骰子声是玩家主动摇出来的道具音,必须无视静音键
    // (派对现场手机常年静音)。错误不 try? 吞,收集起来回报到屏幕。
    let session = AVAudioSession.sharedInstance()
    do {
      try session.setCategory(.playback, mode: .default, options: [])
      try session.setActive(true)
      status["session"] = "ok"
    } catch {
      status["session"] = "FAIL: \(error.localizedDescription)"
    }

    func load(_ name: String) -> AVAudioPlayer? {
      guard let url = Bundle.main.url(forResource: name, withExtension: "wav") else { return nil }
      let p = try? AVAudioPlayer(contentsOf: url)
      p?.prepareToPlay()
      return p
    }

    // 8 个撞击实例(4 个音色各 2 份)轮流放 → 相邻撞击落在不同实例上,能重叠
    clickPlayers = (0..<8).compactMap { load("dice_click_\($0 % 4)") }
    settlePlayer2 = load("dice_settle")
    revealPlayer2 = load("dice_reveal")
    status["clicks"] = clickPlayers.count
    status["settle"] = settlePlayer2 != nil
    status["reveal"] = revealPlayer2 != nil
    status["engine"] = "AVAudioPlayer"

    audioReady = true
    lastAudioStatus = status
    // 自检音:装载完立刻响一声。点"开始摇"就该听到"嗒"——
    // 听到了 = 音频通路全通,不用再看任何状态文字。
    playClick(norm: 0.8)
  }

  /// 播一颗骰子撞击声,按力度选切片。
  /// clickPlayers 布局:8 个实例 = 4 段切片(0最轻…3最响)各 2 份,
  /// 切片 s 在下标 s 和 s+4。轻摇取安静切片、猛摇取响的,音量再按力度细调。
  private func playClick(norm: Double) {
    guard audioReady, clickPlayers.count >= 8 else { return }
    let t = max(0.0, min(norm, 1.0))
    let slice = min(3, Int(t * 4))                  // 力度 → 切片 0..3
    // 该切片的两个实例交替用,让相邻同力度撞击能重叠不打断
    let inst = slice + (clickPlayerIdx % 2) * 4
    clickPlayerIdx += 1
    let p = clickPlayers[inst]
    p.volume = Float(0.45 + t * 0.55) * Float.random(in: 0.9...1.0)
    p.currentTime = 0
    p.play()
  }

  private func playSettleSound() {
    settlePlayer2?.currentTime = 0
    settlePlayer2?.play()
  }

  private func playRevealSound() {
    revealPlayer2?.currentTime = 0
    revealPlayer2?.play()
  }

  // MARK: - 引擎（增强）

  private func startEngine() throws {
    guard CHHapticEngine.capabilitiesForHardware().supportsHaptics else { return }
    if engine == nil {
      // 共享同一个 AVAudioSession:音频侧 setCategory(.playback)+setActive 会打断
      // 独立会话上的 haptic 引擎(截图里的 -4805 engineNotRunning 就是这么来的)。
      // 挂到同一个 session 上,两边不再互相打断。
      let e = try CHHapticEngine(audioSession: AVAudioSession.sharedInstance())
      // 系统会在来电、切后台等时机回收引擎,不自恢复的话摇到一半就哑了
      e.resetHandler = { [weak self] in
        guard let self else { return }
        try? self.engine?.start()
        try? self.buildPlayers()      // 引擎重置后旧播放器全失效,必须重建
      }
      e.stoppedHandler = { _ in }
      e.isAutoShutdownEnabled = false
      engine = e
    }
    try engine?.start()
  }

  /// 预建全部播放器。只在开摇和引擎重置时调用。
  private func buildPlayers() throws {
    guard let engine else { return }
    rattleTiers.removeAll()
    // 每档 3 个变体轮着放:既避免听感重复,也让相邻两次撞击落在不同播放器上,
    // 短模式重叠时不会互相打断
    for tier in 0..<Self.tierCount {
      var variants: [CHHapticAdvancedPatternPlayer] = []
      for _ in 0..<Self.variantCount {
        let pattern = try CHHapticPattern(events: makeRattleEvents(tier: tier), parameters: [])
        variants.append(try engine.makeAdvancedPlayer(with: pattern))
      }
      rattleTiers.append(variants)
    }
    settlePlayer = try engine.makeAdvancedPlayer(with: CHHapticPattern(events: makeSettleEvents(), parameters: []))
    revealPlayer = try engine.makeAdvancedPlayer(with: CHHapticPattern(events: makeRevealEvents(), parameters: []))
  }

  /// 统一的播放入口:动态调强度 + 出错自愈
  private func fire(_ player: CHHapticAdvancedPatternPlayer?, intensity: Float) {
    guard let player else { return }
    do {
      try player.sendParameters([
        CHHapticDynamicParameter(parameterID: .hapticIntensityControl,
                                 value: max(0.1, min(intensity, 1.0)),
                                 relativeTime: 0),
      ], atTime: CHHapticTimeImmediate)
      try player.start(atTime: CHHapticTimeImmediate)
    } catch {
      // 引擎被打断(常见 -4805):重启后【重试当次】,这一下震动不丢。
      try? engine?.start()
      if (try? player.start(atTime: CHHapticTimeImmediate)) != nil { return }
      // 重试也失败才重建 + 上报
      try? buildPlayers()
      sendEvent("onHapticError", ["message": String(describing: error)])
    }
  }

  // MARK: - 波形

  /**
   一群骰子撞壁。

   核心是"不齐":N 颗骰子在 0~85ms 内先后撞上,每颗的强度和锐度都不同。
   所有事件对齐在同一时刻的话,手上感觉到的是"一下"而不是"一把"。

   强度按满档写,实际播放时用 hapticIntensityControl 往下压 ——
   这样动态范围最大,不会一开始就顶到天花板。
   */
  private func makeRattleEvents(tier: Int) -> [CHHapticEvent] {
    // 档位决定"这一把有多少颗骰子在撞、撞得多实"
    //   0 轻摇  : 2 颗, 无重击, 短共鸣  —— 手腕轻轻带一下
    //   1 中等  : 3 颗, 无重击
    //   2 用力  : 5 颗 + 1 记重击
    //   3 猛摇  : 5 颗 + 3 记重击, 长共鸣 —— 整盅在手里翻
    let hits = [2, 3, currentDiceCount, currentDiceCount][min(tier, 3)]
    let heavy = [0, 0, 1, 3][min(tier, 3)]
    let spread = [0.055, 0.07, 0.085, 0.095][min(tier, 3)]
    let resonance: Float = [0.35, 0.5, 0.7, 0.85][min(tier, 3)]
    let resonanceDur = [0.08, 0.10, 0.13, 0.17][min(tier, 3)]

    var events: [CHHapticEvent] = []

    for _ in 0..<hits {
      events.append(CHHapticEvent(
        eventType: .hapticTransient,
        parameters: [
          .init(parameterID: .hapticIntensity, value: Float.random(in: 0.75...1.0)),
          // 锐度随档位走:摇得越猛撞得越脆
          .init(parameterID: .hapticSharpness,
                value: Float.random(in: 0.6...0.85) + Float(tier) * 0.04),
        ],
        relativeTime: Double.random(in: 0...spread)
      ))
    }

    // 重击:每一把里"撞得特别实"的那几颗,层次感来自这里
    for _ in 0..<heavy {
      events.append(CHHapticEvent(
        eventType: .hapticTransient,
        parameters: [
          .init(parameterID: .hapticIntensity, value: 1.0),
          .init(parameterID: .hapticSharpness, value: Float.random(in: 0.4...0.6)),
        ],
        relativeTime: Double.random(in: 0...spread * 0.6)
      ))
    }

    // 盅体共鸣 = 厚度
    events.append(CHHapticEvent(
      eventType: .hapticContinuous,
      parameters: [
        .init(parameterID: .hapticIntensity, value: resonance),
        .init(parameterID: .hapticSharpness, value: 0.3),
      ],
      relativeTime: 0,
      duration: resonanceDur
    ))

    return events
  }

  private func makeSettleEvents() -> [CHHapticEvent] {
    [
      CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: 1.0),
        .init(parameterID: .hapticSharpness, value: 0.35),
      ], relativeTime: 0),
      CHHapticEvent(eventType: .hapticContinuous, parameters: [
        .init(parameterID: .hapticIntensity, value: 0.6),
        .init(parameterID: .hapticSharpness, value: 0.2),
      ], relativeTime: 0, duration: 0.15),
      CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: 0.5),
        .init(parameterID: .hapticSharpness, value: 0.5),
      ], relativeTime: 0.13),
      CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: 0.26),
        .init(parameterID: .hapticSharpness, value: 0.6),
      ], relativeTime: 0.21),
    ]
  }

  private func makeRevealEvents() -> [CHHapticEvent] {
    [
      CHHapticEvent(eventType: .hapticContinuous, parameters: [
        .init(parameterID: .hapticIntensity, value: 0.7),
        .init(parameterID: .hapticSharpness, value: 0.7),
      ], relativeTime: 0, duration: 0.18),
      CHHapticEvent(eventType: .hapticTransient, parameters: [
        .init(parameterID: .hapticIntensity, value: 1.0),
        .init(parameterID: .hapticSharpness, value: 0.9),
      ], relativeTime: 0.18),
    ]
  }

  /// norm: 0~1 的归一化摇动幅度
  private func playRattle(norm: Double) {
    guard !rattleTiers.isEmpty else { return }
    let t = max(0, min(norm, 1))
    // 选档:颗数/重击/共鸣一起变,这是手上真正分辨得出来的差异
    let tier = min(Int(t * Double(Self.tierCount)), Self.tierCount - 1)
    let variants = rattleTiers[tier]
    let p = variants[rattleIdx % variants.count]
    rattleIdx += 1
    rattleCount += 1
    lastTier = tier
    // 档内再用动态参数细调,下限 0.6
    fire(p, intensity: Float(0.6 + t * 0.4))
  }

  // MARK: - 摇动检测

  private func startMotion() {
    guard motion.isDeviceMotionAvailable, !running else { return }
    running = true
    energy = 0
    rattleCount = 0
    armed = false
    settleSoundPlayed = false
    quietFrames = 0
    motion.deviceMotionUpdateInterval = 1.0 / 60.0
    motion.startDeviceMotionUpdates(to: .main) { [weak self] dm, _ in
      guard let self, let dm else { return }
      let a = dm.userAcceleration
      let mag = sqrt(a.x * a.x + a.y * a.y + a.z * a.z)
      let now = CACurrentMediaTime()

      self.energy = max(0, self.energy * 0.94 + mag * 0.6)

      let norm = min(mag / 2.6, 1.0)
      let interval = max(0.05, 0.2 - min(mag, 2.5) * 0.058)
      if mag > 0.3, now - self.lastRattle > interval {
        self.lastRattle = now
        self.playClick(norm: norm)
        self.playRattle(norm: norm)
        self.sendEvent("onShakeTick", [
          "magnitude": mag, "energy": self.energy, "tier": self.lastTier,
        ])
      }

      // 摇够了 → 只解锁揭盅按钮(一次),【不】在这里播落定音。
      // 之前在能量到阈值就播,猛摇时手还在使劲摇、撞击声正密,落定音插进来撞车,
      // 而且能量清零后反复触发。落定音必须等真的停手。
      if self.energy > 14 && !self.armed {
        self.armed = true
        self.sendEvent("onSettled", [:])
      }

      // 落定音:解锁后,检测到手真停下(连续低幅度 ~130ms)才播,且只播一次。
      // 此时 mag 已低于撞击阈值(0.3),不会和撞击声撞车。
      if self.armed && !self.settleSoundPlayed {
        if mag < 0.32 {
          self.quietFrames += 1
          if self.quietFrames >= 8 {
            self.settleSoundPlayed = true
            self.playSettleSound()
            self.fire(self.settlePlayer, intensity: 1.0)
          }
        } else {
          self.quietFrames = 0
        }
      }
    }
  }

  private func stopMotion() {
    guard running else { return }
    running = false
    motion.stopDeviceMotionUpdates()
  }
}
