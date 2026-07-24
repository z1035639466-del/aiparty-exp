Pod::Spec.new do |s|
  s.name           = 'Propfeel'
  s.version        = '1.0.0'
  s.summary        = 'Yappa 道具手感:骰盅/炸弹/左轮的 Core Haptics + 音频 + 手电原生模块'
  s.description    = '四件道具原语里需要原生手感的三件(额头牌走 expo-haptics 不在此)。零 3D:手机是私密信道+判定仪器,不是游戏屏幕。'
  s.author         = ''
  s.homepage       = 'https://docs.expo.dev/modules/'
  # 必须 ≤ App 的部署目标(ios/Podfile 的 platform,现为 15.1)。高于它 expo 自动链接会
  # 【静默跳过】这个 pod——不报错,只是 ExpoModulesProvider 里没有,JS 端才炸
  # "Cannot find native module 'DiceFeel'"。16.4 是 RealityKit 模板的遗留数字,
  # 这里的 Core Haptics/AVFoundation/CoreMotion 都是 iOS 13 就有的,用不着那么高。
  s.platforms      = { :ios => '15.1' }
  s.source         = { git: '' }
  s.static_framework = true

  s.dependency 'ExpoModulesCore'

  s.pod_target_xcconfig = {
    'DEFINES_MODULE' => 'YES',
  }

  s.source_files = "**/*.{h,m,mm,swift,hpp,cpp}"
  # 撞击/爆炸/枪声 Foley 直接拷进 App 包根,Bundle.main.url(forResource:) 取
  s.resources    = ["*.wav"]
end
