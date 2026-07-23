# 公网主路 · Cloudflare Tunnel(纯出站,零入站端口)

> 2026-07-23 网络裁定:内网 ACL 只放 iPhone→mini 的 8747/8081;无公网 IPv4、
> 无端口映射。公网 HTTPS 一律走 Cloudflare Tunnel——cloudflared 从 Mac mini
> **主动出站**连到 Cloudflare 边缘,玩家访问域名时流量经边缘回灌,路由器与
> 网管侧零要求。原 RUNBOOK 的 Caddy+80/443 映射路线降为备选存档,不再是主路。

## 前置:域名托管到 Cloudflare(一次性)

1. cloudflare.com 免费注册,Add site 填你的域名
2. 按提示到你的域名注册商处,把 NS(域名服务器)改成 Cloudflare 给的两条
3. 等状态变 Active(几分钟到几小时)

## 装隧道(Mac mini,一次性,全程零配置文件)

1. Cloudflare 面板 → Zero Trust → Networks → Tunnels → Create a tunnel
   → 类型选 Cloudflared → 起名(如 zakzok)
2. 环境选 macOS,页面会给你一条**带 token 的安装命令**(形如
   `sudo cloudflared service install eyJh...`)——先装二进制再执行它:

```
curl -L -o /tmp/cf.tgz https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-darwin-arm64.tgz
sudo tar -xzf /tmp/cf.tgz -C /usr/local/bin
sudo cloudflared service install 面板给你的那串token
```

   (service install 会自动装成 LaunchDaemon,开机自启、断线自连。)
3. 回面板同一页 → Public Hostname → Add:
   - Subdomain/Domain:你的域名(或 play.你的域名)
   - Service:`http://localhost:8747`
4. 保存。到这里公网就通了。

## 服务端配套(Mac mini)

- 游戏服务**不需要 --lan**:隧道打的是 localhost,127.0.0.1 监听即可
  (deploy/com.yappa.server.plist 默认就这么绑,更安全)。
- 仓库根 `.env` 加一行(入座链接用域名而不是内网 IP):

```
PUBLIC_BASE_URL=https://你的域名
```

- 重启游戏服务后验证(任何网络下,包括手机蜂窝):

```
curl -s https://你的域名/api/state
```

  返回 JSON 即全通。App 服务器栏填 `https://你的域名`
  (域名定稿后把 app/App.js 的 DEFAULT_SERVER 填上,输入框整体消失)。

## iPhone 内网直连(开发低延迟用,与隧道并存)

- 网管按 iPhone 的**该 SSID 私有 Wi-Fi MAC** 做 DHCP 保留 + ACL 放行
  `iPhone → 192.168.1.20 TCP 8747,8081`。
- iPhone 侧务必:设置 → Wi-Fi → 该网络 (i) → 私有 Wi-Fi 地址 → **固定**
  (别用"轮换",MAC 一换保留和 ACL 全失效)。
- 快枪手/开牌这类毫秒级判定,开发调试优先走内网直连;朋友局一律走域名。

## 故障速查

| 症状 | 查什么 |
|---|---|
| 域名打不开 | 面板 Tunnels 页看隧道状态是否 HEALTHY;`sudo launchctl list | grep cloudflared` |
| 打开是 Cloudflare 错误页 1033 | Public Hostname 的 Service 是否写成 `http://localhost:8747` |
| 通了但入座链接还是内网 IP | `.env` 的 PUBLIC_BASE_URL 没配或服务没重启 |
