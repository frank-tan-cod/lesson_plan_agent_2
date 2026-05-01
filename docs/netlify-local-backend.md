# Netlify 前端 + 本地后端公网访问

目标：别人只需要打开 `https://lesson-agent.netlify.app/` 就能使用前端；你的 FastAPI 后端仍然运行在本机，通过公网 HTTPS 隧道让外部浏览器访问。

## 推荐方案

最适合演示和小范围试用：

1. 前端部署到 Netlify。
2. 本地后端运行在 `127.0.0.1:8000`。
3. 用 ngrok 或 Cloudflare Tunnel 给本地后端分配一个稳定 HTTPS 地址。
4. Netlify 的 `NEXT_PUBLIC_API_URL` 指向这个后端 HTTPS 地址。

限制：你的电脑必须开机，后端进程和隧道进程都必须运行。电脑断网或隧道关闭后，别人就不能使用。

## 第 1 步：部署前端到 Netlify

项目根目录已经提供 `netlify.toml`，Netlify 会使用这些设置：

```txt
Base directory: frontend
Build command: npm run build
Publish directory: .next
```

在 Netlify 页面：

1. 打开 `https://app.netlify.com/`。
2. 选择 Add new project。
3. 连接你的 Git 仓库。
4. 确认站点域名为 `lesson-agent.netlify.app`，或在 Domain settings 里改成这个名字。

## 第 2 步：准备后端公网地址

### 方案 A：ngrok，最快

安装并登录 ngrok 后运行：

```powershell
ngrok http 8000
```

它会给出一个 HTTPS 地址，例如：

```txt
https://abc123.ngrok-free.app
```

这个地址就是后端公网地址。免费地址可能会变化；如果地址变了，需要同步更新 Netlify 环境变量并重新部署。

### 方案 B：Cloudflare Tunnel，适合长期稳定域名

如果你有自己的域名，例如 `example.com`，可以在 Cloudflare Tunnel 里发布：

```txt
Public hostname: api.example.com
Service: http://localhost:8000
```

最终后端公网地址类似：

```txt
https://api.example.com
```

不要用 Quick Tunnel 做长期方案，因为这个项目有 SSE 流式请求，Cloudflare 官方说明 Quick Tunnels 不支持 SSE。

## 第 3 步：配置 Netlify 环境变量

进入 Netlify 项目：

```txt
Project configuration -> Environment variables -> Add a variable
```

添加：

```txt
NEXT_PUBLIC_API_URL=https://你的后端公网地址
```

例如：

```txt
NEXT_PUBLIC_API_URL=https://abc123.ngrok-free.app
```

保存后重新 Deploy。Next.js 的 `NEXT_PUBLIC_*` 变量会在构建时写入前端包，所以改完变量必须重新部署。

## 第 4 步：启动本地后端

在 PowerShell 里运行：

```powershell
cd D:\desktop\lesson_plan_agent_2

$env:CORS_ALLOW_ORIGINS="https://lesson-agent.netlify.app"
$env:PUBLIC_BASE_URL="https://你的后端公网地址"
$env:AUTH_COOKIE_SECURE="1"
$env:AUTH_COOKIE_SAMESITE="none"
$env:JWT_SECRET_KEY="换成一个很长的随机字符串"

.\scripts\run-backend-dev.ps1
```

如果你想隐藏窗口后台运行：

```powershell
.\scripts\start-backend-dev.ps1
```

注意：`PUBLIC_BASE_URL` 必须和隧道给出的 HTTPS 地址一致，否则导出的课件、游戏链接、上传资源链接可能仍然指向本地地址。

## 第 5 步：保持两个进程运行

至少要保持这两个窗口或后台进程都在运行：

```txt
FastAPI 后端: 127.0.0.1:8000
公网隧道: ngrok 或 cloudflared
```

别人访问：

```txt
https://lesson-agent.netlify.app/
```

实际请求链路是：

```txt
访问者浏览器 -> Netlify 前端 -> 后端公网 HTTPS 隧道 -> 你本机 FastAPI
```

## 更正式的上线方案

如果你希望别人随时都能访问，不依赖你的电脑，建议把后端也部署到云端：

1. 前端：Netlify。
2. 后端：Render、Railway、Fly.io、VPS、阿里云、腾讯云等。
3. 数据库和上传文件：迁移到云数据库和对象存储。
4. `NEXT_PUBLIC_API_URL` 指向云端后端域名。

这是长期使用的正确方案。本地后端 + 隧道更适合临时演示、内测和课堂试用。

## 常见问题

不能把 `NEXT_PUBLIC_API_URL` 设置成 `http://localhost:8000`。部署以后，访问者浏览器里的 localhost 是访问者自己的电脑，不是你的电脑。

Netlify 是 HTTPS 页面，所以后端也必须是 HTTPS 地址。不要让线上前端请求普通 HTTP 后端。

如果登录失败但接口能访问，通常是跨域 cookie 被浏览器拦截。可以改成同域代理，或者让前端保存登录返回的 token 并通过 `Authorization: Bearer ...` 调用后端。
