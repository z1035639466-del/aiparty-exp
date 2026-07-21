# Yappa 公网部署手册(从零到手机随处可连)

面向房主本人,不需要任何运维经验。照着从上往下做一遍,大约 30–60 分钟
(大头是等 DNS 生效)。做完之后:Mac mini 开机即起服务,iPhone 上的
Expo App 和玩家手机浏览器在 4G / 任何 Wi-Fi 都能直接连,不再受局域网
防火墙和 AP 隔离折磨。

架构一句话:玩家手机 → `https://你的域名`(Caddy,自动 HTTPS)→ 本机
`127.0.0.1:8747`(Yappa 游戏服务)。两个服务都由 launchd 常驻,崩了自动拉起。

---

## 第 1 步:买个域名

随便哪家注册商都行(Cloudflare、Namecheap、阿里云、腾讯云……),买最便宜的
后缀即可,一年几十块。买完你会得到一个「DNS 解析设置」页面,下一步要用。

下文所有 `yappa.example.com` 都指代你买的域名(可以直接用主域名,也可以用
一个子域名,比如 `party.你的域名`)。

## 第 2 步:让域名指向你家的网

### 2.1 查自己的公网 IP

在 Mac mini 的终端里执行:

```
curl -4 ifconfig.me
```

会打印一个 IP,比如 `123.45.67.89`。**先做个判断**:再打开路由器管理页
(一般是 http://192.168.1.1 或 http://192.168.31.1),看路由器 WAN 口的 IP:

- 如果 WAN 口 IP 和 `curl` 查到的一致 → 你有公网 IP,走 2.2 + 2.3。
- 如果 WAN 口是 `100.64.x.x` / `10.x.x.x` / `172.16-31.x.x` 这类内网地址,
  和 `curl` 查到的不一样 → 你是 CGNAT(运营商没给公网 IP),端口映射没用,
  直接跳到 2.4 的 cloudflared 方案(或先打运营商客服电话要一个公网 IP,
  家宽通常报备一下就给)。

### 2.2 DNS A 记录

到注册商的 DNS 解析页面,添加一条记录:

| 类型 | 主机记录(名称) | 值 |
|---|---|---|
| A | `@`(用主域名)或 `party`(用子域名) | 你的公网 IP |

保存后等 5–30 分钟,在终端验证(输出应是你的公网 IP):

```
nslookup yappa.example.com
```

注意:家宽公网 IP 可能隔几天变一次。变了就回来改这条 A 记录;嫌麻烦可以
以后再配 DDNS(动态域名),第一次部署先不管。

### 2.3 路由器端口映射(有公网 IP 的走这条)

在路由器管理页找「端口映射 / 端口转发 / 虚拟服务器 / Port Forwarding」,
加两条规则,都指向 Mac mini 的局域网 IP(在 Mac 上执行
`ipconfig getifaddr en0` 可查,顺手在路由器里把这台 Mac 设成静态 DHCP
绑定,防止 IP 变):

| 外部端口 | 内部 IP | 内部端口 | 协议 |
|---|---|---|---|
| 80 | Mac mini 的局域网 IP | 80 | TCP |
| 443 | Mac mini 的局域网 IP | 443 | TCP |

80 用于自动签发 HTTPS 证书,443 用于正式访问,**两条都必须配**。
配完直接跳到第 3 步。

### 2.4 备选:cloudflared tunnel(CGNAT / 不想开端口映射的走这条)

免公网 IP、免端口映射,原理是从你家往外主动连一条隧道。简要步骤:

1. 把域名的 DNS 托管迁到 Cloudflare(免费版即可,注册商处把 NS 改成
   Cloudflare 给的两条)。
2. 下载 cloudflared(macOS arm64 官方二进制):
   https://github.com/cloudflare/cloudflared/releases 下载
   `cloudflared-darwin-arm64.tgz`,解压后放 `~/bin/cloudflared`。
3. 依次执行 `cloudflared tunnel login`(浏览器授权)→
   `cloudflared tunnel create yappa` →
   `cloudflared tunnel route dns yappa yappa.example.com` →
   `cloudflared tunnel run --url http://127.0.0.1:8747 yappa`。
4. 走这条路线就不需要 Caddy 了(HTTPS 由 Cloudflare 出):第 4 步装完后
   执行 `launchctl unload ~/Library/LaunchAgents/com.yappa.caddy.plist`
   把 Caddy 停掉,再用 `cloudflared service install` 让隧道常驻。

以下按主路线(公网 IP + Caddy)继续。

## 第 3 步:配置 .env

仓库根的 `.env`(已 gitignore,密钥不进仓库)里追加一行,把入座链接从
局域网 IP 换成公网域名:

```
PUBLIC_BASE_URL=https://yappa.example.com
```

然后把权限收紧(里面有 API 密钥):

```
chmod 600 ~/aiparty-exp/.env
```

## 第 4 步:安装并启动(install.sh)

```
cd ~/aiparty-exp
zsh deploy/install.sh
```

脚本是幂等的,报错解决后重跑即可。它会:建日志目录 → 下载并校验 caddy
官方静态二进制到 `~/bin/caddy`(无 brew、无 sudo)→ 校验 Caddyfile →
把两个 plist(占位路径替换成你的实际家目录)装进 `~/Library/LaunchAgents`
→ `launchctl load` 启动。

**记得先把 `deploy/Caddyfile` 里的 `yappa.example.com` 换成你的域名再跑**
(只有一处,脚本发现没换会提醒)。

### 手动逐行执行版

不想跑脚本、或想看清每一步的,依次粘贴以下命令。每条都不带行内注释,
可整段粘贴;`yappa.example.com` 换成你的域名,前四段只在首次需要。

创建日志目录:

```
mkdir -p ~/Library/Logs/yappa
```

下载并校验 caddy,安装到 ~/bin:

```
mkdir -p ~/bin
cd "$(mktemp -d)"
curl -fsSLO https://github.com/caddyserver/caddy/releases/download/v2.10.0/caddy_2.10.0_mac_arm64.tar.gz
curl -fsSLO https://github.com/caddyserver/caddy/releases/download/v2.10.0/caddy_2.10.0_checksums.txt
grep " caddy_2.10.0_mac_arm64.tar.gz$" caddy_2.10.0_checksums.txt | shasum -a 256 -c -
tar -xzf caddy_2.10.0_mac_arm64.tar.gz caddy
mv caddy ~/bin/caddy
chmod +x ~/bin/caddy
~/bin/caddy version
```

校验 Caddyfile:

```
~/bin/caddy validate --config ~/aiparty-exp/deploy/Caddyfile
```

安装两个 plist(把占位路径替换成你的家目录):

```
sed "s|/Users/YOUR_USERNAME/aiparty-exp|$HOME/aiparty-exp|g; s|/Users/YOUR_USERNAME|$HOME|g" ~/aiparty-exp/deploy/com.yappa.server.plist > ~/Library/LaunchAgents/com.yappa.server.plist
sed "s|/Users/YOUR_USERNAME/aiparty-exp|$HOME/aiparty-exp|g; s|/Users/YOUR_USERNAME|$HOME|g" ~/aiparty-exp/deploy/com.yappa.caddy.plist > ~/Library/LaunchAgents/com.yappa.caddy.plist
```

加载启动:

```
launchctl unload ~/Library/LaunchAgents/com.yappa.server.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.yappa.server.plist
launchctl unload ~/Library/LaunchAgents/com.yappa.caddy.plist 2>/dev/null
launchctl load ~/Library/LaunchAgents/com.yappa.caddy.plist
```

## 第 5 步:验证

在 Mac 上(或直接用手机流量,更能证明公网通了):

```
curl https://yappa.example.com/api/state
```

看到一段 JSON(没开局时是 `{"no_session": true, ...}`)就是全通了。
首次访问 Caddy 要现签证书,可能慢 10–30 秒,属正常。

最后:**iPhone 上的 Expo App,服务器栏填 `https://yappa.example.com`**
(带 https://,不带端口);玩家手机浏览器开 `https://yappa.example.com/play`
入座,驾驶舱是 `https://yappa.example.com/`。

---

## 常见故障表

| 症状 | 最可能的原因 | 怎么办 |
|---|---|---|
| Caddy 日志里证书签发失败(obtaining certificate / challenge failed) | 80 端口从公网不通,或 DNS 还没生效/指错 IP | 先 `nslookup 域名` 确认解析到你的公网 IP;再检查路由器 80+443 两条映射都配了;个别宽带封 80,打运营商客服解封或换 cloudflared 方案 |
| 手机流量下连不上 / 超时 | 端口映射没配好,或公网 IP 变了 | 路由器里核对映射规则和 Mac 的内网 IP;`curl -4 ifconfig.me` 对比 DNS A 记录,变了就改记录 |
| `curl https://域名/api/state` 报 502 | Caddy 活着但游戏服务没起 | 看 `~/Library/Logs/yappa/server.err.log`,常见是 WorkingDirectory 路径不对或 python3 报错 |
| 服务根本没起 | plist 路径没替换 / 没加载 | `launchctl list \| grep yappa` 看两个服务在不在;不在就重跑 install.sh;在但 PID 为 `-` 且状态非 0,看对应 `.err.log` |
| 入座链接还是 `http://192.168.x.x:8747` | `.env` 里没配 PUBLIC_BASE_URL,或配完没重启服务 | 补上后执行下面「重启服务」命令 |
| 本机 curl 通、别人手机不通 | 只在同一 Wi-Fi 下测的,走了局域网回环 | 用手机关掉 Wi-Fi 走流量再测,才是真·公网验证 |

## 日常运维(三条命令)

看日志(另有 server.err.log / caddy.log / caddy.err.log):

```
tail -f ~/Library/Logs/yappa/server.err.log
```

重启游戏服务(KeepAlive 会自动拉起新进程;重启 Caddy 同理换文件名):

```
launchctl kickstart -k gui/$(id -u)/com.yappa.server
```

更新代码并重启:

```
cd ~/aiparty-exp && git pull && launchctl kickstart -k gui/$(id -u)/com.yappa.server
```

## 安全注意

- **8747 不要直接暴露公网**:plist 故意不加 `--lan`,游戏服务只绑
  `127.0.0.1`,外界只能走 Caddy 的 HTTPS 进来。不要在路由器上映射 8747,
  也不要为了省事给服务加回 `--lan`。
- **`.env` 权限保持 600**(`chmod 600 ~/aiparty-exp/.env`),它已在
  gitignore 里,永远不要提交或截图。
- 域名公开后任何人都能打开入座页;开局房间码就是门票,别在公开场合晒
  带房间码的截图。
