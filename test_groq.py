
from dotenv import load_dotenv
from groq import Groq
import os

load_dotenv()

api_key = os.getenv("LLM_API_KEY")
model = os.getenv("LLM_MODEL")

if not api_key or not model:
    print("ERROR: Check LLM_API_KEY and LLM_MODEL in .env")
else:
    try:
        client = Groq(api_key=api_key)

        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": "Explain an AI Planner Agent in 2 simple sentences."
                }
            ],
        )

        print("AI CONNECTION SUCCESSFUL!")
        print(response.choices[0].message.content)

    except Exception as error:
        print("AI CONNECTION FAILED!")
        print(type(error).__name__, str(error))
