"""Test script for MiniMax model via deepagents CLI config."""

import os

# Set up environment variables for MiniMax
os.environ["ANTHROPIC_AUTH_TOKEN"] = "your-api-key-here"
os.environ["ANTHROPIC_BASE_URL"] = "https://api.minimaxi.com/anthropic"

from deepagents_cli.config import create_model


def test_minimax_model():
    """Test creating and using MiniMax model."""
    print("Testing MiniMax model...")

    # Create the model using the custom class_path
    result = create_model("anthropic:MiniMax-M2.7-highspeed")
    model = result.model

    print(f"Model type: {type(model).__name__}")
    print(f"Model name: {model.model}")
    print(f"Base URL: {model.anthropic_api_url}")
    print(f"API key set: {bool(model.anthropic_api_key)}")

    # Test a simple chat call
    print("\nTesting chat completion...")
    response = model.invoke("Say 'Hello from MiniMax' in exactly those words.")
    print(f"Response: {response.content}")


if __name__ == "__main__":
    test_minimax_model()