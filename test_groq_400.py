import httpx
import asyncio
import os

async def main():
    api_key = os.getenv("GROQ_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"}
    
    tools = [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string"}
                },
                "required": ["location"]
            }
        }
    }]
    
    payload = {
        "model": "allam-2-7b",
        "messages": [{"role": "user", "content": "What is the weather in Seattle?"}],
        "tools": tools,
        "tool_choice": "auto"
    }
    
    async with httpx.AsyncClient() as client:
        res = await client.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers)
        print("Status:", res.status_code)
        print("Body:", res.text)
        
    payload["model"] = "llama-3.1-8b-instant"
    async with httpx.AsyncClient() as client:
        res = await client.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers)
        print("Status:", res.status_code)
        print("Body:", res.text)

asyncio.run(main())
