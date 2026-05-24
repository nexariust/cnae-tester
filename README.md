# 探测节点

## 安装依赖

```bash
python -m pip install -r tester/requirements.txt
```

## 启动

```bash
export TESTER_SERVER_URL="https://api.cnae.top"
export TESTER_BOOTSTRAP_TOKEN="你在控制台生成的"
export TESTER_MAX_DOWNLOAD_SPEED_MBPS=3
python tester/tester_client.py
```

## Docker Compose 示例

```yaml
services:
  cnae-tester:
    image: ghcr.io/nexariust/cnae-tester:latest
    container_name: cnae-tester
    restart: unless-stopped
    environment:
      TESTER_SERVER_URL: https://api.cnae.top
      TESTER_BOOTSTRAP_TOKEN: 你在控制台生成的
      TESTER_MAX_DOWNLOAD_SPEED_MBPS: 3
      TZ: Asia/Shanghai
    volumes:
      - ./data:/data
```

## Linux 启动

```bash
cd /opt/cnae
python3 -m pip install -r tester/requirements.txt

export TESTER_SERVER_URL="https://api.cnae.top"
export TESTER_BOOTSTRAP_TOKEN="你在控制台生成的"
export TESTER_MAX_DOWNLOAD_SPEED_MBPS=3

python3 tester/tester_client.py
```

如果你想长期常驻运行，可以用 `systemd`：

```ini
[Unit]
Description=CNAE Tester Client
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/cnae
Environment=TESTER_SERVER_URL=https://api.cnae.top
Environment=TESTER_BOOTSTRAP_TOKEN=你在控制台生成的
Environment=TESTER_MAX_DOWNLOAD_SPEED_MBPS=3
ExecStart=/usr/bin/python3 /opt/cnae/tester/tester_client.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## 常用环境变量

- `TESTER_SERVER_URL`: 控制台地址，必须包含协议和端口
- `TESTER_BOOTSTRAP_TOKEN`: 首次注册探测节点时使用的共享口令
- `TESTER_MAX_DOWNLOAD_SPEED_MBPS`: 下载测速阶段的最大速度上限，单位 MB/s，默认 `0` 表示不限速
- `TESTER_TOKEN_FILE`: 唯一探测节点 token 的本地缓存路径，默认 `tester/.tester-token`
- `TESTER_HTTP_TIMEOUT`: 请求控制台接口的超时时间，默认 `30`
- `TESTER_HEARTBEAT_INTERVAL`: WebSocket 心跳间隔，默认 `30`

## 说明

- 首次启动会用你在控制台生成的换取唯一探测节点 token
- 控制台会在首次注册时自动分配一个 12 位探测节点名称
- token 会缓存到本地，后续启动会自动复用
- 删除 `tester/.tester-token` 后可重新注册一个新的探测节点身份
- 如果管理员在后台删除了该探测节点，当前 token 会立即失效；客户端会清理本地 token，并在下一轮自动重新注册
