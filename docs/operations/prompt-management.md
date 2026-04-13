# Prompt Management

## Principle

- 所有 prompt 统一存放在根目录 `prompts/`
- 代码里只做变量注入，不直接写长 prompt 文本
- 每次模型调用前，通过 `PromptRegistry` 加载并渲染 prompt

## Injection Flow

1. 从 `prompts/deepseek/*.md` 读取模板
2. 用运行时上下文填充变量
3. 将渲染后的 prompt 传给 DeepSeek 客户端

## Benefits

- prompt 可以独立版本管理
- debug 时可以直接查看 prompt 文件
- 不同模型后续可以按目录扩展
