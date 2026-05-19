"""检查 API Key 配置是否正确"""

import os
import sys


def check_env_var(name: str) -> str | None:
    """检查环境变量是否存在"""
    value = os.environ.get(name)
    if value:
        # 只显示前4位和后4位，中间用***隐藏
        masked = f"{value[:4]}...{value[-4:]}" if len(value) > 8 else "***"
        print(f"  {name}: {masked}")
        return value
    else:
        print(f"  {name}: (未设置)")
        return None

from deepagents_cli.server_graph import make_graph
def test_minimax():
    sys.path.insert(0, '../../../..')
    make_graph()
    """测试 MiniMax API (兼容 Anthropic 格式)"""
    print("\n=== 测试 MiniMax (Anthropic 兼容) ===")

    base_url = os.environ.get("ANTHROPIC_BASE_URL", "https://api.minimaxi.com/anthropic")
    api_key = os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("ANTHROPIC_MODEL", "MiniMax-M2.7-highspeed")

    print(f"  Base URL: {base_url}")
    print(f"  Model: {model}")
    if api_key:
        masked = f"{api_key[:8]}...{api_key[-4:]}" if len(api_key) > 12 else "***"
        print(f"  API Key: {masked}")
    else:
        print("  API Key: (未设置)")
        return False

    try:
        import anthropic
        client = anthropic.Anthropic(
            api_key=api_key,
            base_url=base_url,
            timeout=300.0,  # 50分钟超时
        )
        message = client.messages.create(
            model=model,
            max_tokens=100,
            messages=[{"role": "user", "content": "Hello, say 'Hi' in one word."}]
        )
        # 处理不同类型的返回内容
        content = message.content[0]
        if hasattr(content, 'text'):
            response_text = content.text
        elif hasattr(content, 'thinking'):
            response_text = f"[thinking: {content.thinking[:50]}...]"
        else:
            response_text = str(content)
        print(f"  成功! Response: {response_text}")
        return True
    except Exception as e:
        print(f"  失败: {type(e).__name__}: {e}")
        return False


def test_openai():
    """测试 OpenAI API"""
    print("\n=== 测试 OpenAI ===")
    api_key = check_env_var("OPENAI_API_KEY")
    if not api_key:
        return False

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=10
        )
        print(f"  成功! Response: {response.choices[0].message.content}")
        return True
    except Exception as e:
        print(f"  失败: {type(e).__name__}: {e}")
        return False


def main():
    print("=== API Key 检查工具 ===")
    print(f"Python: {sys.version}")
    print(f"工作目录: {os.getcwd()}")

    # 检查常见 API Key 环境变量
    print("\n=== 环境变量 ===")
    check_env_var("ANTHROPIC_BASE_URL")
    check_env_var("ANTHROPIC_AUTH_TOKEN")
    check_env_var("ANTHROPIC_API_KEY")
    check_env_var("ANTHROPIC_MODEL")
    check_env_var("OPENAI_API_KEY")

    # 测试各个 API
    results = []
    results.append(("MiniMax", test_minimax()))
    results.append(("OpenAI", test_openai()))

    print("\n=== 总结 ===")
    for name, success in results:
        status = "✓ 通过" if success else "✗ 失败"
        print(f"  {name}: {status}")

    if not any(s for _, s in results):
        print("\n没有可用的 API Key，请设置环境变量后重试")


if __name__ == "__main__":
    main()