# Lesson Plan Agent Frontend

基于 Next.js 14 App Router + TypeScript + TailwindCSS 构建的前端工作台，对接当前仓库中的 FastAPI 后端。

## 已实现页面

- `/login` / `/register`：注册登录与 `httpOnly cookie` 会话鉴权
- `/documents`：教案 / 演示文稿列表、筛选、搜索、删除
- `/documents/create`：新建教案或 PPT
- `/documents/[id]/editor`：预览 + SSE 对话编辑器、导出、回退点、临时偏好
- `/knowledge`：知识库上传、列表、删除、语义搜索
- `/preferences`：全局偏好管理、自然语言解析建议
- `/profile`：个人信息与退出登录

## 环境变量

默认已提供 `frontend/.env.local`：

```bash
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## 本地运行

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:3000`。

## 说明

- JWT 会话保存在后端签发的 `httpOnly cookie`，前端仅缓存非敏感用户资料
- 编辑器通过 `fetch + ReadableStream` 处理后端 SSE
- 为避免重复执行写操作，流式编辑默认不做自动重试续连
- 修改密码入口暂未实现，因为后端当前没有对应接口
