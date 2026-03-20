"""
Quick diagnostic: test each pipeline step independently.
Run from backend/ with: python test_pipeline.py
"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

async def test_ollama():
    """Test if Ollama is reachable and the model works."""
    print("\n[TEST] Ollama connectivity...")
    from services.scoring import _ollama_chat
    try:
        result = await _ollama_chat("You are helpful.", "Say 'hello' in one word.")
        print(f"  ✓ Ollama responded: {result[:100]}")
        return True
    except Exception as e:
        print(f"  ✗ Ollama FAILED: {e}")
        print(f"  → Is 'ollama serve' running? Is the model pulled?")
        return False

async def test_tinyfish_api():
    """Test if TinyFish API key works with a minimal agent call."""
    print("\n[TEST] TinyFish API connectivity...")
    key = os.getenv("TINYFISH_API_KEY")
    if not key:
        print(f"  ✗ TINYFISH_API_KEY is not set!")
        return False
    print(f"  Key loaded: {key[:20]}...")

    from services.tinyfish import run_agent
    try:
        result = await run_agent(
            url="https://www.google.com",
            goal="What is the page title? Return JSON: {\"title\": str}",
            stealth=False
        )
        print(f"  ✓ TinyFish responded: {result}")
        return True
    except Exception as e:
        print(f"  ✗ TinyFish FAILED: {e}")
        return False

async def test_keyword_expansion():
    """Test Ollama keyword expansion with a sample brief."""
    print("\n[TEST] Keyword expansion...")
    from services.scoring import expand_keywords
    brief = {"niche": "fitness", "target_audience": "men 18-35"}
    try:
        keywords = await expand_keywords(brief)
        print(f"  ✓ Keywords: {keywords}")
        return True
    except Exception as e:
        print(f"  ✗ FAILED: {e}")
        return False

async def main():
    print("="*60)
    print("CreatorLens Pipeline Diagnostic")
    print("="*60)

    # Check env vars
    print("\n[TEST] Environment variables...")
    key = os.getenv("TINYFISH_API_KEY")
    ollama_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.getenv("OLLAMA_MODEL", "llama3.2")
    print(f"  TINYFISH_API_KEY: {'✓ set' if key else '✗ NOT SET'}")
    print(f"  OLLAMA_BASE_URL: {ollama_url}")
    print(f"  OLLAMA_MODEL: {model}")

    # Test Ollama
    ollama_ok = await test_ollama()

    # Test TinyFish
    tinyfish_ok = await test_tinyfish_api()

    # Test keyword expansion (depends on Ollama)
    if ollama_ok:
        await test_keyword_expansion()

    print(f"\n{'='*60}")
    print(f"Summary:")
    print(f"  Ollama:   {'✓ PASS' if ollama_ok else '✗ FAIL'}")
    print(f"  TinyFish: {'✓ PASS' if tinyfish_ok else '✗ FAIL'}")
    print(f"{'='*60}")

if __name__ == "__main__":
    asyncio.run(main())
